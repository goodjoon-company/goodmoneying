-- migrate:up

SET TIME ZONE 'UTC';

ALTER TABLE backtest_runs
  DROP CONSTRAINT IF EXISTS backtest_runs_check,
  DROP CONSTRAINT IF EXISTS backtest_runs_check1,
  DROP CONSTRAINT IF EXISTS backtest_runs_check2,
  DROP CONSTRAINT IF EXISTS backtest_runs_check3,
  DROP CONSTRAINT IF EXISTS backtest_runs_status_check;

ALTER TABLE backtest_runs
  ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3,
  ADD COLUMN next_retry_at TIMESTAMPTZ,
  ADD COLUMN lease_owner TEXT,
  ADD COLUMN lease_expires_at TIMESTAMPTZ,
  ADD COLUMN lease_generation INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN last_error_code TEXT,
  ADD COLUMN last_error_message TEXT,
  ADD COLUMN dead_letter_reason TEXT,
  ADD CONSTRAINT backtest_runs_status_check
    CHECK (status IN ('queued','running','retry_wait','succeeded','failed','cancelled','dead_letter')),
  ADD CONSTRAINT backtest_runs_attempt_count_check CHECK (attempt_count >= 0),
  ADD CONSTRAINT backtest_runs_max_attempts_check CHECK (max_attempts > 0),
  ADD CONSTRAINT backtest_runs_lease_generation_check CHECK (lease_generation >= 0),
  ADD CONSTRAINT backtest_runs_running_lease_check
    CHECK (status <> 'running' OR (
      started_at IS NOT NULL
      AND finished_at IS NULL
      AND btrim(COALESCE(lease_owner, '')) <> ''
      AND lease_expires_at IS NOT NULL
    )),
  ADD CONSTRAINT backtest_runs_non_running_lease_check
    CHECK (status = 'running' OR (lease_owner IS NULL AND lease_expires_at IS NULL)),
  ADD CONSTRAINT backtest_runs_queued_check
    CHECK (status <> 'queued' OR (
      started_at IS NULL
      AND finished_at IS NULL
      AND next_retry_at IS NULL
    )),
  ADD CONSTRAINT backtest_runs_retry_wait_check
    CHECK (status <> 'retry_wait' OR (
      next_retry_at IS NOT NULL
      AND finished_at IS NULL
    )),
  ADD CONSTRAINT backtest_runs_terminal_finished_check
    CHECK (status NOT IN ('succeeded','failed','cancelled','dead_letter') OR finished_at IS NOT NULL),
  ADD CONSTRAINT backtest_runs_succeeded_result_check
    CHECK (status <> 'succeeded' OR result_hash IS NOT NULL),
  ADD CONSTRAINT backtest_runs_dead_letter_reason_check
    CHECK (status <> 'dead_letter' OR btrim(COALESCE(dead_letter_reason, '')) <> '');

CREATE INDEX backtest_runs_worker_lease_idx
  ON backtest_runs (status, next_retry_at, lease_expires_at, requested_at, id);

-- migrate:down

SET TIME ZONE 'UTC';

DROP INDEX IF EXISTS backtest_runs_worker_lease_idx;

ALTER TABLE backtest_runs
  DROP CONSTRAINT IF EXISTS backtest_runs_dead_letter_reason_check,
  DROP CONSTRAINT IF EXISTS backtest_runs_succeeded_result_check,
  DROP CONSTRAINT IF EXISTS backtest_runs_terminal_finished_check,
  DROP CONSTRAINT IF EXISTS backtest_runs_retry_wait_check,
  DROP CONSTRAINT IF EXISTS backtest_runs_queued_check,
  DROP CONSTRAINT IF EXISTS backtest_runs_non_running_lease_check,
  DROP CONSTRAINT IF EXISTS backtest_runs_running_lease_check,
  DROP CONSTRAINT IF EXISTS backtest_runs_lease_generation_check,
  DROP CONSTRAINT IF EXISTS backtest_runs_max_attempts_check,
  DROP CONSTRAINT IF EXISTS backtest_runs_attempt_count_check,
  DROP CONSTRAINT IF EXISTS backtest_runs_status_check;

UPDATE backtest_runs
SET status='failed', finished_at=COALESCE(finished_at, clock_timestamp())
WHERE status='dead_letter';

UPDATE backtest_runs
SET status='queued', started_at=NULL, finished_at=NULL
WHERE status='retry_wait';

ALTER TABLE backtest_runs
  DROP COLUMN IF EXISTS dead_letter_reason,
  DROP COLUMN IF EXISTS last_error_message,
  DROP COLUMN IF EXISTS last_error_code,
  DROP COLUMN IF EXISTS lease_generation,
  DROP COLUMN IF EXISTS lease_expires_at,
  DROP COLUMN IF EXISTS lease_owner,
  DROP COLUMN IF EXISTS next_retry_at,
  DROP COLUMN IF EXISTS max_attempts,
  DROP COLUMN IF EXISTS attempt_count,
  ADD CONSTRAINT backtest_runs_status_check
    CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  ADD CONSTRAINT backtest_runs_check
    CHECK (status <> 'running' OR started_at IS NOT NULL),
  ADD CONSTRAINT backtest_runs_check1
    CHECK (status <> 'queued' OR (started_at IS NULL AND finished_at IS NULL)),
  ADD CONSTRAINT backtest_runs_check2
    CHECK (status NOT IN ('succeeded','failed','cancelled') OR finished_at IS NOT NULL),
  ADD CONSTRAINT backtest_runs_check3
    CHECK (status <> 'succeeded' OR result_hash IS NOT NULL);
