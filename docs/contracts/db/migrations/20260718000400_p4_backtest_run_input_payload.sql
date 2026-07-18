-- migrate:up

SET TIME ZONE 'UTC';

ALTER TABLE backtest_runs
  ADD COLUMN input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD CONSTRAINT backtest_runs_input_payload_object_check
    CHECK (jsonb_typeof(input_payload) = 'object');

CREATE OR REPLACE FUNCTION enforce_backtest_run_terminal_seal()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'backtest_runs is append-only';
  END IF;

  IF OLD.status IN ('succeeded','failed','cancelled','dead_letter') THEN
    RAISE EXCEPTION 'backtest_runs is append-only';
  END IF;

  IF NEW.strategy_version_id <> OLD.strategy_version_id
     OR NEW.strategy_graph_hash <> OLD.strategy_graph_hash
     OR NEW.dataset_version_id <> OLD.dataset_version_id
     OR NEW.dataset_content_hash <> OLD.dataset_content_hash
     OR NEW.engine_version <> OLD.engine_version
     OR NEW.input_hash <> OLD.input_hash
     OR NEW.input_payload <> OLD.input_payload
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

-- migrate:down

SET TIME ZONE 'UTC';

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

ALTER TABLE backtest_runs
  DROP CONSTRAINT IF EXISTS backtest_runs_input_payload_object_check,
  DROP COLUMN IF EXISTS input_payload;
