-- migrate:up

SET TIME ZONE 'UTC';

-- 요청 수락 시점의 asOf, projection ceiling, mutable 시장 상태를 먼저 고정한다.
CREATE TABLE dataset_builds (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  request_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  requested_at TIMESTAMPTZ NOT NULL,
  reason TEXT NOT NULL,
  frozen_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  schema_version TEXT NOT NULL,
  as_of TIMESTAMPTZ NOT NULL,
  input_start_at TIMESTAMPTZ NOT NULL,
  output_start_at TIMESTAMPTZ NOT NULL,
  end_at TIMESTAMPTZ NOT NULL,
  fill_policy TEXT NOT NULL CHECK (fill_policy IN ('none','no_trade_carry_forward_v1')),
  missing_policy TEXT NOT NULL CHECK (missing_policy IN ('fail','null','drop')),
  ordering_policy TEXT NOT NULL,
  request_payload JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','running','retry_wait','succeeded','failed','dead_letter','cancelled')),
  lease_owner TEXT,
  lease_expires_at TIMESTAMPTZ,
  lease_generation INTEGER NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
  next_retry_at TIMESTAMPTZ,
  last_error_code TEXT,
  last_error_message TEXT,
  dead_letter_reason TEXT,
  dataset_version_id BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  CHECK (input_start_at <= output_start_at AND output_start_at < end_at AND end_at <= as_of),
  CHECK ((status = 'running') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)),
  CHECK ((status = 'retry_wait') = (next_retry_at IS NOT NULL)),
  CHECK ((status = 'dead_letter') = (dead_letter_reason IS NOT NULL)),
  CHECK ((status = 'succeeded') = (dataset_version_id IS NOT NULL))
);

CREATE TABLE dataset_build_series (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dataset_build_id BIGINT NOT NULL REFERENCES dataset_builds(id) ON DELETE RESTRICT,
  market_id BIGINT NOT NULL REFERENCES markets(id) ON DELETE RESTRICT,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id) ON DELETE RESTRICT,
  data_kind TEXT NOT NULL
    CHECK (data_kind IN ('candle','indicator','market_statistic','microstructure')),
  unit TEXT NOT NULL,
  definition_set_hash TEXT CHECK (
    definition_set_hash IS NULL OR definition_set_hash ~ '^[0-9a-f]{64}$'
  ),
  calculation_version TEXT NOT NULL,
  fill_policy TEXT NOT NULL CHECK (fill_policy IN ('none','no_trade_carry_forward_v1')),
  source_revision_through_id BIGINT REFERENCES source_candle_revisions(id),
  candle_rollup_through_id BIGINT REFERENCES candle_rollups(id),
  quality_event_through_id BIGINT REFERENCES data_quality_events(id),
  indicator_materialization_through_id BIGINT REFERENCES indicator_materializations(id),
  market_statistic_through_id BIGINT REFERENCES market_statistics(id),
  microstructure_materialization_through_id BIGINT REFERENCES microstructure_materializations(id),
  market_status_history_through_id BIGINT REFERENCES market_status_history(id),
  orderbook_snapshot_through_id BIGINT REFERENCES orderbook_snapshots(id),
  trade_event_through_id BIGINT REFERENCES trade_events(id),
  source_receipt_through_id BIGINT REFERENCES source_receipts(id),
  connection_quality_through_id BIGINT REFERENCES realtime_connection_quality_intervals(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE NULLS NOT DISTINCT (
    dataset_build_id, instrument_id, data_kind, unit,
    definition_set_hash, calculation_version
  ),
  CHECK (fill_policy = 'none' OR data_kind = 'candle'),
  CHECK (unit <> '')
);

-- valid_to가 나중에 닫히거나 다시 열려도 pending build의 의미가 바뀌지 않는다.
CREATE TABLE dataset_build_market_status_snapshots (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dataset_build_id BIGINT NOT NULL REFERENCES dataset_builds(id) ON DELETE RESTRICT,
  source_market_status_history_id BIGINT NOT NULL REFERENCES market_status_history(id),
  market_id BIGINT NOT NULL REFERENCES markets(id),
  exchange TEXT NOT NULL,
  market_code TEXT NOT NULL,
  trading_status TEXT NOT NULL,
  market_warning TEXT NOT NULL,
  market_event JSONB NOT NULL,
  source_payload_checksum TEXT NOT NULL,
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ,
  observed_at TIMESTAMPTZ NOT NULL,
  snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (dataset_build_id, source_market_status_history_id),
  CHECK (valid_to IS NULL OR valid_from < valid_to)
);

