-- goodmoneying M1 DB contract
-- Source of truth for PostgreSQL schema used by the Upbit Collection Pipeline.

SET TIME ZONE 'Asia/Seoul';
ALTER DATABASE goodmoneying SET timezone TO 'Asia/Seoul';

CREATE TABLE IF NOT EXISTS instruments (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  exchange TEXT NOT NULL,
  market_code TEXT NOT NULL,
  quote_currency TEXT NOT NULL,
  base_asset TEXT NOT NULL,
  display_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT instruments_exchange_market_code_uk UNIQUE (exchange, market_code),
  CONSTRAINT instruments_status_ck CHECK (status IN ('active', 'inactive'))
);

CREATE TABLE IF NOT EXISTS candidate_universe_snapshots (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source TEXT NOT NULL,
  exchange TEXT NOT NULL,
  quote_currency TEXT NOT NULL,
  ranked_at TIMESTAMPTZ NOT NULL,
  generated_by TEXT NOT NULL DEFAULT 'system',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT candidate_universe_snapshots_source_ck CHECK (source IN ('UPBIT'))
);

CREATE TABLE IF NOT EXISTS candidate_universe_entries (
  snapshot_id BIGINT NOT NULL REFERENCES candidate_universe_snapshots(id) ON DELETE CASCADE,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  rank INTEGER NOT NULL,
  acc_trade_price_24h NUMERIC NOT NULL,
  is_default_selected BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (snapshot_id, instrument_id),
  CONSTRAINT candidate_universe_entries_rank_uk UNIQUE (snapshot_id, rank),
  CONSTRAINT candidate_universe_entries_rank_ck CHECK (rank BETWEEN 1 AND 100)
);

CREATE TABLE IF NOT EXISTS collection_targets (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  status TEXT NOT NULL,
  activated_at TIMESTAMPTZ,
  deactivated_at TIMESTAMPTZ,
  candidate_status TEXT NOT NULL DEFAULT 'in_universe',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT collection_targets_instrument_uk UNIQUE (instrument_id),
  CONSTRAINT collection_targets_status_ck CHECK (status IN ('active', 'inactive')),
  CONSTRAINT collection_targets_candidate_status_ck CHECK (candidate_status IN ('in_universe', 'out_of_universe'))
);

CREATE TABLE IF NOT EXISTS collection_target_changes (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  previous_status TEXT,
  new_status TEXT NOT NULL,
  actor TEXT NOT NULL,
  reason TEXT,
  changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT collection_target_changes_previous_status_ck CHECK (previous_status IS NULL OR previous_status IN ('active', 'inactive')),
  CONSTRAINT collection_target_changes_new_status_ck CHECK (new_status IN ('active', 'inactive')),
  CONSTRAINT collection_target_changes_actor_ck CHECK (actor IN ('system', 'local_user'))
);

CREATE TABLE IF NOT EXISTS collection_plans (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  preset TEXT NOT NULL,
  range_start_at TIMESTAMPTZ NOT NULL,
  range_end_at TIMESTAMPTZ,
  is_continuous BOOLEAN NOT NULL DEFAULT true,
  method TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT collection_plans_instrument_uk UNIQUE (instrument_id),
  CONSTRAINT collection_plans_method_ck CHECK (method IN ('safe_restart', 'incremental')),
  CONSTRAINT collection_plans_status_ck CHECK (status IN ('latest_collecting', 'collecting', 'paused', 'stopped')),
  CONSTRAINT collection_plans_range_ck CHECK (range_end_at IS NULL OR range_start_at < range_end_at)
);

CREATE TABLE IF NOT EXISTS collection_coverage_snapshots (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  data_type TEXT NOT NULL,
  range_start_at TIMESTAMPTZ NOT NULL,
  range_end_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  progress_percent NUMERIC NOT NULL,
  last_successful_at TIMESTAMPTZ NOT NULL,
  missing_segment_count INTEGER NOT NULL DEFAULT 0,
  calculated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT collection_coverage_snapshots_data_type_ck CHECK (data_type IN ('source_candle', 'ticker_snapshot', 'orderbook_summary')),
  CONSTRAINT collection_coverage_snapshots_status_ck CHECK (status IN ('normal', 'warning', 'incident', 'backfilling')),
  CONSTRAINT collection_coverage_snapshots_progress_ck CHECK (progress_percent >= 0 AND progress_percent <= 100),
  CONSTRAINT collection_coverage_snapshots_missing_count_ck CHECK (missing_segment_count >= 0)
);

