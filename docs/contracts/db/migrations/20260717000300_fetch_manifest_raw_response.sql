-- migrate:up

ALTER TABLE fetch_manifests
  ADD COLUMN IF NOT EXISTS response_payload JSONB,
  ADD COLUMN IF NOT EXISTS error_message TEXT;

-- migrate:down
-- 원문 응답과 오류 증적은 감사 이력이므로 되돌림에서도 보존한다.
SELECT 1;
