-- migrate:up

SET TIME ZONE 'UTC';

ALTER TABLE dataset_versions
  ADD CONSTRAINT dataset_versions_id_content_hash_key UNIQUE (id, content_hash);

CREATE TABLE backtest_runs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  strategy_version_id BIGINT NOT NULL REFERENCES strategy_versions(id) ON DELETE RESTRICT,
  strategy_graph_hash TEXT NOT NULL CHECK (strategy_graph_hash ~ '^[0-9a-f]{64}$'),
  dataset_version_id BIGINT NOT NULL REFERENCES dataset_versions(id) ON DELETE RESTRICT,
  dataset_content_hash TEXT NOT NULL CHECK (dataset_content_hash ~ '^[0-9a-f]{64}$'),
  engine_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  input_hash TEXT NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
  result_hash TEXT CHECK (result_hash IS NULL OR result_hash ~ '^[0-9a-f]{64}$'),
  parameter_hash TEXT NOT NULL CHECK (parameter_hash ~ '^[0-9a-f]{64}$'),
  seed BIGINT NOT NULL,
  assumptions JSONB NOT NULL DEFAULT '[]'::jsonb,
  idempotency_key TEXT NOT NULL,
  request_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  requested_at TIMESTAMPTZ NOT NULL,
  reason TEXT NOT NULL,
  request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (strategy_version_id, dataset_version_id, engine_version, parameter_hash, seed),
  UNIQUE (input_hash),
  UNIQUE (idempotency_key),
  FOREIGN KEY (strategy_version_id, strategy_graph_hash)
    REFERENCES strategy_versions(id, graph_hash) ON DELETE RESTRICT,
  FOREIGN KEY (dataset_version_id, dataset_content_hash)
    REFERENCES dataset_versions(id, content_hash) ON DELETE RESTRICT,
  CHECK (btrim(engine_version) <> ''),
  CHECK (btrim(idempotency_key) <> ''),
  CHECK (btrim(request_id) <> ''),
  CHECK (btrim(actor_id) <> ''),
  CHECK (btrim(reason) <> ''),
  CHECK (jsonb_typeof(assumptions) = 'array'),
  CHECK (status <> 'running' OR started_at IS NOT NULL),
  CHECK (status <> 'queued' OR (started_at IS NULL AND finished_at IS NULL)),
  CHECK (status NOT IN ('succeeded','failed','cancelled') OR finished_at IS NOT NULL),
  CHECK (status <> 'succeeded' OR result_hash IS NOT NULL)
);

CREATE TABLE backtest_trades (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id BIGINT NOT NULL REFERENCES backtest_runs(id) ON DELETE RESTRICT,
  trade_sequence INTEGER NOT NULL CHECK (trade_sequence > 0),
  signal_sequence INTEGER,
  side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  requested_quantity NUMERIC(38,18) NOT NULL CHECK (requested_quantity >= 0),
  filled_quantity NUMERIC(38,18) NOT NULL CHECK (filled_quantity >= 0),
  remaining_quantity NUMERIC(38,18) NOT NULL CHECK (remaining_quantity >= 0),
  fill_price NUMERIC(38,18) NOT NULL CHECK (fill_price >= 0),
  fee_paid NUMERIC(38,18) NOT NULL CHECK (fee_paid >= 0),
  status TEXT NOT NULL CHECK (status IN ('filled','partially_filled','rejected')),
  occurred_at TIMESTAMPTZ NOT NULL,
  knowledge_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (run_id, trade_sequence),
  CHECK (filled_quantity + remaining_quantity = requested_quantity),
  CHECK (knowledge_at >= occurred_at)
);

CREATE TABLE backtest_equity_points (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id BIGINT NOT NULL REFERENCES backtest_runs(id) ON DELETE RESTRICT,
  point_sequence INTEGER NOT NULL CHECK (point_sequence > 0),
  occurred_at TIMESTAMPTZ NOT NULL,
  knowledge_at TIMESTAMPTZ NOT NULL,
  cash NUMERIC(38,18) NOT NULL,
  base_position NUMERIC(38,18) NOT NULL,
  equity NUMERIC(38,18) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (run_id, point_sequence),
  UNIQUE (run_id, occurred_at),
  CHECK (knowledge_at >= occurred_at)
);

