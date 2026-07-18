-- migrate:up

SET TIME ZONE 'UTC';

CREATE TABLE upbit_api_key_permission_attestations (
  id BIGSERIAL PRIMARY KEY,
  exchange_account_id BIGINT NOT NULL REFERENCES exchange_accounts(id) ON DELETE RESTRICT,
  has_order_permission BOOLEAN NOT NULL,
  has_order_read_permission BOOLEAN NOT NULL,
  has_withdraw_permission BOOLEAN NOT NULL,
  attested_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  actor_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  request_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  UNIQUE (request_id),
  UNIQUE (idempotency_key),
  CHECK (has_order_permission IS TRUE),
  CHECK (has_order_read_permission IS TRUE),
  CHECK (has_withdraw_permission IS FALSE),
  CHECK (expires_at > attested_at),
  CHECK (actor_id <> ''),
  CHECK (actor_id !~* '^(ci|ai|service):'),
  CHECK (reason <> ''),
  CHECK (request_id <> ''),
  CHECK (idempotency_key <> ''),
  CHECK (jsonb_typeof(evidence) = 'object')
);

CREATE TABLE upbit_order_outbox (
  id BIGSERIAL PRIMARY KEY,
  exchange_account_id BIGINT NOT NULL REFERENCES exchange_accounts(id) ON DELETE RESTRICT,
  order_intent_id BIGINT NOT NULL REFERENCES order_intents(id) ON DELETE RESTRICT,
  live_order_identifier_id BIGINT NOT NULL REFERENCES live_order_identifiers(id) ON DELETE RESTRICT,
  permission_attestation_id BIGINT REFERENCES upbit_api_key_permission_attestations(id) ON DELETE RESTRICT,
  status TEXT NOT NULL,
  blocked_reason TEXT,
  request_payload JSONB NOT NULL,
  request_hash TEXT NOT NULL,
  submit_attempt_count INTEGER NOT NULL DEFAULT 0,
  actor_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  request_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  UNIQUE (order_intent_id),
  UNIQUE (request_id),
  UNIQUE (idempotency_key),
  CHECK (status IN ('ready','blocked')),
  CHECK (blocked_reason IS NULL OR blocked_reason IN (
    'live_disabled',
    'permission_missing',
    'permission_not_ready',
    'permission_expired',
    'withdraw_permission_present',
    'kill_switch_armed'
  )),
  CHECK (
    (status = 'ready' AND blocked_reason IS NULL AND permission_attestation_id IS NOT NULL)
    OR
    (status = 'blocked' AND blocked_reason IS NOT NULL)
  ),
  CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  CHECK (submit_attempt_count = 0),
  CHECK (actor_id <> ''),
  CHECK (actor_id !~* '^(ci|ai|service):'),
  CHECK (reason <> ''),
  CHECK (request_id <> ''),
  CHECK (idempotency_key <> ''),
  CHECK (jsonb_typeof(request_payload) = 'object')
);

CREATE FUNCTION reject_p6_upbit_permission_attestation_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'upbit api key permission attestation is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION reject_p6_upbit_order_outbox_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'upbit order outbox evidence is append-only in P6-6';
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION validate_p6_upbit_order_outbox_consistency()
RETURNS TRIGGER AS $$
DECLARE
  live_identity RECORD;
  permission RECORD;