CREATE TABLE IF NOT EXISTS collection_coverage_segments (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  snapshot_id BIGINT NOT NULL REFERENCES collection_coverage_snapshots(id) ON DELETE CASCADE,
  data_type TEXT NOT NULL,
  status TEXT NOT NULL,
  offset_percent NUMERIC NOT NULL,
  width_percent NUMERIC NOT NULL,
  segment_start_at TIMESTAMPTZ NOT NULL,
  segment_end_at TIMESTAMPTZ NOT NULL,
  label TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT collection_coverage_segments_data_type_ck CHECK (data_type IN ('source_candle', 'ticker_snapshot', 'orderbook_summary')),
  CONSTRAINT collection_coverage_segments_status_ck CHECK (status IN ('collected', 'missing', 'collecting', 'future')),
  CONSTRAINT collection_coverage_segments_percent_ck CHECK (offset_percent >= 0 AND width_percent >= 0 AND offset_percent + width_percent <= 100),
  CONSTRAINT collection_coverage_segments_range_ck CHECK (segment_start_at < segment_end_at)
);

CREATE TABLE IF NOT EXISTS collection_settings (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL,
  updated_by TEXT NOT NULL DEFAULT 'system',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT collection_settings_updated_by_ck CHECK (updated_by IN ('system', 'local_user'))
);

CREATE TABLE IF NOT EXISTS collection_runs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_type TEXT NOT NULL,
  data_type TEXT NOT NULL,
  status TEXT NOT NULL,
  trigger_type TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  error_code TEXT,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT collection_runs_run_type_ck CHECK (run_type IN ('candidate_refresh', 'incremental', 'backfill', 'completeness_check')),
  CONSTRAINT collection_runs_data_type_ck CHECK (data_type IN ('candidate_universe', 'source_candle', 'ticker_snapshot', 'orderbook_summary', 'trade_event', 'missing_range')),
  CONSTRAINT collection_runs_status_ck CHECK (status IN ('running', 'succeeded', 'partial', 'failed', 'cancelled')),
  CONSTRAINT collection_runs_trigger_type_ck CHECK (trigger_type IN ('schedule', 'manual', 'backfill_job', 'system'))
);

CREATE TABLE IF NOT EXISTS collection_worker_heartbeats (
  worker_type TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  last_heartbeat_at TIMESTAMPTZ NOT NULL,
  last_started_at TIMESTAMPTZ,
  last_successful_at TIMESTAMPTZ,
  last_error_at TIMESTAMPTZ,
  last_error_message TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT collection_worker_heartbeats_worker_type_ck CHECK (worker_type IN ('realtime_collection', 'backfill_collection')),
  CONSTRAINT collection_worker_heartbeats_status_ck CHECK (status IN ('running', 'failed'))
);

CREATE TABLE IF NOT EXISTS target_collection_results (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  collection_run_id BIGINT NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
  instrument_id BIGINT REFERENCES instruments(id),
  data_type TEXT NOT NULL,
  status TEXT NOT NULL,
  target_start_at TIMESTAMPTZ,
  target_end_at TIMESTAMPTZ,
  latency_ms INTEGER,
  retry_count INTEGER NOT NULL DEFAULT 0,
  rows_written INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT target_collection_results_data_type_ck CHECK (data_type IN ('source_candle', 'ticker_snapshot', 'orderbook_summary', 'trade_event', 'candidate_universe', 'missing_range')),
  CONSTRAINT target_collection_results_status_ck CHECK (status IN ('succeeded', 'failed', 'delayed', 'no_data', 'skipped')),
  CONSTRAINT target_collection_results_latency_ck CHECK (latency_ms IS NULL OR latency_ms >= 0),
  CONSTRAINT target_collection_results_retry_count_ck CHECK (retry_count >= 0),
  CONSTRAINT target_collection_results_rows_written_ck CHECK (rows_written >= 0)
);

CREATE TABLE IF NOT EXISTS source_candles (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  source TEXT NOT NULL,
  candle_unit TEXT NOT NULL,
  candle_start_at TIMESTAMPTZ NOT NULL,
  open_price NUMERIC NOT NULL,
  high_price NUMERIC NOT NULL,
  low_price NUMERIC NOT NULL,
  close_price NUMERIC NOT NULL,
  trade_volume NUMERIC NOT NULL,
  trade_amount NUMERIC NOT NULL,
  source_timestamp_at TIMESTAMPTZ,
  collected_at TIMESTAMPTZ NOT NULL,
  collection_run_id BIGINT REFERENCES collection_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT source_candles_uk UNIQUE (instrument_id, source, candle_unit, candle_start_at),
  CONSTRAINT source_candles_source_ck CHECK (source IN ('UPBIT')),
  CONSTRAINT source_candles_candle_unit_ck CHECK (candle_unit IN ('1m', '1d'))
);