CREATE TABLE dataset_build_coverage_snapshots (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dataset_build_id BIGINT NOT NULL REFERENCES dataset_builds(id) ON DELETE RESTRICT,
  dataset_build_series_id BIGINT NOT NULL REFERENCES dataset_build_series(id) ON DELETE RESTRICT,
  source_data_quality_event_id BIGINT REFERENCES data_quality_events(id),
  exchange TEXT NOT NULL,
  market_code TEXT NOT NULL,
  data_kind TEXT NOT NULL
    CHECK (data_kind IN ('candle','indicator','market_statistic','microstructure')),
  unit TEXT NOT NULL,
  definition_set_hash TEXT CHECK (
    definition_set_hash IS NULL OR definition_set_hash ~ '^[0-9a-f]{64}$'
  ),
  calculation_version TEXT NOT NULL,
  range_start_at TIMESTAMPTZ NOT NULL,
  range_end_at TIMESTAMPTZ NOT NULL,
  knowledge_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL
    CHECK (status IN ('available','no_trade','missing','unavailable','unverified')),
  observed_count INTEGER NOT NULL CHECK (observed_count >= 0),
  expected_count INTEGER NOT NULL CHECK (expected_count >= 0),
  evidence_hash TEXT NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (dataset_build_series_id, range_start_at, range_end_at),
  CHECK (range_start_at < range_end_at)
);

CREATE TABLE dataset_versions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  schema_version TEXT NOT NULL,
  as_of TIMESTAMPTZ NOT NULL,
  input_start_at TIMESTAMPTZ NOT NULL,
  output_start_at TIMESTAMPTZ NOT NULL,
  end_at TIMESTAMPTZ NOT NULL,
  fill_policy TEXT NOT NULL CHECK (fill_policy IN ('none','no_trade_carry_forward_v1')),
  missing_policy TEXT NOT NULL CHECK (missing_policy IN ('fail','null','drop')),
  ordering_policy TEXT NOT NULL,
  selection_hash TEXT NOT NULL CHECK (selection_hash ~ '^[0-9a-f]{64}$'),
  manifest_hash TEXT NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
  market_status_hash TEXT NOT NULL CHECK (market_status_hash ~ '^[0-9a-f]{64}$'),
  coverage_hash TEXT NOT NULL CHECK (coverage_hash ~ '^[0-9a-f]{64}$'),
  content_hash TEXT NOT NULL UNIQUE CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  sealed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CHECK (input_start_at <= output_start_at AND output_start_at < end_at AND end_at <= as_of)
);

ALTER TABLE dataset_builds
  ADD CONSTRAINT dataset_builds_version_fk
  FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id) ON DELETE RESTRICT;

