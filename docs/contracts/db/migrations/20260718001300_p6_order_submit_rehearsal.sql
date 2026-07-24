-- migrate:up

SET TIME ZONE 'UTC';

CREATE TABLE upbit_order_submit_rehearsals (
  id BIGSERIAL PRIMARY KEY,
  exchange_account_id BIGINT NOT NULL REFERENCES exchange_accounts(id) ON DELETE RESTRICT,
  order_intent_id BIGINT NOT NULL REFERENCES order_intents(id) ON DELETE RESTRICT,
  live_order_identifier_id BIGINT NOT NULL REFERENCES live_order_identifiers(id) ON DELETE RESTRICT,
  upbit_order_outbox_id BIGINT NOT NULL REFERENCES upbit_order_outbox(id) ON DELETE RESTRICT,
  permission_attestation_id BIGINT REFERENCES upbit_api_key_permission_attestations(id) ON DELETE RESTRICT,
  rehearsal_status TEXT NOT NULL,
  blocked_reason TEXT,
  endpoint_key TEXT NOT NULL,
  http_method TEXT NOT NULL,
  request_path TEXT NOT NULL,
  request_payload JSONB NOT NULL,
  request_hash TEXT NOT NULL,
  query_string TEXT NOT NULL,
  query_hash TEXT NOT NULL,
  actual_request_sent BOOLEAN NOT NULL DEFAULT false,
  would_submit BOOLEAN NOT NULL DEFAULT false,
  can_bind_response BOOLEAN NOT NULL DEFAULT false,
  response_uuid TEXT,
  response_identifier TEXT,
  rehearsed_at TIMESTAMPTZ NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  actor_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  request_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  UNIQUE (upbit_order_outbox_id),
  UNIQUE (request_id),
  UNIQUE (idempotency_key),
  CHECK (rehearsal_status IN ('passed','blocked')),
  CHECK (
    (rehearsal_status = 'passed' AND blocked_reason IS NULL)
    OR
    (rehearsal_status = 'blocked' AND blocked_reason IS NOT NULL)
  ),
  CHECK (blocked_reason IS NULL OR blocked_reason IN (
    'outbox_not_ready',
    'permission_expired',
    'live_identifier_not_reserved',
    'already_bound',
    'request_mismatch',
    'policy_blocked'
  )),
  CHECK (endpoint_key = 'rest.new-order'),
  CHECK (http_method = 'POST'),
  CHECK (request_path = '/v1/orders'),
  CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  CHECK (query_string <> ''),
  CHECK (query_hash ~ '^[0-9a-f]{128}$'),
  CHECK (actual_request_sent IS FALSE),
  CHECK (would_submit IS FALSE),
  CHECK (can_bind_response IS FALSE),
  CHECK (response_uuid IS NULL),
  CHECK (response_identifier IS NULL),
  CHECK (rehearsed_at <= recorded_at),
  CHECK (actor_id <> ''),
  CHECK (actor_id !~* '^(ci|ai|service):'),
  CHECK (reason <> ''),
  CHECK (request_id <> ''),
  CHECK (idempotency_key <> ''),
  CHECK (jsonb_typeof(request_payload) = 'object'),
  CHECK (jsonb_typeof(evidence) = 'object')
);

CREATE FUNCTION validate_p6_order_submit_rehearsal()
RETURNS TRIGGER AS $$
DECLARE
  live_identifier RECORD;
  outbox RECORD;
  permission RECORD;
