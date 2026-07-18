-- migrate:up

SET TIME ZONE 'UTC';

CREATE TABLE trading_capabilities (
  id BIGSERIAL PRIMARY KEY,
  scope_type TEXT NOT NULL DEFAULT 'global',
  scope_key TEXT NOT NULL DEFAULT 'global',
  state TEXT NOT NULL DEFAULT 'live_disabled',
  deployment_sha TEXT NOT NULL,
  approved_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  actor_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  request_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  UNIQUE (request_id),
  UNIQUE (idempotency_key),
  CHECK (scope_type = 'global'),
  CHECK (scope_key = 'global'),
  CHECK (state IN ('live_disabled','live_enabled')),
  CHECK (deployment_sha ~ '^[0-9a-f]{40}$'),
  CHECK (expires_at > approved_at),
  CHECK (actor_id <> ''),
  CHECK (actor_id !~ '^(ci|ai|service):'),
  CHECK (reason <> ''),
  CHECK (request_id <> ''),
  CHECK (idempotency_key <> ''),
  CHECK (jsonb_typeof(evidence) = 'object')
);

CREATE FUNCTION reject_p6_trading_capability_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'trading capability evidence is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trading_capabilities_append_only_update
  BEFORE UPDATE ON trading_capabilities
  FOR EACH ROW EXECUTE FUNCTION reject_p6_trading_capability_mutation();

CREATE TRIGGER trading_capabilities_append_only_delete
  BEFORE DELETE ON trading_capabilities
  FOR EACH ROW EXECUTE FUNCTION reject_p6_trading_capability_mutation();

CREATE INDEX trading_capabilities_global_latest_idx
  ON trading_capabilities (scope_type, scope_key, created_at DESC, id DESC);

-- migrate:down

SET TIME ZONE 'UTC';

DROP INDEX IF EXISTS trading_capabilities_global_latest_idx;
DROP TRIGGER IF EXISTS trading_capabilities_append_only_delete ON trading_capabilities;
DROP TRIGGER IF EXISTS trading_capabilities_append_only_update ON trading_capabilities;
DROP FUNCTION IF EXISTS reject_p6_trading_capability_mutation();
DROP TABLE IF EXISTS trading_capabilities;
