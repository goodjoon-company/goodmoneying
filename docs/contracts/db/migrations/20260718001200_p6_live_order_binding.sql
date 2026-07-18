-- migrate:up

SET TIME ZONE 'UTC';

ALTER TABLE exchange_orders
  DROP CONSTRAINT exchange_orders_execution_mode_check;

ALTER TABLE exchange_orders
  ADD CONSTRAINT exchange_orders_execution_mode_check
  CHECK (execution_mode IN ('paper','shadow','live'));

CREATE TABLE upbit_live_exchange_order_bindings (
  id BIGSERIAL PRIMARY KEY,
  exchange_account_id BIGINT NOT NULL REFERENCES exchange_accounts(id) ON DELETE RESTRICT,
  order_intent_id BIGINT NOT NULL REFERENCES order_intents(id) ON DELETE RESTRICT,
  exchange_order_id BIGINT NOT NULL REFERENCES exchange_orders(id) ON DELETE RESTRICT,
  live_order_identifier_id BIGINT NOT NULL REFERENCES live_order_identifiers(id) ON DELETE RESTRICT,
  upbit_order_outbox_id BIGINT NOT NULL REFERENCES upbit_order_outbox(id) ON DELETE RESTRICT,
  upbit_order_uuid TEXT NOT NULL,
  upbit_identifier TEXT NOT NULL,
  source TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  bound_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  actor_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  request_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  UNIQUE (exchange_order_id),
  UNIQUE (live_order_identifier_id),
  UNIQUE (upbit_order_outbox_id),
  UNIQUE (request_id),
  UNIQUE (idempotency_key),
  UNIQUE (exchange_account_id, upbit_order_uuid),
  UNIQUE (exchange_account_id, upbit_identifier),
  CHECK (upbit_order_uuid ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
  CHECK (upbit_identifier ~ '^gm1_[a-z2-7]{52}$'),
  CHECK (source IN ('order_submit_response','rest_order_snapshot','myorder_event')),
  CHECK (observed_at <= bound_at),
  CHECK (actor_id <> ''),
  CHECK (actor_id !~* '^(ci|ai|service):'),
  CHECK (reason <> ''),
  CHECK (request_id <> ''),
  CHECK (idempotency_key <> ''),
  CHECK (jsonb_typeof(evidence) = 'object')
);

CREATE FUNCTION validate_p6_live_exchange_order_binding()
RETURNS TRIGGER AS $$
DECLARE
  exchange_order RECORD;
  live_identifier RECORD;
  outbox RECORD;
BEGIN
  SELECT order_intent_id, execution_mode, simulated_order_key
    INTO exchange_order
  FROM exchange_orders
  WHERE id = NEW.exchange_order_id;

  IF exchange_order IS NULL THEN
    RAISE EXCEPTION 'live exchange order binding references missing exchange order';
  END IF;

  IF exchange_order.execution_mode <> 'live' THEN
    RAISE EXCEPTION 'live exchange order binding requires live exchange order';
  END IF;

  IF exchange_order.order_intent_id <> NEW.order_intent_id THEN
    RAISE EXCEPTION 'live binding order intent does not match exchange order';
  END IF;

  IF exchange_order.simulated_order_key <> NEW.upbit_identifier THEN
    RAISE EXCEPTION 'live exchange order key must match Upbit identifier';
  END IF;

  SELECT exchange_account_id, order_intent_id, identifier, status
    INTO live_identifier
  FROM live_order_identifiers
  WHERE id = NEW.live_order_identifier_id;

  IF live_identifier IS NULL THEN
    RAISE EXCEPTION 'live exchange order binding references missing live identifier';
  END IF;

  IF live_identifier.exchange_account_id <> NEW.exchange_account_id THEN
    RAISE EXCEPTION 'live binding exchange account does not match live identifier';
  END IF;

  IF live_identifier.order_intent_id <> NEW.order_intent_id THEN
    RAISE EXCEPTION 'live binding order intent does not match live identifier';
  END IF;

  IF live_identifier.identifier <> NEW.upbit_identifier THEN
    RAISE EXCEPTION 'live binding Upbit identifier must match live identifier';
  END IF;

  IF live_identifier.status <> 'reserved' THEN
    RAISE EXCEPTION 'live binding requires reserved live identifier';
  END IF;

  SELECT exchange_account_id, order_intent_id, live_order_identifier_id, status
    INTO outbox
  FROM upbit_order_outbox
  WHERE id = NEW.upbit_order_outbox_id;

  IF outbox IS NULL THEN
    RAISE EXCEPTION 'live exchange order binding references missing safe order outbox';
  END IF;

  IF outbox.status <> 'ready' THEN
    RAISE EXCEPTION 'live exchange order binding requires ready outbox';
  END IF;

  IF outbox.exchange_account_id <> NEW.exchange_account_id
     OR outbox.order_intent_id <> NEW.order_intent_id
     OR outbox.live_order_identifier_id <> NEW.live_order_identifier_id THEN
    RAISE EXCEPTION 'live exchange order binding does not match safe order outbox';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM upbit_order_test_runs test_run
    WHERE test_run.exchange_account_id = NEW.exchange_account_id
      AND (
        NEW.upbit_order_uuid IN (
          test_run.response_uuid,
          test_run.response_identifier
        )
        OR NEW.upbit_identifier IN (
          test_run.response_uuid,
          test_run.response_identifier
        )
      )
  ) THEN
    RAISE EXCEPTION 'order-test response identifier cannot be bound as live exchange order';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION mark_p6_live_identifier_submitted()
