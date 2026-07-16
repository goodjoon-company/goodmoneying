-- migrate:up

ALTER TABLE data_quality_events
  ADD COLUMN IF NOT EXISTS fetch_manifest_id BIGINT REFERENCES fetch_manifests(id);
ALTER TABLE backfill_job_targets
  ADD COLUMN IF NOT EXISTS last_fetch_manifest_id BIGINT REFERENCES fetch_manifests(id);

ALTER TABLE coverage_intervals DROP CONSTRAINT IF EXISTS coverage_intervals_status_ck;
ALTER TABLE data_quality_events DROP CONSTRAINT IF EXISTS data_quality_events_previous_status_ck;
ALTER TABLE data_quality_events DROP CONSTRAINT IF EXISTS data_quality_events_new_status_ck;

UPDATE coverage_intervals SET status = 'available' WHERE status = 'observed';
UPDATE coverage_intervals SET status = 'missing' WHERE status = 'failed';
UPDATE data_quality_events SET previous_status = 'available' WHERE previous_status = 'observed';
UPDATE data_quality_events SET previous_status = 'missing' WHERE previous_status = 'failed';
UPDATE data_quality_events SET new_status = 'available' WHERE new_status = 'observed';
UPDATE data_quality_events SET new_status = 'missing' WHERE new_status = 'failed';

ALTER TABLE coverage_intervals
  ADD CONSTRAINT coverage_intervals_status_ck CHECK (
    status IN ('available', 'no_trade', 'missing', 'unavailable', 'unverified')
  );
ALTER TABLE data_quality_events
  ADD CONSTRAINT data_quality_events_previous_status_ck CHECK (
    previous_status IS NULL OR previous_status IN (
      'available', 'no_trade', 'missing', 'unavailable', 'unverified'
    )
  );
ALTER TABLE data_quality_events
  ADD CONSTRAINT data_quality_events_new_status_ck CHECK (
    new_status IN ('available', 'no_trade', 'missing', 'unavailable', 'unverified')
  );

ALTER TABLE data_quality_events
  DROP CONSTRAINT IF EXISTS data_quality_events_fingerprint_uk;
ALTER TABLE data_quality_events
  ADD CONSTRAINT data_quality_events_fingerprint_uk
  UNIQUE (target_spec_id, event_type, detected_at, fingerprint);

-- migrate:down
-- 커버리지 분류·manifest 연결·감사 행을 잃지 않도록 축소 마이그레이션은 no-op이다.
SELECT 1;
