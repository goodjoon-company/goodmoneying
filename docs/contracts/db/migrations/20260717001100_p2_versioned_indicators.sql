-- migrate:up

CREATE TABLE indicator_definitions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  indicator_key TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE indicator_definition_versions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  definition_id BIGINT NOT NULL REFERENCES indicator_definitions(id),
  version INTEGER NOT NULL CHECK (version > 0),
  definition_hash TEXT NOT NULL UNIQUE CHECK (definition_hash ~ '^[0-9a-f]{64}$'),
  algorithm TEXT NOT NULL,
  parameters JSONB NOT NULL,
  decimal_precision INTEGER NOT NULL CHECK (decimal_precision = 50),
  rounding TEXT NOT NULL CHECK (rounding = 'ROUND_HALF_EVEN'),
  implementation_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (definition_id, version),
  UNIQUE (definition_id, implementation_version)
);

INSERT INTO indicator_definitions (indicator_key, display_name) VALUES
  ('sma20', '20 구간 단순 이동 평균'), ('sma60', '60 구간 단순 이동 평균'),
  ('ema20', '20 구간 지수 이동 평균'), ('bollinger20', '20 구간 볼린저 밴드'),
  ('rsi14', '14 구간 Wilder 상대 강도 지수');

INSERT INTO indicator_definition_versions (
  definition_id, version, definition_hash, algorithm, parameters,
  decimal_precision, rounding, implementation_version
)
SELECT definition.id, 1, seed.definition_hash, seed.algorithm, seed.parameters,
       50, 'ROUND_HALF_EVEN', 'indicator-engine-v1'
FROM indicator_definitions definition
JOIN (VALUES
  ('sma20', 'bde386c8798974f4946b4f9fc4d0d23aa83be8c83e1577e0c716b79f539b98d6', 'simple-moving-average', '{"period":20}'::jsonb),
  ('sma60', 'cde4a2750b8db54bff6afb27ff2e61c2b14380351e14370b839bf9c77941030f', 'simple-moving-average', '{"period":60}'::jsonb),
  ('ema20', 'e6dc0200b12329828f40ee2f4450093050ad45306940ba916f4017f7bf6eb83c', 'exponential-moving-average', '{"period":20,"seed":"sma20"}'::jsonb),
  ('bollinger20', '0f144e5a28affeb473d79d253a71e5f3b8015827f7e8e41e9a340ad0122636da', 'bollinger-bands-population-standard-deviation', '{"period":20,"standardDeviations":"2"}'::jsonb),
  ('rsi14', '37cbb67e056502b4b5fa52be4006a60c5a33737849d75869c61124143feea219', 'wilder-relative-strength-index', '{"period":14,"flat":"50","onlyGain":"100","onlyLoss":"0"}'::jsonb)
) AS seed(indicator_key, definition_hash, algorithm, parameters)
  ON seed.indicator_key = definition.indicator_key;

CREATE TABLE indicator_materializations (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  market_id BIGINT NOT NULL REFERENCES markets(id),
  candle_unit TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  definition_set_hash TEXT NOT NULL CHECK (definition_set_hash ~ '^[0-9a-f]{64}$'),
  parent_materialization_id BIGINT REFERENCES indicator_materializations(id) ON DELETE RESTRICT,
  current_rollup_id BIGINT REFERENCES candle_rollups(id) ON DELETE RESTRICT,
  current_source_revision_id BIGINT REFERENCES source_candle_revisions(id) ON DELETE RESTRICT,
  lineage_hash TEXT NOT NULL CHECK (lineage_hash ~ '^[0-9a-f]{64}$'),
  source_revision_through_id BIGINT NOT NULL CHECK (source_revision_through_id >= 0),
  quality_event_through_id BIGINT REFERENCES data_quality_events(id),
  knowledge_at TIMESTAMPTZ NOT NULL,
  source_as_of TIMESTAMPTZ NOT NULL,
  calculation_status TEXT NOT NULL CHECK (calculation_status IN ('warming_up','ready','missing')),
  checkpoint_state JSONB NOT NULL,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CHECK ((current_rollup_id IS NOT NULL)::integer +
         (current_source_revision_id IS NOT NULL)::integer = 1),
  UNIQUE NULLS NOT DISTINCT (instrument_id, candle_unit, occurred_at, definition_set_hash,
          current_rollup_id, current_source_revision_id,
          source_revision_through_id, quality_event_through_id, content_hash)
);

