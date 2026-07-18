-- migrate:up

SET TIME ZONE 'UTC';

CREATE TABLE reconciliation_runs (
  id BIGSERIAL PRIMARY KEY,
  exchange_order_id BIGINT NOT NULL REFERENCES exchange_orders(id) ON DELETE RESTRICT,
  run_key TEXT NOT NULL,
  status TEXT NOT NULL,
  observed_status TEXT NOT NULL,
  observed_fill_count INTEGER NOT NULL DEFAULT 0,
  request_hash TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  completed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (exchange_order_id, run_key),
  CHECK (run_key <> ''),
  CHECK (status IN ('succeeded','mismatch','outcome_unknown')),
  CHECK (observed_status IN ('done','cancel','prevented','rejected','outcome_unknown','missing')),
  CHECK (observed_fill_count >= 0),
  CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  CHECK (actor_id <> ''),
  CHECK (reason <> ''),
  CHECK (jsonb_typeof(evidence) = 'object'),
  CHECK (completed_at >= started_at)
);

CREATE INDEX reconciliation_runs_status_idx
  ON reconciliation_runs (status, completed_at DESC, id DESC);

CREATE TRIGGER reconciliation_runs_append_only_update
  BEFORE UPDATE ON reconciliation_runs
  FOR EACH ROW EXECUTE FUNCTION reject_p5_append_only_mutation();

CREATE TRIGGER reconciliation_runs_append_only_delete
  BEFORE DELETE ON reconciliation_runs
  FOR EACH ROW EXECUTE FUNCTION reject_p5_append_only_mutation();

-- migrate:down

SET TIME ZONE 'UTC';

DROP TRIGGER IF EXISTS reconciliation_runs_append_only_delete ON reconciliation_runs;
DROP TRIGGER IF EXISTS reconciliation_runs_append_only_update ON reconciliation_runs;
DROP INDEX IF EXISTS reconciliation_runs_status_idx;
DROP TABLE IF EXISTS reconciliation_runs;
