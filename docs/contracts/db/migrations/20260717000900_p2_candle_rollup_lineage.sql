-- migrate:up

-- 원천 내용만 해시한다. 수집 시각이 달라도 같은 내용이면 같은 개정이다.
CREATE OR REPLACE FUNCTION source_candle_content_hash(
  p_open NUMERIC, p_high NUMERIC, p_low NUMERIC, p_close NUMERIC,
  p_volume NUMERIC, p_trade_amount NUMERIC
) RETURNS TEXT
LANGUAGE SQL IMMUTABLE STRICT PARALLEL SAFE
RETURN encode(sha256(convert_to(concat_ws('|',
  trim_scale(p_open)::text, trim_scale(p_high)::text, trim_scale(p_low)::text,
  trim_scale(p_close)::text, trim_scale(p_volume)::text, trim_scale(p_trade_amount)::text
), 'UTF8')), 'hex');

-- 001 적용 뒤 레거시 writer가 market_id 없이 적재한 행도 빠짐없이 개정 1로 백필한다.
INSERT INTO markets (
  exchange, market_code, quote_currency, base_asset, korean_name, english_name,
  legacy_instrument_id, first_observed_at, last_observed_at
)
SELECT
  instrument.exchange,
  instrument.market_code,
  instrument.quote_currency,
  instrument.base_asset,
  instrument.display_name,
  instrument.display_name,
  instrument.id,
  instrument.created_at,
  GREATEST(instrument.created_at, instrument.updated_at)
FROM instruments instrument
ON CONFLICT (exchange, market_code) DO UPDATE SET
  legacy_instrument_id = COALESCE(markets.legacy_instrument_id, excluded.legacy_instrument_id),
  last_observed_at = GREATEST(markets.last_observed_at, excluded.last_observed_at),
  updated_at = now();

UPDATE source_candles source
SET
  market_id = market.id,
  occurred_at = COALESCE(source.occurred_at, source.candle_start_at),
  received_at = COALESCE(source.received_at, source.collected_at),
  stored_at = COALESCE(source.stored_at, source.created_at),
  knowledge_at = COALESCE(source.knowledge_at, source.collected_at)
FROM markets market
WHERE market.legacy_instrument_id = source.instrument_id
  AND source.market_id IS NULL;

CREATE TABLE source_candle_revisions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_candle_id BIGINT NOT NULL REFERENCES source_candles(id),
  revision_number INTEGER NOT NULL CHECK (revision_number > 0),
  market_id BIGINT NOT NULL REFERENCES markets(id),
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  source TEXT NOT NULL CHECK (source IN ('UPBIT')),
  candle_unit TEXT NOT NULL CHECK (candle_unit IN ('1m', '1d')),
  candle_start_at TIMESTAMPTZ NOT NULL,
  open_price NUMERIC NOT NULL,
  high_price NUMERIC NOT NULL,
  low_price NUMERIC NOT NULL,
  close_price NUMERIC NOT NULL,
  trade_volume NUMERIC NOT NULL,
  trade_amount NUMERIC NOT NULL,
  source_as_of TIMESTAMPTZ NOT NULL,
  knowledge_at TIMESTAMPTZ NOT NULL,
  input_content_hash TEXT NOT NULL CHECK (input_content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (source_candle_id, revision_number)
);

INSERT INTO source_candle_revisions (
  source_candle_id, revision_number, market_id, instrument_id, source, candle_unit,
  candle_start_at, open_price, high_price, low_price, close_price, trade_volume,
  trade_amount, source_as_of, knowledge_at, input_content_hash
)
SELECT
  candle.id, 1, candle.market_id, candle.instrument_id, candle.source, candle.candle_unit,
  candle.candle_start_at, candle.open_price, candle.high_price, candle.low_price,
  candle.close_price, candle.trade_volume, candle.trade_amount, candle.collected_at,
  COALESCE(candle.knowledge_at, candle.collected_at),
  source_candle_content_hash(
    candle.open_price, candle.high_price, candle.low_price, candle.close_price,
    candle.trade_volume, candle.trade_amount
  )