RETURNS TRIGGER AS $$
BEGIN
  UPDATE live_order_identifiers
  SET status = 'submitted'
  WHERE id = NEW.live_order_identifier_id
    AND status = 'reserved';
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION validate_p6_live_exchange_order_has_binding()
RETURNS TRIGGER AS $$
DECLARE
  binding RECORD;
BEGIN
  IF NEW.execution_mode <> 'live' THEN
    RETURN NEW;
  END IF;

  SELECT order_intent_id, upbit_identifier
    INTO binding
  FROM upbit_live_exchange_order_bindings
  WHERE exchange_order_id = NEW.id;

  IF binding IS NULL THEN
    RAISE EXCEPTION 'live exchange order requires Upbit live binding';
  END IF;

  IF binding.order_intent_id <> NEW.order_intent_id
     OR binding.upbit_identifier <> NEW.simulated_order_key THEN
    RAISE EXCEPTION 'live exchange order no longer matches Upbit live binding';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION reject_p6_live_exchange_order_binding_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'Upbit live exchange order binding is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER upbit_live_exchange_order_bindings_validate
  BEFORE INSERT ON upbit_live_exchange_order_bindings
  FOR EACH ROW EXECUTE FUNCTION validate_p6_live_exchange_order_binding();

CREATE TRIGGER upbit_live_exchange_order_bindings_mark_submitted
  AFTER INSERT ON upbit_live_exchange_order_bindings
  FOR EACH ROW EXECUTE FUNCTION mark_p6_live_identifier_submitted();

CREATE TRIGGER upbit_live_exchange_order_bindings_append_only_update
  BEFORE UPDATE ON upbit_live_exchange_order_bindings
  FOR EACH ROW EXECUTE FUNCTION reject_p6_live_exchange_order_binding_mutation();

CREATE TRIGGER upbit_live_exchange_order_bindings_append_only_delete
  BEFORE DELETE ON upbit_live_exchange_order_bindings
  FOR EACH ROW EXECUTE FUNCTION reject_p6_live_exchange_order_binding_mutation();

CREATE CONSTRAINT TRIGGER exchange_orders_require_live_binding
  AFTER INSERT OR UPDATE ON exchange_orders
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION validate_p6_live_exchange_order_has_binding();

CREATE INDEX upbit_live_exchange_order_bindings_observed_idx
  ON upbit_live_exchange_order_bindings (source, observed_at, id);

-- migrate:down

SET TIME ZONE 'UTC';

DROP INDEX IF EXISTS upbit_live_exchange_order_bindings_observed_idx;
DROP TRIGGER IF EXISTS upbit_live_exchange_order_bindings_append_only_delete
  ON upbit_live_exchange_order_bindings;
DROP TRIGGER IF EXISTS upbit_live_exchange_order_bindings_append_only_update
  ON upbit_live_exchange_order_bindings;
DROP TRIGGER IF EXISTS upbit_live_exchange_order_bindings_mark_submitted
  ON upbit_live_exchange_order_bindings;
DROP TRIGGER IF EXISTS upbit_live_exchange_order_bindings_validate
  ON upbit_live_exchange_order_bindings;
DROP TRIGGER IF EXISTS exchange_orders_require_live_binding ON exchange_orders;
DROP FUNCTION IF EXISTS reject_p6_live_exchange_order_binding_mutation();
DROP FUNCTION IF EXISTS validate_p6_live_exchange_order_has_binding();
DROP FUNCTION IF EXISTS mark_p6_live_identifier_submitted();
DROP FUNCTION IF EXISTS validate_p6_live_exchange_order_binding();
DROP TABLE IF EXISTS upbit_live_exchange_order_bindings;

ALTER TABLE exchange_orders
  DROP CONSTRAINT exchange_orders_execution_mode_check;

ALTER TABLE exchange_orders
  ADD CONSTRAINT exchange_orders_execution_mode_check
  CHECK (execution_mode IN ('paper','shadow'));
