-- migrate:up

SET TIME ZONE 'UTC';

ALTER TABLE portfolios
  ADD COLUMN request_id TEXT,
  ADD COLUMN idempotency_key TEXT,
  ADD COLUMN requested_at TIMESTAMPTZ,
  ADD COLUMN request_hash TEXT;

CREATE UNIQUE INDEX portfolios_idempotency_key_unique
  ON portfolios (idempotency_key)
  WHERE idempotency_key IS NOT NULL;

ALTER TABLE portfolios
  ADD CONSTRAINT portfolios_api_command_all_or_none CHECK (
    (request_id IS NULL AND idempotency_key IS NULL AND requested_at IS NULL AND request_hash IS NULL)
    OR
    (request_id IS NOT NULL AND idempotency_key IS NOT NULL AND requested_at IS NOT NULL AND request_hash IS NOT NULL)
  ),
  ADD CONSTRAINT portfolios_api_command_non_blank CHECK (
    (request_id IS NULL OR btrim(request_id) <> '')
    AND (idempotency_key IS NULL OR btrim(idempotency_key) <> '')
  ),
  ADD CONSTRAINT portfolios_request_hash_format CHECK (
    request_hash IS NULL OR request_hash ~ '^[0-9a-f]{64}$'
  );

-- migrate:down

SET TIME ZONE 'UTC';

ALTER TABLE portfolios
  DROP CONSTRAINT portfolios_request_hash_format,
  DROP CONSTRAINT portfolios_api_command_non_blank,
  DROP CONSTRAINT portfolios_api_command_all_or_none;

DROP INDEX portfolios_idempotency_key_unique;

ALTER TABLE portfolios
  DROP COLUMN request_hash,
  DROP COLUMN requested_at,
  DROP COLUMN idempotency_key,
  DROP COLUMN request_id;