CREATE TABLE backtest_metrics (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id BIGINT NOT NULL REFERENCES backtest_runs(id) ON DELETE RESTRICT,
  metric_name TEXT NOT NULL,
  scope_key TEXT NOT NULL DEFAULT 'run',
  metric_value NUMERIC(38,18),
  metric_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (run_id, metric_name, scope_key),
  CHECK (btrim(metric_name) <> ''),
  CHECK (btrim(scope_key) <> ''),
  CHECK (metric_value IS NOT NULL OR metric_payload <> '{}'::jsonb)
);

CREATE TABLE backtest_artifacts (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id BIGINT NOT NULL REFERENCES backtest_runs(id) ON DELETE RESTRICT,
  artifact_type TEXT NOT NULL,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  media_type TEXT NOT NULL,
  storage_uri TEXT,
  artifact_json JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (run_id, artifact_type, content_hash),
  CHECK (btrim(artifact_type) <> ''),
  CHECK (btrim(media_type) <> ''),
  CHECK (storage_uri IS NOT NULL OR artifact_json IS NOT NULL)
);

CREATE INDEX backtest_runs_strategy_dataset_idx
  ON backtest_runs (strategy_version_id, dataset_version_id, created_at DESC, id DESC);
CREATE INDEX backtest_runs_status_idx ON backtest_runs (status, created_at, id);