CREATE TABLE indicator_values (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  materialization_id BIGINT NOT NULL REFERENCES indicator_materializations(id) ON DELETE RESTRICT,
  definition_version_id BIGINT NOT NULL REFERENCES indicator_definition_versions(id) ON DELETE RESTRICT,
  value_name TEXT NOT NULL,
  value NUMERIC,
  calculation_status TEXT NOT NULL CHECK (calculation_status IN ('warming_up','ready','missing')),
  parent_value_id BIGINT REFERENCES indicator_values(id) ON DELETE RESTRICT,
  UNIQUE (materialization_id, definition_version_id, value_name)
);

CREATE TABLE indicator_value_rollups (
  indicator_value_id BIGINT NOT NULL REFERENCES indicator_values(id) ON DELETE RESTRICT,
  candle_rollup_id BIGINT NOT NULL REFERENCES candle_rollups(id) ON DELETE RESTRICT,
  PRIMARY KEY (indicator_value_id, candle_rollup_id)
);

CREATE TABLE market_statistics (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  market_id BIGINT NOT NULL REFERENCES markets(id),
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  interval TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  calculation_version TEXT NOT NULL,
  close_return_1 NUMERIC,
  realized_volatility_20 NUMERIC,
  trade_volume NUMERIC,
  trade_amount NUMERIC,
  volatility_sample_count INTEGER NOT NULL CHECK (volatility_sample_count BETWEEN 0 AND 20),
  input_completeness_ratio NUMERIC NOT NULL CHECK (input_completeness_ratio BETWEEN 0 AND 1),
  return_status TEXT NOT NULL CHECK (return_status IN ('warming_up','ready','missing')),
  volatility_status TEXT NOT NULL CHECK (volatility_status IN ('warming_up','ready','missing')),
  trade_status TEXT NOT NULL CHECK (trade_status IN ('warming_up','ready','missing')),
  parent_statistic_id BIGINT REFERENCES market_statistics(id) ON DELETE RESTRICT,
  current_rollup_id BIGINT REFERENCES candle_rollups(id) ON DELETE RESTRICT,
  current_source_revision_id BIGINT REFERENCES source_candle_revisions(id) ON DELETE RESTRICT,
  source_revision_through_id BIGINT NOT NULL CHECK (source_revision_through_id >= 0),
  quality_event_through_id BIGINT REFERENCES data_quality_events(id),
  source_as_of TIMESTAMPTZ NOT NULL,
  knowledge_at TIMESTAMPTZ NOT NULL,
  lineage_hash TEXT NOT NULL CHECK (lineage_hash ~ '^[0-9a-f]{64}$'),
  checkpoint_state JSONB NOT NULL,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CHECK ((current_rollup_id IS NOT NULL)::integer +
         (current_source_revision_id IS NOT NULL)::integer = 1),
  UNIQUE NULLS NOT DISTINCT (
    market_id, interval, occurred_at, calculation_version,
    current_rollup_id, current_source_revision_id,
    source_revision_through_id, quality_event_through_id, content_hash
  )
);

CREATE TABLE indicator_invalidations (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  candle_unit TEXT NOT NULL,
  changed_rollup_id BIGINT UNIQUE REFERENCES candle_rollups(id),
  changed_source_revision_id BIGINT UNIQUE REFERENCES source_candle_revisions(id),
  changed_quality_event_id BIGINT UNIQUE REFERENCES data_quality_events(id),
  changed_rollup_invalidation_id BIGINT UNIQUE REFERENCES candle_rollup_invalidations(id),
  impact_start_at TIMESTAMPTZ NOT NULL,
  impact_end_at TIMESTAMPTZ,
  progress_at TIMESTAMPTZ,
  indicator_checkpoint_state JSONB,
  statistic_checkpoint_state JSONB,
  source_revision_through_id BIGINT NOT NULL,
  quality_event_through_id BIGINT,
  knowledge_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','succeeded','retry_wait','dead_letter')),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
  next_retry_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  lease_owner TEXT,
  lease_expires_at TIMESTAMPTZ,
  lease_generation INTEGER NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
  last_error_code TEXT,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CHECK ((status = 'running') =
         (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)),
  CHECK ((indicator_checkpoint_state IS NULL) =
         (statistic_checkpoint_state IS NULL)),
  CHECK ((progress_at IS NULL) =
         (indicator_checkpoint_state IS NULL)),
  CHECK ((changed_rollup_id IS NOT NULL)::integer +
         (changed_source_revision_id IS NOT NULL)::integer +
         (changed_quality_event_id IS NOT NULL)::integer +
         (changed_rollup_invalidation_id IS NOT NULL)::integer = 1)
);

