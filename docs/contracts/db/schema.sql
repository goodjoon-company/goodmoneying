\restrict dbmate

-- Dumped from database version (normalized)
-- Dumped by pg_dump version (normalized)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id bigint NOT NULL,
    actor text NOT NULL,
    action text NOT NULL,
    target_type text NOT NULL,
    target_id text,
    request_id text,
    before_data jsonb,
    after_data jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT audit_logs_actor_ck CHECK ((actor = ANY (ARRAY['system'::text, 'local_user'::text])))
);


--
-- Name: audit_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.audit_logs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.audit_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: backfill_job_targets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.backfill_job_targets (
    backfill_job_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    status text NOT NULL,
    last_completed_at timestamp with time zone,
    processed_missing_range_count integer DEFAULT 0 NOT NULL,
    estimated_missing_range_count integer DEFAULT 0 NOT NULL,
    rows_written_count integer DEFAULT 0 NOT NULL,
    error_code text,
    error_message text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT backfill_job_targets_status_ck CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'paused'::text, 'stopped'::text, 'succeeded'::text, 'failed'::text])))
);


--
-- Name: backfill_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.backfill_jobs (
    id bigint NOT NULL,
    status text NOT NULL,
    data_type text NOT NULL,
    plan jsonb NOT NULL,
    target_start_at timestamp with time zone NOT NULL,
    target_end_at timestamp with time zone NOT NULL,
    estimated_request_count integer NOT NULL,
    estimated_row_count bigint NOT NULL,
    estimated_storage_bytes bigint,
    restart_mode text,
    created_by text DEFAULT 'local_user'::text NOT NULL,
    approved_by text,
    approved_at timestamp with time zone,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT backfill_jobs_data_type_ck CHECK ((data_type = 'source_candle'::text)),
    CONSTRAINT backfill_jobs_estimated_request_count_ck CHECK ((estimated_request_count >= 0)),
    CONSTRAINT backfill_jobs_estimated_row_count_ck CHECK ((estimated_row_count >= 0)),
    CONSTRAINT backfill_jobs_restart_mode_ck CHECK (((restart_mode IS NULL) OR (restart_mode = 'safe_restart'::text))),
    CONSTRAINT backfill_jobs_status_ck CHECK ((status = ANY (ARRAY['planned'::text, 'pending'::text, 'running'::text, 'paused'::text, 'stopped'::text, 'succeeded'::text, 'failed'::text]))),
    CONSTRAINT backfill_jobs_target_range_ck CHECK ((target_start_at < target_end_at))
);


--
-- Name: backfill_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.backfill_jobs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.backfill_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: candidate_universe_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candidate_universe_entries (
    snapshot_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    rank integer NOT NULL,
    acc_trade_price_24h numeric NOT NULL,
    is_default_selected boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT candidate_universe_entries_rank_ck CHECK (((rank >= 1) AND (rank <= 100)))
);


--
-- Name: candidate_universe_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candidate_universe_snapshots (
    id bigint NOT NULL,
    source text NOT NULL,
    exchange text NOT NULL,
    quote_currency text NOT NULL,
    ranked_at timestamp with time zone NOT NULL,
    generated_by text DEFAULT 'system'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT candidate_universe_snapshots_source_ck CHECK ((source = 'UPBIT'::text))
);


--
-- Name: candidate_universe_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.candidate_universe_snapshots ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.candidate_universe_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: candle_aggregation_job_targets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candle_aggregation_job_targets (
    job_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    candle_unit text NOT NULL,
    status text NOT NULL,
    rows_written integer DEFAULT 0 NOT NULL,
    error_message text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT candle_aggregation_job_targets_status_ck CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'succeeded'::text, 'failed'::text]))),
    CONSTRAINT candle_aggregation_job_targets_unit_ck CHECK ((candle_unit = ANY (ARRAY['5m'::text, '10m'::text, '30m'::text, '60m'::text, '1d'::text, '1w'::text, '1M'::text])))
);


