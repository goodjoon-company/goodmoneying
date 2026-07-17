-- migrate:up

UPDATE p1_audit_recovery_gate
SET confirmed_at = NULL,
    confirmed_by = NULL,
    backup_reference = NULL,
    updated_at = now()
WHERE (
    confirmed_at IS NULL
    OR confirmed_by IS NULL
    OR btrim(confirmed_by) = ''
    OR backup_reference IS NULL
    OR btrim(backup_reference) = ''
  )
  AND (
    confirmed_at IS NOT NULL
    OR confirmed_by IS NOT NULL
    OR backup_reference IS NOT NULL
  );

-- 신규 DB는 파괴적인 004가 빈 품질 이벤트 테이블에만 적용됐으므로 복구 대상이 없다.
UPDATE p1_audit_recovery_gate
SET recovery_required = false,
    detected_at = NULL,
    reason = NULL,
    updated_at = now()
WHERE recovery_required
  AND confirmed_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM data_quality_events);

ALTER TABLE p1_audit_recovery_gate
  DROP CONSTRAINT IF EXISTS p1_audit_recovery_gate_confirmation_ck;
ALTER TABLE p1_audit_recovery_gate
  ADD CONSTRAINT p1_audit_recovery_gate_confirmation_ck CHECK (
    (
      confirmed_at IS NULL
      AND confirmed_by IS NULL
      AND backup_reference IS NULL
    )
    OR (
      recovery_required
      AND confirmed_at IS NOT NULL
      AND confirmed_by IS NOT NULL
      AND btrim(confirmed_by) <> ''
      AND backup_reference IS NOT NULL
      AND btrim(backup_reference) <> ''
    )
  );

-- migrate:down
-- 기존 DB의 불완전 확인값 정리와 복구 확인 무결성은 되돌리지 않는다.
SELECT 1;