CREATE OR REPLACE FUNCTION reject_indicator_immutable_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END;
$$;

CREATE TRIGGER indicator_definitions_append_only BEFORE UPDATE OR DELETE ON indicator_definitions FOR EACH ROW EXECUTE FUNCTION reject_indicator_immutable_mutation();
CREATE TRIGGER indicator_definition_versions_append_only BEFORE UPDATE OR DELETE ON indicator_definition_versions FOR EACH ROW EXECUTE FUNCTION reject_indicator_immutable_mutation();
CREATE TRIGGER indicator_materializations_append_only BEFORE UPDATE OR DELETE ON indicator_materializations FOR EACH ROW EXECUTE FUNCTION reject_indicator_immutable_mutation();
CREATE TRIGGER indicator_values_append_only BEFORE UPDATE OR DELETE ON indicator_values FOR EACH ROW EXECUTE FUNCTION reject_indicator_immutable_mutation();
CREATE TRIGGER indicator_value_rollups_append_only BEFORE UPDATE OR DELETE ON indicator_value_rollups FOR EACH ROW EXECUTE FUNCTION reject_indicator_immutable_mutation();
CREATE TRIGGER market_statistics_append_only BEFORE UPDATE OR DELETE ON market_statistics FOR EACH ROW EXECUTE FUNCTION reject_indicator_immutable_mutation();

CREATE OR REPLACE FUNCTION enqueue_indicator_invalidation()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog, public AS $$
BEGIN
  INSERT INTO indicator_invalidations (
    instrument_id, candle_unit, changed_rollup_id, impact_start_at,
    source_revision_through_id, quality_event_through_id, knowledge_at
  ) VALUES (
    NEW.instrument_id, NEW.candle_unit, NEW.id, NEW.candle_start_at,
    NEW.source_revision_through_id, NEW.quality_event_through_id, NEW.knowledge_at
  ) ON CONFLICT (changed_rollup_id) DO NOTHING;
  RETURN NEW;
END;
$$;
CREATE TRIGGER candle_rollup_indicator_invalidation
AFTER INSERT ON candle_rollups FOR EACH ROW EXECUTE FUNCTION enqueue_indicator_invalidation();

CREATE OR REPLACE FUNCTION enqueue_source_indicator_invalidation()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog, public AS $$
DECLARE latest_quality_event_id BIGINT;
BEGIN
  IF NEW.candle_unit <> '1m' THEN RETURN NEW; END IF;
  SELECT MAX(event.id) INTO latest_quality_event_id
  FROM data_quality_events event
  JOIN collection_target_specs specification ON specification.id=event.target_spec_id
  WHERE specification.market_id=NEW.market_id
    AND specification.data_type='source_candle'
    AND specification.candle_unit='1m'
    AND event.detected_at <= NEW.knowledge_at;
  INSERT INTO indicator_invalidations (
    instrument_id, candle_unit, changed_source_revision_id, impact_start_at,
    source_revision_through_id, quality_event_through_id, knowledge_at
  ) VALUES (
    NEW.instrument_id, NEW.candle_unit, NEW.id, NEW.candle_start_at,
    NEW.id, latest_quality_event_id, NEW.knowledge_at
  ) ON CONFLICT (changed_source_revision_id) DO NOTHING;
  RETURN NEW;
END;
$$;
CREATE TRIGGER source_revision_indicator_invalidation
AFTER INSERT ON source_candle_revisions FOR EACH ROW
EXECUTE FUNCTION enqueue_source_indicator_invalidation();

CREATE OR REPLACE FUNCTION enqueue_completed_rollup_indicator_invalidation()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog, public AS $$
DECLARE changed candle_rollup_invalidations%ROWTYPE;
BEGIN
  IF NEW.status <> 'succeeded' OR OLD.status = 'succeeded' THEN RETURN NEW; END IF;
  SELECT invalidation.* INTO changed
  FROM candle_rollup_invalidations invalidation
  WHERE invalidation.id = NEW.invalidation_id;
  INSERT INTO indicator_invalidations (
    instrument_id, candle_unit, changed_rollup_invalidation_id,
    impact_start_at, impact_end_at, source_revision_through_id,
    quality_event_through_id, knowledge_at
  ) VALUES (
    changed.instrument_id, changed.candle_unit, changed.id,
    changed.range_start_at, changed.range_end_at, changed.source_revision_through_id,
    changed.quality_event_through_id, changed.knowledge_at
  ) ON CONFLICT (changed_rollup_invalidation_id) DO NOTHING;
  RETURN NEW;