--
-- Name: candle_aggregation_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candle_aggregation_jobs (
    id bigint NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    CONSTRAINT candle_aggregation_jobs_status_ck CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'succeeded'::text, 'failed'::text])))
);


--
-- Name: candle_aggregation_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.candle_aggregation_jobs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.candle_aggregation_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: candle_rollups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candle_rollups (
    instrument_id bigint NOT NULL,
    candle_unit text NOT NULL,
    candle_start_at timestamp with time zone NOT NULL,
    open_price numeric NOT NULL,
    high_price numeric NOT NULL,
    low_price numeric NOT NULL,
    close_price numeric NOT NULL,
    trade_volume numeric NOT NULL,
    trade_amount numeric NOT NULL,
    completeness text NOT NULL,
    materialized_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT candle_rollups_completeness_ck CHECK ((completeness = ANY (ARRAY['complete'::text, 'partial'::text, 'empty'::text]))),
    CONSTRAINT candle_rollups_unit_ck CHECK ((candle_unit = ANY (ARRAY['5m'::text, '10m'::text, '30m'::text, '60m'::text, '1d'::text, '1w'::text, '1M'::text])))
);


--
-- Name: collection_coverage_segments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_coverage_segments (
    id bigint NOT NULL,
    snapshot_id bigint NOT NULL,
    data_type text NOT NULL,
    status text NOT NULL,
    offset_percent numeric NOT NULL,
    width_percent numeric NOT NULL,
    segment_start_at timestamp with time zone NOT NULL,
    segment_end_at timestamp with time zone NOT NULL,
    label text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT collection_coverage_segments_data_type_ck CHECK ((data_type = ANY (ARRAY['source_candle'::text, 'ticker_snapshot'::text, 'orderbook_summary'::text]))),
    CONSTRAINT collection_coverage_segments_percent_ck CHECK (((offset_percent >= (0)::numeric) AND (width_percent >= (0)::numeric) AND ((offset_percent + width_percent) <= (100)::numeric))),
    CONSTRAINT collection_coverage_segments_range_ck CHECK ((segment_start_at < segment_end_at)),
    CONSTRAINT collection_coverage_segments_status_ck CHECK ((status = ANY (ARRAY['collected'::text, 'missing'::text, 'collecting'::text, 'future'::text])))
);


--
-- Name: collection_coverage_segments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.collection_coverage_segments ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.collection_coverage_segments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: collection_coverage_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_coverage_snapshots (
    id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    data_type text NOT NULL,
    range_start_at timestamp with time zone NOT NULL,
    range_end_at timestamp with time zone,
    status text NOT NULL,
    progress_percent numeric NOT NULL,
    last_successful_at timestamp with time zone NOT NULL,
    missing_segment_count integer DEFAULT 0 NOT NULL,
    calculated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT collection_coverage_snapshots_data_type_ck CHECK ((data_type = ANY (ARRAY['source_candle'::text, 'ticker_snapshot'::text, 'orderbook_summary'::text]))),
    CONSTRAINT collection_coverage_snapshots_missing_count_ck CHECK ((missing_segment_count >= 0)),
    CONSTRAINT collection_coverage_snapshots_progress_ck CHECK (((progress_percent >= (0)::numeric) AND (progress_percent <= (100)::numeric))),
    CONSTRAINT collection_coverage_snapshots_status_ck CHECK ((status = ANY (ARRAY['normal'::text, 'warning'::text, 'incident'::text, 'backfilling'::text])))
);


