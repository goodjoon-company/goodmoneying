-- migrate:up

-- P2-3/P2-5의 시점 기준 재현성을 위해 집계도 제자리 갱신 행이 아니라
-- 입력 내용별 불변 개정 원장으로 승격한다.
ALTER TABLE candle_rollups
  DROP CONSTRAINT candle_rollups_pkey,
  ADD COLUMN id BIGINT GENERATED ALWAYS AS IDENTITY,
  ADD COLUMN source_revision_through_id BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN quality_event_through_id BIGINT REFERENCES data_quality_events(id),
  ADD COLUMN coverage_snapshot_hash TEXT,
  ADD COLUMN result_content_hash TEXT,
  ADD PRIMARY KEY (id);

UPDATE candle_rollups rollup
SET source_revision_through_id = COALESCE((
      SELECT MAX(revision_id) FROM unnest(rollup.input_revision_ids) AS revision_id
    ), 0),
    coverage_snapshot_hash = encode(sha256(convert_to('legacy-unfrozen', 'UTF8')), 'hex'),
    result_content_hash = encode(sha256(convert_to(concat_ws('|',
      rollup.calculation_version,
      trim_scale(rollup.open_price)::text, trim_scale(rollup.high_price)::text,
      trim_scale(rollup.low_price)::text, trim_scale(rollup.close_price)::text,
      trim_scale(rollup.trade_volume)::text, trim_scale(rollup.trade_amount)::text,
      rollup.completeness, rollup.quality
    ), 'UTF8')), 'hex');

ALTER TABLE candle_rollups
  ALTER COLUMN coverage_snapshot_hash SET NOT NULL,
  ALTER COLUMN result_content_hash SET NOT NULL,
  ADD CONSTRAINT candle_rollups_coverage_hash_ck CHECK (
    coverage_snapshot_hash ~ '^[0-9a-f]{64}$'
  ),
  ADD CONSTRAINT candle_rollups_result_hash_ck CHECK (
    result_content_hash ~ '^[0-9a-f]{64}$'
  ),
  ADD CONSTRAINT candle_rollups_revision_uk UNIQUE NULLS NOT DISTINCT (
    instrument_id, candle_unit, candle_start_at, calculation_version,
    input_content_hash, coverage_snapshot_hash,
    source_revision_through_id, quality_event_through_id
  );

CREATE INDEX candle_rollups_current_projection_idx
  ON candle_rollups (
    instrument_id, candle_unit, calculation_version, candle_start_at,
    source_revision_through_id DESC, quality_event_through_id DESC NULLS LAST,
    knowledge_at DESC, id DESC
  );

CREATE OR REPLACE FUNCTION current_rollup_quality_ceiling(
  p_market_id BIGINT,
  p_range_start_at TIMESTAMPTZ,
  p_range_end_at TIMESTAMPTZ
)
RETURNS TABLE (quality_event_through_id BIGINT, knowledge_at TIMESTAMPTZ)
LANGUAGE SQL STABLE SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
  SELECT MAX(event.id), MAX(event.detected_at)
  FROM public.data_quality_events event
  JOIN public.collection_target_specs specification
    ON specification.id = event.target_spec_id
  WHERE specification.market_id = p_market_id
    AND specification.data_type = 'source_candle'
    AND specification.candle_unit IN ('1m', '1d')
    AND tstzrange(event.range_start_at, event.range_end_at, '[)')
        && tstzrange(p_range_start_at, p_range_end_at, '[)')
$$;

CREATE OR REPLACE FUNCTION reject_candle_rollup_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'candle_rollups is append-only';
END;
$$;

CREATE TRIGGER candle_rollups_append_only_update
BEFORE UPDATE ON candle_rollups
FOR EACH ROW EXECUTE FUNCTION reject_candle_rollup_mutation();
CREATE TRIGGER candle_rollups_append_only_delete
BEFORE DELETE ON candle_rollups
FOR EACH ROW EXECUTE FUNCTION reject_candle_rollup_mutation();

