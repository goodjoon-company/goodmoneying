-- migrate:up

SET TIME ZONE 'UTC';

CREATE TABLE upbit_live_reconciliation_applications (
  id BIGSERIAL PRIMARY KEY,
  exchange_account_id BIGINT NOT NULL REFERENCES exchange_accounts(id) ON DELETE RESTRICT,
  order_intent_id BIGINT NOT NULL REFERENCES order_intents(id) ON DELETE RESTRICT,
  exchange_order_id BIGINT NOT NULL REFERENCES exchange_orders(id) ON DELETE RESTRICT,
  live_exchange_order_binding_id BIGINT NOT NULL REFERENCES upbit_live_exchange_order_bindings(id) ON DELETE RESTRICT,
  reconciliation_run_id BIGINT NOT NULL REFERENCES reconciliation_runs(id) ON DELETE RESTRICT,
  source TEXT NOT NULL,
  source_endpoint TEXT NOT NULL,
  observed_upbit_order_uuid TEXT NOT NULL,
  observed_upbit_identifier TEXT NOT NULL,
  observed_state TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  request_hash TEXT NOT NULL,
  can_resubmit BOOLEAN NOT NULL DEFAULT false,
  actual_request_sent BOOLEAN NOT NULL DEFAULT false,
  actual_order_cancel_sent BOOLEAN NOT NULL DEFAULT false,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  actor_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  request_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  UNIQUE (reconciliation_run_id),
  UNIQUE (request_id),
  UNIQUE (idempotency_key),
  CHECK (source = 'rest_order_snapshot'),
  CHECK (source_endpoint IN (
    'GET /v1/order',
    'GET /v1/orders/open',
    'GET /v1/orders/closed',
    'GET /v1/orders/uuids'
  )),
  CHECK (observed_upbit_order_uuid ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
  CHECK (observed_upbit_identifier ~ '^gm1_[a-z2-7]{52}$'),
  CHECK (observed_state IN ('done','cancel','prevented','rejected')),
  CHECK (applied_at <= recorded_at),
  CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  CHECK (can_resubmit IS FALSE),
  CHECK (actual_request_sent IS FALSE),
  CHECK (actual_order_cancel_sent IS FALSE),
  CHECK (actor_id <> ''),
  CHECK (actor_id !~* '^(ci|ai|service):'),
  CHECK (reason <> ''),
  CHECK (request_id <> ''),
  CHECK (idempotency_key <> ''),
  CHECK (jsonb_typeof(evidence) = 'object')
);

CREATE FUNCTION validate_p6_live_reconciliation_application()
RETURNS TRIGGER AS $$
DECLARE
  binding RECORD;
  exchange_order RECORD;
  run RECORD;
BEGIN
  SELECT exchange_account_id, order_intent_id, exchange_order_id,
         upbit_order_uuid, upbit_identifier
    INTO binding
  FROM upbit_live_exchange_order_bindings
  WHERE id = NEW.live_exchange_order_binding_id;

  IF binding IS NULL THEN
    RAISE EXCEPTION 'live reconciliation application references missing binding';
  END IF;

  IF binding.exchange_account_id <> NEW.exchange_account_id
     OR binding.order_intent_id <> NEW.order_intent_id
     OR binding.exchange_order_id <> NEW.exchange_order_id
     OR binding.upbit_order_uuid <> NEW.observed_upbit_order_uuid
     OR binding.upbit_identifier <> NEW.observed_upbit_identifier THEN
    RAISE EXCEPTION 'live reconciliation application requires matching binding';
  END IF;

  SELECT order_intent_id, execution_mode, simulated_order_key
    INTO exchange_order
  FROM exchange_orders
  WHERE id = NEW.exchange_order_id;

  IF exchange_order IS NULL THEN
    RAISE EXCEPTION 'live reconciliation application references missing exchange order';
  END IF;

  IF exchange_order.execution_mode <> 'live'
     OR exchange_order.order_intent_id <> NEW.order_intent_id
     OR exchange_order.simulated_order_key <> NEW.observed_upbit_identifier THEN
    RAISE EXCEPTION 'live reconciliation application requires live exchange order';
  END IF;

  SELECT exchange_order_id, status, observed_status, evidence
    INTO run
  FROM reconciliation_runs
  WHERE id = NEW.reconciliation_run_id;

  IF run IS NULL THEN
    RAISE EXCEPTION 'live reconciliation application references missing reconciliation run';
  END IF;

  IF run.exchange_order_id <> NEW.exchange_order_id THEN
    RAISE EXCEPTION 'live reconciliation application run does not match exchange order';
  END IF;

  IF run.status <> 'succeeded'
     OR run.observed_status <> NEW.observed_state THEN
    RAISE EXCEPTION 'live reconciliation application requires succeeded reconciliation run';
  END IF;

  IF run.evidence->>'source' <> 'upbit-rest-order-snapshot'
     OR run.evidence->>'sourceEndpoint' <> NEW.source_endpoint
     OR run.evidence->>'orderUuid' <> NEW.observed_upbit_order_uuid
     OR run.evidence->>'identifier' <> NEW.observed_upbit_identifier
     OR run.evidence->>'state' <> NEW.observed_state
     OR run.evidence->>'canResubmit' <> 'false' THEN
    RAISE EXCEPTION 'live reconciliation application snapshot must match reconciliation evidence';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION reject_p6_live_reconciliation_application_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'Upbit live reconciliation application is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION validate_p6_live_reconciliation_run_has_application()
RETURNS TRIGGER AS $$
DECLARE
  exchange_order RECORD;
BEGIN
  IF NEW.status <> 'succeeded' THEN
    RETURN NEW;
  END IF;

  SELECT execution_mode
    INTO exchange_order
  FROM exchange_orders
  WHERE id = NEW.exchange_order_id;

  IF exchange_order IS NULL OR exchange_order.execution_mode <> 'live' THEN
    RETURN NEW;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM upbit_live_reconciliation_applications application
    WHERE application.reconciliation_run_id = NEW.id
      AND application.exchange_order_id = NEW.exchange_order_id
  ) THEN
    RAISE EXCEPTION 'live succeeded reconciliation run requires live application';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER upbit_live_reconciliation_applications_validate
  BEFORE INSERT ON upbit_live_reconciliation_applications
  FOR EACH ROW EXECUTE FUNCTION validate_p6_live_reconciliation_application();