--
-- Name: collection_coverage_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.collection_coverage_snapshots ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.collection_coverage_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: collection_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_plans (
    id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    preset text NOT NULL,
    range_start_at timestamp with time zone NOT NULL,
    range_end_at timestamp with time zone,
    is_continuous boolean DEFAULT true NOT NULL,
    method text NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT collection_plans_method_ck CHECK ((method = ANY (ARRAY['safe_restart'::text, 'incremental'::text]))),
    CONSTRAINT collection_plans_range_ck CHECK (((range_end_at IS NULL) OR (range_start_at < range_end_at))),
    CONSTRAINT collection_plans_status_ck CHECK ((status = ANY (ARRAY['latest_collecting'::text, 'collecting'::text, 'paused'::text, 'stopped'::text])))
);


--
-- Name: collection_plans_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.collection_plans ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.collection_plans_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: collection_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_runs (
    id bigint NOT NULL,
    run_type text NOT NULL,
    data_type text NOT NULL,
    status text NOT NULL,
    trigger_type text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    error_code text,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT collection_runs_data_type_ck CHECK ((data_type = ANY (ARRAY['candidate_universe'::text, 'source_candle'::text, 'ticker_snapshot'::text, 'orderbook_summary'::text, 'trade_event'::text, 'missing_range'::text]))),
    CONSTRAINT collection_runs_run_type_ck CHECK ((run_type = ANY (ARRAY['candidate_refresh'::text, 'incremental'::text, 'backfill'::text, 'completeness_check'::text]))),
    CONSTRAINT collection_runs_status_ck CHECK ((status = ANY (ARRAY['running'::text, 'succeeded'::text, 'partial'::text, 'failed'::text, 'cancelled'::text]))),
    CONSTRAINT collection_runs_trigger_type_ck CHECK ((trigger_type = ANY (ARRAY['schedule'::text, 'manual'::text, 'backfill_job'::text, 'system'::text])))
);


--
-- Name: collection_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.collection_runs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.collection_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: collection_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_settings (
    key text NOT NULL,
    value jsonb NOT NULL,
    updated_by text DEFAULT 'system'::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT collection_settings_updated_by_ck CHECK ((updated_by = ANY (ARRAY['system'::text, 'local_user'::text])))
);


--
-- Name: collection_target_changes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_target_changes (
    id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    previous_status text,
    new_status text NOT NULL,
    actor text NOT NULL,
    reason text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT collection_target_changes_actor_ck CHECK ((actor = ANY (ARRAY['system'::text, 'local_user'::text]))),
    CONSTRAINT collection_target_changes_new_status_ck CHECK ((new_status = ANY (ARRAY['active'::text, 'inactive'::text]))),
    CONSTRAINT collection_target_changes_previous_status_ck CHECK (((previous_status IS NULL) OR (previous_status = ANY (ARRAY['active'::text, 'inactive'::text]))))
);


--
-- Name: collection_target_changes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.collection_target_changes ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.collection_target_changes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: collection_targets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_targets (
    id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    status text NOT NULL,
    activated_at timestamp with time zone,
    deactivated_at timestamp with time zone,
    target_order integer,
    candidate_status text DEFAULT 'in_universe'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT collection_targets_candidate_status_ck CHECK ((candidate_status = ANY (ARRAY['in_universe'::text, 'out_of_universe'::text]))),
    CONSTRAINT collection_targets_order_ck CHECK (((target_order IS NULL) OR (target_order >= 1))),
    CONSTRAINT collection_targets_status_ck CHECK ((status = ANY (ARRAY['active'::text, 'inactive'::text])))
);


--
-- Name: collection_targets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.collection_targets ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.collection_targets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: collection_worker_heartbeats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_worker_heartbeats (
    worker_type text NOT NULL,
    status text NOT NULL,
    last_heartbeat_at timestamp with time zone NOT NULL,
    last_started_at timestamp with time zone,
    last_successful_at timestamp with time zone,
    last_error_at timestamp with time zone,
    last_error_message text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT collection_worker_heartbeats_status_ck CHECK ((status = ANY (ARRAY['running'::text, 'failed'::text]))),
    CONSTRAINT collection_worker_heartbeats_worker_type_ck CHECK ((worker_type = ANY (ARRAY['realtime_collection'::text, 'backfill_collection'::text, 'candle_aggregation'::text])))
);


