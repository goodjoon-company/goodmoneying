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

DELETE FROM data_quality_events newer
USING data_quality_events older
WHERE newer.target_spec_id = older.target_spec_id
  AND newer.fingerprint = older.fingerprint
  AND newer.id > older.id;

ALTER TABLE data_quality_events
  DROP CONSTRAINT IF EXISTS data_quality_events_fingerprint_uk;
ALTER TABLE data_quality_events
  ADD CONSTRAINT data_quality_events_fingerprint_uk UNIQUE (target_spec_id, fingerprint);

-- migrate:down

ALTER TABLE data_quality_events
  DROP CONSTRAINT IF EXISTS data_quality_events_fingerprint_uk;
ALTER TABLE data_quality_events
  ADD CONSTRAINT data_quality_events_fingerprint_uk UNIQUE (
    target_spec_id, event_type, detected_at, fingerprint
  );

ALTER TABLE coverage_intervals DROP CONSTRAINT IF EXISTS coverage_intervals_status_ck;
ALTER TABLE data_quality_events DROP CONSTRAINT IF EXISTS data_quality_events_previous_status_ck;
ALTER TABLE data_quality_events DROP CONSTRAINT IF EXISTS data_quality_events_new_status_ck;

UPDATE coverage_intervals SET status = 'observed' WHERE status = 'available';
UPDATE coverage_intervals SET status = 'failed' WHERE status = 'missing';
UPDATE data_quality_events SET previous_status = 'observed' WHERE previous_status = 'available';
UPDATE data_quality_events SET previous_status = 'failed' WHERE previous_status = 'missing';
UPDATE data_quality_events SET new_status = 'observed' WHERE new_status = 'available';
UPDATE data_quality_events SET new_status = 'failed' WHERE new_status = 'missing';

ALTER TABLE coverage_intervals
  ADD CONSTRAINT coverage_intervals_status_ck CHECK (
    status IN ('observed', 'no_trade', 'unavailable', 'unverified', 'failed')
  );
ALTER TABLE data_quality_events
  ADD CONSTRAINT data_quality_events_previous_status_ck CHECK (
    previous_status IS NULL OR previous_status IN (
      'observed', 'no_trade', 'unavailable', 'unverified', 'failed'
    )
  );
ALTER TABLE data_quality_events
  ADD CONSTRAINT data_quality_events_new_status_ck CHECK (
    new_status IN ('observed', 'no_trade', 'unavailable', 'unverified', 'failed')
  );

ALTER TABLE data_quality_events DROP COLUMN IF EXISTS fetch_manifest_id;
ALTER TABLE backfill_job_targets DROP COLUMN IF EXISTS last_fetch_manifest_id;
