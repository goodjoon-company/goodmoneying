-- migrate:up

ALTER TABLE fetch_manifests
  ADD COLUMN IF NOT EXISTS response_payload JSONB,
  ADD COLUMN IF NOT EXISTS error_message TEXT;

-- migrate:down

ALTER TABLE fetch_manifests
  DROP COLUMN IF EXISTS error_message,
  DROP COLUMN IF EXISTS response_payload;