--
-- Name: instruments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.instruments (
    id bigint NOT NULL,
    exchange text NOT NULL,
    market_code text NOT NULL,
    quote_currency text NOT NULL,
    base_asset text NOT NULL,
    display_name text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT instruments_status_ck CHECK ((status = ANY (ARRAY['active'::text, 'inactive'::text])))
);


--
-- Name: instruments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.instruments ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.instruments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: missing_ranges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.missing_ranges (
    id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    data_type text NOT NULL,
    unit text,
    range_start_at timestamp with time zone NOT NULL,
    range_end_at timestamp with time zone NOT NULL,
    reason text NOT NULL,
    status text NOT NULL,
    detected_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT missing_ranges_data_type_ck CHECK ((data_type = ANY (ARRAY['source_candle'::text, 'ticker_snapshot'::text, 'orderbook_summary'::text]))),
    CONSTRAINT missing_ranges_range_ck CHECK ((range_start_at < range_end_at)),
    CONSTRAINT missing_ranges_status_ck CHECK ((status = ANY (ARRAY['open'::text, 'resolved'::text, 'ignored'::text])))
);


--
-- Name: missing_ranges_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.missing_ranges ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.missing_ranges_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: notification_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_events (
    id bigint NOT NULL,
    severity text NOT NULL,
    event_type text NOT NULL,
    target_type text,
    target_id text,
    title text NOT NULL,
    message text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    acknowledged_at timestamp with time zone,
    resolved_at timestamp with time zone,
    CONSTRAINT notification_events_severity_ck CHECK ((severity = ANY (ARRAY['info'::text, 'warning'::text, 'error'::text, 'critical'::text]))),
    CONSTRAINT notification_events_status_ck CHECK ((status = ANY (ARRAY['open'::text, 'acknowledged'::text, 'resolved'::text])))
);


--
-- Name: notification_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.notification_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.notification_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: orderbook_summaries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.orderbook_summaries (
    id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    source text NOT NULL,
    bucket_at timestamp with time zone NOT NULL,
    best_bid_price numeric NOT NULL,
    best_bid_size numeric NOT NULL,
    best_ask_price numeric NOT NULL,
    best_ask_size numeric NOT NULL,
    spread numeric NOT NULL,
    bid_depth_10 numeric NOT NULL,
    ask_depth_10 numeric NOT NULL,
    imbalance_10 numeric NOT NULL,
    source_timestamp_at timestamp with time zone,
    collected_at timestamp with time zone NOT NULL,
    collection_run_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT orderbook_summaries_source_ck CHECK ((source = 'UPBIT'::text))
);


--
-- Name: orderbook_summaries_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.orderbook_summaries ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.orderbook_summaries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: raw_response_samples; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raw_response_samples (
    id bigint NOT NULL,
    source text NOT NULL,
    endpoint text NOT NULL,
    reason text NOT NULL,
    sampled_at timestamp with time zone DEFAULT now() NOT NULL,
    request_summary jsonb,
    response_status integer,
    response_body jsonb,
    error_message text,
    CONSTRAINT raw_response_samples_reason_ck CHECK ((reason = ANY (ARRAY['parse_error'::text, 'schema_mismatch'::text, 'unexpected_response'::text, 'fixture_sample'::text]))),
    CONSTRAINT raw_response_samples_source_ck CHECK ((source = 'UPBIT'::text))
);


--
-- Name: raw_response_samples_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.raw_response_samples ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.raw_response_samples_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying NOT NULL
);