CREATE TABLE IF NOT EXISTS ticker_snapshots (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  source TEXT NOT NULL,
  bucket_at TIMESTAMPTZ NOT NULL,
  trade_price NUMERIC NOT NULL,
  opening_price NUMERIC,
  high_price NUMERIC,
  low_price NUMERIC,
  prev_closing_price NUMERIC,
  change_rate NUMERIC,
  signed_change_rate NUMERIC,
  acc_trade_price_24h NUMERIC,
  acc_trade_volume_24h NUMERIC,
  source_timestamp_at TIMESTAMPTZ,
  collected_at TIMESTAMPTZ NOT NULL,
  collection_run_id BIGINT REFERENCES collection_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ticker_snapshots_uk UNIQUE (instrument_id, source, bucket_at),
  CONSTRAINT ticker_snapshots_source_ck CHECK (source IN ('UPBIT'))
);

CREATE TABLE IF NOT EXISTS orderbook_summaries (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  source TEXT NOT NULL,
  bucket_at TIMESTAMPTZ NOT NULL,
  best_bid_price NUMERIC NOT NULL,
  best_bid_size NUMERIC NOT NULL,
  best_ask_price NUMERIC NOT NULL,
  best_ask_size NUMERIC NOT NULL,
  spread NUMERIC NOT NULL,
  bid_depth_10 NUMERIC NOT NULL,
  ask_depth_10 NUMERIC NOT NULL,
  imbalance_10 NUMERIC NOT NULL,
  source_timestamp_at TIMESTAMPTZ,
  collected_at TIMESTAMPTZ NOT NULL,
  collection_run_id BIGINT REFERENCES collection_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT orderbook_summaries_uk UNIQUE (instrument_id, source, bucket_at),
  CONSTRAINT orderbook_summaries_source_ck CHECK (source IN ('UPBIT'))
);

CREATE TABLE IF NOT EXISTS trade_events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  source TEXT NOT NULL,
  sequential_id BIGINT NOT NULL,
  trade_timestamp_at TIMESTAMPTZ NOT NULL,
  trade_price NUMERIC NOT NULL,
  trade_volume NUMERIC NOT NULL,
  trade_amount NUMERIC NOT NULL,
  ask_bid TEXT NOT NULL,
  collected_at TIMESTAMPTZ NOT NULL,
  collection_run_id BIGINT REFERENCES collection_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT trade_events_uk UNIQUE (instrument_id, source, sequential_id),
  CONSTRAINT trade_events_source_ck CHECK (source IN ('UPBIT')),
  CONSTRAINT trade_events_ask_bid_ck CHECK (ask_bid IN ('ASK', 'BID'))
);

CREATE TABLE IF NOT EXISTS missing_ranges (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  data_type TEXT NOT NULL,
  unit TEXT,
  range_start_at TIMESTAMPTZ NOT NULL,
  range_end_at TIMESTAMPTZ NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL,
  detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT missing_ranges_uk UNIQUE (instrument_id, data_type, unit, range_start_at, range_end_at),
  CONSTRAINT missing_ranges_data_type_ck CHECK (data_type IN ('source_candle', 'ticker_snapshot', 'orderbook_summary')),
  CONSTRAINT missing_ranges_status_ck CHECK (status IN ('open', 'resolved', 'ignored')),
  CONSTRAINT missing_ranges_range_ck CHECK (range_start_at < range_end_at)
);

CREATE TABLE IF NOT EXISTS backfill_jobs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  status TEXT NOT NULL,
  data_type TEXT NOT NULL,
  plan JSONB NOT NULL,
  target_start_at TIMESTAMPTZ NOT NULL,
  target_end_at TIMESTAMPTZ NOT NULL,
  estimated_request_count INTEGER NOT NULL,
  estimated_row_count BIGINT NOT NULL,
  estimated_storage_bytes BIGINT,
  restart_mode TEXT,
  created_by TEXT NOT NULL DEFAULT 'local_user',
  approved_by TEXT,
  approved_at TIMESTAMPTZ,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT backfill_jobs_status_ck CHECK (status IN ('planned', 'pending', 'running', 'paused', 'stopped', 'succeeded', 'failed')),
  CONSTRAINT backfill_jobs_data_type_ck CHECK (data_type IN ('source_candle')),
  CONSTRAINT backfill_jobs_restart_mode_ck CHECK (restart_mode IS NULL OR restart_mode IN ('safe_restart')),
  CONSTRAINT backfill_jobs_target_range_ck CHECK (target_start_at < target_end_at),
  CONSTRAINT backfill_jobs_estimated_request_count_ck CHECK (estimated_request_count >= 0),
  CONSTRAINT backfill_jobs_estimated_row_count_ck CHECK (estimated_row_count >= 0)
);

