-- migrate:up

-- P1 시스템 트레이딩 데이터 기반 확장 계약.
-- 이 migration은 legacy 테이블과 행을 제거하지 않고 신규 계약을 병행 추가한다.

SET TIME ZONE 'UTC';
DO $$
BEGIN
  EXECUTE format(
    'ALTER DATABASE %I SET timezone TO %L',
    current_database(),
    'UTC'
  );
END
$$;

CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE IF NOT EXISTS markets (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  exchange TEXT NOT NULL,
  market_code TEXT NOT NULL,
  quote_currency TEXT NOT NULL,
  base_asset TEXT NOT NULL,
  korean_name TEXT NOT NULL,
  english_name TEXT NOT NULL,
  legacy_instrument_id BIGINT UNIQUE REFERENCES instruments(id),
  first_observed_at TIMESTAMPTZ NOT NULL,
  last_observed_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT markets_exchange_market_code_uk UNIQUE (exchange, market_code),
  CONSTRAINT markets_exchange_ck CHECK (exchange IN ('UPBIT')),
  CONSTRAINT markets_observation_range_ck CHECK (first_observed_at <= last_observed_at)
);

CREATE TABLE IF NOT EXISTS market_status_history (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  market_id BIGINT NOT NULL REFERENCES markets(id),
  trading_status TEXT NOT NULL,
  market_warning TEXT NOT NULL,
  market_event JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_payload_checksum TEXT NOT NULL,
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ,
  observed_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT market_status_history_market_from_uk UNIQUE (market_id, valid_from),
  CONSTRAINT market_status_history_status_ck CHECK (
    trading_status IN ('active', 'inactive', 'delisted', 'unknown')
  ),
  CONSTRAINT market_status_history_range_ck CHECK (
    valid_to IS NULL OR valid_from < valid_to
  ),
  EXCLUDE USING gist (
    market_id WITH =,
    tstzrange(valid_from, valid_to, '[)') WITH &&
  )
);

CREATE TABLE IF NOT EXISTS collection_policies (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  exchange TEXT NOT NULL,
  quote_currency TEXT NOT NULL,
  name TEXT NOT NULL,
  default_start_at TIMESTAMPTZ,
  lookback_years INTEGER,
  retention_days INTEGER,
  priority INTEGER NOT NULL DEFAULT 100,
  auto_include_new_markets BOOLEAN NOT NULL DEFAULT true,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT collection_policies_natural_uk UNIQUE (exchange, quote_currency, name),
  CONSTRAINT collection_policies_range_ck CHECK (
    (default_start_at IS NOT NULL) <> (lookback_years IS NOT NULL)
  ),
  CONSTRAINT collection_policies_lookback_ck CHECK (
    lookback_years IS NULL OR lookback_years > 0
  ),
  CONSTRAINT collection_policies_retention_ck CHECK (
    retention_days IS NULL OR retention_days > 0
  ),
  CONSTRAINT collection_policies_priority_ck CHECK (priority BETWEEN 1 AND 1000),
  CONSTRAINT collection_policies_status_ck CHECK (status IN ('active', 'paused'))
);

CREATE TABLE IF NOT EXISTS collection_target_specs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  policy_id BIGINT NOT NULL REFERENCES collection_policies(id),
  market_id BIGINT NOT NULL REFERENCES markets(id),
  legacy_target_id BIGINT REFERENCES collection_targets(id),
  data_type TEXT NOT NULL,
  candle_unit TEXT,
  range_start_at TIMESTAMPTZ NOT NULL,
  retention_days INTEGER,
  priority INTEGER NOT NULL,
  continuous BOOLEAN NOT NULL DEFAULT true,
  auto_managed BOOLEAN NOT NULL DEFAULT true,
  status TEXT NOT NULL DEFAULT 'active',
  excluded_by TEXT,
  exclusion_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT collection_target_specs_natural_uk UNIQUE NULLS NOT DISTINCT (
    policy_id, market_id, data_type, candle_unit
  ),
  CONSTRAINT collection_target_specs_data_type_ck CHECK (
    data_type IN (
      'source_candle', 'trade_event', 'orderbook_snapshot', 'ticker_snapshot'
    )
  ),
  CONSTRAINT collection_target_specs_candle_unit_ck CHECK (
    (data_type = 'source_candle' AND candle_unit IN ('1m', '1d'))
    OR (data_type <> 'source_candle' AND candle_unit IS NULL)
  ),
  CONSTRAINT collection_target_specs_status_ck CHECK (
    status IN ('active', 'paused', 'excluded')
  ),
  CONSTRAINT collection_target_specs_priority_ck CHECK (priority BETWEEN 1 AND 1000),
  CONSTRAINT collection_target_specs_retention_ck CHECK (
    retention_days IS NULL OR retention_days > 0
  )
);