--
-- Name: source_candles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_candles (
    id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    source text NOT NULL,
    candle_unit text NOT NULL,
    candle_start_at timestamp with time zone NOT NULL,
    open_price numeric NOT NULL,
    high_price numeric NOT NULL,
    low_price numeric NOT NULL,
    close_price numeric NOT NULL,
    trade_volume numeric NOT NULL,
    trade_amount numeric NOT NULL,
    source_timestamp_at timestamp with time zone,
    collected_at timestamp with time zone NOT NULL,
    collection_run_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT source_candles_candle_unit_ck CHECK ((candle_unit = ANY (ARRAY['1m'::text, '1d'::text]))),
    CONSTRAINT source_candles_source_ck CHECK ((source = 'UPBIT'::text))
);


--
-- Name: source_candles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.source_candles ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.source_candles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: target_collection_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.target_collection_results (
    id bigint NOT NULL,
    collection_run_id bigint NOT NULL,
    instrument_id bigint,
    data_type text NOT NULL,
    status text NOT NULL,
    target_start_at timestamp with time zone,
    target_end_at timestamp with time zone,
    latency_ms integer,
    retry_count integer DEFAULT 0 NOT NULL,
    rows_written integer DEFAULT 0 NOT NULL,
    error_code text,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT target_collection_results_data_type_ck CHECK ((data_type = ANY (ARRAY['source_candle'::text, 'ticker_snapshot'::text, 'orderbook_summary'::text, 'trade_event'::text, 'candidate_universe'::text, 'missing_range'::text]))),
    CONSTRAINT target_collection_results_latency_ck CHECK (((latency_ms IS NULL) OR (latency_ms >= 0))),
    CONSTRAINT target_collection_results_retry_count_ck CHECK ((retry_count >= 0)),
    CONSTRAINT target_collection_results_rows_written_ck CHECK ((rows_written >= 0)),
    CONSTRAINT target_collection_results_status_ck CHECK ((status = ANY (ARRAY['succeeded'::text, 'failed'::text, 'delayed'::text, 'no_data'::text, 'skipped'::text])))
);


--
-- Name: target_collection_results_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.target_collection_results ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.target_collection_results_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ticker_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ticker_snapshots (
    id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    source text NOT NULL,
    bucket_at timestamp with time zone NOT NULL,
    trade_price numeric NOT NULL,
    opening_price numeric,
    high_price numeric,
    low_price numeric,
    prev_closing_price numeric,
    change_rate numeric,
    signed_change_rate numeric,
    acc_trade_price_24h numeric,
    acc_trade_volume_24h numeric,
    source_timestamp_at timestamp with time zone,
    collected_at timestamp with time zone NOT NULL,
    collection_run_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ticker_snapshots_source_ck CHECK ((source = 'UPBIT'::text))
);


--
-- Name: ticker_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ticker_snapshots ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.ticker_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: trade_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trade_events (
    id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    source text NOT NULL,
    sequential_id bigint NOT NULL,
    trade_timestamp_at timestamp with time zone NOT NULL,
    trade_price numeric NOT NULL,
    trade_volume numeric NOT NULL,
    trade_amount numeric NOT NULL,
    ask_bid text NOT NULL,
    collected_at timestamp with time zone NOT NULL,
    collection_run_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trade_events_ask_bid_ck CHECK ((ask_bid = ANY (ARRAY['ASK'::text, 'BID'::text]))),
    CONSTRAINT trade_events_source_ck CHECK ((source = 'UPBIT'::text))
);


--
-- Name: trade_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.trade_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.trade_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);


--
-- Name: backfill_job_targets backfill_job_targets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backfill_job_targets
    ADD CONSTRAINT backfill_job_targets_pkey PRIMARY KEY (backfill_job_id, instrument_id);


--
-- Name: backfill_jobs backfill_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backfill_jobs
    ADD CONSTRAINT backfill_jobs_pkey PRIMARY KEY (id);


--
-- Name: candidate_universe_entries candidate_universe_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_universe_entries
    ADD CONSTRAINT candidate_universe_entries_pkey PRIMARY KEY (snapshot_id, instrument_id);


--
-- Name: candidate_universe_entries candidate_universe_entries_rank_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_universe_entries
    ADD CONSTRAINT candidate_universe_entries_rank_uk UNIQUE (snapshot_id, rank);