END;
$$;
CREATE TRIGGER completed_rollup_indicator_invalidation
AFTER UPDATE OF status ON candle_rollup_recompute_jobs
FOR EACH ROW EXECUTE FUNCTION enqueue_completed_rollup_indicator_invalidation();

REVOKE ALL ON FUNCTION enqueue_indicator_invalidation() FROM PUBLIC;
REVOKE ALL ON FUNCTION enqueue_source_indicator_invalidation() FROM PUBLIC;
REVOKE ALL ON FUNCTION enqueue_completed_rollup_indicator_invalidation() FROM PUBLIC;

-- 기존 이력은 트리거 생성 이전에 저장됐으므로 상품·단위별 현재 frontier 하나만 시드한다.
-- 행별 시드를 피하여 업그레이드 큐 크기를 O(상품 수 × 단위 수)로 제한한다.
WITH source_bounds AS (
  SELECT instrument_id, '1m'::text AS candle_unit,
         MAX(market_id) AS market_id,
         MIN(candle_start_at) AS impact_start_at,
         MAX(id) AS changed_source_revision_id,
         MAX(id) AS source_revision_through_id,
         MAX(knowledge_at) AS source_knowledge_at
  FROM source_candle_revisions
  WHERE candle_unit = '1m'
  GROUP BY instrument_id
), source_frontier AS (
  SELECT source.instrument_id, source.candle_unit, source.impact_start_at,
         source.changed_source_revision_id, source.source_revision_through_id,
         quality.id AS quality_event_through_id,
         GREATEST(source.source_knowledge_at,
                  COALESCE(quality.detected_at, source.source_knowledge_at)) AS knowledge_at
  FROM source_bounds source
  LEFT JOIN LATERAL (
    SELECT event.id, event.detected_at
    FROM data_quality_events event
    JOIN collection_target_specs specification ON specification.id=event.target_spec_id
    WHERE specification.market_id=source.market_id
      AND specification.data_type='source_candle'
      AND specification.candle_unit='1m'
    ORDER BY event.id DESC LIMIT 1
  ) quality ON TRUE
)
INSERT INTO indicator_invalidations (
  instrument_id, candle_unit, changed_source_revision_id, impact_start_at,
  source_revision_through_id, quality_event_through_id, knowledge_at
)
SELECT instrument_id, candle_unit, changed_source_revision_id, impact_start_at,
       source_revision_through_id, quality_event_through_id, knowledge_at
FROM source_frontier
ON CONFLICT (changed_source_revision_id) DO NOTHING;

WITH rollup_frontier AS (
  SELECT instrument_id, candle_unit,
         MIN(candle_start_at) AS impact_start_at,
         MAX(id) AS changed_rollup_id,
         MAX(source_revision_through_id) AS source_revision_through_id,
         MAX(quality_event_through_id) AS quality_event_through_id,
         MAX(knowledge_at) AS knowledge_at
  FROM candle_rollups
  WHERE candle_unit <> '1m'
  GROUP BY instrument_id, candle_unit
)
INSERT INTO indicator_invalidations (
  instrument_id, candle_unit, changed_rollup_id, impact_start_at,
  source_revision_through_id, quality_event_through_id, knowledge_at
)
SELECT instrument_id, candle_unit, changed_rollup_id, impact_start_at,
       source_revision_through_id, quality_event_through_id, knowledge_at
FROM rollup_frontier
ON CONFLICT (changed_rollup_id) DO NOTHING;

CREATE INDEX indicator_materializations_projection_idx ON indicator_materializations (
  instrument_id, candle_unit, occurred_at, knowledge_at, source_revision_through_id DESC,
  quality_event_through_id DESC NULLS LAST, id DESC
);
CREATE INDEX indicator_invalidations_claim_idx ON indicator_invalidations (status, impact_start_at, created_at);

GRANT SELECT ON indicator_definitions, indicator_definition_versions TO CURRENT_USER;
GRANT SELECT, INSERT ON indicator_materializations, indicator_values, indicator_value_rollups, market_statistics TO CURRENT_USER;
GRANT SELECT, INSERT, UPDATE ON indicator_invalidations TO CURRENT_USER;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO CURRENT_USER;
REVOKE UPDATE, DELETE ON indicator_definitions, indicator_definition_versions, indicator_materializations, indicator_values, indicator_value_rollups, market_statistics FROM CURRENT_USER;

-- migrate:down
SELECT 1;