CREATE TABLE dataset_version_series (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dataset_version_id BIGINT NOT NULL REFERENCES dataset_versions(id) ON DELETE RESTRICT,
  source_build_series_id BIGINT NOT NULL REFERENCES dataset_build_series(id) ON DELETE RESTRICT,
  market_id BIGINT NOT NULL REFERENCES markets(id),
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  data_kind TEXT NOT NULL
    CHECK (data_kind IN ('candle','indicator','market_statistic','microstructure')),
  unit TEXT NOT NULL,
  definition_set_hash TEXT CHECK (
    definition_set_hash IS NULL OR definition_set_hash ~ '^[0-9a-f]{64}$'
  ),
  calculation_version TEXT NOT NULL,
  source_revision_through_id BIGINT REFERENCES source_candle_revisions(id),
  candle_rollup_through_id BIGINT REFERENCES candle_rollups(id),
  quality_event_through_id BIGINT REFERENCES data_quality_events(id),
  indicator_materialization_through_id BIGINT REFERENCES indicator_materializations(id),
  market_statistic_through_id BIGINT REFERENCES market_statistics(id),
  microstructure_materialization_through_id BIGINT REFERENCES microstructure_materializations(id),
  market_status_history_through_id BIGINT REFERENCES market_status_history(id),
  orderbook_snapshot_through_id BIGINT REFERENCES orderbook_snapshots(id),
  trade_event_through_id BIGINT REFERENCES trade_events(id),
  source_receipt_through_id BIGINT REFERENCES source_receipts(id),
  connection_quality_through_id BIGINT REFERENCES realtime_connection_quality_intervals(id),
  member_count INTEGER NOT NULL CHECK (member_count >= 0),
  members_hash TEXT NOT NULL CHECK (members_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (dataset_version_id, id),
  UNIQUE NULLS NOT DISTINCT (
    dataset_version_id, instrument_id, data_kind, unit,
    definition_set_hash, calculation_version
  )
);

CREATE TABLE dataset_version_candles (
  dataset_version_id BIGINT NOT NULL,
  dataset_version_series_id BIGINT NOT NULL,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  unit TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  source_candle_revision_id BIGINT REFERENCES source_candle_revisions(id),
  candle_rollup_id BIGINT REFERENCES candle_rollups(id),
  quality TEXT NOT NULL CHECK (quality IN ('available','no_trade','missing','unavailable','unverified')),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  knowledge_at TIMESTAMPTZ NOT NULL,
  source_as_of TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (dataset_version_id, dataset_version_series_id, occurred_at),
  FOREIGN KEY (dataset_version_id, dataset_version_series_id)
    REFERENCES dataset_version_series(dataset_version_id, id) ON DELETE RESTRICT,
  CHECK ((source_candle_revision_id IS NOT NULL)::integer +
         (candle_rollup_id IS NOT NULL)::integer = 1)
);

CREATE TABLE dataset_version_indicators (
  dataset_version_id BIGINT NOT NULL,
  dataset_version_series_id BIGINT NOT NULL,
  indicator_materialization_id BIGINT NOT NULL REFERENCES indicator_materializations(id),
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  unit TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  quality TEXT NOT NULL CHECK (quality IN ('available','no_trade','missing','unavailable','unverified')),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  knowledge_at TIMESTAMPTZ NOT NULL,
  source_as_of TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (dataset_version_id, dataset_version_series_id, occurred_at),
  FOREIGN KEY (dataset_version_id, dataset_version_series_id)
    REFERENCES dataset_version_series(dataset_version_id, id) ON DELETE RESTRICT
);

CREATE TABLE dataset_version_market_statistics (
  dataset_version_id BIGINT NOT NULL,
  dataset_version_series_id BIGINT NOT NULL,
  market_statistic_id BIGINT NOT NULL REFERENCES market_statistics(id),
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  unit TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  quality TEXT NOT NULL CHECK (quality IN ('available','no_trade','missing','unavailable','unverified')),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  knowledge_at TIMESTAMPTZ NOT NULL,
  source_as_of TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (dataset_version_id, dataset_version_series_id, occurred_at),
  FOREIGN KEY (dataset_version_id, dataset_version_series_id)
    REFERENCES dataset_version_series(dataset_version_id, id) ON DELETE RESTRICT
);

CREATE TABLE dataset_version_microstructures (
  dataset_version_id BIGINT NOT NULL,
  dataset_version_series_id BIGINT NOT NULL,
  microstructure_materialization_id BIGINT NOT NULL REFERENCES microstructure_materializations(id),
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  unit TEXT NOT NULL CHECK (unit = '1m'),
  occurred_at TIMESTAMPTZ NOT NULL,
  quality TEXT NOT NULL CHECK (quality IN ('available','no_trade','missing','unavailable','unverified')),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  knowledge_at TIMESTAMPTZ NOT NULL,
  source_as_of TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (dataset_version_id, dataset_version_series_id, occurred_at),
  FOREIGN KEY (dataset_version_id, dataset_version_series_id)
    REFERENCES dataset_version_series(dataset_version_id, id) ON DELETE RESTRICT
);

CREATE TABLE dataset_version_market_status_snapshots (
  dataset_version_id BIGINT NOT NULL REFERENCES dataset_versions(id) ON DELETE RESTRICT,
  source_build_snapshot_id BIGINT NOT NULL REFERENCES dataset_build_market_status_snapshots(id),
  source_market_status_history_id BIGINT NOT NULL REFERENCES market_status_history(id),
  market_id BIGINT NOT NULL REFERENCES markets(id),
  exchange TEXT NOT NULL,
  market_code TEXT NOT NULL,
  trading_status TEXT NOT NULL,
  market_warning TEXT NOT NULL,
  market_event JSONB NOT NULL,
  source_payload_checksum TEXT NOT NULL,
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ,
  observed_at TIMESTAMPTZ NOT NULL,
  snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
  PRIMARY KEY (dataset_version_id, source_build_snapshot_id)
);

CREATE TABLE dataset_version_coverage_snapshots (
  dataset_version_id BIGINT NOT NULL REFERENCES dataset_versions(id) ON DELETE RESTRICT,
  source_build_coverage_snapshot_id BIGINT NOT NULL
    REFERENCES dataset_build_coverage_snapshots(id),
  dataset_version_series_id BIGINT NOT NULL,
  source_data_quality_event_id BIGINT REFERENCES data_quality_events(id),
  exchange TEXT NOT NULL,
  market_code TEXT NOT NULL,
  data_kind TEXT NOT NULL,
  unit TEXT NOT NULL,
  definition_set_hash TEXT CHECK (
    definition_set_hash IS NULL OR definition_set_hash ~ '^[0-9a-f]{64}$'
  ),
  calculation_version TEXT NOT NULL,
  range_start_at TIMESTAMPTZ NOT NULL,
  range_end_at TIMESTAMPTZ NOT NULL,
  knowledge_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL
    CHECK (status IN ('available','no_trade','missing','unavailable','unverified')),
  observed_count INTEGER NOT NULL CHECK (observed_count >= 0),
  expected_count INTEGER NOT NULL CHECK (expected_count >= 0),
  evidence_hash TEXT NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
  PRIMARY KEY (dataset_version_id, source_build_coverage_snapshot_id),
  FOREIGN KEY (dataset_version_id, dataset_version_series_id)
    REFERENCES dataset_version_series(dataset_version_id, id) ON DELETE RESTRICT
);

CREATE INDEX dataset_builds_claim_idx
  ON dataset_builds (COALESCE(next_retry_at, created_at), id)
  WHERE status IN ('pending','running','retry_wait');
CREATE INDEX dataset_versions_as_of_idx ON dataset_versions (as_of, id);

CREATE OR REPLACE FUNCTION reject_dataset_version_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_dataset_version_seal()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE'
     AND OLD.sealed_at IS NULL AND NEW.sealed_at IS NOT NULL
     AND (to_jsonb(NEW) - 'sealed_at') = (to_jsonb(OLD) - 'sealed_at') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

CREATE OR REPLACE FUNCTION reject_sealed_dataset_version_child_insert()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  PERFORM 1 FROM dataset_versions
  WHERE id=NEW.dataset_version_id AND sealed_at IS NULL FOR KEY SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION '게시된 dataset version child는 append-only immutable이다';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION validate_dataset_version_typed_member()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
  parent dataset_version_series%ROWTYPE;
  source_instrument BIGINT;
  source_unit TEXT;
  source_occurred TIMESTAMPTZ;
  source_calculation TEXT;
  source_definition TEXT;
  source_content_hash TEXT;
  source_knowledge_at TIMESTAMPTZ;
  source_as_of TIMESTAMPTZ;
  source_id BIGINT;
  source_ceiling BIGINT;
  parent_as_of TIMESTAMPTZ;
BEGIN
  SELECT * INTO parent FROM dataset_version_series
  WHERE dataset_version_id=NEW.dataset_version_id AND id=NEW.dataset_version_series_id;
  IF NOT FOUND OR parent.instrument_id <> NEW.instrument_id OR parent.unit <> NEW.unit THEN
    RAISE EXCEPTION 'typed dataset member identity가 parent series와 다르다';
  END IF;
  SELECT version.as_of INTO parent_as_of
  FROM dataset_versions version WHERE version.id=NEW.dataset_version_id;
  IF TG_TABLE_NAME='dataset_version_candles' THEN
    IF parent.data_kind <> 'candle' THEN RAISE EXCEPTION 'typed dataset member kind 불일치'; END IF;
    IF NEW.source_candle_revision_id IS NOT NULL THEN
      source_id := NEW.source_candle_revision_id;
      source_ceiling := parent.source_revision_through_id;
      SELECT revision.instrument_id, revision.candle_unit, revision.candle_start_at,
             revision.input_content_hash, revision.knowledge_at, revision.source_as_of
        INTO source_instrument, source_unit, source_occurred, source_content_hash,
             source_knowledge_at, source_as_of
      FROM source_candle_revisions revision WHERE revision.id=NEW.source_candle_revision_id;
      source_calculation := 'source-candle-v1';
    ELSE
      source_id := NEW.candle_rollup_id;
      source_ceiling := parent.candle_rollup_through_id;
      SELECT rollup.instrument_id, rollup.candle_unit, rollup.candle_start_at,
             rollup.calculation_version, rollup.result_content_hash,
             rollup.knowledge_at, rollup.source_as_of
        INTO source_instrument, source_unit, source_occurred, source_calculation,
             source_content_hash, source_knowledge_at, source_as_of
      FROM candle_rollups rollup WHERE rollup.id=NEW.candle_rollup_id;
    END IF;
  ELSIF TG_TABLE_NAME='dataset_version_indicators' THEN
    IF parent.data_kind <> 'indicator' THEN RAISE EXCEPTION 'typed dataset member kind 불일치'; END IF;
    source_id := NEW.indicator_materialization_id;
    source_ceiling := parent.indicator_materialization_through_id;
    SELECT materialization.instrument_id, materialization.candle_unit,
           materialization.occurred_at, materialization.definition_set_hash,
           materialization.content_hash, materialization.knowledge_at,
           materialization.source_as_of
      INTO source_instrument, source_unit, source_occurred, source_definition,
           source_content_hash, source_knowledge_at, source_as_of
    FROM indicator_materializations materialization
    WHERE materialization.id=NEW.indicator_materialization_id;
    source_calculation := 'indicator-v1';
  ELSIF TG_TABLE_NAME='dataset_version_market_statistics' THEN
    IF parent.data_kind <> 'market_statistic' THEN RAISE EXCEPTION 'typed dataset member kind 불일치'; END IF;
    source_id := NEW.market_statistic_id;
    source_ceiling := parent.market_statistic_through_id;
    SELECT statistic.instrument_id, statistic.interval, statistic.occurred_at,
           statistic.calculation_version, statistic.content_hash,
           statistic.knowledge_at, statistic.source_as_of
      INTO source_instrument, source_unit, source_occurred, source_calculation,
           source_content_hash, source_knowledge_at, source_as_of
    FROM market_statistics statistic WHERE statistic.id=NEW.market_statistic_id;
  ELSE
    IF parent.data_kind <> 'microstructure' THEN RAISE EXCEPTION 'typed dataset member kind 불일치'; END IF;
    source_id := NEW.microstructure_materialization_id;
    source_ceiling := parent.microstructure_materialization_through_id;
    SELECT materialization.instrument_id, '1m', materialization.bucket_start_at,
           definition.calculation_version, definition.definition_hash,
           statistic.content_hash, materialization.knowledge_at,
           materialization.source_as_of
      INTO source_instrument, source_unit, source_occurred,
           source_calculation, source_definition, source_content_hash,
           source_knowledge_at, source_as_of
    FROM microstructure_materializations materialization
    JOIN microstructure_definition_versions definition
      ON definition.id=materialization.definition_version_id
    JOIN microstructure_statistics statistic
      ON statistic.materialization_id=materialization.id
    WHERE materialization.id=NEW.microstructure_materialization_id;
  END IF;
  IF source_ceiling IS NULL OR source_id > source_ceiling THEN
    RAISE EXCEPTION 'typed dataset member가 고정된 원천 frontier를 넘는다';
  END IF;
  IF source_knowledge_at > parent_as_of THEN
    RAISE EXCEPTION 'typed dataset member knowledge_at이 dataset version asOf를 넘는다';
  END IF;
  IF source_instrument IS NULL OR source_instrument <> NEW.instrument_id
     OR source_unit <> NEW.unit OR source_occurred <> NEW.occurred_at THEN
    RAISE EXCEPTION 'typed dataset member source 자연키가 다르다';
  END IF;
  IF parent.definition_set_hash IS DISTINCT FROM source_definition THEN
    RAISE EXCEPTION 'typed dataset member definition 자연키가 다르다';
  END IF;
  IF parent.calculation_version <> 'daily-source-preferred-v1'
     AND parent.calculation_version IS DISTINCT FROM source_calculation THEN
    RAISE EXCEPTION 'typed dataset member calculation version이 다르다';
  END IF;
  IF NEW.content_hash IS DISTINCT FROM source_content_hash
     OR NEW.knowledge_at IS DISTINCT FROM source_knowledge_at
     OR NEW.source_as_of IS DISTINCT FROM source_as_of THEN
    RAISE EXCEPTION 'typed dataset member 내용 또는 인과 시각이 원천과 다르다';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER dataset_build_series_append_only_update BEFORE UPDATE ON dataset_build_series
  FOR EACH ROW EXECUTE FUNCTION reject_dataset_version_mutation();
CREATE TRIGGER dataset_build_series_append_only_delete BEFORE DELETE ON dataset_build_series
  FOR EACH ROW EXECUTE FUNCTION reject_dataset_version_mutation();
CREATE TRIGGER dataset_build_status_snapshots_append_only_update BEFORE UPDATE ON dataset_build_market_status_snapshots
  FOR EACH ROW EXECUTE FUNCTION reject_dataset_version_mutation();
CREATE TRIGGER dataset_build_status_snapshots_append_only_delete BEFORE DELETE ON dataset_build_market_status_snapshots
  FOR EACH ROW EXECUTE FUNCTION reject_dataset_version_mutation();
CREATE TRIGGER dataset_build_coverage_snapshots_append_only_update BEFORE UPDATE ON dataset_build_coverage_snapshots
  FOR EACH ROW EXECUTE FUNCTION reject_dataset_version_mutation();
CREATE TRIGGER dataset_build_coverage_snapshots_append_only_delete BEFORE DELETE ON dataset_build_coverage_snapshots
  FOR EACH ROW EXECUTE FUNCTION reject_dataset_version_mutation();

DO $$
DECLARE
  table_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'dataset_version_series', 'dataset_version_candles',
    'dataset_version_indicators', 'dataset_version_market_statistics',
    'dataset_version_microstructures', 'dataset_version_market_status_snapshots',
    'dataset_version_coverage_snapshots'
  ]
  LOOP
    EXECUTE format(
      'CREATE TRIGGER %I_append_only_update BEFORE UPDATE ON %I '
      'FOR EACH ROW EXECUTE FUNCTION reject_dataset_version_mutation()',
      table_name, table_name
    );
    EXECUTE format(
      'CREATE TRIGGER %I_append_only_delete BEFORE DELETE ON %I '
      'FOR EACH ROW EXECUTE FUNCTION reject_dataset_version_mutation()',
      table_name, table_name
    );
  END LOOP;