--
-- Name: candidate_universe_snapshots candidate_universe_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_universe_snapshots
    ADD CONSTRAINT candidate_universe_snapshots_pkey PRIMARY KEY (id);


--
-- Name: candle_aggregation_job_targets candle_aggregation_job_targets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_aggregation_job_targets
    ADD CONSTRAINT candle_aggregation_job_targets_pkey PRIMARY KEY (job_id, instrument_id, candle_unit);


--
-- Name: candle_aggregation_jobs candle_aggregation_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_aggregation_jobs
    ADD CONSTRAINT candle_aggregation_jobs_pkey PRIMARY KEY (id);


--
-- Name: candle_rollups candle_rollups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollups
    ADD CONSTRAINT candle_rollups_pkey PRIMARY KEY (instrument_id, candle_unit, candle_start_at);


--
-- Name: collection_coverage_segments collection_coverage_segments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_coverage_segments
    ADD CONSTRAINT collection_coverage_segments_pkey PRIMARY KEY (id);


--
-- Name: collection_coverage_snapshots collection_coverage_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_coverage_snapshots
    ADD CONSTRAINT collection_coverage_snapshots_pkey PRIMARY KEY (id);


--
-- Name: collection_plans collection_plans_instrument_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_plans
    ADD CONSTRAINT collection_plans_instrument_uk UNIQUE (instrument_id);


--
-- Name: collection_plans collection_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_plans
    ADD CONSTRAINT collection_plans_pkey PRIMARY KEY (id);


--
-- Name: collection_runs collection_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_runs
    ADD CONSTRAINT collection_runs_pkey PRIMARY KEY (id);


--
-- Name: collection_settings collection_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_settings
    ADD CONSTRAINT collection_settings_pkey PRIMARY KEY (key);


--
-- Name: collection_target_changes collection_target_changes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_target_changes
    ADD CONSTRAINT collection_target_changes_pkey PRIMARY KEY (id);


--
-- Name: collection_targets collection_targets_instrument_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_targets
    ADD CONSTRAINT collection_targets_instrument_uk UNIQUE (instrument_id);


--
-- Name: collection_targets collection_targets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_targets
    ADD CONSTRAINT collection_targets_pkey PRIMARY KEY (id);


--
-- Name: collection_worker_heartbeats collection_worker_heartbeats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_worker_heartbeats
    ADD CONSTRAINT collection_worker_heartbeats_pkey PRIMARY KEY (worker_type);


--
-- Name: instruments instruments_exchange_market_code_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instruments
    ADD CONSTRAINT instruments_exchange_market_code_uk UNIQUE (exchange, market_code);


--
-- Name: instruments instruments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instruments
    ADD CONSTRAINT instruments_pkey PRIMARY KEY (id);


--
-- Name: missing_ranges missing_ranges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.missing_ranges
    ADD CONSTRAINT missing_ranges_pkey PRIMARY KEY (id);


--
-- Name: missing_ranges missing_ranges_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.missing_ranges
    ADD CONSTRAINT missing_ranges_uk UNIQUE (instrument_id, data_type, unit, range_start_at, range_end_at);


--
-- Name: notification_events notification_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_events
    ADD CONSTRAINT notification_events_pkey PRIMARY KEY (id);


--
-- Name: orderbook_summaries orderbook_summaries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_summaries
    ADD CONSTRAINT orderbook_summaries_pkey PRIMARY KEY (id);


--
-- Name: orderbook_summaries orderbook_summaries_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_summaries
    ADD CONSTRAINT orderbook_summaries_uk UNIQUE (instrument_id, source, bucket_at);