BEGIN
  SELECT live.exchange_account_id, live.order_intent_id, intent.status AS order_intent_status
    INTO live_identity
  FROM live_order_identifiers live
  JOIN order_intents intent ON intent.id = live.order_intent_id
  WHERE live.id = NEW.live_order_identifier_id;

  IF live_identity IS NULL THEN
    RAISE EXCEPTION 'live order identifier does not exist';
  END IF;

  IF live_identity.exchange_account_id <> NEW.exchange_account_id THEN
    RAISE EXCEPTION 'outbox exchange account does not match live identifier';
  END IF;

  IF live_identity.order_intent_id <> NEW.order_intent_id THEN
    RAISE EXCEPTION 'outbox order intent does not match live identifier';
  END IF;

  IF NEW.permission_attestation_id IS NOT NULL THEN
    SELECT exchange_account_id, has_order_permission, has_order_read_permission,
           has_withdraw_permission, expires_at
      INTO permission
    FROM upbit_api_key_permission_attestations
    WHERE id = NEW.permission_attestation_id;

    IF permission IS NULL THEN
      RAISE EXCEPTION 'ready outbox requires permission attestation';
    END IF;

    IF permission.exchange_account_id <> NEW.exchange_account_id THEN
      RAISE EXCEPTION 'outbox exchange account does not match permission attestation';
    END IF;
  END IF;

  IF NEW.status = 'ready' THEN
    IF live_identity.order_intent_status <> 'approved' THEN
      RAISE EXCEPTION 'ready outbox requires approved order intent';
    END IF;

    IF permission.has_order_permission IS NOT TRUE
       OR permission.has_order_read_permission IS NOT TRUE
       OR permission.has_withdraw_permission IS NOT FALSE THEN
      RAISE EXCEPTION 'permission attestation is not order-ready';
    END IF;

    IF permission.expires_at <= clock_timestamp() THEN
      RAISE EXCEPTION 'permission attestation is expired';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER upbit_order_outbox_validate_consistency
  BEFORE INSERT ON upbit_order_outbox
  FOR EACH ROW EXECUTE FUNCTION validate_p6_upbit_order_outbox_consistency();

CREATE TRIGGER upbit_api_key_permission_attestations_append_only_update
  BEFORE UPDATE ON upbit_api_key_permission_attestations
  FOR EACH ROW EXECUTE FUNCTION reject_p6_upbit_permission_attestation_mutation();

CREATE TRIGGER upbit_api_key_permission_attestations_append_only_delete
  BEFORE DELETE ON upbit_api_key_permission_attestations
  FOR EACH ROW EXECUTE FUNCTION reject_p6_upbit_permission_attestation_mutation();

CREATE TRIGGER upbit_order_outbox_append_only_update
  BEFORE UPDATE ON upbit_order_outbox
  FOR EACH ROW EXECUTE FUNCTION reject_p6_upbit_order_outbox_mutation();

CREATE TRIGGER upbit_order_outbox_append_only_delete
  BEFORE DELETE ON upbit_order_outbox
  FOR EACH ROW EXECUTE FUNCTION reject_p6_upbit_order_outbox_mutation();

CREATE INDEX upbit_api_key_permission_attestations_latest_idx
  ON upbit_api_key_permission_attestations (exchange_account_id, expires_at DESC, id DESC);

CREATE INDEX upbit_order_outbox_status_idx
  ON upbit_order_outbox (status, created_at, id);

-- migrate:down

SET TIME ZONE 'UTC';

DROP INDEX IF EXISTS upbit_order_outbox_status_idx;
DROP INDEX IF EXISTS upbit_api_key_permission_attestations_latest_idx;
DROP TRIGGER IF EXISTS upbit_order_outbox_validate_consistency ON upbit_order_outbox;
DROP TRIGGER IF EXISTS upbit_order_outbox_append_only_delete ON upbit_order_outbox;
DROP TRIGGER IF EXISTS upbit_order_outbox_append_only_update ON upbit_order_outbox;
DROP TRIGGER IF EXISTS upbit_api_key_permission_attestations_append_only_delete ON upbit_api_key_permission_attestations;
DROP TRIGGER IF EXISTS upbit_api_key_permission_attestations_append_only_update ON upbit_api_key_permission_attestations;
DROP FUNCTION IF EXISTS reject_p6_upbit_order_outbox_mutation();
DROP FUNCTION IF EXISTS validate_p6_upbit_order_outbox_consistency();
DROP FUNCTION IF EXISTS reject_p6_upbit_permission_attestation_mutation();
DROP TABLE IF EXISTS upbit_order_outbox;
DROP TABLE IF EXISTS upbit_api_key_permission_attestations;
