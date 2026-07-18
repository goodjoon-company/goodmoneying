-- migrate:up

SET TIME ZONE 'UTC';

CREATE TABLE paper_execution_jobs (
  id BIGSERIAL PRIMARY KEY,
  order_intent_id BIGINT NOT NULL REFERENCES order_intents(id) ON DELETE RESTRICT,
  status TEXT NOT NULL DEFAULT 'pending',
  priority INTEGER NOT NULL DEFAULT 100,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  next_retry_at TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01T00:00:00Z',
  lease_owner TEXT,
  lease_expires_at TIMESTAMPTZ,
  lease_generation INTEGER NOT NULL DEFAULT 0,
  last_error_code TEXT,
  dead_letter_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (order_intent_id),
  CHECK (status IN ('pending','running','retry_wait','succeeded','dead_letter')),
  CHECK (priority >= 0),
  CHECK (attempt_count >= 0),
  CHECK (max_attempts >= 1),
  CHECK (attempt_count <= max_attempts),
  CHECK (lease_generation >= 0),
  CHECK (
    (status = 'running' AND lease_owner IS NOT NULL AND btrim(lease_owner) <> '' AND lease_expires_at IS NOT NULL)
    OR
    (status <> 'running' AND lease_owner IS NULL AND lease_expires_at IS NULL)
  ),
  CHECK (last_error_code IS NULL OR btrim(last_error_code) <> ''),
  CHECK (dead_letter_reason IS NULL OR btrim(dead_letter_reason) <> '')
);

CREATE INDEX paper_execution_jobs_claim_idx
  ON paper_execution_jobs (status, next_retry_at, lease_expires_at, priority DESC, created_at, id);

-- migrate:down

SET TIME ZONE 'UTC';

DROP INDEX IF EXISTS paper_execution_jobs_claim_idx;
DROP TABLE IF EXISTS paper_execution_jobs;