--
-- Name: raw_response_samples raw_response_samples_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_response_samples
    ADD CONSTRAINT raw_response_samples_pkey PRIMARY KEY (id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: source_candles source_candles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_candles
    ADD CONSTRAINT source_candles_pkey PRIMARY KEY (id);


--
-- Name: source_candles source_candles_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_candles
    ADD CONSTRAINT source_candles_uk UNIQUE (instrument_id, source, candle_unit, candle_start_at);


--
-- Name: target_collection_results target_collection_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.target_collection_results
    ADD CONSTRAINT target_collection_results_pkey PRIMARY KEY (id);


--
-- Name: ticker_snapshots ticker_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticker_snapshots
    ADD CONSTRAINT ticker_snapshots_pkey PRIMARY KEY (id);


--
-- Name: ticker_snapshots ticker_snapshots_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticker_snapshots
    ADD CONSTRAINT ticker_snapshots_uk UNIQUE (instrument_id, source, bucket_at);


--
-- Name: trade_events trade_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trade_events
    ADD CONSTRAINT trade_events_pkey PRIMARY KEY (id);


--
-- Name: trade_events trade_events_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trade_events
    ADD CONSTRAINT trade_events_uk UNIQUE (instrument_id, source, sequential_id);


--
-- Name: audit_logs_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX audit_logs_created_at_idx ON public.audit_logs USING btree (created_at DESC);


--
-- Name: backfill_jobs_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX backfill_jobs_status_idx ON public.backfill_jobs USING btree (status, created_at DESC);


--
-- Name: collection_coverage_segments_snapshot_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX collection_coverage_segments_snapshot_idx ON public.collection_coverage_segments USING btree (snapshot_id, data_type);


--
-- Name: collection_coverage_snapshots_latest_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX collection_coverage_snapshots_latest_idx ON public.collection_coverage_snapshots USING btree (instrument_id, data_type, calculated_at DESC);


--
-- Name: collection_plans_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX collection_plans_status_idx ON public.collection_plans USING btree (status, instrument_id);


--
-- Name: collection_runs_started_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX collection_runs_started_at_idx ON public.collection_runs USING btree (started_at DESC);


--
-- Name: collection_worker_heartbeats_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX collection_worker_heartbeats_status_idx ON public.collection_worker_heartbeats USING btree (status, last_heartbeat_at DESC);


--
-- Name: missing_ranges_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX missing_ranges_status_idx ON public.missing_ranges USING btree (status, instrument_id, data_type);


--
-- Name: notification_events_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notification_events_status_idx ON public.notification_events USING btree (status, created_at DESC);


--
-- Name: orderbook_summaries_collected_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX orderbook_summaries_collected_at_idx ON public.orderbook_summaries USING btree (collected_at DESC);


--
-- Name: orderbook_summaries_instrument_bucket_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX orderbook_summaries_instrument_bucket_idx ON public.orderbook_summaries USING btree (instrument_id, bucket_at DESC);


--
-- Name: source_candles_collected_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX source_candles_collected_at_idx ON public.source_candles USING btree (collected_at DESC);


--
-- Name: source_candles_instrument_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX source_candles_instrument_time_idx ON public.source_candles USING btree (instrument_id, candle_unit, candle_start_at DESC);


--
-- Name: target_collection_results_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX target_collection_results_created_at_idx ON public.target_collection_results USING btree (created_at DESC, collection_run_id) INCLUDE (rows_written);


--
-- Name: target_collection_results_run_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX target_collection_results_run_idx ON public.target_collection_results USING btree (collection_run_id, instrument_id);


--
-- Name: ticker_snapshots_collected_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ticker_snapshots_collected_at_idx ON public.ticker_snapshots USING btree (collected_at DESC);


--
-- Name: ticker_snapshots_instrument_bucket_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ticker_snapshots_instrument_bucket_idx ON public.ticker_snapshots USING btree (instrument_id, bucket_at DESC);


--
-- Name: trade_events_instrument_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trade_events_instrument_time_idx ON public.trade_events USING btree (instrument_id, trade_timestamp_at DESC);


--
-- Name: backfill_job_targets backfill_job_targets_backfill_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backfill_job_targets
    ADD CONSTRAINT backfill_job_targets_backfill_job_id_fkey FOREIGN KEY (backfill_job_id) REFERENCES public.backfill_jobs(id) ON DELETE CASCADE;


--
-- Name: backfill_job_targets backfill_job_targets_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backfill_job_targets
    ADD CONSTRAINT backfill_job_targets_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: candidate_universe_entries candidate_universe_entries_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_universe_entries
    ADD CONSTRAINT candidate_universe_entries_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: candidate_universe_entries candidate_universe_entries_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_universe_entries
    ADD CONSTRAINT candidate_universe_entries_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES public.candidate_universe_snapshots(id) ON DELETE CASCADE;


--
-- Name: candle_aggregation_job_targets candle_aggregation_job_targets_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_aggregation_job_targets
    ADD CONSTRAINT candle_aggregation_job_targets_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: candle_aggregation_job_targets candle_aggregation_job_targets_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_aggregation_job_targets
    ADD CONSTRAINT candle_aggregation_job_targets_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.candle_aggregation_jobs(id) ON DELETE CASCADE;


--
-- Name: candle_rollups candle_rollups_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollups
    ADD CONSTRAINT candle_rollups_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: collection_coverage_segments collection_coverage_segments_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_coverage_segments
    ADD CONSTRAINT collection_coverage_segments_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES public.collection_coverage_snapshots(id) ON DELETE CASCADE;


--
-- Name: collection_coverage_snapshots collection_coverage_snapshots_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_coverage_snapshots
    ADD CONSTRAINT collection_coverage_snapshots_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: collection_plans collection_plans_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_plans
    ADD CONSTRAINT collection_plans_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: collection_target_changes collection_target_changes_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_target_changes
    ADD CONSTRAINT collection_target_changes_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: collection_targets collection_targets_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_targets
    ADD CONSTRAINT collection_targets_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: missing_ranges missing_ranges_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.missing_ranges
    ADD CONSTRAINT missing_ranges_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: orderbook_summaries orderbook_summaries_collection_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_summaries
    ADD CONSTRAINT orderbook_summaries_collection_run_id_fkey FOREIGN KEY (collection_run_id) REFERENCES public.collection_runs(id);


--
-- Name: orderbook_summaries orderbook_summaries_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_summaries
    ADD CONSTRAINT orderbook_summaries_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: source_candles source_candles_collection_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_candles
    ADD CONSTRAINT source_candles_collection_run_id_fkey FOREIGN KEY (collection_run_id) REFERENCES public.collection_runs(id);


--
-- Name: source_candles source_candles_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_candles
    ADD CONSTRAINT source_candles_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: target_collection_results target_collection_results_collection_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.target_collection_results
    ADD CONSTRAINT target_collection_results_collection_run_id_fkey FOREIGN KEY (collection_run_id) REFERENCES public.collection_runs(id) ON DELETE CASCADE;


--
-- Name: target_collection_results target_collection_results_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.target_collection_results
    ADD CONSTRAINT target_collection_results_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: ticker_snapshots ticker_snapshots_collection_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticker_snapshots
    ADD CONSTRAINT ticker_snapshots_collection_run_id_fkey FOREIGN KEY (collection_run_id) REFERENCES public.collection_runs(id);


--
-- Name: ticker_snapshots ticker_snapshots_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticker_snapshots
    ADD CONSTRAINT ticker_snapshots_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: trade_events trade_events_collection_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trade_events
    ADD CONSTRAINT trade_events_collection_run_id_fkey FOREIGN KEY (collection_run_id) REFERENCES public.collection_runs(id);


--
-- Name: trade_events trade_events_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trade_events
    ADD CONSTRAINT trade_events_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- PostgreSQL database dump complete
--

\unrestrict dbmate


--
-- Dbmate schema migrations
--

INSERT INTO public.schema_migrations (version) VALUES
    ('20260715000100');
