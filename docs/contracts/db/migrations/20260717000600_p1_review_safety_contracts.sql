-- migrate:up

ALTER TABLE market_status_history
  ADD COLUMN IF NOT EXISTS fetch_manifest_id BIGINT REFERENCES fetch_manifests(id);

ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS audit_logs_actor_ck;
ALTER TABLE audit_logs
  ADD CONSTRAINT audit_logs_actor_ck CHECK (btrim(actor) <> '');

ALTER TABLE collection_worker_heartbeats
  DROP CONSTRAINT IF EXISTS collection_worker_heartbeats_status_ck;
ALTER TABLE collection_worker_heartbeats
  ADD CONSTRAINT collection_worker_heartbeats_status_ck
  CHECK (status IN ('running', 'gated', 'failed'));

CREATE TABLE IF NOT EXISTS command_idempotency_records (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  scope TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  requested_at TIMESTAMPTZ NOT NULL,
  payload_hash TEXT NOT NULL,
  result_payload JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  CONSTRAINT command_idempotency_records_scope_key_uk UNIQUE (scope, idempotency_key)
);

CREATE TABLE IF NOT EXISTS backfill_safety_gate (
  singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
  enabled BOOLEAN NOT NULL DEFAULT false,
  backup_verified_at TIMESTAMPTZ,
  free_capacity_bytes BIGINT NOT NULL DEFAULT 0 CHECK (free_capacity_bytes >= 0),
  required_capacity_bytes BIGINT NOT NULL DEFAULT 0 CHECK (required_capacity_bytes >= 0),
  approved_sha TEXT,
  approved_by TEXT,
  approved_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT backfill_safety_gate_approval_ck CHECK (
    NOT enabled OR (
      backup_verified_at IS NOT NULL
      AND free_capacity_bytes > 0
      AND required_capacity_bytes > 0
      AND approved_sha IS NOT NULL
      AND approved_by IS NOT NULL
      AND approved_at IS NOT NULL
    )
  )
);

INSERT INTO backfill_safety_gate (singleton) VALUES (true)
ON CONFLICT (singleton) DO NOTHING;

-- migrate:down
-- 원문 연결, 변경 명령 결과와 운영 승인 증적은 되돌림에서도 보존한다.
SELECT 1;