END $$;

CREATE TRIGGER dataset_versions_seal_update BEFORE UPDATE ON dataset_versions
  FOR EACH ROW EXECUTE FUNCTION enforce_dataset_version_seal();
CREATE TRIGGER dataset_versions_append_only_delete BEFORE DELETE ON dataset_versions
  FOR EACH ROW EXECUTE FUNCTION enforce_dataset_version_seal();

DO $$
DECLARE table_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'dataset_version_series', 'dataset_version_candles',
    'dataset_version_indicators', 'dataset_version_market_statistics',
    'dataset_version_microstructures', 'dataset_version_market_status_snapshots',
    'dataset_version_coverage_snapshots'
  ] LOOP
    EXECUTE format(
      'CREATE TRIGGER %I_a_sealed_insert BEFORE INSERT ON %I '
      'FOR EACH ROW EXECUTE FUNCTION reject_sealed_dataset_version_child_insert()',
      table_name, table_name
    );
  END LOOP;
END $$;

CREATE TRIGGER dataset_version_candles_identity BEFORE INSERT ON dataset_version_candles
  FOR EACH ROW EXECUTE FUNCTION validate_dataset_version_typed_member();
CREATE TRIGGER dataset_version_indicators_identity BEFORE INSERT ON dataset_version_indicators
  FOR EACH ROW EXECUTE FUNCTION validate_dataset_version_typed_member();