FROM source_candles candle
WHERE candle.market_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- 원장은 append-only다. 잘못된 수정과 삭제는 DB 경계에서도 거부한다.
CREATE OR REPLACE FUNCTION reject_source_candle_revision_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'source_candle_revisions is append-only';
END;
$$;

CREATE TRIGGER source_candle_revisions_append_only_update
BEFORE UPDATE ON source_candle_revisions
FOR EACH ROW EXECUTE FUNCTION reject_source_candle_revision_mutation();
CREATE TRIGGER source_candle_revisions_append_only_delete
BEFORE DELETE ON source_candle_revisions
FOR EACH ROW EXECUTE FUNCTION reject_source_candle_revision_mutation();

UPDATE candle_rollups SET candle_unit = '1h' WHERE candle_unit = '60m';
UPDATE candle_aggregation_job_targets SET candle_unit = '1h' WHERE candle_unit = '60m';

ALTER TABLE candle_rollups
  DROP CONSTRAINT candle_rollups_pkey,
  DROP CONSTRAINT candle_rollups_unit_ck,
  ADD COLUMN calculation_version TEXT NOT NULL DEFAULT 'candle-rollup-v2',
  ADD COLUMN source_as_of TIMESTAMPTZ,
  ADD COLUMN knowledge_at TIMESTAMPTZ,
  ADD COLUMN input_content_hash TEXT,
  ADD COLUMN input_revision_ids BIGINT[] NOT NULL DEFAULT '{}'::BIGINT[],
  ADD COLUMN quality TEXT NOT NULL DEFAULT 'unverified';

UPDATE candle_rollups rollup
SET source_as_of = rollup.materialized_at,
    knowledge_at = rollup.materialized_at,
    input_content_hash = encode(sha256(convert_to(concat_ws('|',
      rollup.instrument_id::text, rollup.candle_unit, rollup.candle_start_at::text,
      rollup.open_price::text, rollup.high_price::text, rollup.low_price::text,
      rollup.close_price::text, rollup.trade_volume::text, rollup.trade_amount::text
    ), 'UTF8')), 'hex');

ALTER TABLE candle_rollups
  ALTER COLUMN source_as_of SET NOT NULL,
  ALTER COLUMN knowledge_at SET NOT NULL,
  ALTER COLUMN input_content_hash SET NOT NULL,
  ADD PRIMARY KEY (instrument_id, candle_unit, candle_start_at, calculation_version),
  ADD CONSTRAINT candle_rollups_unit_ck CHECK (candle_unit IN (
    '3m', '5m', '10m', '15m', '30m', '1h', '4h', '1d', '1w', '1M'
  )),
  ADD CONSTRAINT candle_rollups_hash_ck CHECK (input_content_hash ~ '^[0-9a-f]{64}$'),
  ADD CONSTRAINT candle_rollups_quality_ck CHECK (quality IN (
    'available', 'no_trade', 'missing', 'unavailable', 'unverified'
  ));

ALTER TABLE candle_aggregation_job_targets
  DROP CONSTRAINT candle_aggregation_job_targets_unit_ck,
  ADD CONSTRAINT candle_aggregation_job_targets_unit_ck CHECK (candle_unit IN (
    '3m', '5m', '10m', '15m', '30m', '1h', '4h', '1d', '1w', '1M'
  ));

CREATE INDEX source_candle_revisions_lookup_idx
  ON source_candle_revisions (instrument_id, candle_unit, candle_start_at, revision_number DESC);
CREATE INDEX candle_rollups_range_idx
  ON candle_rollups (instrument_id, candle_unit, calculation_version, candle_start_at DESC);

-- 운영에서는 migration 사용자와 runtime 사용자가 같다. 별도 역할을 쓰는 환경은
-- 준비성(readiness) 권한 집합을 통해 같은 최소 권한을 부여한다.
GRANT SELECT, INSERT ON TABLE source_candle_revisions TO CURRENT_USER;
GRANT USAGE, SELECT ON SEQUENCE source_candle_revisions_id_seq TO CURRENT_USER;
GRANT SELECT, INSERT, UPDATE ON TABLE candle_rollups TO CURRENT_USER;

-- migrate:down
-- 원천 개정 원장과 계보 정보는 forward-only 계약이므로 자동 삭제하지 않는다.
SELECT 1;