CREATE TABLE candle_rollup_invalidations (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  market_id BIGINT NOT NULL REFERENCES markets(id),
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  candle_unit TEXT NOT NULL CHECK (candle_unit IN (
    '3m', '5m', '10m', '15m', '30m', '1h', '4h', '1d', '1w', '1M'
  )),
  calculation_version TEXT NOT NULL,
  range_start_at TIMESTAMPTZ NOT NULL,
  range_end_at TIMESTAMPTZ NOT NULL,
  output_bucket_count INTEGER NOT NULL CHECK (output_bucket_count BETWEEN 1 AND 512),
  source_revision_ids BIGINT[] NOT NULL,
  source_revision_through_id BIGINT NOT NULL REFERENCES source_candle_revisions(id),
  quality_event_through_id BIGINT REFERENCES data_quality_events(id),
  coverage_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb,
  coverage_snapshot_hash TEXT NOT NULL CHECK (coverage_snapshot_hash ~ '^[0-9a-f]{64}$'),
  knowledge_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CHECK (range_start_at < range_end_at)
);

CREATE TABLE candle_rollup_recompute_jobs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  invalidation_id BIGINT NOT NULL UNIQUE REFERENCES candle_rollup_invalidations(id),
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
    'pending', 'running', 'retry_wait', 'succeeded', 'dead_letter', 'cancelled'
  )),
  priority INTEGER NOT NULL DEFAULT 100,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
  next_retry_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  lease_owner TEXT,
  lease_expires_at TIMESTAMPTZ,
  processing_source_revision_through_id BIGINT REFERENCES source_candle_revisions(id),
  processing_quality_event_through_id BIGINT REFERENCES data_quality_events(id),
  rows_written INTEGER NOT NULL DEFAULT 0 CHECK (rows_written >= 0),
  last_error_code TEXT,
  dead_letter_reason TEXT,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CHECK ((status = 'running') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL))
);

CREATE INDEX candle_rollup_recompute_jobs_claim_idx
  ON candle_rollup_recompute_jobs (
    status, next_retry_at, lease_expires_at, priority DESC, created_at
  );
CREATE INDEX candle_rollup_invalidations_range_idx
  ON candle_rollup_invalidations (
    instrument_id, candle_unit, calculation_version, range_start_at, range_end_at
  );
CREATE INDEX source_candle_revisions_incremental_lookup_idx
  ON source_candle_revisions (
    instrument_id, candle_unit, candle_start_at, id DESC
  );

CREATE OR REPLACE FUNCTION reject_candle_rollup_invalidation_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'candle_rollup_invalidations is append-only';
END;
$$;

CREATE TRIGGER candle_rollup_invalidations_append_only_update
BEFORE UPDATE ON candle_rollup_invalidations
FOR EACH ROW EXECUTE FUNCTION reject_candle_rollup_invalidation_mutation();
CREATE TRIGGER candle_rollup_invalidations_append_only_delete
BEFORE DELETE ON candle_rollup_invalidations
FOR EACH ROW EXECUTE FUNCTION reject_candle_rollup_invalidation_mutation();

GRANT SELECT, INSERT ON TABLE candle_rollup_invalidations TO CURRENT_USER;
GRANT USAGE, SELECT ON SEQUENCE candle_rollup_invalidations_id_seq TO CURRENT_USER;
GRANT SELECT, INSERT, UPDATE ON TABLE candle_rollup_recompute_jobs TO CURRENT_USER;
GRANT USAGE, SELECT ON SEQUENCE candle_rollup_recompute_jobs_id_seq TO CURRENT_USER;
GRANT SELECT, INSERT ON TABLE candle_rollups TO CURRENT_USER;
REVOKE UPDATE, DELETE ON TABLE candle_rollups FROM CURRENT_USER;
GRANT USAGE, SELECT ON SEQUENCE candle_rollups_id_seq TO CURRENT_USER;
REVOKE ALL ON FUNCTION current_rollup_quality_ceiling(BIGINT, TIMESTAMPTZ, TIMESTAMPTZ)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION current_rollup_quality_ceiling(BIGINT, TIMESTAMPTZ, TIMESTAMPTZ)
  TO CURRENT_USER;

-- migrate:down
-- 무효화 계보와 작업 이력은 재현성 증적이므로 자동 삭제하지 않는다.
SELECT 1;