CREATE TRIGGER dataset_version_market_statistics_identity BEFORE INSERT ON dataset_version_market_statistics
  FOR EACH ROW EXECUTE FUNCTION validate_dataset_version_typed_member();
CREATE TRIGGER dataset_version_microstructures_identity BEFORE INSERT ON dataset_version_microstructures
  FOR EACH ROW EXECUTE FUNCTION validate_dataset_version_typed_member();

GRANT SELECT, INSERT, UPDATE ON dataset_builds TO CURRENT_USER;
GRANT SELECT, INSERT ON dataset_build_series, dataset_build_market_status_snapshots,
  dataset_build_coverage_snapshots,
  dataset_version_series, dataset_version_candles,
  dataset_version_indicators, dataset_version_market_statistics,
  dataset_version_microstructures, dataset_version_market_status_snapshots,
  dataset_version_coverage_snapshots TO CURRENT_USER;
GRANT SELECT, INSERT, UPDATE ON dataset_versions TO CURRENT_USER;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO CURRENT_USER;
REVOKE UPDATE, DELETE ON dataset_build_series, dataset_build_market_status_snapshots,
  dataset_build_coverage_snapshots,
  dataset_version_series, dataset_version_candles,
  dataset_version_indicators, dataset_version_market_statistics,
  dataset_version_microstructures, dataset_version_market_status_snapshots,
  dataset_version_coverage_snapshots FROM CURRENT_USER;
REVOKE DELETE ON dataset_versions FROM CURRENT_USER;

-- migrate:down
-- 빌드·버전은 재현성 증적이므로 자동 삭제하지 않는다.
SELECT 1;