CREATE TABLE IF NOT EXISTS backfill_job_targets (
  backfill_job_id BIGINT NOT NULL REFERENCES backfill_jobs(id) ON DELETE CASCADE,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  status TEXT NOT NULL,
  last_completed_at TIMESTAMPTZ,
  processed_missing_range_count INTEGER NOT NULL DEFAULT 0,
  estimated_missing_range_count INTEGER NOT NULL DEFAULT 0,
  rows_written_count INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  error_message TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (backfill_job_id, instrument_id),
  CONSTRAINT backfill_job_targets_status_ck CHECK (status IN ('pending', 'running', 'paused', 'stopped', 'succeeded', 'failed'))
);

ALTER TABLE backfill_job_targets
  ADD COLUMN IF NOT EXISTS processed_missing_range_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE backfill_job_targets
  ADD COLUMN IF NOT EXISTS estimated_missing_range_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE backfill_job_targets
  ADD COLUMN IF NOT EXISTS rows_written_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS audit_logs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT,
  request_id TEXT,
  before_data JSONB,
  after_data JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT audit_logs_actor_ck CHECK (actor IN ('system', 'local_user'))
);

CREATE TABLE IF NOT EXISTS notification_events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  severity TEXT NOT NULL,
  event_type TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  acknowledged_at TIMESTAMPTZ,
  resolved_at TIMESTAMPTZ,
  CONSTRAINT notification_events_severity_ck CHECK (severity IN ('info', 'warning', 'error', 'critical')),
  CONSTRAINT notification_events_status_ck CHECK (status IN ('open', 'acknowledged', 'resolved'))
);

CREATE TABLE IF NOT EXISTS raw_response_samples (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  reason TEXT NOT NULL,
  sampled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  request_summary JSONB,
  response_status INTEGER,
  response_body JSONB,
  error_message TEXT,
  CONSTRAINT raw_response_samples_source_ck CHECK (source IN ('UPBIT')),
  CONSTRAINT raw_response_samples_reason_ck CHECK (reason IN ('parse_error', 'schema_mismatch', 'unexpected_response', 'fixture_sample'))
);

CREATE INDEX IF NOT EXISTS source_candles_instrument_time_idx ON source_candles (instrument_id, candle_unit, candle_start_at DESC);
CREATE INDEX IF NOT EXISTS ticker_snapshots_instrument_bucket_idx ON ticker_snapshots (instrument_id, bucket_at DESC);
CREATE INDEX IF NOT EXISTS orderbook_summaries_instrument_bucket_idx ON orderbook_summaries (instrument_id, bucket_at DESC);
CREATE INDEX IF NOT EXISTS trade_events_instrument_time_idx ON trade_events (instrument_id, trade_timestamp_at DESC);
CREATE INDEX IF NOT EXISTS collection_runs_started_at_idx ON collection_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS collection_worker_heartbeats_status_idx ON collection_worker_heartbeats (status, last_heartbeat_at DESC);
CREATE INDEX IF NOT EXISTS target_collection_results_run_idx ON target_collection_results (collection_run_id, instrument_id);
CREATE INDEX IF NOT EXISTS collection_plans_status_idx ON collection_plans (status, instrument_id);
CREATE INDEX IF NOT EXISTS collection_coverage_snapshots_latest_idx ON collection_coverage_snapshots (instrument_id, data_type, calculated_at DESC);
CREATE INDEX IF NOT EXISTS collection_coverage_segments_snapshot_idx ON collection_coverage_segments (snapshot_id, data_type);
CREATE INDEX IF NOT EXISTS missing_ranges_status_idx ON missing_ranges (status, instrument_id, data_type);
CREATE INDEX IF NOT EXISTS backfill_jobs_status_idx ON backfill_jobs (status, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_logs_created_at_idx ON audit_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS notification_events_status_idx ON notification_events (status, created_at DESC);
