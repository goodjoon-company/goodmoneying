-- migrate:up

CREATE TABLE IF NOT EXISTS p1_audit_recovery_gate (
  singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
  recovery_required BOOLEAN NOT NULL DEFAULT false,
  detected_at TIMESTAMPTZ,
  confirmed_at TIMESTAMPTZ,
  confirmed_by TEXT,
  backup_reference TEXT,
  reason TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT p1_audit_recovery_gate_confirmation_ck CHECK (
    confirmed_at IS NULL OR (
      recovery_required
      AND btrim(confirmed_by) <> ''
      AND btrim(backup_reference) <> ''
    )
  )
);

DO $$
DECLARE
  legacy_destructive_constraint BOOLEAN;
BEGIN
  SELECT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conrelid = 'data_quality_events'::regclass
      AND contype = 'u'
      AND pg_get_constraintdef(oid) = 'UNIQUE (target_spec_id, fingerprint)'
  ) INTO legacy_destructive_constraint;

  INSERT INTO p1_audit_recovery_gate (
    singleton, recovery_required, detected_at, reason
  ) VALUES (
    true,
    legacy_destructive_constraint,
    CASE WHEN legacy_destructive_constraint THEN now() ELSE NULL END,
    CASE WHEN legacy_destructive_constraint THEN
      '20260717000400 구버전이 중복 감사 행을 삭제했을 가능성이 있어 백업 비교와 복구 확인이 필요하다.'
    ELSE NULL END
  )
  ON CONFLICT (singleton) DO UPDATE SET
    recovery_required = p1_audit_recovery_gate.recovery_required
      OR excluded.recovery_required,
    detected_at = COALESCE(p1_audit_recovery_gate.detected_at, excluded.detected_at),
    reason = COALESCE(p1_audit_recovery_gate.reason, excluded.reason),
    updated_at = now();
END $$;

ALTER TABLE data_quality_events
  DROP CONSTRAINT IF EXISTS data_quality_events_fingerprint_uk;
ALTER TABLE data_quality_events
  ADD CONSTRAINT data_quality_events_fingerprint_uk
  UNIQUE (target_spec_id, event_type, detected_at, fingerprint);

ALTER TABLE collection_worker_heartbeats
  DROP CONSTRAINT IF EXISTS collection_worker_heartbeats_worker_type_ck;
ALTER TABLE collection_worker_heartbeats
  ADD CONSTRAINT collection_worker_heartbeats_worker_type_ck
  CHECK (worker_type IN (
    'realtime_collection', 'backfill_collection', 'candle_aggregation', 'market_sync'
  ));

-- migrate:down
-- 감사 행 복구 확인과 자연키 교정은 되돌리지 않는다.
SELECT 1;