CREATE OR REPLACE FUNCTION validate_backtest_run_inputs()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  -- strategy.status <> 'published'은 백테스트 실행 입력으로 거부한다.
  IF NOT EXISTS (
    SELECT 1 FROM strategy_versions strategy
    WHERE strategy.id = NEW.strategy_version_id AND strategy.status = 'published'
  ) THEN
    RAISE EXCEPTION 'backtest_runs requires published strategy version';
  END IF;

  -- version.sealed_at IS NULL인 데이터셋은 백테스트 실행 입력으로 거부한다.
  IF NOT EXISTS (
    SELECT 1 FROM dataset_versions version
    WHERE version.id = NEW.dataset_version_id AND version.sealed_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'backtest_runs requires sealed dataset version';
  END IF;

  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_backtest_run_terminal_seal()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'backtest_runs is append-only';
  END IF;

  IF OLD.status IN ('succeeded','failed','cancelled') THEN
    RAISE EXCEPTION 'backtest_runs is append-only';
  END IF;

  IF NEW.strategy_version_id <> OLD.strategy_version_id
     OR NEW.strategy_graph_hash <> OLD.strategy_graph_hash
     OR NEW.dataset_version_id <> OLD.dataset_version_id
     OR NEW.dataset_content_hash <> OLD.dataset_content_hash
     OR NEW.engine_version <> OLD.engine_version
     OR NEW.input_hash <> OLD.input_hash
     OR NEW.parameter_hash <> OLD.parameter_hash
     OR NEW.seed <> OLD.seed
     OR NEW.idempotency_key <> OLD.idempotency_key
     OR NEW.request_id <> OLD.request_id
     OR NEW.actor_id <> OLD.actor_id
     OR NEW.requested_at <> OLD.requested_at
     OR NEW.reason <> OLD.reason
     OR NEW.request_hash <> OLD.request_hash THEN
    RAISE EXCEPTION 'backtest_runs immutable identity fields cannot be changed';
  END IF;

  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION reject_backtest_result_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER backtest_runs_validate_insert BEFORE INSERT ON backtest_runs
  FOR EACH ROW EXECUTE FUNCTION validate_backtest_run_inputs();
CREATE TRIGGER backtest_runs_validate_update BEFORE UPDATE ON backtest_runs
  FOR EACH ROW EXECUTE FUNCTION validate_backtest_run_inputs();
CREATE TRIGGER backtest_runs_terminal_update BEFORE UPDATE ON backtest_runs
  FOR EACH ROW EXECUTE FUNCTION enforce_backtest_run_terminal_seal();
CREATE TRIGGER backtest_runs_append_only_delete BEFORE DELETE ON backtest_runs
  FOR EACH ROW EXECUTE FUNCTION enforce_backtest_run_terminal_seal();

CREATE TRIGGER backtest_trades_append_only_update BEFORE UPDATE ON backtest_trades
  FOR EACH ROW EXECUTE FUNCTION reject_backtest_result_mutation();
CREATE TRIGGER backtest_trades_append_only_delete BEFORE DELETE ON backtest_trades
  FOR EACH ROW EXECUTE FUNCTION reject_backtest_result_mutation();
CREATE TRIGGER backtest_equity_points_append_only_update BEFORE UPDATE ON backtest_equity_points
  FOR EACH ROW EXECUTE FUNCTION reject_backtest_result_mutation();
CREATE TRIGGER backtest_equity_points_append_only_delete BEFORE DELETE ON backtest_equity_points
  FOR EACH ROW EXECUTE FUNCTION reject_backtest_result_mutation();
CREATE TRIGGER backtest_metrics_append_only_update BEFORE UPDATE ON backtest_metrics
  FOR EACH ROW EXECUTE FUNCTION reject_backtest_result_mutation();
CREATE TRIGGER backtest_metrics_append_only_delete BEFORE DELETE ON backtest_metrics
  FOR EACH ROW EXECUTE FUNCTION reject_backtest_result_mutation();
CREATE TRIGGER backtest_artifacts_append_only_update BEFORE UPDATE ON backtest_artifacts
  FOR EACH ROW EXECUTE FUNCTION reject_backtest_result_mutation();
CREATE TRIGGER backtest_artifacts_append_only_delete BEFORE DELETE ON backtest_artifacts
  FOR EACH ROW EXECUTE FUNCTION reject_backtest_result_mutation();

GRANT SELECT, INSERT, UPDATE ON backtest_runs TO CURRENT_USER;
GRANT SELECT, INSERT ON backtest_trades, backtest_equity_points, backtest_metrics,
  backtest_artifacts TO CURRENT_USER;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO CURRENT_USER;

-- migrate:down

DROP TRIGGER IF EXISTS backtest_artifacts_append_only_delete ON backtest_artifacts;
DROP TRIGGER IF EXISTS backtest_artifacts_append_only_update ON backtest_artifacts;
DROP TRIGGER IF EXISTS backtest_metrics_append_only_delete ON backtest_metrics;
DROP TRIGGER IF EXISTS backtest_metrics_append_only_update ON backtest_metrics;
DROP TRIGGER IF EXISTS backtest_equity_points_append_only_delete ON backtest_equity_points;
DROP TRIGGER IF EXISTS backtest_equity_points_append_only_update ON backtest_equity_points;
DROP TRIGGER IF EXISTS backtest_trades_append_only_delete ON backtest_trades;
DROP TRIGGER IF EXISTS backtest_trades_append_only_update ON backtest_trades;
DROP TRIGGER IF EXISTS backtest_runs_append_only_delete ON backtest_runs;
DROP TRIGGER IF EXISTS backtest_runs_terminal_update ON backtest_runs;
DROP TRIGGER IF EXISTS backtest_runs_validate_update ON backtest_runs;
DROP TRIGGER IF EXISTS backtest_runs_validate_insert ON backtest_runs;
DROP FUNCTION IF EXISTS reject_backtest_result_mutation();
DROP FUNCTION IF EXISTS enforce_backtest_run_terminal_seal();
DROP FUNCTION IF EXISTS validate_backtest_run_inputs();
DROP TABLE IF EXISTS backtest_artifacts;
DROP TABLE IF EXISTS backtest_metrics;
DROP TABLE IF EXISTS backtest_equity_points;
DROP TABLE IF EXISTS backtest_trades;
DROP TABLE IF EXISTS backtest_runs;
ALTER TABLE dataset_versions
  DROP CONSTRAINT IF EXISTS dataset_versions_id_content_hash_key;