CREATE TABLE IF NOT EXISTS fetch_manifests (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  target_spec_id BIGINT REFERENCES collection_target_specs(id),
  collection_run_id BIGINT REFERENCES collection_runs(id),
  source TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  request_parameters JSONB NOT NULL,
  request_fingerprint TEXT NOT NULL,
  requested_at TIMESTAMPTZ NOT NULL,
  responded_at TIMESTAMPTZ,
  response_status INTEGER,
  response_checksum TEXT,
  collector_version TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  outcome TEXT NOT NULL,
  error_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fetch_manifests_request_uk UNIQUE (
    source, request_fingerprint, requested_at
  ),
  CONSTRAINT fetch_manifests_source_ck CHECK (source IN ('UPBIT', 'LEGACY')),
  CONSTRAINT fetch_manifests_outcome_ck CHECK (
    outcome IN ('succeeded', 'rate_limited', 'blocked', 'failed', 'unknown')
  )
);

CREATE TABLE IF NOT EXISTS coverage_intervals (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  target_spec_id BIGINT NOT NULL REFERENCES collection_target_specs(id),
  range_start_at TIMESTAMPTZ NOT NULL,
  range_end_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  fetch_manifest_id BIGINT REFERENCES fetch_manifests(id),
  assessed_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT coverage_intervals_natural_uk UNIQUE (
    target_spec_id, range_start_at, range_end_at, status
  ),
  CONSTRAINT coverage_intervals_status_ck CHECK (
    status IN ('observed', 'no_trade', 'unavailable', 'unverified', 'failed')
  ),
  CONSTRAINT coverage_intervals_range_ck CHECK (range_start_at < range_end_at),
  EXCLUDE USING gist (
    target_spec_id WITH =,
    tstzrange(range_start_at, range_end_at, '[)') WITH &&
  )
);

CREATE TABLE IF NOT EXISTS data_quality_events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  target_spec_id BIGINT NOT NULL REFERENCES collection_target_specs(id),
  event_type TEXT NOT NULL,
  previous_status TEXT,
  new_status TEXT NOT NULL,
  range_start_at TIMESTAMPTZ NOT NULL,
  range_end_at TIMESTAMPTZ NOT NULL,
  fingerprint TEXT NOT NULL,
  evidence JSONB NOT NULL,
  detected_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT data_quality_events_fingerprint_uk UNIQUE (
    target_spec_id, event_type, detected_at, fingerprint
  ),
  CONSTRAINT data_quality_events_previous_status_ck CHECK (
    previous_status IS NULL OR previous_status IN (
      'observed', 'no_trade', 'unavailable', 'unverified', 'failed'
    )
  ),
  CONSTRAINT data_quality_events_new_status_ck CHECK (
    new_status IN ('observed', 'no_trade', 'unavailable', 'unverified', 'failed')
  )
);

CREATE TABLE IF NOT EXISTS collection_subscription_desires (
  target_spec_id BIGINT PRIMARY KEY REFERENCES collection_target_specs(id) ON DELETE CASCADE,
  desired_state TEXT NOT NULL,
  generation BIGINT NOT NULL DEFAULT 1,
  applied_generation BIGINT,
  connection_id TEXT,
  last_applied_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT collection_subscription_desires_state_ck CHECK (
    desired_state IN ('subscribed', 'unsubscribed')
  )
);