CREATE TRIGGER upbit_live_reconciliation_applications_append_only_update
  BEFORE UPDATE ON upbit_live_reconciliation_applications
  FOR EACH ROW EXECUTE FUNCTION reject_p6_live_reconciliation_application_mutation();

CREATE TRIGGER upbit_live_reconciliation_applications_append_only_delete
  BEFORE DELETE ON upbit_live_reconciliation_applications
  FOR EACH ROW EXECUTE FUNCTION reject_p6_live_reconciliation_application_mutation();

CREATE CONSTRAINT TRIGGER reconciliation_runs_require_live_application
  AFTER INSERT ON reconciliation_runs
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION validate_p6_live_reconciliation_run_has_application();

CREATE INDEX upbit_live_reconciliation_applications_observed_idx
  ON upbit_live_reconciliation_applications (source_endpoint, observed_state, applied_at, id);

-- migrate:down

SET TIME ZONE 'UTC';

DROP INDEX IF EXISTS upbit_live_reconciliation_applications_observed_idx;
DROP TRIGGER IF EXISTS upbit_live_reconciliation_applications_append_only_delete
  ON upbit_live_reconciliation_applications;
DROP TRIGGER IF EXISTS upbit_live_reconciliation_applications_append_only_update
  ON upbit_live_reconciliation_applications;
DROP TRIGGER IF EXISTS upbit_live_reconciliation_applications_validate
  ON upbit_live_reconciliation_applications;
DROP TRIGGER IF EXISTS reconciliation_runs_require_live_application
  ON reconciliation_runs;
DROP FUNCTION IF EXISTS reject_p6_live_reconciliation_application_mutation();
DROP FUNCTION IF EXISTS validate_p6_live_reconciliation_run_has_application();
DROP FUNCTION IF EXISTS validate_p6_live_reconciliation_application();
DROP TABLE IF EXISTS upbit_live_reconciliation_applications;