BEGIN
  SELECT exchange_account_id, order_intent_id, identifier, status
    INTO live_identifier
  FROM live_order_identifiers
  WHERE id = NEW.live_order_identifier_id;

  IF live_identifier IS NULL THEN
    RAISE EXCEPTION 'order submit rehearsal references missing live identifier';
  END IF;

  SELECT exchange_account_id, order_intent_id, live_order_identifier_id,
         permission_attestation_id, status, request_payload, request_hash
    INTO outbox
  FROM upbit_order_outbox
  WHERE id = NEW.upbit_order_outbox_id;

  IF outbox IS NULL THEN
    RAISE EXCEPTION 'order submit rehearsal references missing outbox';
  END IF;

  IF live_identifier.exchange_account_id <> NEW.exchange_account_id
     OR live_identifier.order_intent_id <> NEW.order_intent_id THEN
    RAISE EXCEPTION 'order submit rehearsal live identifier account or intent mismatch';
  END IF;

  IF outbox.exchange_account_id <> NEW.exchange_account_id
     OR outbox.order_intent_id <> NEW.order_intent_id
     OR outbox.live_order_identifier_id <> NEW.live_order_identifier_id THEN
    RAISE EXCEPTION 'order submit rehearsal outbox account or intent mismatch';
  END IF;

  IF NEW.request_payload <> outbox.request_payload
     OR NEW.request_hash <> outbox.request_hash THEN
    RAISE EXCEPTION 'order submit rehearsal request mismatch';
  END IF;

  IF NEW.request_payload->>'identifier' <> live_identifier.identifier THEN
    RAISE EXCEPTION 'order submit rehearsal identifier must match live identifier';
  END IF;

  IF NEW.rehearsal_status = 'passed' THEN
    IF outbox.status <> 'ready' THEN
      RAISE EXCEPTION 'order submit rehearsal requires ready outbox';
    END IF;

    IF live_identifier.status <> 'reserved' THEN
      RAISE EXCEPTION 'order submit rehearsal requires reserved live identifier';
    END IF;

    IF NEW.permission_attestation_id IS NULL
       OR outbox.permission_attestation_id IS NULL
       OR NEW.permission_attestation_id <> outbox.permission_attestation_id THEN
      RAISE EXCEPTION 'order submit rehearsal permission attestation mismatch';
    END IF;

    SELECT exchange_account_id, expires_at
      INTO permission
    FROM upbit_api_key_permission_attestations
    WHERE id = NEW.permission_attestation_id;

    IF permission IS NULL THEN
      RAISE EXCEPTION 'order submit rehearsal references missing permission attestation';
    END IF;

    IF permission.exchange_account_id <> NEW.exchange_account_id THEN
      RAISE EXCEPTION 'order submit rehearsal permission account mismatch';
    END IF;

    IF permission.expires_at <= clock_timestamp() THEN
      RAISE EXCEPTION 'order submit rehearsal permission expired';
    END IF;

    IF EXISTS (
      SELECT 1
      FROM upbit_live_exchange_order_bindings binding
      WHERE binding.upbit_order_outbox_id = NEW.upbit_order_outbox_id
         OR binding.live_order_identifier_id = NEW.live_order_identifier_id
    ) THEN
      RAISE EXCEPTION 'order submit rehearsal cannot follow live binding';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION reject_p6_order_submit_rehearsal_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'Upbit order submit rehearsal is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER upbit_order_submit_rehearsals_validate
  BEFORE INSERT ON upbit_order_submit_rehearsals
  FOR EACH ROW EXECUTE FUNCTION validate_p6_order_submit_rehearsal();

CREATE TRIGGER upbit_order_submit_rehearsals_append_only_update
  BEFORE UPDATE ON upbit_order_submit_rehearsals
  FOR EACH ROW EXECUTE FUNCTION reject_p6_order_submit_rehearsal_mutation();

CREATE TRIGGER upbit_order_submit_rehearsals_append_only_delete
  BEFORE DELETE ON upbit_order_submit_rehearsals
  FOR EACH ROW EXECUTE FUNCTION reject_p6_order_submit_rehearsal_mutation();

CREATE INDEX upbit_order_submit_rehearsals_status_idx
  ON upbit_order_submit_rehearsals (rehearsal_status, rehearsed_at, id);

-- migrate:down

SET TIME ZONE 'UTC';

DROP INDEX IF EXISTS upbit_order_submit_rehearsals_status_idx;
DROP TRIGGER IF EXISTS upbit_order_submit_rehearsals_append_only_delete
  ON upbit_order_submit_rehearsals;
DROP TRIGGER IF EXISTS upbit_order_submit_rehearsals_append_only_update
  ON upbit_order_submit_rehearsals;
DROP TRIGGER IF EXISTS upbit_order_submit_rehearsals_validate
  ON upbit_order_submit_rehearsals;
DROP FUNCTION IF EXISTS reject_p6_order_submit_rehearsal_mutation();
DROP FUNCTION IF EXISTS validate_p6_order_submit_rehearsal();
DROP TABLE IF EXISTS upbit_order_submit_rehearsals;