ALTER TABLE collection_runs
  ADD COLUMN IF NOT EXISTS worker_role TEXT,
  ADD COLUMN IF NOT EXISTS run_key TEXT,
  ADD COLUMN IF NOT EXISTS request_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS collection_runs_worker_run_key_uk
  ON collection_runs (worker_role, run_key)
  WHERE worker_role IS NOT NULL AND run_key IS NOT NULL;

ALTER TABLE backfill_jobs
  ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
  ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 100,
  ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS max_attempts INTEGER NOT NULL DEFAULT 5,
  ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS lease_owner TEXT,
  ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_error_code TEXT,
  ADD COLUMN IF NOT EXISTS dead_letter_reason TEXT;

UPDATE backfill_jobs
SET idempotency_key = 'legacy:' || id::text
WHERE idempotency_key IS NULL;

ALTER TABLE backfill_jobs
  ALTER COLUMN idempotency_key SET NOT NULL;

ALTER TABLE backfill_jobs
  DROP CONSTRAINT IF EXISTS backfill_jobs_status_ck;
ALTER TABLE backfill_jobs
  ADD CONSTRAINT backfill_jobs_status_ck CHECK (
    status IN (
      'planned', 'pending', 'leased', 'running', 'retry_wait', 'paused',
      'stopped', 'succeeded', 'failed', 'dead_letter', 'cancelled'
    )
  );

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'backfill_jobs'::regclass
      AND conname = 'backfill_jobs_idempotency_key_uk'
  ) THEN
    ALTER TABLE backfill_jobs
      ADD CONSTRAINT backfill_jobs_idempotency_key_uk UNIQUE (idempotency_key);
  END IF;
END $$;

ALTER TABLE backfill_job_targets
  ADD COLUMN IF NOT EXISTS target_spec_id BIGINT REFERENCES collection_target_specs(id);

ALTER TABLE source_candles
  ADD COLUMN IF NOT EXISTS market_id BIGINT REFERENCES markets(id),
  ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS stored_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS knowledge_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS fetch_manifest_id BIGINT REFERENCES fetch_manifests(id);

ALTER TABLE trade_events
  ADD COLUMN IF NOT EXISTS market_id BIGINT REFERENCES markets(id),
  ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS stored_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS knowledge_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS fetch_manifest_id BIGINT REFERENCES fetch_manifests(id);

ALTER TABLE ticker_snapshots
  ADD COLUMN IF NOT EXISTS market_id BIGINT REFERENCES markets(id),
  ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS stored_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS knowledge_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS fetch_manifest_id BIGINT REFERENCES fetch_manifests(id);

ALTER TABLE orderbook_summaries
  ADD COLUMN IF NOT EXISTS market_id BIGINT REFERENCES markets(id),
  ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS stored_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS knowledge_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS fetch_manifest_id BIGINT REFERENCES fetch_manifests(id);

INSERT INTO markets (
  exchange, market_code, quote_currency, base_asset, korean_name, english_name,
  legacy_instrument_id, first_observed_at, last_observed_at
)
SELECT
  i.exchange,
  i.market_code,
  i.quote_currency,
  i.base_asset,
  i.display_name,
  i.display_name,
  i.id,
  i.created_at,
  GREATEST(i.created_at, i.updated_at)
FROM instruments i
ON CONFLICT (exchange, market_code) DO UPDATE SET
  legacy_instrument_id = COALESCE(markets.legacy_instrument_id, excluded.legacy_instrument_id),
  last_observed_at = GREATEST(markets.last_observed_at, excluded.last_observed_at),
  updated_at = now();

INSERT INTO collection_policies (
  exchange, quote_currency, name, default_start_at, retention_days, priority,
  auto_include_new_markets, status
)
VALUES (
  'UPBIT', 'KRW', 'default-krw-2024', '2024-01-01T00:00:00Z', NULL, 100,
  true, 'active'
)
ON CONFLICT (exchange, quote_currency, name) DO NOTHING;

INSERT INTO collection_target_specs (
  policy_id, market_id, legacy_target_id, data_type, candle_unit,
  range_start_at, retention_days, priority, continuous, auto_managed, status
)
SELECT
  policy.id,
  market.id,
  target.id,
  specification.data_type,
  specification.candle_unit,
  LEAST(plan.range_start_at, policy.default_start_at),
  policy.retention_days,
  policy.priority,
  plan.is_continuous,
  false,
  CASE target.status WHEN 'active' THEN 'active' ELSE 'paused' END
FROM collection_targets target
JOIN instruments instrument ON instrument.id = target.instrument_id
JOIN markets market ON market.legacy_instrument_id = instrument.id
JOIN collection_plans plan ON plan.instrument_id = instrument.id
JOIN collection_policies policy
  ON policy.exchange = instrument.exchange
 AND policy.quote_currency = instrument.quote_currency
 AND policy.name = 'default-krw-2024'
CROSS JOIN (
  VALUES
    ('source_candle', '1m'),
    ('trade_event', NULL),
    ('orderbook_snapshot', NULL),
    ('ticker_snapshot', NULL)
) AS specification(data_type, candle_unit)
ON CONFLICT (policy_id, market_id, data_type, candle_unit) DO NOTHING;

INSERT INTO collection_subscription_desires (target_spec_id, desired_state)
SELECT
  id,
  CASE status WHEN 'active' THEN 'subscribed' ELSE 'unsubscribed' END
FROM collection_target_specs
WHERE data_type IN ('trade_event', 'orderbook_snapshot', 'ticker_snapshot')
ON CONFLICT (target_spec_id) DO NOTHING;

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

UPDATE trade_events source
SET
  market_id = market.id,
  occurred_at = COALESCE(source.occurred_at, source.trade_timestamp_at),
  received_at = COALESCE(source.received_at, source.collected_at),
  stored_at = COALESCE(source.stored_at, source.created_at),
  knowledge_at = COALESCE(source.knowledge_at, source.collected_at)
FROM markets market
WHERE market.legacy_instrument_id = source.instrument_id
  AND source.market_id IS NULL;

UPDATE ticker_snapshots source
SET
  market_id = market.id,
  occurred_at = COALESCE(source.occurred_at, source.bucket_at),
  received_at = COALESCE(source.received_at, source.collected_at),
  stored_at = COALESCE(source.stored_at, source.created_at),
  knowledge_at = COALESCE(source.knowledge_at, source.collected_at)
FROM markets market
WHERE market.legacy_instrument_id = source.instrument_id
  AND source.market_id IS NULL;

UPDATE orderbook_summaries source
SET
  market_id = market.id,
  occurred_at = COALESCE(source.occurred_at, source.bucket_at),
  received_at = COALESCE(source.received_at, source.collected_at),
  stored_at = COALESCE(source.stored_at, source.created_at),
  knowledge_at = COALESCE(source.knowledge_at, source.collected_at)
FROM markets market
WHERE market.legacy_instrument_id = source.instrument_id
  AND source.market_id IS NULL;

CREATE INDEX IF NOT EXISTS markets_quote_status_idx
  ON markets (exchange, quote_currency, market_code);
CREATE INDEX IF NOT EXISTS market_status_history_point_in_time_idx
  ON market_status_history (market_id, valid_from DESC, valid_to);
CREATE INDEX IF NOT EXISTS collection_target_specs_scheduler_idx
  ON collection_target_specs (status, priority DESC, updated_at);
CREATE INDEX IF NOT EXISTS coverage_intervals_target_time_idx
  ON coverage_intervals (target_spec_id, range_start_at, range_end_at);
CREATE INDEX IF NOT EXISTS backfill_jobs_lease_idx
  ON backfill_jobs (status, next_retry_at, lease_expires_at, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS collection_subscription_desires_generation_idx
  ON collection_subscription_desires (desired_state, generation, applied_generation);

-- migrate:down

-- P1 확장은 legacy 행과 신규 계약 행을 함께 보존한다.
-- 안전한 백업·복원 리허설과 소비자 전환 승인 전에는 자동 수축을 제공하지 않는다.
