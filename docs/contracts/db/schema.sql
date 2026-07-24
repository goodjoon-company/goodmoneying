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

--
-- Name: btree_gist; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;


--
-- Name: EXTENSION btree_gist; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION btree_gist IS 'support for indexing common datatypes in GiST';


--
-- Name: current_rollup_quality_ceiling(bigint, timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.current_rollup_quality_ceiling(p_market_id bigint, p_range_start_at timestamp with time zone, p_range_end_at timestamp with time zone) RETURNS TABLE(quality_event_through_id bigint, knowledge_at timestamp with time zone)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
  SELECT MAX(event.id), MAX(event.detected_at)
  FROM public.data_quality_events event
  JOIN public.collection_target_specs specification
    ON specification.id = event.target_spec_id
  WHERE specification.market_id = p_market_id
    AND specification.data_type = 'source_candle'
    AND specification.candle_unit IN ('1m', '1d')
    AND tstzrange(event.range_start_at, event.range_end_at, '[)')
        && tstzrange(p_range_start_at, p_range_end_at, '[)')
$$;


--
-- Name: enforce_backtest_run_terminal_seal(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enforce_backtest_run_terminal_seal() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'backtest_runs is append-only';
  END IF;

  IF OLD.status IN ('succeeded','failed','cancelled','dead_letter') THEN
    RAISE EXCEPTION 'backtest_runs is append-only';
  END IF;

  IF NEW.strategy_version_id <> OLD.strategy_version_id
     OR NEW.strategy_graph_hash <> OLD.strategy_graph_hash
     OR NEW.dataset_version_id <> OLD.dataset_version_id
     OR NEW.dataset_content_hash <> OLD.dataset_content_hash
     OR NEW.engine_version <> OLD.engine_version
     OR NEW.input_hash <> OLD.input_hash
     OR NEW.input_payload <> OLD.input_payload
     OR NEW.parameter_hash <> OLD.parameter_hash
     OR NEW.seed <> OLD.seed
     OR NEW.idempotency_key <> OLD.idempotency_key
     OR NEW.request_id <> OLD.request_id
     OR NEW.actor_id <> OLD.actor_id
     OR NEW.requested_at <> OLD.requested_at
     OR NEW.reason <> OLD.reason
     OR NEW.request_hash <> OLD.request_hash THEN
    RAISE EXCEPTION 'backtest_runs immutable identity fields cannot be changed';
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: enforce_dataset_version_seal(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enforce_dataset_version_seal() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF TG_OP = 'UPDATE'
     AND OLD.sealed_at IS NULL AND NEW.sealed_at IS NOT NULL
     AND (to_jsonb(NEW) - 'sealed_at') = (to_jsonb(OLD) - 'sealed_at') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;


--
-- Name: enqueue_completed_rollup_indicator_invalidation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enqueue_completed_rollup_indicator_invalidation() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE changed candle_rollup_invalidations%ROWTYPE;
BEGIN
  IF NEW.status <> 'succeeded' OR OLD.status = 'succeeded' THEN RETURN NEW; END IF;
  SELECT invalidation.* INTO changed
  FROM candle_rollup_invalidations invalidation
  WHERE invalidation.id = NEW.invalidation_id;
  INSERT INTO indicator_invalidations (
    instrument_id, candle_unit, changed_rollup_invalidation_id,
    impact_start_at, impact_end_at, source_revision_through_id,
    quality_event_through_id, knowledge_at
  ) VALUES (
    changed.instrument_id, changed.candle_unit, changed.id,
    changed.range_start_at, changed.range_end_at, changed.source_revision_through_id,
    changed.quality_event_through_id, changed.knowledge_at
  ) ON CONFLICT (changed_rollup_invalidation_id) DO NOTHING;
  RETURN NEW;
END;
$$;


--
-- Name: enqueue_indicator_invalidation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enqueue_indicator_invalidation() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
  INSERT INTO indicator_invalidations (
    instrument_id, candle_unit, changed_rollup_id, impact_start_at,
    source_revision_through_id, quality_event_through_id, knowledge_at
  ) VALUES (
    NEW.instrument_id, NEW.candle_unit, NEW.id, NEW.candle_start_at,
    NEW.source_revision_through_id, NEW.quality_event_through_id, NEW.knowledge_at
  ) ON CONFLICT (changed_rollup_id) DO NOTHING;
  RETURN NEW;
END;
$$;


--
-- Name: enqueue_microstructure_invalidation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enqueue_microstructure_invalidation() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE
  v_market_id BIGINT;
  v_instrument_id BIGINT;
  v_occurred_at TIMESTAMPTZ;
  v_knowledge_at TIMESTAMPTZ;
  v_receipt_id BIGINT;
  v_snapshot_id BIGINT;
  v_trade_id BIGINT;
  v_snapshot_through BIGINT;
  v_trade_through BIGINT;
  v_receipt_through BIGINT;
  v_candle_revision_id BIGINT;
  v_quality_event_id BIGINT;
  v_connection_quality_id BIGINT;
BEGIN
  -- 자문 잠금 namespace: microstructure-invalidations-active-bucket
  v_market_id := NEW.market_id;
  v_instrument_id := NEW.instrument_id;
  v_occurred_at := NEW.occurred_at;
  v_knowledge_at := NEW.knowledge_at;
  v_receipt_id := NEW.source_receipt_id;
  IF TG_TABLE_NAME = 'orderbook_snapshots' THEN
    v_snapshot_id := NEW.id;
  ELSE
    v_trade_id := NEW.id;
  END IF;
  SELECT COALESCE(MAX(id),0) INTO v_snapshot_through FROM orderbook_snapshots
    WHERE instrument_id=v_instrument_id;
  SELECT COALESCE(MAX(id),0) INTO v_trade_through FROM trade_events
    WHERE instrument_id=v_instrument_id;
  SELECT COALESCE(MAX(id),0) INTO v_receipt_through FROM source_receipts
    WHERE instrument_id=v_instrument_id;
  SELECT COALESCE(MAX(id),0) INTO v_connection_quality_id
  FROM realtime_connection_quality_intervals
  WHERE detected_at <= v_knowledge_at;
  SELECT id INTO v_candle_revision_id
  FROM source_candle_revisions
  WHERE instrument_id=v_instrument_id AND candle_unit='1m'
    AND candle_start_at=date_trunc('minute',v_occurred_at)
    AND knowledge_at <= v_knowledge_at
  ORDER BY knowledge_at DESC, revision_number DESC, id DESC LIMIT 1;
  SELECT event.id INTO v_quality_event_id
  FROM data_quality_events event
  JOIN collection_target_specs specification ON specification.id=event.target_spec_id
  WHERE specification.market_id=v_market_id
    AND specification.data_type='source_candle'
    AND specification.candle_unit='1m'
    AND event.detected_at <= v_knowledge_at
    AND tstzrange(event.range_start_at,event.range_end_at,'[)')
        @> date_trunc('minute',v_occurred_at)
  ORDER BY event.id DESC LIMIT 1;

  INSERT INTO microstructure_invalidations (
    instrument_id, market_id, bucket_start_at,
    changed_orderbook_snapshot_id, changed_trade_event_id,
    orderbook_snapshot_through_id, trade_event_through_id,
    source_receipt_through_id, source_candle_revision_id,
    quality_event_through_id, connection_quality_through_id,
    knowledge_at, source_as_of
  ) VALUES (
    v_instrument_id, v_market_id, date_trunc('minute', v_occurred_at),
    v_snapshot_id, v_trade_id, v_snapshot_through, v_trade_through,
    GREATEST(v_receipt_through, COALESCE(v_receipt_id,0)),
    v_candle_revision_id, v_quality_event_id, v_connection_quality_id,
    v_knowledge_at, v_occurred_at
  )
  ON CONFLICT (
    instrument_id, bucket_start_at, source_candle_revision_id,
    quality_event_through_id, connection_quality_through_id
  ) WHERE status IN ('pending','retry_wait')
  DO UPDATE SET
    changed_orderbook_snapshot_id=COALESCE(
      EXCLUDED.changed_orderbook_snapshot_id,
      microstructure_invalidations.changed_orderbook_snapshot_id
    ),
    changed_trade_event_id=COALESCE(
      EXCLUDED.changed_trade_event_id,
      microstructure_invalidations.changed_trade_event_id
    ),
    orderbook_snapshot_through_id=GREATEST(
      microstructure_invalidations.orderbook_snapshot_through_id,
      EXCLUDED.orderbook_snapshot_through_id
    ),
    trade_event_through_id=GREATEST(
      microstructure_invalidations.trade_event_through_id,
      EXCLUDED.trade_event_through_id
    ),
    source_receipt_through_id=GREATEST(
      microstructure_invalidations.source_receipt_through_id,
      EXCLUDED.source_receipt_through_id
    ),
    knowledge_at=GREATEST(
      microstructure_invalidations.knowledge_at, EXCLUDED.knowledge_at
    ),
    source_as_of=GREATEST(
      microstructure_invalidations.source_as_of, EXCLUDED.source_as_of
    ),
    status='pending', next_retry_at=clock_timestamp(), updated_at=clock_timestamp();
  RETURN NEW;
END;
$$;


--
-- Name: enqueue_quality_microstructure_invalidation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enqueue_quality_microstructure_invalidation() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE
  v_market RECORD;
  v_bucket_start TIMESTAMPTZ;
  v_snapshot_through BIGINT;
  v_trade_through BIGINT;
  v_receipt_through BIGINT;
  v_candle_revision_id BIGINT;
  v_quality_event_id BIGINT;
BEGIN
  IF NEW.data_type NOT IN ('trade_event','orderbook_snapshot') THEN
    RETURN NEW;
  END IF;

  FOR v_market IN
    SELECT market.id AS market_id, market.legacy_instrument_id AS instrument_id
    FROM markets market
    JOIN realtime_connection_sessions session
      ON session.connection_id=NEW.connection_id
    WHERE (
      NEW.market_id=market.id
      OR (
        NEW.market_id IS NULL
        AND session.subscription_scope @> jsonb_build_object(
          'marketIds', jsonb_build_array(market.id)
        )
        AND session.subscription_scope @> jsonb_build_object(
          'dataTypes', jsonb_build_array(NEW.data_type)
        )
      )
    )
  LOOP
    SELECT COALESCE(MAX(id),0) INTO v_snapshot_through FROM orderbook_snapshots
      WHERE instrument_id=v_market.instrument_id;
    SELECT COALESCE(MAX(id),0) INTO v_trade_through FROM trade_events
      WHERE instrument_id=v_market.instrument_id;
    SELECT COALESCE(MAX(id),0) INTO v_receipt_through FROM source_receipts
      WHERE instrument_id=v_market.instrument_id;

    FOR v_bucket_start IN
      SELECT bucket_start
      FROM (
        SELECT generate_series(
          date_trunc('minute', NEW.range_start_at),
          date_trunc('minute', NEW.range_end_at - interval '1 microsecond'),
          interval '1 minute'
        ) AS bucket_start
        WHERE NEW.range_end_at - NEW.range_start_at <= interval '1 day'
        UNION
        SELECT revision.candle_start_at
        FROM source_candle_revisions revision
        WHERE revision.instrument_id=v_market.instrument_id
          AND revision.candle_unit='1m'
          AND revision.candle_start_at >= NEW.range_start_at
          AND revision.candle_start_at < NEW.range_end_at
        UNION
        SELECT date_trunc('minute',snapshot.occurred_at)
        FROM orderbook_snapshots snapshot
        WHERE snapshot.instrument_id=v_market.instrument_id
          AND snapshot.occurred_at >= NEW.range_start_at
          AND snapshot.occurred_at < NEW.range_end_at
        UNION
        SELECT date_trunc('minute',trade.occurred_at)
        FROM trade_events trade
        WHERE trade.instrument_id=v_market.instrument_id
          AND trade.occurred_at >= NEW.range_start_at
          AND trade.occurred_at < NEW.range_end_at
        UNION
        SELECT invalidation.bucket_start_at
        FROM microstructure_invalidations invalidation
        WHERE invalidation.instrument_id=v_market.instrument_id
          AND invalidation.bucket_start_at >= NEW.range_start_at
          AND invalidation.bucket_start_at < NEW.range_end_at
      ) affected
    LOOP
      SELECT id INTO v_candle_revision_id
      FROM source_candle_revisions
      WHERE instrument_id=v_market.instrument_id AND candle_unit='1m'
        AND candle_start_at=v_bucket_start AND knowledge_at <= NEW.detected_at
      ORDER BY knowledge_at DESC, revision_number DESC, id DESC LIMIT 1;
      SELECT event.id INTO v_quality_event_id
      FROM data_quality_events event
      JOIN collection_target_specs specification
        ON specification.id=event.target_spec_id
      WHERE specification.market_id=v_market.market_id
        AND specification.data_type='source_candle'
        AND specification.candle_unit='1m'
        AND event.detected_at <= NEW.detected_at
        AND tstzrange(event.range_start_at,event.range_end_at,'[)') @> v_bucket_start
      ORDER BY event.id DESC LIMIT 1;
      INSERT INTO microstructure_invalidations (
        instrument_id, market_id, bucket_start_at,
        changed_connection_quality_interval_id,
        orderbook_snapshot_through_id, trade_event_through_id,
        source_receipt_through_id, source_candle_revision_id,
        quality_event_through_id, connection_quality_through_id,
        knowledge_at, source_as_of
      ) VALUES (
        v_market.instrument_id, v_market.market_id, v_bucket_start, NEW.id,
        v_snapshot_through, v_trade_through, v_receipt_through,
        v_candle_revision_id, v_quality_event_id, NEW.id,
        NEW.detected_at, NEW.range_end_at
      )
      ON CONFLICT DO NOTHING;
    END LOOP;
  END LOOP;
  RETURN NEW;
END;
$$;


--
-- Name: enqueue_source_candle_microstructure_invalidation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enqueue_source_candle_microstructure_invalidation() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE
  v_instrument_id BIGINT;
  v_market_id BIGINT;
  v_started_at TIMESTAMPTZ;
  v_ended_at TIMESTAMPTZ;
  v_revision_id BIGINT;
  v_quality_id BIGINT;
  v_knowledge_at TIMESTAMPTZ;
  v_snapshot_through BIGINT;
  v_trade_through BIGINT;
  v_receipt_through BIGINT;
  v_connection_quality_id BIGINT;
BEGIN
  IF TG_TABLE_NAME = 'source_candle_revisions' THEN
    IF NEW.candle_unit <> '1m' THEN RETURN NEW; END IF;
    v_instrument_id := NEW.instrument_id;
    v_market_id := NEW.market_id;
    v_started_at := NEW.candle_start_at;
    v_ended_at := NEW.candle_start_at + interval '1 minute';
    v_revision_id := NEW.id;
    v_knowledge_at := NEW.knowledge_at;
  ELSE
    SELECT market.legacy_instrument_id, specification.market_id
      INTO v_instrument_id, v_market_id
    FROM collection_target_specs specification
    JOIN markets market ON market.id=specification.market_id
    WHERE specification.id=NEW.target_spec_id
      AND specification.data_type='source_candle'
      AND specification.candle_unit='1m';
    IF v_instrument_id IS NULL THEN RETURN NEW; END IF;
    v_started_at := date_trunc('minute', NEW.range_start_at);
    v_ended_at := NEW.range_end_at;
    v_quality_id := NEW.id;
    v_knowledge_at := NEW.detected_at;
  END IF;
  SELECT COALESCE(MAX(id),0) INTO v_snapshot_through FROM orderbook_snapshots
    WHERE instrument_id=v_instrument_id;
  SELECT COALESCE(MAX(id),0) INTO v_trade_through FROM trade_events
    WHERE instrument_id=v_instrument_id;
  SELECT COALESCE(MAX(id),0) INTO v_receipt_through FROM source_receipts
    WHERE instrument_id=v_instrument_id;
  SELECT COALESCE(MAX(id),0) INTO v_connection_quality_id
  FROM realtime_connection_quality_intervals
  WHERE detected_at <= v_knowledge_at;
  FOR v_started_at IN
    SELECT bucket_start
    FROM (
      SELECT generate_series(
        date_trunc('minute',v_started_at),
        date_trunc('minute',v_ended_at - interval '1 microsecond'),
        interval '1 minute'
      ) AS bucket_start
      WHERE v_ended_at - v_started_at <= interval '1 day'
      UNION
      SELECT revision.candle_start_at
      FROM source_candle_revisions revision
      WHERE revision.instrument_id=v_instrument_id
        AND revision.candle_unit='1m'
        AND revision.candle_start_at >= v_started_at
        AND revision.candle_start_at < v_ended_at
      UNION
      SELECT invalidation.bucket_start_at
      FROM microstructure_invalidations invalidation
      WHERE invalidation.instrument_id=v_instrument_id
        AND invalidation.bucket_start_at >= v_started_at
        AND invalidation.bucket_start_at < v_ended_at
    ) affected
  LOOP
    IF TG_TABLE_NAME <> 'source_candle_revisions' THEN
      SELECT id INTO v_revision_id
      FROM source_candle_revisions
      WHERE instrument_id=v_instrument_id AND candle_unit='1m'
        AND candle_start_at=v_started_at AND knowledge_at <= v_knowledge_at
      ORDER BY knowledge_at DESC, revision_number DESC, id DESC LIMIT 1;
    END IF;
  INSERT INTO microstructure_invalidations (
    instrument_id, market_id, bucket_start_at,
    changed_source_candle_revision_id, changed_quality_event_id,
    orderbook_snapshot_through_id, trade_event_through_id,
    source_receipt_through_id, source_candle_revision_id,
    quality_event_through_id, connection_quality_through_id,
    knowledge_at, source_as_of
  ) VALUES (
    v_instrument_id, v_market_id, v_started_at, v_revision_id, v_quality_id,
    v_snapshot_through, v_trade_through, v_receipt_through,
    v_revision_id, v_quality_id, v_connection_quality_id,
    v_knowledge_at, v_knowledge_at
  )
  ON CONFLICT DO NOTHING;
  END LOOP;
  RETURN NEW;
END;
$$;


--
-- Name: enqueue_source_indicator_invalidation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enqueue_source_indicator_invalidation() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE latest_quality_event_id BIGINT;
BEGIN
  IF NEW.candle_unit <> '1m' THEN RETURN NEW; END IF;
  SELECT MAX(event.id) INTO latest_quality_event_id
  FROM data_quality_events event
  JOIN collection_target_specs specification ON specification.id=event.target_spec_id
  WHERE specification.market_id=NEW.market_id
    AND specification.data_type='source_candle'
    AND specification.candle_unit='1m'
    AND event.detected_at <= NEW.knowledge_at;
  INSERT INTO indicator_invalidations (
    instrument_id, candle_unit, changed_source_revision_id, impact_start_at,
    source_revision_through_id, quality_event_through_id, knowledge_at
  ) VALUES (
    NEW.instrument_id, NEW.candle_unit, NEW.id, NEW.candle_start_at,
    NEW.id, latest_quality_event_id, NEW.knowledge_at
  ) ON CONFLICT (changed_source_revision_id) DO NOTHING;
  RETURN NEW;
END;
$$;


--
-- Name: mark_p6_live_identifier_submitted(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.mark_p6_live_identifier_submitted() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  UPDATE live_order_identifiers
  SET status = 'submitted'
  WHERE id = NEW.live_order_identifier_id
    AND status = 'reserved';
  RETURN NEW;
END;
$$;


--
-- Name: p6_base32lower_no_padding(bytea); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.p6_base32lower_no_padding(value bytea) RETURNS text
    LANGUAGE plpgsql IMMUTABLE STRICT
    AS $$
DECLARE
  alphabet TEXT := 'abcdefghijklmnopqrstuvwxyz234567';
  output TEXT := '';
  buffer BIGINT := 0;
  bit_count INTEGER := 0;
  byte_value INTEGER;
  index_value INTEGER;
  byte_index INTEGER;
BEGIN
  FOR byte_index IN 0..length(value) - 1 LOOP
    byte_value := get_byte(value, byte_index);
    buffer := (buffer << 8) | byte_value;
    bit_count := bit_count + 8;
    WHILE bit_count >= 5 LOOP
      index_value := (buffer >> (bit_count - 5)) & 31;
      output := output || substr(alphabet, index_value + 1, 1);
      bit_count := bit_count - 5;
      buffer := buffer & ((1::BIGINT << bit_count) - 1);
    END LOOP;
  END LOOP;
  IF bit_count > 0 THEN
    index_value := (buffer << (5 - bit_count)) & 31;
    output := output || substr(alphabet, index_value + 1, 1);
  END IF;
  RETURN output;
END;
$$;


--
-- Name: p6_upbit_live_order_identifier(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.p6_upbit_live_order_identifier(account_stable_id text, idempotency_key text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $$
  SELECT 'gm1_' || p6_base32lower_no_padding(
    sha256(convert_to(account_stable_id || ':' || idempotency_key, 'UTF8'))
  );
$$;


--
-- Name: prepare_realtime_source_receipt(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.prepare_realtime_source_receipt() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
  INSERT INTO realtime_connection_sessions (
    connection_id, connected_at, status, first_frame_sequence,
    last_frame_sequence, last_received_at
  ) VALUES (
    NEW.connection_id, NEW.received_at, 'active', NEW.frame_sequence,
    NEW.frame_sequence, NEW.received_at
  )
  ON CONFLICT (connection_id) DO NOTHING;
  NEW.knowledge_at := COALESCE(NEW.knowledge_at, NEW.received_at);
  NEW.collector_version := COALESCE(NEW.collector_version, 'realtime-collector-v1');
  NEW.schema_version := COALESCE(NEW.schema_version, 'upbit-websocket-default-v1');
  RETURN NEW;
END;
$$;


--
-- Name: reject_backtest_result_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_backtest_result_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;


--
-- Name: reject_candle_rollup_invalidation_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_candle_rollup_invalidation_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION 'candle_rollup_invalidations is append-only';
END;
$$;


--
-- Name: reject_candle_rollup_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_candle_rollup_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION 'candle_rollups is append-only';
END;
$$;


--
-- Name: reject_conflicting_trade_event(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_conflicting_trade_event() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE existing trade_events%ROWTYPE;
BEGIN
  SELECT * INTO existing
  FROM trade_events
  WHERE instrument_id=NEW.instrument_id AND source=NEW.source
    AND sequential_id=NEW.sequential_id;
  IF FOUND AND (
    existing.trade_timestamp_at IS DISTINCT FROM NEW.trade_timestamp_at OR
    existing.trade_price IS DISTINCT FROM NEW.trade_price OR
    existing.trade_volume IS DISTINCT FROM NEW.trade_volume OR
    existing.trade_amount IS DISTINCT FROM NEW.trade_amount OR
    existing.ask_bid IS DISTINCT FROM NEW.ask_bid
  ) THEN
    RAISE EXCEPTION 'conflicting trade duplicate: instrument %, sequential_id %',
      NEW.instrument_id, NEW.sequential_id;
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: reject_dataset_version_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_dataset_version_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;


--
-- Name: reject_indicator_immutable_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_indicator_immutable_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END;
$$;


--
-- Name: reject_microstructure_immutable_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_microstructure_immutable_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END;
$$;


--
-- Name: reject_p5_append_only_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_p5_append_only_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;


--
-- Name: reject_p6_live_exchange_order_binding_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_p6_live_exchange_order_binding_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION 'Upbit live exchange order binding is append-only';
END;
$$;


--
-- Name: reject_p6_live_reconciliation_application_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_p6_live_reconciliation_application_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION 'Upbit live reconciliation application is append-only';
END;
$$;


--
-- Name: reject_p6_order_submit_rehearsal_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_p6_order_submit_rehearsal_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION 'Upbit order submit rehearsal is append-only';
END;
$$;


--
-- Name: reject_p6_order_test_run_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_p6_order_test_run_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION 'Upbit order-test run evidence is append-only';
END;
$$;


--
-- Name: reject_p6_trading_capability_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_p6_trading_capability_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION 'trading capability evidence is append-only';
END;
$$;


--
-- Name: reject_p6_upbit_order_outbox_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_p6_upbit_order_outbox_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION 'upbit order outbox evidence is append-only in P6-6';
END;
$$;


--
-- Name: reject_p6_upbit_permission_attestation_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_p6_upbit_permission_attestation_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION 'upbit api key permission attestation is append-only';
END;
$$;


--
-- Name: reject_sealed_dataset_version_child_insert(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_sealed_dataset_version_child_insert() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  PERFORM 1 FROM dataset_versions
  WHERE id=NEW.dataset_version_id AND sealed_at IS NULL FOR KEY SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION '게시된 dataset version child는 append-only immutable이다';
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: reject_source_candle_revision_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_source_candle_revision_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION 'source_candle_revisions is append-only';
END;
$$;


--
-- Name: reject_strategy_version_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_strategy_version_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;


--
-- Name: reserve_p6_live_order_identifier(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reserve_p6_live_order_identifier() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  PERFORM reserve_p6_upbit_order_identifier(
    NEW.exchange_account_id,
    NEW.identifier,
    'live_order_identifiers',
    'identifier',
    NEW.id
  );

  RETURN NEW;
END;
$$;


--
-- Name: reserve_p6_order_test_identifier(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reserve_p6_order_test_identifier() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  PERFORM reserve_p6_upbit_order_identifier(
    NEW.exchange_account_id,
    NEW.response_uuid,
    'upbit_order_test_runs',
    'response_uuid',
    NEW.id
  );

  IF NEW.response_identifier IS DISTINCT FROM NEW.response_uuid THEN
    PERFORM reserve_p6_upbit_order_identifier(
      NEW.exchange_account_id,
      NEW.response_identifier,
      'upbit_order_test_runs',
      'response_identifier',
      NEW.id
    );
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: reserve_p6_upbit_order_identifier(bigint, text, text, text, bigint); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reserve_p6_upbit_order_identifier(exchange_account_id_value bigint, identifier_value text, source_table_value text, source_column_value text, source_id_value bigint) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF identifier_value IS NULL OR identifier_value = '' THEN
    RETURN;
  END IF;

  INSERT INTO upbit_order_identifier_reservations (
    exchange_account_id,
    identifier,
    source_table,
    source_column,
    source_id
  ) VALUES (
    exchange_account_id_value,
    identifier_value,
    source_table_value,
    source_column_value,
    source_id_value
  )
  ON CONFLICT (exchange_account_id, identifier) DO UPDATE
    SET reserved_at = upbit_order_identifier_reservations.reserved_at
    WHERE upbit_order_identifier_reservations.source_table = source_table_value
      AND upbit_order_identifier_reservations.source_column = source_column_value
      AND upbit_order_identifier_reservations.source_id = source_id_value;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Upbit order identifier is already reserved for another source';
  END IF;
END;
$$;


--
-- Name: source_candle_content_hash(numeric, numeric, numeric, numeric, numeric, numeric); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.source_candle_content_hash(p_open numeric, p_high numeric, p_low numeric, p_close numeric, p_volume numeric, p_trade_amount numeric) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    RETURN encode(sha256(convert_to(concat_ws('|'::text, (trim_scale(p_open))::text, (trim_scale(p_high))::text, (trim_scale(p_low))::text, (trim_scale(p_close))::text, (trim_scale(p_volume))::text, (trim_scale(p_trade_amount))::text), 'UTF8'::name)), 'hex'::text);


--
-- Name: validate_backtest_run_inputs(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.validate_backtest_run_inputs() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  -- strategy.status <> 'published'은 백테스트 실행 입력으로 거부한다.
  IF NOT EXISTS (
    SELECT 1 FROM strategy_versions strategy
    WHERE strategy.id = NEW.strategy_version_id AND strategy.status = 'published'
  ) THEN
    RAISE EXCEPTION 'backtest_runs requires published strategy version';
  END IF;

  -- version.sealed_at IS NULL인 데이터셋은 백테스트 실행 입력으로 거부한다.
  IF NOT EXISTS (
    SELECT 1 FROM dataset_versions version
    WHERE version.id = NEW.dataset_version_id AND version.sealed_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'backtest_runs requires sealed dataset version';
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: validate_dataset_version_typed_member(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.validate_dataset_version_typed_member() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  parent dataset_version_series%ROWTYPE;
  source_instrument BIGINT;
  source_unit TEXT;
  source_occurred TIMESTAMPTZ;
  source_calculation TEXT;
  source_definition TEXT;
  source_content_hash TEXT;
  source_knowledge_at TIMESTAMPTZ;
  source_as_of TIMESTAMPTZ;
  source_id BIGINT;
  source_ceiling BIGINT;
  parent_as_of TIMESTAMPTZ;
BEGIN
  SELECT * INTO parent FROM dataset_version_series
  WHERE dataset_version_id=NEW.dataset_version_id AND id=NEW.dataset_version_series_id;
  IF NOT FOUND OR parent.instrument_id <> NEW.instrument_id OR parent.unit <> NEW.unit THEN
    RAISE EXCEPTION 'typed dataset member identity가 parent series와 다르다';
  END IF;
  SELECT version.as_of INTO parent_as_of
  FROM dataset_versions version WHERE version.id=NEW.dataset_version_id;
  IF TG_TABLE_NAME='dataset_version_candles' THEN
    IF parent.data_kind <> 'candle' THEN RAISE EXCEPTION 'typed dataset member kind 불일치'; END IF;
    IF NEW.source_candle_revision_id IS NOT NULL THEN
      source_id := NEW.source_candle_revision_id;
      source_ceiling := parent.source_revision_through_id;
      SELECT revision.instrument_id, revision.candle_unit, revision.candle_start_at,
             revision.input_content_hash, revision.knowledge_at, revision.source_as_of
        INTO source_instrument, source_unit, source_occurred, source_content_hash,
             source_knowledge_at, source_as_of
      FROM source_candle_revisions revision WHERE revision.id=NEW.source_candle_revision_id;
      source_calculation := 'source-candle-v1';
    ELSE
      source_id := NEW.candle_rollup_id;
      source_ceiling := parent.candle_rollup_through_id;
      SELECT rollup.instrument_id, rollup.candle_unit, rollup.candle_start_at,
             rollup.calculation_version, rollup.result_content_hash,
             rollup.knowledge_at, rollup.source_as_of
        INTO source_instrument, source_unit, source_occurred, source_calculation,
             source_content_hash, source_knowledge_at, source_as_of
      FROM candle_rollups rollup WHERE rollup.id=NEW.candle_rollup_id;
    END IF;
  ELSIF TG_TABLE_NAME='dataset_version_indicators' THEN
    IF parent.data_kind <> 'indicator' THEN RAISE EXCEPTION 'typed dataset member kind 불일치'; END IF;
    source_id := NEW.indicator_materialization_id;
    source_ceiling := parent.indicator_materialization_through_id;
    SELECT materialization.instrument_id, materialization.candle_unit,
           materialization.occurred_at, materialization.definition_set_hash,
           materialization.content_hash, materialization.knowledge_at,
           materialization.source_as_of
      INTO source_instrument, source_unit, source_occurred, source_definition,
           source_content_hash, source_knowledge_at, source_as_of
    FROM indicator_materializations materialization
    WHERE materialization.id=NEW.indicator_materialization_id;
    source_calculation := 'indicator-v1';
  ELSIF TG_TABLE_NAME='dataset_version_market_statistics' THEN
    IF parent.data_kind <> 'market_statistic' THEN RAISE EXCEPTION 'typed dataset member kind 불일치'; END IF;
    source_id := NEW.market_statistic_id;
    source_ceiling := parent.market_statistic_through_id;
    SELECT statistic.instrument_id, statistic.interval, statistic.occurred_at,
           statistic.calculation_version, statistic.content_hash,
           statistic.knowledge_at, statistic.source_as_of
      INTO source_instrument, source_unit, source_occurred, source_calculation,
           source_content_hash, source_knowledge_at, source_as_of
    FROM market_statistics statistic WHERE statistic.id=NEW.market_statistic_id;
  ELSE
    IF parent.data_kind <> 'microstructure' THEN RAISE EXCEPTION 'typed dataset member kind 불일치'; END IF;
    source_id := NEW.microstructure_materialization_id;
    source_ceiling := parent.microstructure_materialization_through_id;
    SELECT materialization.instrument_id, '1m', materialization.bucket_start_at,
           definition.calculation_version, definition.definition_hash,
           statistic.content_hash, materialization.knowledge_at,
           materialization.source_as_of
      INTO source_instrument, source_unit, source_occurred,
           source_calculation, source_definition, source_content_hash,
           source_knowledge_at, source_as_of
    FROM microstructure_materializations materialization
    JOIN microstructure_definition_versions definition
      ON definition.id=materialization.definition_version_id
    JOIN microstructure_statistics statistic
      ON statistic.materialization_id=materialization.id
    WHERE materialization.id=NEW.microstructure_materialization_id;
  END IF;
  IF source_ceiling IS NULL OR source_id > source_ceiling THEN
    RAISE EXCEPTION 'typed dataset member가 고정된 원천 frontier를 넘는다';
  END IF;
  IF source_knowledge_at > parent_as_of THEN
    RAISE EXCEPTION 'typed dataset member knowledge_at이 dataset version asOf를 넘는다';
  END IF;
  IF source_instrument IS NULL OR source_instrument <> NEW.instrument_id
     OR source_unit <> NEW.unit OR source_occurred <> NEW.occurred_at THEN
    RAISE EXCEPTION 'typed dataset member source 자연키가 다르다';
  END IF;
  IF parent.definition_set_hash IS DISTINCT FROM source_definition THEN
    RAISE EXCEPTION 'typed dataset member definition 자연키가 다르다';
  END IF;
  IF parent.calculation_version <> 'daily-source-preferred-v1'
     AND parent.calculation_version IS DISTINCT FROM source_calculation THEN
    RAISE EXCEPTION 'typed dataset member calculation version이 다르다';
  END IF;
  IF NEW.content_hash IS DISTINCT FROM source_content_hash
     OR NEW.knowledge_at IS DISTINCT FROM source_knowledge_at
     OR NEW.source_as_of IS DISTINCT FROM source_as_of THEN
    RAISE EXCEPTION 'typed dataset member 내용 또는 인과 시각이 원천과 다르다';
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: validate_p6_live_exchange_order_binding(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.validate_p6_live_exchange_order_binding() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  exchange_order RECORD;
  live_identifier RECORD;
  outbox RECORD;
BEGIN
  SELECT order_intent_id, execution_mode, simulated_order_key
    INTO exchange_order
  FROM exchange_orders
  WHERE id = NEW.exchange_order_id;

  IF exchange_order IS NULL THEN
    RAISE EXCEPTION 'live exchange order binding references missing exchange order';
  END IF;

  IF exchange_order.execution_mode <> 'live' THEN
    RAISE EXCEPTION 'live exchange order binding requires live exchange order';
  END IF;

  IF exchange_order.order_intent_id <> NEW.order_intent_id THEN
    RAISE EXCEPTION 'live binding order intent does not match exchange order';
  END IF;

  IF exchange_order.simulated_order_key <> NEW.upbit_identifier THEN
    RAISE EXCEPTION 'live exchange order key must match Upbit identifier';
  END IF;

  SELECT exchange_account_id, order_intent_id, identifier, status
    INTO live_identifier
  FROM live_order_identifiers
  WHERE id = NEW.live_order_identifier_id;

  IF live_identifier IS NULL THEN
    RAISE EXCEPTION 'live exchange order binding references missing live identifier';
  END IF;

  IF live_identifier.exchange_account_id <> NEW.exchange_account_id THEN
    RAISE EXCEPTION 'live binding exchange account does not match live identifier';
  END IF;

  IF live_identifier.order_intent_id <> NEW.order_intent_id THEN
    RAISE EXCEPTION 'live binding order intent does not match live identifier';
  END IF;

  IF live_identifier.identifier <> NEW.upbit_identifier THEN
    RAISE EXCEPTION 'live binding Upbit identifier must match live identifier';
  END IF;

  IF live_identifier.status <> 'reserved' THEN
    RAISE EXCEPTION 'live binding requires reserved live identifier';
  END IF;

  SELECT exchange_account_id, order_intent_id, live_order_identifier_id, status
    INTO outbox
  FROM upbit_order_outbox
  WHERE id = NEW.upbit_order_outbox_id;

  IF outbox IS NULL THEN
    RAISE EXCEPTION 'live exchange order binding references missing safe order outbox';
  END IF;

  IF outbox.status <> 'ready' THEN
    RAISE EXCEPTION 'live exchange order binding requires ready outbox';
  END IF;

  IF outbox.exchange_account_id <> NEW.exchange_account_id
     OR outbox.order_intent_id <> NEW.order_intent_id
     OR outbox.live_order_identifier_id <> NEW.live_order_identifier_id THEN
    RAISE EXCEPTION 'live exchange order binding does not match safe order outbox';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM upbit_order_test_runs test_run
    WHERE test_run.exchange_account_id = NEW.exchange_account_id
      AND (
        NEW.upbit_order_uuid IN (
          test_run.response_uuid,
          test_run.response_identifier
        )
        OR NEW.upbit_identifier IN (
          test_run.response_uuid,
          test_run.response_identifier
        )
      )
  ) THEN
    RAISE EXCEPTION 'order-test response identifier cannot be bound as live exchange order';
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: validate_p6_live_exchange_order_has_binding(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.validate_p6_live_exchange_order_has_binding() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  binding RECORD;
BEGIN
  IF NEW.execution_mode <> 'live' THEN
    RETURN NEW;
  END IF;

  SELECT order_intent_id, upbit_identifier
    INTO binding
  FROM upbit_live_exchange_order_bindings
  WHERE exchange_order_id = NEW.id;

  IF binding IS NULL THEN
    RAISE EXCEPTION 'live exchange order requires Upbit live binding';
  END IF;

  IF binding.order_intent_id <> NEW.order_intent_id
     OR binding.upbit_identifier <> NEW.simulated_order_key THEN
    RAISE EXCEPTION 'live exchange order no longer matches Upbit live binding';
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: validate_p6_live_order_identifier(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.validate_p6_live_order_identifier() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  actual_idempotency_key TEXT;
  account_stable_id_value TEXT;
  expected_identifier TEXT;
BEGIN
  SELECT intent.idempotency_key, account.account_stable_id
    INTO actual_idempotency_key, account_stable_id_value
  FROM order_intents intent
  JOIN exchange_accounts account ON account.id = NEW.exchange_account_id
  WHERE intent.id = NEW.order_intent_id;

  IF actual_idempotency_key IS NULL THEN
    RAISE EXCEPTION 'live order identifier references missing account or order intent';
  END IF;
  IF NEW.idempotency_key <> actual_idempotency_key THEN
    RAISE EXCEPTION 'live order identifier idempotency_key must match order_intents.idempotency_key';
  END IF;

  expected_identifier := p6_upbit_live_order_identifier(
    account_stable_id_value,
    actual_idempotency_key
  );
  IF NEW.identifier <> expected_identifier THEN
    RAISE EXCEPTION 'live order identifier must be derived from account_stable_id and order intent idempotency_key';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM upbit_order_test_runs test_run
    WHERE test_run.exchange_account_id = NEW.exchange_account_id
      AND NEW.identifier IN (
        test_run.response_uuid,
        test_run.response_identifier
      )
  ) THEN
    RAISE EXCEPTION 'order-test response identifier cannot be reserved as a live order identifier';
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: validate_p6_live_reconciliation_application(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.validate_p6_live_reconciliation_application() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  binding RECORD;
  exchange_order RECORD;
  run RECORD;
BEGIN
  SELECT exchange_account_id, order_intent_id, exchange_order_id,
         upbit_order_uuid, upbit_identifier
    INTO binding
  FROM upbit_live_exchange_order_bindings
  WHERE id = NEW.live_exchange_order_binding_id;

  IF binding IS NULL THEN
    RAISE EXCEPTION 'live reconciliation application references missing binding';
  END IF;

  IF binding.exchange_account_id <> NEW.exchange_account_id
     OR binding.order_intent_id <> NEW.order_intent_id
     OR binding.exchange_order_id <> NEW.exchange_order_id
     OR binding.upbit_order_uuid <> NEW.observed_upbit_order_uuid
     OR binding.upbit_identifier <> NEW.observed_upbit_identifier THEN
    RAISE EXCEPTION 'live reconciliation application requires matching binding';
  END IF;

  SELECT order_intent_id, execution_mode, simulated_order_key
    INTO exchange_order
  FROM exchange_orders
  WHERE id = NEW.exchange_order_id;

  IF exchange_order IS NULL THEN
    RAISE EXCEPTION 'live reconciliation application references missing exchange order';
  END IF;

  IF exchange_order.execution_mode <> 'live'
     OR exchange_order.order_intent_id <> NEW.order_intent_id
     OR exchange_order.simulated_order_key <> NEW.observed_upbit_identifier THEN
    RAISE EXCEPTION 'live reconciliation application requires live exchange order';
  END IF;

  SELECT exchange_order_id, status, observed_status, evidence
    INTO run
  FROM reconciliation_runs
  WHERE id = NEW.reconciliation_run_id;

  IF run IS NULL THEN
    RAISE EXCEPTION 'live reconciliation application references missing reconciliation run';
  END IF;

  IF run.exchange_order_id <> NEW.exchange_order_id THEN
    RAISE EXCEPTION 'live reconciliation application run does not match exchange order';
  END IF;

  IF run.status <> 'succeeded'
     OR run.observed_status <> NEW.observed_state THEN
    RAISE EXCEPTION 'live reconciliation application requires succeeded reconciliation run';
  END IF;

  IF run.evidence->>'source' <> 'upbit-rest-order-snapshot'
     OR run.evidence->>'sourceEndpoint' <> NEW.source_endpoint
     OR run.evidence->>'orderUuid' <> NEW.observed_upbit_order_uuid
     OR run.evidence->>'identifier' <> NEW.observed_upbit_identifier
     OR run.evidence->>'state' <> NEW.observed_state
     OR run.evidence->>'canResubmit' <> 'false' THEN
    RAISE EXCEPTION 'live reconciliation application snapshot must match reconciliation evidence';
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: validate_p6_live_reconciliation_run_has_application(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.validate_p6_live_reconciliation_run_has_application() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  exchange_order RECORD;
BEGIN
  IF NEW.status <> 'succeeded' THEN
    RETURN NEW;
  END IF;

  SELECT execution_mode
    INTO exchange_order
  FROM exchange_orders
  WHERE id = NEW.exchange_order_id;

  IF exchange_order IS NULL OR exchange_order.execution_mode <> 'live' THEN
    RETURN NEW;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM upbit_live_reconciliation_applications application
    WHERE application.reconciliation_run_id = NEW.id
      AND application.exchange_order_id = NEW.exchange_order_id
  ) THEN
    RAISE EXCEPTION 'live succeeded reconciliation run requires live application';
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: validate_p6_order_submit_rehearsal(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.validate_p6_order_submit_rehearsal() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  live_identifier RECORD;
  outbox RECORD;
  permission RECORD;
BEGIN
  SELECT exchange_account_id, order_intent_id, identifier, status
    INTO live_identifier
  FROM live_order_identifiers
  WHERE id = NEW.live_order_identifier_id;

  IF live_identifier IS NULL THEN
    RAISE EXCEPTION 'order submit rehearsal references missing live identifier';
  END IF;

  SELECT exchange_account_id, order_intent_id, live_order_identifier_id,
         permission_attestation_id, status, request_payload, request_hash
    INTO outbox
  FROM upbit_order_outbox
  WHERE id = NEW.upbit_order_outbox_id;

  IF outbox IS NULL THEN
    RAISE EXCEPTION 'order submit rehearsal references missing outbox';
  END IF;

  IF live_identifier.exchange_account_id <> NEW.exchange_account_id
     OR live_identifier.order_intent_id <> NEW.order_intent_id THEN
    RAISE EXCEPTION 'order submit rehearsal live identifier account or intent mismatch';
  END IF;

  IF outbox.exchange_account_id <> NEW.exchange_account_id
     OR outbox.order_intent_id <> NEW.order_intent_id
     OR outbox.live_order_identifier_id <> NEW.live_order_identifier_id THEN
    RAISE EXCEPTION 'order submit rehearsal outbox account or intent mismatch';
  END IF;

  IF NEW.request_payload <> outbox.request_payload
     OR NEW.request_hash <> outbox.request_hash THEN
    RAISE EXCEPTION 'order submit rehearsal request mismatch';
  END IF;

  IF NEW.request_payload->>'identifier' <> live_identifier.identifier THEN
    RAISE EXCEPTION 'order submit rehearsal identifier must match live identifier';
  END IF;

  IF NEW.rehearsal_status = 'passed' THEN
    IF outbox.status <> 'ready' THEN
      RAISE EXCEPTION 'order submit rehearsal requires ready outbox';
    END IF;

    IF live_identifier.status <> 'reserved' THEN
      RAISE EXCEPTION 'order submit rehearsal requires reserved live identifier';
    END IF;

    IF NEW.permission_attestation_id IS NULL
       OR outbox.permission_attestation_id IS NULL
       OR NEW.permission_attestation_id <> outbox.permission_attestation_id THEN
      RAISE EXCEPTION 'order submit rehearsal permission attestation mismatch';
    END IF;

    SELECT exchange_account_id, expires_at
      INTO permission
    FROM upbit_api_key_permission_attestations
    WHERE id = NEW.permission_attestation_id;

    IF permission IS NULL THEN
      RAISE EXCEPTION 'order submit rehearsal references missing permission attestation';
    END IF;

    IF permission.exchange_account_id <> NEW.exchange_account_id THEN
      RAISE EXCEPTION 'order submit rehearsal permission account mismatch';
    END IF;

    IF permission.expires_at <= clock_timestamp() THEN
      RAISE EXCEPTION 'order submit rehearsal permission expired';
    END IF;

    IF EXISTS (
      SELECT 1
      FROM upbit_live_exchange_order_bindings binding
      WHERE binding.upbit_order_outbox_id = NEW.upbit_order_outbox_id
         OR binding.live_order_identifier_id = NEW.live_order_identifier_id
    ) THEN
      RAISE EXCEPTION 'order submit rehearsal cannot follow live binding';
    END IF;
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: validate_p6_order_test_identifier_not_live(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.validate_p6_order_test_identifier_not_live() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM live_order_identifiers live_identifier
    WHERE live_identifier.exchange_account_id = NEW.exchange_account_id
      AND live_identifier.identifier IN (
        NEW.response_uuid,
        NEW.response_identifier
      )
  ) THEN
    RAISE EXCEPTION 'live order identifier cannot be recorded as an order-test response identifier';
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: validate_p6_upbit_order_outbox_consistency(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.validate_p6_upbit_order_outbox_consistency() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  live_identity RECORD;
  permission RECORD;
BEGIN
  SELECT live.exchange_account_id, live.order_intent_id, intent.status AS order_intent_status
    INTO live_identity
  FROM live_order_identifiers live
  JOIN order_intents intent ON intent.id = live.order_intent_id
  WHERE live.id = NEW.live_order_identifier_id;

  IF live_identity IS NULL THEN
    RAISE EXCEPTION 'live order identifier does not exist';
  END IF;

  IF live_identity.exchange_account_id <> NEW.exchange_account_id THEN
    RAISE EXCEPTION 'outbox exchange account does not match live identifier';
  END IF;

  IF live_identity.order_intent_id <> NEW.order_intent_id THEN
    RAISE EXCEPTION 'outbox order intent does not match live identifier';
  END IF;

  IF NEW.permission_attestation_id IS NOT NULL THEN
    SELECT exchange_account_id, has_order_permission, has_order_read_permission,
           has_withdraw_permission, expires_at
      INTO permission
    FROM upbit_api_key_permission_attestations
    WHERE id = NEW.permission_attestation_id;

    IF permission IS NULL THEN
      RAISE EXCEPTION 'ready outbox requires permission attestation';
    END IF;

    IF permission.exchange_account_id <> NEW.exchange_account_id THEN
      RAISE EXCEPTION 'outbox exchange account does not match permission attestation';
    END IF;
  END IF;

  IF NEW.status = 'ready' THEN
    IF live_identity.order_intent_status <> 'approved' THEN
      RAISE EXCEPTION 'ready outbox requires approved order intent';
    END IF;

    IF permission.has_order_permission IS NOT TRUE
       OR permission.has_order_read_permission IS NOT TRUE
       OR permission.has_withdraw_permission IS NOT FALSE THEN
      RAISE EXCEPTION 'permission attestation is not order-ready';
    END IF;

    IF permission.expires_at <= clock_timestamp() THEN
      RAISE EXCEPTION 'permission attestation is expired';
    END IF;
  END IF;

  RETURN NEW;
END;
$$;


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
    CONSTRAINT audit_logs_actor_ck CHECK ((btrim(actor) <> ''::text))
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
    target_spec_id bigint,
    last_fetch_manifest_id bigint,
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
    idempotency_key text NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 5 NOT NULL,
    next_retry_at timestamp with time zone,
    lease_owner text,
    lease_expires_at timestamp with time zone,
    last_error_code text,
    dead_letter_reason text,
    CONSTRAINT backfill_jobs_data_type_ck CHECK ((data_type = 'source_candle'::text)),
    CONSTRAINT backfill_jobs_estimated_request_count_ck CHECK ((estimated_request_count >= 0)),
    CONSTRAINT backfill_jobs_estimated_row_count_ck CHECK ((estimated_row_count >= 0)),
    CONSTRAINT backfill_jobs_restart_mode_ck CHECK (((restart_mode IS NULL) OR (restart_mode = 'safe_restart'::text))),
    CONSTRAINT backfill_jobs_status_ck CHECK ((status = ANY (ARRAY['planned'::text, 'pending'::text, 'leased'::text, 'running'::text, 'retry_wait'::text, 'paused'::text, 'stopped'::text, 'succeeded'::text, 'failed'::text, 'dead_letter'::text, 'cancelled'::text]))),
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
-- Name: backfill_safety_gate; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.backfill_safety_gate (
    singleton boolean DEFAULT true NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    backup_verified_at timestamp with time zone,
    free_capacity_bytes bigint DEFAULT 0 NOT NULL,
    required_capacity_bytes bigint DEFAULT 0 NOT NULL,
    approved_sha text,
    approved_by text,
    approved_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT backfill_safety_gate_approval_ck CHECK (((NOT enabled) OR ((backup_verified_at IS NOT NULL) AND (free_capacity_bytes > 0) AND (required_capacity_bytes > 0) AND (approved_sha IS NOT NULL) AND (approved_by IS NOT NULL) AND (approved_at IS NOT NULL)))),
    CONSTRAINT backfill_safety_gate_free_capacity_bytes_check CHECK ((free_capacity_bytes >= 0)),
    CONSTRAINT backfill_safety_gate_required_capacity_bytes_check CHECK ((required_capacity_bytes >= 0)),
    CONSTRAINT backfill_safety_gate_singleton_check CHECK (singleton)
);


--
-- Name: backtest_artifacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.backtest_artifacts (
    id bigint NOT NULL,
    run_id bigint NOT NULL,
    artifact_type text NOT NULL,
    content_hash text NOT NULL,
    media_type text NOT NULL,
    storage_uri text,
    artifact_json jsonb,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT backtest_artifacts_artifact_type_check CHECK ((btrim(artifact_type) <> ''::text)),
    CONSTRAINT backtest_artifacts_check CHECK (((storage_uri IS NOT NULL) OR (artifact_json IS NOT NULL))),
    CONSTRAINT backtest_artifacts_content_hash_check CHECK ((content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT backtest_artifacts_media_type_check CHECK ((btrim(media_type) <> ''::text))
);


--
-- Name: backtest_artifacts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.backtest_artifacts ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.backtest_artifacts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: backtest_equity_points; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.backtest_equity_points (
    id bigint NOT NULL,
    run_id bigint NOT NULL,
    point_sequence integer NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    cash numeric(38,18) NOT NULL,
    base_position numeric(38,18) NOT NULL,
    equity numeric(38,18) NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT backtest_equity_points_check CHECK ((knowledge_at >= occurred_at)),
    CONSTRAINT backtest_equity_points_point_sequence_check CHECK ((point_sequence > 0))
);


--
-- Name: backtest_equity_points_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.backtest_equity_points ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.backtest_equity_points_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: backtest_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.backtest_metrics (
    id bigint NOT NULL,
    run_id bigint NOT NULL,
    metric_name text NOT NULL,
    scope_key text DEFAULT 'run'::text NOT NULL,
    metric_value numeric(38,18),
    metric_payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT backtest_metrics_check CHECK (((metric_value IS NOT NULL) OR (metric_payload <> '{}'::jsonb))),
    CONSTRAINT backtest_metrics_metric_name_check CHECK ((btrim(metric_name) <> ''::text)),
    CONSTRAINT backtest_metrics_scope_key_check CHECK ((btrim(scope_key) <> ''::text))
);


--
-- Name: backtest_metrics_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.backtest_metrics ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.backtest_metrics_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: backtest_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.backtest_runs (
    id bigint NOT NULL,
    strategy_version_id bigint NOT NULL,
    strategy_graph_hash text NOT NULL,
    dataset_version_id bigint NOT NULL,
    dataset_content_hash text NOT NULL,
    engine_version text NOT NULL,
    status text NOT NULL,
    input_hash text NOT NULL,
    result_hash text,
    parameter_hash text NOT NULL,
    seed bigint NOT NULL,
    assumptions jsonb DEFAULT '[]'::jsonb NOT NULL,
    idempotency_key text NOT NULL,
    request_id text NOT NULL,
    actor_id text NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    reason text NOT NULL,
    request_hash text NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    next_retry_at timestamp with time zone,
    lease_owner text,
    lease_expires_at timestamp with time zone,
    lease_generation integer DEFAULT 0 NOT NULL,
    last_error_code text,
    last_error_message text,
    dead_letter_reason text,
    input_payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT backtest_runs_actor_id_check CHECK ((btrim(actor_id) <> ''::text)),
    CONSTRAINT backtest_runs_assumptions_check CHECK ((jsonb_typeof(assumptions) = 'array'::text)),
    CONSTRAINT backtest_runs_attempt_count_check CHECK ((attempt_count >= 0)),
    CONSTRAINT backtest_runs_dataset_content_hash_check CHECK ((dataset_content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT backtest_runs_dead_letter_reason_check CHECK (((status <> 'dead_letter'::text) OR (btrim(COALESCE(dead_letter_reason, ''::text)) <> ''::text))),
    CONSTRAINT backtest_runs_engine_version_check CHECK ((btrim(engine_version) <> ''::text)),
    CONSTRAINT backtest_runs_idempotency_key_check CHECK ((btrim(idempotency_key) <> ''::text)),
    CONSTRAINT backtest_runs_input_hash_check CHECK ((input_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT backtest_runs_input_payload_object_check CHECK ((jsonb_typeof(input_payload) = 'object'::text)),
    CONSTRAINT backtest_runs_lease_generation_check CHECK ((lease_generation >= 0)),
    CONSTRAINT backtest_runs_max_attempts_check CHECK ((max_attempts > 0)),
    CONSTRAINT backtest_runs_non_running_lease_check CHECK (((status = 'running'::text) OR ((lease_owner IS NULL) AND (lease_expires_at IS NULL)))),
    CONSTRAINT backtest_runs_parameter_hash_check CHECK ((parameter_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT backtest_runs_queued_check CHECK (((status <> 'queued'::text) OR ((started_at IS NULL) AND (finished_at IS NULL) AND (next_retry_at IS NULL)))),
    CONSTRAINT backtest_runs_reason_check CHECK ((btrim(reason) <> ''::text)),
    CONSTRAINT backtest_runs_request_hash_check CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT backtest_runs_request_id_check CHECK ((btrim(request_id) <> ''::text)),
    CONSTRAINT backtest_runs_result_hash_check CHECK (((result_hash IS NULL) OR (result_hash ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT backtest_runs_retry_wait_check CHECK (((status <> 'retry_wait'::text) OR ((next_retry_at IS NOT NULL) AND (finished_at IS NULL)))),
    CONSTRAINT backtest_runs_running_lease_check CHECK (((status <> 'running'::text) OR ((started_at IS NOT NULL) AND (finished_at IS NULL) AND (btrim(COALESCE(lease_owner, ''::text)) <> ''::text) AND (lease_expires_at IS NOT NULL)))),
    CONSTRAINT backtest_runs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'retry_wait'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text, 'dead_letter'::text]))),
    CONSTRAINT backtest_runs_strategy_graph_hash_check CHECK ((strategy_graph_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT backtest_runs_succeeded_result_check CHECK (((status <> 'succeeded'::text) OR (result_hash IS NOT NULL))),
    CONSTRAINT backtest_runs_terminal_finished_check CHECK (((status <> ALL (ARRAY['succeeded'::text, 'failed'::text, 'cancelled'::text, 'dead_letter'::text])) OR (finished_at IS NOT NULL)))
);


--
-- Name: backtest_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.backtest_runs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.backtest_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: backtest_trades; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.backtest_trades (
    id bigint NOT NULL,
    run_id bigint NOT NULL,
    trade_sequence integer NOT NULL,
    signal_sequence integer,
    side text NOT NULL,
    requested_quantity numeric(38,18) NOT NULL,
    filled_quantity numeric(38,18) NOT NULL,
    remaining_quantity numeric(38,18) NOT NULL,
    fill_price numeric(38,18) NOT NULL,
    fee_paid numeric(38,18) NOT NULL,
    status text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT backtest_trades_check CHECK (((filled_quantity + remaining_quantity) = requested_quantity)),
    CONSTRAINT backtest_trades_check1 CHECK ((knowledge_at >= occurred_at)),
    CONSTRAINT backtest_trades_fee_paid_check CHECK ((fee_paid >= (0)::numeric)),
    CONSTRAINT backtest_trades_fill_price_check CHECK ((fill_price >= (0)::numeric)),
    CONSTRAINT backtest_trades_filled_quantity_check CHECK ((filled_quantity >= (0)::numeric)),
    CONSTRAINT backtest_trades_remaining_quantity_check CHECK ((remaining_quantity >= (0)::numeric)),
    CONSTRAINT backtest_trades_requested_quantity_check CHECK ((requested_quantity >= (0)::numeric)),
    CONSTRAINT backtest_trades_side_check CHECK ((side = ANY (ARRAY['buy'::text, 'sell'::text]))),
    CONSTRAINT backtest_trades_status_check CHECK ((status = ANY (ARRAY['filled'::text, 'partially_filled'::text, 'rejected'::text]))),
    CONSTRAINT backtest_trades_trade_sequence_check CHECK ((trade_sequence > 0))
);


--
-- Name: backtest_trades_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.backtest_trades ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.backtest_trades_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: bot_definitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bot_definitions (
    id bigint NOT NULL,
    owner_id text NOT NULL,
    name text NOT NULL,
    strategy_version_id bigint NOT NULL,
    portfolio_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    created_by text NOT NULL,
    reason text NOT NULL,
    CONSTRAINT bot_definitions_created_by_check CHECK ((created_by <> ''::text)),
    CONSTRAINT bot_definitions_reason_check CHECK ((reason <> ''::text))
);


--
-- Name: bot_definitions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bot_definitions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bot_definitions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bot_definitions_id_seq OWNED BY public.bot_definitions.id;


--
-- Name: bot_instances; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bot_instances (
    id bigint NOT NULL,
    bot_definition_id bigint NOT NULL,
    strategy_version_id bigint NOT NULL,
    portfolio_policy_id bigint NOT NULL,
    backtest_run_id bigint,
    stage text DEFAULT 'draft'::text NOT NULL,
    previous_stage text,
    execution_mode text NOT NULL,
    started_at timestamp with time zone,
    stopped_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    created_by text NOT NULL,
    reason text NOT NULL,
    CONSTRAINT bot_instances_check CHECK (((stopped_at IS NULL) OR (started_at IS NULL) OR (stopped_at >= started_at))),
    CONSTRAINT bot_instances_created_by_check CHECK ((created_by <> ''::text)),
    CONSTRAINT bot_instances_execution_mode_check CHECK ((execution_mode = ANY (ARRAY['paper'::text, 'shadow'::text]))),
    CONSTRAINT bot_instances_previous_stage_check CHECK (((previous_stage IS NULL) OR (previous_stage = ANY (ARRAY['draft'::text, 'backtest'::text, 'paper'::text, 'shadow'::text])))),
    CONSTRAINT bot_instances_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT bot_instances_stage_check CHECK ((stage = ANY (ARRAY['draft'::text, 'backtest'::text, 'paper'::text, 'shadow'::text, 'paused'::text, 'stopped'::text, 'faulted'::text])))
);


--
-- Name: bot_instances_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bot_instances_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bot_instances_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bot_instances_id_seq OWNED BY public.bot_instances.id;


--
-- Name: bot_state_transitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bot_state_transitions (
    id bigint NOT NULL,
    bot_instance_id bigint NOT NULL,
    from_stage text,
    to_stage text NOT NULL,
    request_id text NOT NULL,
    actor_id text NOT NULL,
    reason text NOT NULL,
    occurred_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT bot_state_transitions_actor_id_check CHECK ((actor_id <> ''::text)),
    CONSTRAINT bot_state_transitions_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text)),
    CONSTRAINT bot_state_transitions_from_stage_check CHECK (((from_stage IS NULL) OR (from_stage = ANY (ARRAY['draft'::text, 'backtest'::text, 'paper'::text, 'shadow'::text, 'paused'::text, 'stopped'::text, 'faulted'::text])))),
    CONSTRAINT bot_state_transitions_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT bot_state_transitions_to_stage_check CHECK ((to_stage = ANY (ARRAY['draft'::text, 'backtest'::text, 'paper'::text, 'shadow'::text, 'paused'::text, 'stopped'::text, 'faulted'::text])))
);


--
-- Name: bot_state_transitions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bot_state_transitions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bot_state_transitions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bot_state_transitions_id_seq OWNED BY public.bot_state_transitions.id;


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
    CONSTRAINT candle_aggregation_job_targets_unit_ck CHECK ((candle_unit = ANY (ARRAY['3m'::text, '5m'::text, '10m'::text, '15m'::text, '30m'::text, '1h'::text, '4h'::text, '1d'::text, '1w'::text, '1M'::text])))
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
-- Name: candle_rollup_invalidations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candle_rollup_invalidations (
    id bigint NOT NULL,
    idempotency_key text NOT NULL,
    market_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    candle_unit text NOT NULL,
    calculation_version text NOT NULL,
    range_start_at timestamp with time zone NOT NULL,
    range_end_at timestamp with time zone NOT NULL,
    output_bucket_count integer NOT NULL,
    source_revision_ids bigint[] NOT NULL,
    source_revision_through_id bigint NOT NULL,
    quality_event_through_id bigint,
    coverage_snapshot jsonb DEFAULT '[]'::jsonb NOT NULL,
    coverage_snapshot_hash text NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT candle_rollup_invalidations_candle_unit_check CHECK ((candle_unit = ANY (ARRAY['3m'::text, '5m'::text, '10m'::text, '15m'::text, '30m'::text, '1h'::text, '4h'::text, '1d'::text, '1w'::text, '1M'::text]))),
    CONSTRAINT candle_rollup_invalidations_check CHECK ((range_start_at < range_end_at)),
    CONSTRAINT candle_rollup_invalidations_coverage_snapshot_hash_check CHECK ((coverage_snapshot_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT candle_rollup_invalidations_output_bucket_count_check CHECK (((output_bucket_count >= 1) AND (output_bucket_count <= 512)))
);


--
-- Name: candle_rollup_invalidations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.candle_rollup_invalidations ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.candle_rollup_invalidations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: candle_rollup_recompute_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candle_rollup_recompute_jobs (
    id bigint NOT NULL,
    invalidation_id bigint NOT NULL,
    idempotency_key text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 5 NOT NULL,
    next_retry_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    lease_owner text,
    lease_expires_at timestamp with time zone,
    processing_source_revision_through_id bigint,
    processing_quality_event_through_id bigint,
    rows_written integer DEFAULT 0 NOT NULL,
    last_error_code text,
    dead_letter_reason text,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT candle_rollup_recompute_jobs_attempt_count_check CHECK ((attempt_count >= 0)),
    CONSTRAINT candle_rollup_recompute_jobs_check CHECK (((status = 'running'::text) = ((lease_owner IS NOT NULL) AND (lease_expires_at IS NOT NULL)))),
    CONSTRAINT candle_rollup_recompute_jobs_max_attempts_check CHECK ((max_attempts > 0)),
    CONSTRAINT candle_rollup_recompute_jobs_rows_written_check CHECK ((rows_written >= 0)),
    CONSTRAINT candle_rollup_recompute_jobs_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'retry_wait'::text, 'succeeded'::text, 'dead_letter'::text, 'cancelled'::text])))
);


--
-- Name: candle_rollup_recompute_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.candle_rollup_recompute_jobs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.candle_rollup_recompute_jobs_id_seq
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
    calculation_version text DEFAULT 'candle-rollup-v2'::text NOT NULL,
    source_as_of timestamp with time zone NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    input_content_hash text NOT NULL,
    input_revision_ids bigint[] DEFAULT '{}'::bigint[] NOT NULL,
    quality text DEFAULT 'unverified'::text NOT NULL,
    id bigint NOT NULL,
    source_revision_through_id bigint DEFAULT 0 NOT NULL,
    quality_event_through_id bigint,
    coverage_snapshot_hash text NOT NULL,
    result_content_hash text NOT NULL,
    CONSTRAINT candle_rollups_completeness_ck CHECK ((completeness = ANY (ARRAY['complete'::text, 'partial'::text, 'empty'::text]))),
    CONSTRAINT candle_rollups_coverage_hash_ck CHECK ((coverage_snapshot_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT candle_rollups_hash_ck CHECK ((input_content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT candle_rollups_quality_ck CHECK ((quality = ANY (ARRAY['available'::text, 'no_trade'::text, 'missing'::text, 'unavailable'::text, 'unverified'::text]))),
    CONSTRAINT candle_rollups_result_hash_ck CHECK ((result_content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT candle_rollups_unit_ck CHECK ((candle_unit = ANY (ARRAY['3m'::text, '5m'::text, '10m'::text, '15m'::text, '30m'::text, '1h'::text, '4h'::text, '1d'::text, '1w'::text, '1M'::text])))
);


--
-- Name: candle_rollups_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.candle_rollups ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.candle_rollups_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: capital_allocations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.capital_allocations (
    id bigint NOT NULL,
    portfolio_policy_id bigint NOT NULL,
    scope_type text NOT NULL,
    scope_key text NOT NULL,
    allocation_pct numeric(20,10) NOT NULL,
    max_notional numeric(38,18),
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT capital_allocations_allocation_pct_check CHECK (((allocation_pct >= (0)::numeric) AND (allocation_pct <= (1)::numeric))),
    CONSTRAINT capital_allocations_max_notional_check CHECK (((max_notional IS NULL) OR (max_notional >= (0)::numeric))),
    CONSTRAINT capital_allocations_scope_key_check CHECK ((scope_key <> ''::text)),
    CONSTRAINT capital_allocations_scope_type_check CHECK ((scope_type = ANY (ARRAY['global'::text, 'instrument'::text, 'strategy'::text, 'bot'::text])))
);


--
-- Name: capital_allocations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.capital_allocations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: capital_allocations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.capital_allocations_id_seq OWNED BY public.capital_allocations.id;


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
-- Name: collection_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_policies (
    id bigint NOT NULL,
    exchange text NOT NULL,
    quote_currency text NOT NULL,
    name text NOT NULL,
    default_start_at timestamp with time zone,
    lookback_years integer,
    retention_days integer,
    priority integer DEFAULT 100 NOT NULL,
    auto_include_new_markets boolean DEFAULT true NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT collection_policies_lookback_ck CHECK (((lookback_years IS NULL) OR (lookback_years > 0))),
    CONSTRAINT collection_policies_priority_ck CHECK (((priority >= 1) AND (priority <= 1000))),
    CONSTRAINT collection_policies_range_ck CHECK (((default_start_at IS NOT NULL) <> (lookback_years IS NOT NULL))),
    CONSTRAINT collection_policies_retention_ck CHECK (((retention_days IS NULL) OR (retention_days > 0))),
    CONSTRAINT collection_policies_status_ck CHECK ((status = ANY (ARRAY['active'::text, 'paused'::text])))
);


--
-- Name: collection_policies_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.collection_policies ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.collection_policies_id_seq
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
    worker_role text,
    run_key text,
    request_id text,
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
-- Name: collection_subscription_desires; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_subscription_desires (
    target_spec_id bigint NOT NULL,
    desired_state text NOT NULL,
    generation bigint DEFAULT 1 NOT NULL,
    applied_generation bigint,
    connection_id text,
    last_applied_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT collection_subscription_desires_state_ck CHECK ((desired_state = ANY (ARRAY['subscribed'::text, 'unsubscribed'::text])))
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
-- Name: collection_target_specs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collection_target_specs (
    id bigint NOT NULL,
    policy_id bigint NOT NULL,
    market_id bigint NOT NULL,
    legacy_target_id bigint,
    data_type text NOT NULL,
    candle_unit text,
    range_start_at timestamp with time zone NOT NULL,
    retention_days integer,
    priority integer NOT NULL,
    continuous boolean DEFAULT true NOT NULL,
    auto_managed boolean DEFAULT true NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    excluded_by text,
    exclusion_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    state_reason text,
    CONSTRAINT collection_target_specs_candle_unit_ck CHECK ((((data_type = 'source_candle'::text) AND (candle_unit = ANY (ARRAY['1m'::text, '1d'::text]))) OR ((data_type <> 'source_candle'::text) AND (candle_unit IS NULL)))),
    CONSTRAINT collection_target_specs_data_type_ck CHECK ((data_type = ANY (ARRAY['source_candle'::text, 'trade_event'::text, 'orderbook_snapshot'::text, 'ticker_snapshot'::text]))),
    CONSTRAINT collection_target_specs_priority_ck CHECK (((priority >= 1) AND (priority <= 1000))),
    CONSTRAINT collection_target_specs_retention_ck CHECK (((retention_days IS NULL) OR (retention_days > 0))),
    CONSTRAINT collection_target_specs_state_reason_ck CHECK ((((status = 'active'::text) AND (state_reason IS NULL)) OR ((status = 'paused'::text) AND (state_reason IS NOT NULL) AND (state_reason = ANY (ARRAY['catalog_missing'::text, 'market_inactive'::text, 'operator_paused'::text, 'policy_data_type_disabled'::text]))) OR ((status = 'excluded'::text) AND (state_reason IS NOT NULL) AND (state_reason = 'operator_excluded'::text)))),
    CONSTRAINT collection_target_specs_status_ck CHECK ((status = ANY (ARRAY['active'::text, 'paused'::text, 'excluded'::text])))
);


--
-- Name: COLUMN collection_target_specs.state_reason; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.collection_target_specs.state_reason IS '상태 원인: catalog_missing, market_inactive, operator_paused, operator_excluded, policy_data_type_disabled';


--
-- Name: collection_target_specs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.collection_target_specs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.collection_target_specs_id_seq
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
    CONSTRAINT collection_worker_heartbeats_status_ck CHECK ((status = ANY (ARRAY['running'::text, 'gated'::text, 'failed'::text]))),
    CONSTRAINT collection_worker_heartbeats_worker_type_ck CHECK ((worker_type = ANY (ARRAY['realtime_collection'::text, 'backfill_collection'::text, 'candle_aggregation'::text, 'market_sync'::text])))
);


--
-- Name: command_idempotency_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.command_idempotency_records (
    id bigint NOT NULL,
    scope text NOT NULL,
    idempotency_key text NOT NULL,
    request_id text NOT NULL,
    actor_id text NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    payload_hash text NOT NULL,
    result_payload jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone
);


--
-- Name: command_idempotency_records_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.command_idempotency_records ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.command_idempotency_records_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: coverage_intervals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.coverage_intervals (
    id bigint NOT NULL,
    target_spec_id bigint NOT NULL,
    range_start_at timestamp with time zone NOT NULL,
    range_end_at timestamp with time zone NOT NULL,
    status text NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    fetch_manifest_id bigint,
    assessed_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT coverage_intervals_range_ck CHECK ((range_start_at < range_end_at)),
    CONSTRAINT coverage_intervals_status_ck CHECK ((status = ANY (ARRAY['available'::text, 'no_trade'::text, 'missing'::text, 'unavailable'::text, 'unverified'::text])))
);


--
-- Name: coverage_intervals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.coverage_intervals ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.coverage_intervals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: data_quality_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_quality_events (
    id bigint NOT NULL,
    target_spec_id bigint NOT NULL,
    event_type text NOT NULL,
    previous_status text,
    new_status text NOT NULL,
    range_start_at timestamp with time zone NOT NULL,
    range_end_at timestamp with time zone NOT NULL,
    fingerprint text NOT NULL,
    evidence jsonb NOT NULL,
    detected_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    fetch_manifest_id bigint,
    CONSTRAINT data_quality_events_new_status_ck CHECK ((new_status = ANY (ARRAY['available'::text, 'no_trade'::text, 'missing'::text, 'unavailable'::text, 'unverified'::text]))),
    CONSTRAINT data_quality_events_previous_status_ck CHECK (((previous_status IS NULL) OR (previous_status = ANY (ARRAY['available'::text, 'no_trade'::text, 'missing'::text, 'unavailable'::text, 'unverified'::text]))))
);


--
-- Name: data_quality_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.data_quality_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.data_quality_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dataset_build_coverage_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dataset_build_coverage_snapshots (
    id bigint NOT NULL,
    dataset_build_id bigint NOT NULL,
    dataset_build_series_id bigint NOT NULL,
    source_data_quality_event_id bigint,
    exchange text NOT NULL,
    market_code text NOT NULL,
    data_kind text NOT NULL,
    unit text NOT NULL,
    definition_set_hash text,
    calculation_version text NOT NULL,
    range_start_at timestamp with time zone NOT NULL,
    range_end_at timestamp with time zone NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    status text NOT NULL,
    observed_count integer NOT NULL,
    expected_count integer NOT NULL,
    evidence_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT dataset_build_coverage_snapshots_check CHECK ((range_start_at < range_end_at)),
    CONSTRAINT dataset_build_coverage_snapshots_data_kind_check CHECK ((data_kind = ANY (ARRAY['candle'::text, 'indicator'::text, 'market_statistic'::text, 'microstructure'::text]))),
    CONSTRAINT dataset_build_coverage_snapshots_definition_set_hash_check CHECK (((definition_set_hash IS NULL) OR (definition_set_hash ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT dataset_build_coverage_snapshots_evidence_hash_check CHECK ((evidence_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT dataset_build_coverage_snapshots_expected_count_check CHECK ((expected_count >= 0)),
    CONSTRAINT dataset_build_coverage_snapshots_observed_count_check CHECK ((observed_count >= 0)),
    CONSTRAINT dataset_build_coverage_snapshots_status_check CHECK ((status = ANY (ARRAY['available'::text, 'no_trade'::text, 'missing'::text, 'unavailable'::text, 'unverified'::text])))
);


--
-- Name: dataset_build_coverage_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.dataset_build_coverage_snapshots ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.dataset_build_coverage_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dataset_build_market_status_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dataset_build_market_status_snapshots (
    id bigint NOT NULL,
    dataset_build_id bigint NOT NULL,
    source_market_status_history_id bigint NOT NULL,
    market_id bigint NOT NULL,
    exchange text NOT NULL,
    market_code text NOT NULL,
    trading_status text NOT NULL,
    market_warning text NOT NULL,
    market_event jsonb NOT NULL,
    source_payload_checksum text NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    observed_at timestamp with time zone NOT NULL,
    snapshot_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT dataset_build_market_status_snapshots_check CHECK (((valid_to IS NULL) OR (valid_from < valid_to))),
    CONSTRAINT dataset_build_market_status_snapshots_snapshot_hash_check CHECK ((snapshot_hash ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: dataset_build_market_status_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.dataset_build_market_status_snapshots ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.dataset_build_market_status_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dataset_build_series; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dataset_build_series (
    id bigint NOT NULL,
    dataset_build_id bigint NOT NULL,
    market_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    data_kind text NOT NULL,
    unit text NOT NULL,
    definition_set_hash text,
    calculation_version text NOT NULL,
    fill_policy text NOT NULL,
    source_revision_through_id bigint,
    candle_rollup_through_id bigint,
    quality_event_through_id bigint,
    indicator_materialization_through_id bigint,
    market_statistic_through_id bigint,
    microstructure_materialization_through_id bigint,
    market_status_history_through_id bigint,
    orderbook_snapshot_through_id bigint,
    trade_event_through_id bigint,
    source_receipt_through_id bigint,
    connection_quality_through_id bigint,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT dataset_build_series_check CHECK (((fill_policy = 'none'::text) OR (data_kind = 'candle'::text))),
    CONSTRAINT dataset_build_series_data_kind_check CHECK ((data_kind = ANY (ARRAY['candle'::text, 'indicator'::text, 'market_statistic'::text, 'microstructure'::text]))),
    CONSTRAINT dataset_build_series_definition_set_hash_check CHECK (((definition_set_hash IS NULL) OR (definition_set_hash ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT dataset_build_series_fill_policy_check CHECK ((fill_policy = ANY (ARRAY['none'::text, 'no_trade_carry_forward_v1'::text]))),
    CONSTRAINT dataset_build_series_unit_check CHECK ((unit <> ''::text))
);


--
-- Name: dataset_build_series_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.dataset_build_series ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.dataset_build_series_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dataset_builds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dataset_builds (
    id bigint NOT NULL,
    idempotency_key text NOT NULL,
    request_id text NOT NULL,
    actor_id text NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    reason text NOT NULL,
    frozen_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    request_hash text NOT NULL,
    schema_version text NOT NULL,
    as_of timestamp with time zone NOT NULL,
    input_start_at timestamp with time zone NOT NULL,
    output_start_at timestamp with time zone NOT NULL,
    end_at timestamp with time zone NOT NULL,
    fill_policy text NOT NULL,
    missing_policy text NOT NULL,
    ordering_policy text NOT NULL,
    request_payload jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    lease_owner text,
    lease_expires_at timestamp with time zone,
    lease_generation integer DEFAULT 0 NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    next_retry_at timestamp with time zone,
    last_error_code text,
    last_error_message text,
    dead_letter_reason text,
    dataset_version_id bigint,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    CONSTRAINT dataset_builds_attempt_count_check CHECK ((attempt_count >= 0)),
    CONSTRAINT dataset_builds_check CHECK (((input_start_at <= output_start_at) AND (output_start_at < end_at) AND (end_at <= as_of))),
    CONSTRAINT dataset_builds_check1 CHECK (((status = 'running'::text) = ((lease_owner IS NOT NULL) AND (lease_expires_at IS NOT NULL)))),
    CONSTRAINT dataset_builds_check2 CHECK (((status = 'retry_wait'::text) = (next_retry_at IS NOT NULL))),
    CONSTRAINT dataset_builds_check3 CHECK (((status = 'dead_letter'::text) = (dead_letter_reason IS NOT NULL))),
    CONSTRAINT dataset_builds_check4 CHECK (((status = 'succeeded'::text) = (dataset_version_id IS NOT NULL))),
    CONSTRAINT dataset_builds_fill_policy_check CHECK ((fill_policy = ANY (ARRAY['none'::text, 'no_trade_carry_forward_v1'::text]))),
    CONSTRAINT dataset_builds_lease_generation_check CHECK ((lease_generation >= 0)),
    CONSTRAINT dataset_builds_max_attempts_check CHECK ((max_attempts > 0)),
    CONSTRAINT dataset_builds_missing_policy_check CHECK ((missing_policy = ANY (ARRAY['fail'::text, 'null'::text, 'drop'::text]))),
    CONSTRAINT dataset_builds_request_hash_check CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT dataset_builds_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'retry_wait'::text, 'succeeded'::text, 'failed'::text, 'dead_letter'::text, 'cancelled'::text])))
);


--
-- Name: dataset_builds_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.dataset_builds ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.dataset_builds_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dataset_version_candles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dataset_version_candles (
    dataset_version_id bigint NOT NULL,
    dataset_version_series_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    unit text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    source_candle_revision_id bigint,
    candle_rollup_id bigint,
    quality text NOT NULL,
    content_hash text NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    source_as_of timestamp with time zone NOT NULL,
    CONSTRAINT dataset_version_candles_check CHECK (((((source_candle_revision_id IS NOT NULL))::integer + ((candle_rollup_id IS NOT NULL))::integer) = 1)),
    CONSTRAINT dataset_version_candles_content_hash_check CHECK ((content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT dataset_version_candles_quality_check CHECK ((quality = ANY (ARRAY['available'::text, 'no_trade'::text, 'missing'::text, 'unavailable'::text, 'unverified'::text])))
);


--
-- Name: dataset_version_coverage_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dataset_version_coverage_snapshots (
    dataset_version_id bigint NOT NULL,
    source_build_coverage_snapshot_id bigint NOT NULL,
    dataset_version_series_id bigint NOT NULL,
    source_data_quality_event_id bigint,
    exchange text NOT NULL,
    market_code text NOT NULL,
    data_kind text NOT NULL,
    unit text NOT NULL,
    definition_set_hash text,
    calculation_version text NOT NULL,
    range_start_at timestamp with time zone NOT NULL,
    range_end_at timestamp with time zone NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    status text NOT NULL,
    observed_count integer NOT NULL,
    expected_count integer NOT NULL,
    evidence_hash text NOT NULL,
    CONSTRAINT dataset_version_coverage_snapshots_definition_set_hash_check CHECK (((definition_set_hash IS NULL) OR (definition_set_hash ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT dataset_version_coverage_snapshots_evidence_hash_check CHECK ((evidence_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT dataset_version_coverage_snapshots_expected_count_check CHECK ((expected_count >= 0)),
    CONSTRAINT dataset_version_coverage_snapshots_observed_count_check CHECK ((observed_count >= 0)),
    CONSTRAINT dataset_version_coverage_snapshots_status_check CHECK ((status = ANY (ARRAY['available'::text, 'no_trade'::text, 'missing'::text, 'unavailable'::text, 'unverified'::text])))
);


--
-- Name: dataset_version_indicators; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dataset_version_indicators (
    dataset_version_id bigint NOT NULL,
    dataset_version_series_id bigint NOT NULL,
    indicator_materialization_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    unit text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    quality text NOT NULL,
    content_hash text NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    source_as_of timestamp with time zone NOT NULL,
    CONSTRAINT dataset_version_indicators_content_hash_check CHECK ((content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT dataset_version_indicators_quality_check CHECK ((quality = ANY (ARRAY['available'::text, 'no_trade'::text, 'missing'::text, 'unavailable'::text, 'unverified'::text])))
);


--
-- Name: dataset_version_market_statistics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dataset_version_market_statistics (
    dataset_version_id bigint NOT NULL,
    dataset_version_series_id bigint NOT NULL,
    market_statistic_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    unit text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    quality text NOT NULL,
    content_hash text NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    source_as_of timestamp with time zone NOT NULL,
    CONSTRAINT dataset_version_market_statistics_content_hash_check CHECK ((content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT dataset_version_market_statistics_quality_check CHECK ((quality = ANY (ARRAY['available'::text, 'no_trade'::text, 'missing'::text, 'unavailable'::text, 'unverified'::text])))
);


--
-- Name: dataset_version_market_status_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dataset_version_market_status_snapshots (
    dataset_version_id bigint NOT NULL,
    source_build_snapshot_id bigint NOT NULL,
    source_market_status_history_id bigint NOT NULL,
    market_id bigint NOT NULL,
    exchange text NOT NULL,
    market_code text NOT NULL,
    trading_status text NOT NULL,
    market_warning text NOT NULL,
    market_event jsonb NOT NULL,
    source_payload_checksum text NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    observed_at timestamp with time zone NOT NULL,
    snapshot_hash text NOT NULL,
    CONSTRAINT dataset_version_market_status_snapshots_snapshot_hash_check CHECK ((snapshot_hash ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: dataset_version_microstructures; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dataset_version_microstructures (
    dataset_version_id bigint NOT NULL,
    dataset_version_series_id bigint NOT NULL,
    microstructure_materialization_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    unit text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    quality text NOT NULL,
    content_hash text NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    source_as_of timestamp with time zone NOT NULL,
    CONSTRAINT dataset_version_microstructures_content_hash_check CHECK ((content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT dataset_version_microstructures_quality_check CHECK ((quality = ANY (ARRAY['available'::text, 'no_trade'::text, 'missing'::text, 'unavailable'::text, 'unverified'::text]))),
    CONSTRAINT dataset_version_microstructures_unit_check CHECK ((unit = '1m'::text))
);


--
-- Name: dataset_version_series; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dataset_version_series (
    id bigint NOT NULL,
    dataset_version_id bigint NOT NULL,
    source_build_series_id bigint NOT NULL,
    market_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    data_kind text NOT NULL,
    unit text NOT NULL,
    definition_set_hash text,
    calculation_version text NOT NULL,
    source_revision_through_id bigint,
    candle_rollup_through_id bigint,
    quality_event_through_id bigint,
    indicator_materialization_through_id bigint,
    market_statistic_through_id bigint,
    microstructure_materialization_through_id bigint,
    market_status_history_through_id bigint,
    orderbook_snapshot_through_id bigint,
    trade_event_through_id bigint,
    source_receipt_through_id bigint,
    connection_quality_through_id bigint,
    member_count integer NOT NULL,
    members_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT dataset_version_series_data_kind_check CHECK ((data_kind = ANY (ARRAY['candle'::text, 'indicator'::text, 'market_statistic'::text, 'microstructure'::text]))),
    CONSTRAINT dataset_version_series_definition_set_hash_check CHECK (((definition_set_hash IS NULL) OR (definition_set_hash ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT dataset_version_series_member_count_check CHECK ((member_count >= 0)),
    CONSTRAINT dataset_version_series_members_hash_check CHECK ((members_hash ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: dataset_version_series_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.dataset_version_series ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.dataset_version_series_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dataset_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dataset_versions (
    id bigint NOT NULL,
    schema_version text NOT NULL,
    as_of timestamp with time zone NOT NULL,
    input_start_at timestamp with time zone NOT NULL,
    output_start_at timestamp with time zone NOT NULL,
    end_at timestamp with time zone NOT NULL,
    fill_policy text NOT NULL,
    missing_policy text NOT NULL,
    ordering_policy text NOT NULL,
    selection_hash text NOT NULL,
    manifest_hash text NOT NULL,
    market_status_hash text NOT NULL,
    coverage_hash text NOT NULL,
    content_hash text NOT NULL,
    sealed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT dataset_versions_check CHECK (((input_start_at <= output_start_at) AND (output_start_at < end_at) AND (end_at <= as_of))),
    CONSTRAINT dataset_versions_content_hash_check CHECK ((content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT dataset_versions_coverage_hash_check CHECK ((coverage_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT dataset_versions_fill_policy_check CHECK ((fill_policy = ANY (ARRAY['none'::text, 'no_trade_carry_forward_v1'::text]))),
    CONSTRAINT dataset_versions_manifest_hash_check CHECK ((manifest_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT dataset_versions_market_status_hash_check CHECK ((market_status_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT dataset_versions_missing_policy_check CHECK ((missing_policy = ANY (ARRAY['fail'::text, 'null'::text, 'drop'::text]))),
    CONSTRAINT dataset_versions_selection_hash_check CHECK ((selection_hash ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: dataset_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.dataset_versions ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.dataset_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: exchange_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exchange_accounts (
    id bigint NOT NULL,
    exchange text NOT NULL,
    account_stable_id text NOT NULL,
    label text NOT NULL,
    status text DEFAULT 'live_disabled'::text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    created_by text NOT NULL,
    reason text NOT NULL,
    CONSTRAINT exchange_accounts_account_stable_id_check CHECK ((account_stable_id ~ '^[A-Za-z0-9:_-]{3,128}$'::text)),
    CONSTRAINT exchange_accounts_created_by_check CHECK ((created_by <> ''::text)),
    CONSTRAINT exchange_accounts_exchange_check CHECK ((exchange = 'upbit'::text)),
    CONSTRAINT exchange_accounts_label_check CHECK ((label <> ''::text)),
    CONSTRAINT exchange_accounts_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT exchange_accounts_status_check CHECK ((status = ANY (ARRAY['live_disabled'::text, 'live_ready'::text, 'live_enabled'::text, 'revoked'::text])))
);


--
-- Name: exchange_accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.exchange_accounts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: exchange_accounts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.exchange_accounts_id_seq OWNED BY public.exchange_accounts.id;


--
-- Name: exchange_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exchange_orders (
    id bigint NOT NULL,
    order_intent_id bigint NOT NULL,
    execution_mode text NOT NULL,
    simulated_order_key text NOT NULL,
    status text DEFAULT 'pending_submit'::text NOT NULL,
    submitted_at timestamp with time zone,
    reconciled_at timestamp with time zone,
    raw_payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT exchange_orders_execution_mode_check CHECK ((execution_mode = ANY (ARRAY['paper'::text, 'shadow'::text, 'live'::text]))),
    CONSTRAINT exchange_orders_raw_payload_check CHECK ((jsonb_typeof(raw_payload) = 'object'::text)),
    CONSTRAINT exchange_orders_simulated_order_key_check CHECK ((simulated_order_key <> ''::text)),
    CONSTRAINT exchange_orders_status_check CHECK ((status = ANY (ARRAY['pending_submit'::text, 'wait'::text, 'watch'::text, 'trade'::text, 'partially_filled'::text, 'done'::text, 'cancel'::text, 'prevented'::text, 'rejected'::text, 'outcome_unknown'::text, 'reconciled'::text])))
);


--
-- Name: exchange_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.exchange_orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: exchange_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.exchange_orders_id_seq OWNED BY public.exchange_orders.id;


--
-- Name: fetch_manifests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fetch_manifests (
    id bigint NOT NULL,
    target_spec_id bigint,
    collection_run_id bigint,
    source text NOT NULL,
    endpoint text NOT NULL,
    request_parameters jsonb NOT NULL,
    request_fingerprint text NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    responded_at timestamp with time zone,
    response_status integer,
    response_checksum text,
    collector_version text NOT NULL,
    schema_version text NOT NULL,
    outcome text NOT NULL,
    error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    response_payload jsonb,
    error_message text,
    CONSTRAINT fetch_manifests_outcome_ck CHECK ((outcome = ANY (ARRAY['succeeded'::text, 'rate_limited'::text, 'blocked'::text, 'failed'::text, 'unknown'::text]))),
    CONSTRAINT fetch_manifests_source_ck CHECK ((source = ANY (ARRAY['UPBIT'::text, 'LEGACY'::text])))
);


--
-- Name: fetch_manifests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.fetch_manifests ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.fetch_manifests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: indicator_definition_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.indicator_definition_versions (
    id bigint NOT NULL,
    definition_id bigint NOT NULL,
    version integer NOT NULL,
    definition_hash text NOT NULL,
    algorithm text NOT NULL,
    parameters jsonb NOT NULL,
    decimal_precision integer NOT NULL,
    rounding text NOT NULL,
    implementation_version text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT indicator_definition_versions_decimal_precision_check CHECK ((decimal_precision = 50)),
    CONSTRAINT indicator_definition_versions_definition_hash_check CHECK ((definition_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT indicator_definition_versions_rounding_check CHECK ((rounding = 'ROUND_HALF_EVEN'::text)),
    CONSTRAINT indicator_definition_versions_version_check CHECK ((version > 0))
);


--
-- Name: indicator_definition_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.indicator_definition_versions ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.indicator_definition_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: indicator_definitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.indicator_definitions (
    id bigint NOT NULL,
    indicator_key text NOT NULL,
    display_name text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: indicator_definitions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.indicator_definitions ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.indicator_definitions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: indicator_invalidations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.indicator_invalidations (
    id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    candle_unit text NOT NULL,
    changed_rollup_id bigint,
    changed_source_revision_id bigint,
    changed_quality_event_id bigint,
    changed_rollup_invalidation_id bigint,
    impact_start_at timestamp with time zone NOT NULL,
    impact_end_at timestamp with time zone,
    progress_at timestamp with time zone,
    indicator_checkpoint_state jsonb,
    statistic_checkpoint_state jsonb,
    source_revision_through_id bigint NOT NULL,
    quality_event_through_id bigint,
    knowledge_at timestamp with time zone NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 5 NOT NULL,
    next_retry_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    lease_owner text,
    lease_expires_at timestamp with time zone,
    lease_generation integer DEFAULT 0 NOT NULL,
    last_error_code text,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT indicator_invalidations_check CHECK (((status = 'running'::text) = ((lease_owner IS NOT NULL) AND (lease_expires_at IS NOT NULL)))),
    CONSTRAINT indicator_invalidations_check1 CHECK (((indicator_checkpoint_state IS NULL) = (statistic_checkpoint_state IS NULL))),
    CONSTRAINT indicator_invalidations_check2 CHECK (((progress_at IS NULL) = (indicator_checkpoint_state IS NULL))),
    CONSTRAINT indicator_invalidations_check3 CHECK (((((((changed_rollup_id IS NOT NULL))::integer + ((changed_source_revision_id IS NOT NULL))::integer) + ((changed_quality_event_id IS NOT NULL))::integer) + ((changed_rollup_invalidation_id IS NOT NULL))::integer) = 1)),
    CONSTRAINT indicator_invalidations_lease_generation_check CHECK ((lease_generation >= 0)),
    CONSTRAINT indicator_invalidations_max_attempts_check CHECK ((max_attempts > 0)),
    CONSTRAINT indicator_invalidations_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'succeeded'::text, 'retry_wait'::text, 'dead_letter'::text])))
);


--
-- Name: indicator_invalidations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.indicator_invalidations ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.indicator_invalidations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: indicator_materializations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.indicator_materializations (
    id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    market_id bigint NOT NULL,
    candle_unit text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    definition_set_hash text NOT NULL,
    parent_materialization_id bigint,
    current_rollup_id bigint,
    current_source_revision_id bigint,
    lineage_hash text NOT NULL,
    source_revision_through_id bigint NOT NULL,
    quality_event_through_id bigint,
    knowledge_at timestamp with time zone NOT NULL,
    source_as_of timestamp with time zone NOT NULL,
    calculation_status text NOT NULL,
    checkpoint_state jsonb NOT NULL,
    content_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT indicator_materializations_calculation_status_check CHECK ((calculation_status = ANY (ARRAY['warming_up'::text, 'ready'::text, 'missing'::text]))),
    CONSTRAINT indicator_materializations_check CHECK (((((current_rollup_id IS NOT NULL))::integer + ((current_source_revision_id IS NOT NULL))::integer) = 1)),
    CONSTRAINT indicator_materializations_content_hash_check CHECK ((content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT indicator_materializations_definition_set_hash_check CHECK ((definition_set_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT indicator_materializations_lineage_hash_check CHECK ((lineage_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT indicator_materializations_source_revision_through_id_check CHECK ((source_revision_through_id >= 0))
);


--
-- Name: indicator_materializations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.indicator_materializations ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.indicator_materializations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: indicator_value_rollups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.indicator_value_rollups (
    indicator_value_id bigint NOT NULL,
    candle_rollup_id bigint NOT NULL
);


--
-- Name: indicator_values; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.indicator_values (
    id bigint NOT NULL,
    materialization_id bigint NOT NULL,
    definition_version_id bigint NOT NULL,
    value_name text NOT NULL,
    value numeric,
    calculation_status text NOT NULL,
    parent_value_id bigint,
    CONSTRAINT indicator_values_calculation_status_check CHECK ((calculation_status = ANY (ARRAY['warming_up'::text, 'ready'::text, 'missing'::text])))
);


--
-- Name: indicator_values_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.indicator_values ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.indicator_values_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
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
-- Name: kill_switches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kill_switches (
    id bigint NOT NULL,
    scope_type text NOT NULL,
    scope_key text NOT NULL,
    state text NOT NULL,
    sequence bigint NOT NULL,
    actor_id text NOT NULL,
    reason text NOT NULL,
    open_order_policy text NOT NULL,
    occurred_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT kill_switches_actor_id_check CHECK ((actor_id <> ''::text)),
    CONSTRAINT kill_switches_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text)),
    CONSTRAINT kill_switches_open_order_policy_check CHECK ((open_order_policy = ANY (ARRAY['leave_open'::text, 'cancel_open'::text]))),
    CONSTRAINT kill_switches_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT kill_switches_scope_key_check CHECK ((scope_key <> ''::text)),
    CONSTRAINT kill_switches_scope_type_check CHECK ((scope_type = ANY (ARRAY['global'::text, 'portfolio'::text, 'bot'::text, 'account'::text]))),
    CONSTRAINT kill_switches_sequence_check CHECK ((sequence >= 1)),
    CONSTRAINT kill_switches_state_check CHECK ((state = ANY (ARRAY['armed'::text, 'released'::text])))
);


--
-- Name: kill_switches_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kill_switches_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kill_switches_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kill_switches_id_seq OWNED BY public.kill_switches.id;


--
-- Name: live_order_identifiers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.live_order_identifiers (
    id bigint NOT NULL,
    exchange_account_id bigint NOT NULL,
    order_intent_id bigint NOT NULL,
    idempotency_key text NOT NULL,
    identifier text NOT NULL,
    status text DEFAULT 'reserved'::text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    created_by text NOT NULL,
    reason text NOT NULL,
    CONSTRAINT live_order_identifiers_created_by_check CHECK ((created_by <> ''::text)),
    CONSTRAINT live_order_identifiers_idempotency_key_check CHECK ((idempotency_key <> ''::text)),
    CONSTRAINT live_order_identifiers_identifier_check CHECK ((identifier ~ '^gm1_[a-z2-7]{52}$'::text)),
    CONSTRAINT live_order_identifiers_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT live_order_identifiers_status_check CHECK ((status = ANY (ARRAY['reserved'::text, 'submitted'::text, 'outcome_unknown'::text, 'retired'::text])))
);


--
-- Name: live_order_identifiers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.live_order_identifiers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: live_order_identifiers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.live_order_identifiers_id_seq OWNED BY public.live_order_identifiers.id;


--
-- Name: market_statistics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_statistics (
    id bigint NOT NULL,
    market_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    "interval" text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    calculation_version text NOT NULL,
    close_return_1 numeric,
    realized_volatility_20 numeric,
    trade_volume numeric,
    trade_amount numeric,
    volatility_sample_count integer NOT NULL,
    input_completeness_ratio numeric NOT NULL,
    return_status text NOT NULL,
    volatility_status text NOT NULL,
    trade_status text NOT NULL,
    parent_statistic_id bigint,
    current_rollup_id bigint,
    current_source_revision_id bigint,
    source_revision_through_id bigint NOT NULL,
    quality_event_through_id bigint,
    source_as_of timestamp with time zone NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    lineage_hash text NOT NULL,
    checkpoint_state jsonb NOT NULL,
    content_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT market_statistics_check CHECK (((((current_rollup_id IS NOT NULL))::integer + ((current_source_revision_id IS NOT NULL))::integer) = 1)),
    CONSTRAINT market_statistics_content_hash_check CHECK ((content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT market_statistics_input_completeness_ratio_check CHECK (((input_completeness_ratio >= (0)::numeric) AND (input_completeness_ratio <= (1)::numeric))),
    CONSTRAINT market_statistics_lineage_hash_check CHECK ((lineage_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT market_statistics_return_status_check CHECK ((return_status = ANY (ARRAY['warming_up'::text, 'ready'::text, 'missing'::text]))),
    CONSTRAINT market_statistics_source_revision_through_id_check CHECK ((source_revision_through_id >= 0)),
    CONSTRAINT market_statistics_trade_status_check CHECK ((trade_status = ANY (ARRAY['warming_up'::text, 'ready'::text, 'missing'::text]))),
    CONSTRAINT market_statistics_volatility_sample_count_check CHECK (((volatility_sample_count >= 0) AND (volatility_sample_count <= 20))),
    CONSTRAINT market_statistics_volatility_status_check CHECK ((volatility_status = ANY (ARRAY['warming_up'::text, 'ready'::text, 'missing'::text])))
);


--
-- Name: market_statistics_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.market_statistics ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.market_statistics_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: market_status_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market_status_history (
    id bigint NOT NULL,
    market_id bigint NOT NULL,
    trading_status text NOT NULL,
    market_warning text NOT NULL,
    market_event jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_payload_checksum text NOT NULL,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    observed_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    fetch_manifest_id bigint,
    CONSTRAINT market_status_history_range_ck CHECK (((valid_to IS NULL) OR (valid_from < valid_to))),
    CONSTRAINT market_status_history_status_ck CHECK ((trading_status = ANY (ARRAY['active'::text, 'inactive'::text, 'delisted'::text, 'unknown'::text])))
);


--
-- Name: market_status_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.market_status_history ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.market_status_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: markets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.markets (
    id bigint NOT NULL,
    exchange text NOT NULL,
    market_code text NOT NULL,
    quote_currency text NOT NULL,
    base_asset text NOT NULL,
    korean_name text NOT NULL,
    english_name text NOT NULL,
    legacy_instrument_id bigint,
    first_observed_at timestamp with time zone NOT NULL,
    last_observed_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT markets_exchange_ck CHECK ((exchange = 'UPBIT'::text)),
    CONSTRAINT markets_observation_range_ck CHECK ((first_observed_at <= last_observed_at))
);


--
-- Name: markets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.markets ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.markets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: microstructure_definition_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.microstructure_definition_versions (
    id bigint NOT NULL,
    calculation_version text NOT NULL,
    definition_hash text NOT NULL,
    bucket_unit text NOT NULL,
    algorithms jsonb NOT NULL,
    decimal_precision integer NOT NULL,
    rounding text NOT NULL,
    implementation_version text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT microstructure_definition_versions_bucket_unit_check CHECK ((bucket_unit = '1m'::text)),
    CONSTRAINT microstructure_definition_versions_decimal_precision_check CHECK ((decimal_precision = 50)),
    CONSTRAINT microstructure_definition_versions_definition_hash_check CHECK ((definition_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT microstructure_definition_versions_rounding_check CHECK ((rounding = 'ROUND_HALF_EVEN'::text))
);


--
-- Name: microstructure_definition_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.microstructure_definition_versions ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.microstructure_definition_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: microstructure_invalidations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.microstructure_invalidations (
    id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    market_id bigint NOT NULL,
    bucket_start_at timestamp with time zone NOT NULL,
    changed_orderbook_snapshot_id bigint,
    changed_trade_event_id bigint,
    changed_source_candle_revision_id bigint,
    changed_quality_event_id bigint,
    changed_connection_quality_interval_id bigint,
    orderbook_snapshot_through_id bigint NOT NULL,
    trade_event_through_id bigint NOT NULL,
    source_receipt_through_id bigint NOT NULL,
    source_candle_revision_id bigint,
    quality_event_through_id bigint,
    connection_quality_through_id bigint DEFAULT 0 NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    source_as_of timestamp with time zone NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 5 NOT NULL,
    next_retry_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    lease_owner text,
    lease_expires_at timestamp with time zone,
    lease_generation integer DEFAULT 0 NOT NULL,
    last_error_code text,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT microstructure_invalidations_attempt_count_check CHECK ((attempt_count >= 0)),
    CONSTRAINT microstructure_invalidations_check CHECK (((status = 'running'::text) = ((lease_owner IS NOT NULL) AND (lease_expires_at IS NOT NULL)))),
    CONSTRAINT microstructure_invalidations_check1 CHECK ((((((((changed_orderbook_snapshot_id IS NOT NULL))::integer + ((changed_trade_event_id IS NOT NULL))::integer) + ((changed_source_candle_revision_id IS NOT NULL))::integer) + ((changed_quality_event_id IS NOT NULL))::integer) + ((changed_connection_quality_interval_id IS NOT NULL))::integer) >= 1)),
    CONSTRAINT microstructure_invalidations_connection_quality_through_i_check CHECK ((connection_quality_through_id >= 0)),
    CONSTRAINT microstructure_invalidations_lease_generation_check CHECK ((lease_generation >= 0)),
    CONSTRAINT microstructure_invalidations_max_attempts_check CHECK ((max_attempts > 0)),
    CONSTRAINT microstructure_invalidations_orderbook_snapshot_through_i_check CHECK ((orderbook_snapshot_through_id >= 0)),
    CONSTRAINT microstructure_invalidations_source_receipt_through_id_check CHECK ((source_receipt_through_id >= 0)),
    CONSTRAINT microstructure_invalidations_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'succeeded'::text, 'retry_wait'::text, 'dead_letter'::text]))),
    CONSTRAINT microstructure_invalidations_trade_event_through_id_check CHECK ((trade_event_through_id >= 0))
);


--
-- Name: microstructure_invalidations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.microstructure_invalidations ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.microstructure_invalidations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: microstructure_materialization_orderbooks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.microstructure_materialization_orderbooks (
    materialization_id bigint NOT NULL,
    orderbook_snapshot_id bigint NOT NULL,
    source_receipt_id bigint NOT NULL
);


--
-- Name: microstructure_materialization_trades; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.microstructure_materialization_trades (
    materialization_id bigint NOT NULL,
    trade_event_id bigint NOT NULL,
    source_receipt_id bigint NOT NULL
);


--
-- Name: microstructure_materializations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.microstructure_materializations (
    id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    market_id bigint NOT NULL,
    definition_version_id bigint NOT NULL,
    bucket_start_at timestamp with time zone NOT NULL,
    parent_materialization_id bigint,
    source_candle_revision_id bigint,
    orderbook_snapshot_through_id bigint NOT NULL,
    trade_event_through_id bigint NOT NULL,
    source_receipt_through_id bigint NOT NULL,
    quality_event_through_id bigint,
    connection_quality_through_id bigint NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    source_as_of timestamp with time zone NOT NULL,
    input_lineage_hash text NOT NULL,
    content_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT microstructure_materializati_orderbook_snapshot_through_i_check CHECK ((orderbook_snapshot_through_id >= 0)),
    CONSTRAINT microstructure_materializations_content_hash_check CHECK ((content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT microstructure_materializations_input_lineage_hash_check CHECK ((input_lineage_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT microstructure_materializations_source_receipt_through_id_check CHECK ((source_receipt_through_id >= 0)),
    CONSTRAINT microstructure_materializations_trade_event_through_id_check CHECK ((trade_event_through_id >= 0))
);


--
-- Name: microstructure_materializations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.microstructure_materializations ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.microstructure_materializations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: microstructure_statistics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.microstructure_statistics (
    id bigint NOT NULL,
    materialization_id bigint NOT NULL,
    parent_statistic_id bigint,
    closing_orderbook_snapshot_id bigint,
    spread numeric,
    spread_bps numeric,
    bid_depth_10 numeric,
    ask_depth_10 numeric,
    orderbook_imbalance_10 numeric,
    trade_count integer,
    trade_intensity_per_minute numeric,
    volume_intensity_per_minute numeric,
    bid_count integer,
    ask_count integer,
    bid_volume numeric,
    ask_volume numeric,
    bid_ask_imbalance numeric,
    execution_strength numeric,
    orderbook_status text NOT NULL,
    orderbook_quality text NOT NULL,
    trade_status text NOT NULL,
    trade_quality text NOT NULL,
    execution_strength_status text NOT NULL,
    content_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT microstructure_statistics_ask_count_check CHECK (((ask_count IS NULL) OR (ask_count >= 0))),
    CONSTRAINT microstructure_statistics_bid_count_check CHECK (((bid_count IS NULL) OR (bid_count >= 0))),
    CONSTRAINT microstructure_statistics_content_hash_check CHECK ((content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT microstructure_statistics_execution_strength_status_check CHECK ((execution_strength_status = ANY (ARRAY['ready'::text, 'missing'::text, 'partial'::text, 'invalid'::text, 'undefined'::text]))),
    CONSTRAINT microstructure_statistics_orderbook_quality_check CHECK ((orderbook_quality = ANY (ARRAY['available'::text, 'no_trade'::text, 'missing'::text, 'unavailable'::text, 'unverified'::text]))),
    CONSTRAINT microstructure_statistics_orderbook_status_check CHECK ((orderbook_status = ANY (ARRAY['ready'::text, 'missing'::text, 'partial'::text, 'invalid'::text, 'undefined'::text]))),
    CONSTRAINT microstructure_statistics_trade_count_check CHECK (((trade_count IS NULL) OR (trade_count >= 0))),
    CONSTRAINT microstructure_statistics_trade_quality_check CHECK ((trade_quality = ANY (ARRAY['available'::text, 'no_trade'::text, 'missing'::text, 'unavailable'::text, 'unverified'::text]))),
    CONSTRAINT microstructure_statistics_trade_status_check CHECK ((trade_status = ANY (ARRAY['ready'::text, 'missing'::text, 'partial'::text, 'invalid'::text, 'undefined'::text])))
);


--
-- Name: microstructure_statistics_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.microstructure_statistics ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.microstructure_statistics_id_seq
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
-- Name: order_fills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.order_fills (
    id bigint NOT NULL,
    exchange_order_id bigint NOT NULL,
    fill_sequence integer NOT NULL,
    fill_source text NOT NULL,
    side text NOT NULL,
    filled_quantity numeric(38,18) NOT NULL,
    fill_price numeric(38,18) NOT NULL,
    fee_paid numeric(38,18) DEFAULT 0 NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT order_fills_check CHECK ((knowledge_at >= occurred_at)),
    CONSTRAINT order_fills_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text)),
    CONSTRAINT order_fills_fee_paid_check CHECK ((fee_paid >= (0)::numeric)),
    CONSTRAINT order_fills_fill_price_check CHECK ((fill_price > (0)::numeric)),
    CONSTRAINT order_fills_fill_sequence_check CHECK ((fill_sequence >= 1)),
    CONSTRAINT order_fills_fill_source_check CHECK ((fill_source = ANY (ARRAY['paper_simulator'::text, 'shadow_observation'::text, 'reconciliation'::text]))),
    CONSTRAINT order_fills_filled_quantity_check CHECK ((filled_quantity > (0)::numeric)),
    CONSTRAINT order_fills_side_check CHECK ((side = ANY (ARRAY['buy'::text, 'sell'::text])))
);


--
-- Name: order_fills_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.order_fills_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: order_fills_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.order_fills_id_seq OWNED BY public.order_fills.id;


--
-- Name: order_intents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.order_intents (
    id bigint NOT NULL,
    bot_instance_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    idempotency_key text NOT NULL,
    side text NOT NULL,
    order_type text NOT NULL,
    requested_quantity numeric(38,18),
    requested_notional numeric(38,18),
    limit_price numeric(38,18),
    status text DEFAULT 'created'::text NOT NULL,
    decision_input_hash text NOT NULL,
    risk_policy_version integer,
    risk_decision_reason text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    created_by text NOT NULL,
    reason text NOT NULL,
    CONSTRAINT order_intents_check CHECK (((requested_quantity IS NOT NULL) OR (requested_notional IS NOT NULL))),
    CONSTRAINT order_intents_created_by_check CHECK ((created_by <> ''::text)),
    CONSTRAINT order_intents_decision_input_hash_check CHECK ((decision_input_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT order_intents_limit_price_check CHECK (((limit_price IS NULL) OR (limit_price > (0)::numeric))),
    CONSTRAINT order_intents_order_type_check CHECK ((order_type = ANY (ARRAY['market'::text, 'limit'::text]))),
    CONSTRAINT order_intents_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT order_intents_requested_notional_check CHECK (((requested_notional IS NULL) OR (requested_notional > (0)::numeric))),
    CONSTRAINT order_intents_requested_quantity_check CHECK (((requested_quantity IS NULL) OR (requested_quantity > (0)::numeric))),
    CONSTRAINT order_intents_risk_policy_version_check CHECK (((risk_policy_version IS NULL) OR (risk_policy_version >= 1))),
    CONSTRAINT order_intents_side_check CHECK ((side = ANY (ARRAY['buy'::text, 'sell'::text]))),
    CONSTRAINT order_intents_status_check CHECK ((status = ANY (ARRAY['created'::text, 'risk_rejected'::text, 'approved'::text, 'paper_filled'::text, 'shadow_observed'::text, 'outcome_unknown'::text, 'reconciled'::text, 'cancelled'::text, 'completed'::text])))
);


--
-- Name: order_intents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.order_intents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: order_intents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.order_intents_id_seq OWNED BY public.order_intents.id;


--
-- Name: orderbook_snapshot_levels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.orderbook_snapshot_levels (
    snapshot_id bigint NOT NULL,
    level_index integer NOT NULL,
    ask_price numeric(38,18) NOT NULL,
    ask_size numeric(38,18) NOT NULL,
    bid_price numeric(38,18) NOT NULL,
    bid_size numeric(38,18) NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT orderbook_snapshot_levels_index_ck CHECK ((level_index >= 0)),
    CONSTRAINT orderbook_snapshot_levels_value_ck CHECK (((ask_price >= (0)::numeric) AND (ask_size >= (0)::numeric) AND (bid_price >= (0)::numeric) AND (bid_size >= (0)::numeric)))
);


--
-- Name: orderbook_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.orderbook_snapshots (
    id bigint NOT NULL,
    market_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    source text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone NOT NULL,
    stored_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    total_ask_size numeric(38,18) NOT NULL,
    total_bid_size numeric(38,18) NOT NULL,
    level_count integer NOT NULL,
    level numeric(38,18),
    stream_type text,
    payload_checksum text NOT NULL,
    fetch_manifest_id bigint,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    source_receipt_id bigint,
    CONSTRAINT orderbook_snapshots_level_count_ck CHECK ((level_count > 0)),
    CONSTRAINT orderbook_snapshots_payload_checksum_ck CHECK ((length(payload_checksum) = 64)),
    CONSTRAINT orderbook_snapshots_source_ck CHECK ((source = 'UPBIT'::text)),
    CONSTRAINT orderbook_snapshots_total_size_ck CHECK (((total_ask_size >= (0)::numeric) AND (total_bid_size >= (0)::numeric)))
);


--
-- Name: orderbook_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.orderbook_snapshots ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.orderbook_snapshots_id_seq
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
    market_id bigint,
    occurred_at timestamp with time zone,
    received_at timestamp with time zone,
    stored_at timestamp with time zone,
    knowledge_at timestamp with time zone,
    fetch_manifest_id bigint,
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
-- Name: p1_audit_recovery_gate; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.p1_audit_recovery_gate (
    singleton boolean DEFAULT true NOT NULL,
    recovery_required boolean DEFAULT false NOT NULL,
    detected_at timestamp with time zone,
    confirmed_at timestamp with time zone,
    confirmed_by text,
    backup_reference text,
    reason text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT p1_audit_recovery_gate_confirmation_ck CHECK ((((confirmed_at IS NULL) AND (confirmed_by IS NULL) AND (backup_reference IS NULL)) OR (recovery_required AND (confirmed_at IS NOT NULL) AND (confirmed_by IS NOT NULL) AND (btrim(confirmed_by) <> ''::text) AND (backup_reference IS NOT NULL) AND (btrim(backup_reference) <> ''::text)))),
    CONSTRAINT p1_audit_recovery_gate_singleton_check CHECK (singleton)
);


--
-- Name: paper_execution_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.paper_execution_jobs (
    id bigint NOT NULL,
    order_intent_id bigint NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    next_retry_at timestamp with time zone DEFAULT '1970-01-01 00:00:00+00'::timestamp with time zone NOT NULL,
    lease_owner text,
    lease_expires_at timestamp with time zone,
    lease_generation integer DEFAULT 0 NOT NULL,
    last_error_code text,
    dead_letter_reason text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT paper_execution_jobs_attempt_count_check CHECK ((attempt_count >= 0)),
    CONSTRAINT paper_execution_jobs_check CHECK ((attempt_count <= max_attempts)),
    CONSTRAINT paper_execution_jobs_check1 CHECK ((((status = 'running'::text) AND (lease_owner IS NOT NULL) AND (btrim(lease_owner) <> ''::text) AND (lease_expires_at IS NOT NULL)) OR ((status <> 'running'::text) AND (lease_owner IS NULL) AND (lease_expires_at IS NULL)))),
    CONSTRAINT paper_execution_jobs_dead_letter_reason_check CHECK (((dead_letter_reason IS NULL) OR (btrim(dead_letter_reason) <> ''::text))),
    CONSTRAINT paper_execution_jobs_last_error_code_check CHECK (((last_error_code IS NULL) OR (btrim(last_error_code) <> ''::text))),
    CONSTRAINT paper_execution_jobs_lease_generation_check CHECK ((lease_generation >= 0)),
    CONSTRAINT paper_execution_jobs_max_attempts_check CHECK ((max_attempts >= 1)),
    CONSTRAINT paper_execution_jobs_priority_check CHECK ((priority >= 0)),
    CONSTRAINT paper_execution_jobs_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, 'retry_wait'::text, 'succeeded'::text, 'dead_letter'::text])))
);


--
-- Name: paper_execution_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.paper_execution_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: paper_execution_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.paper_execution_jobs_id_seq OWNED BY public.paper_execution_jobs.id;


--
-- Name: portfolio_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portfolio_policies (
    id bigint NOT NULL,
    portfolio_id bigint NOT NULL,
    version integer NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    max_gross_exposure numeric(38,18) NOT NULL,
    max_single_position_pct numeric(20,10) NOT NULL,
    cash_reserve_pct numeric(20,10) DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    created_by text NOT NULL,
    reason text NOT NULL,
    CONSTRAINT portfolio_policies_cash_reserve_pct_check CHECK (((cash_reserve_pct >= (0)::numeric) AND (cash_reserve_pct <= (1)::numeric))),
    CONSTRAINT portfolio_policies_created_by_check CHECK ((created_by <> ''::text)),
    CONSTRAINT portfolio_policies_max_gross_exposure_check CHECK ((max_gross_exposure >= (0)::numeric)),
    CONSTRAINT portfolio_policies_max_single_position_pct_check CHECK (((max_single_position_pct >= (0)::numeric) AND (max_single_position_pct <= (1)::numeric))),
    CONSTRAINT portfolio_policies_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT portfolio_policies_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'published'::text, 'retired'::text]))),
    CONSTRAINT portfolio_policies_version_check CHECK ((version >= 1))
);


--
-- Name: portfolio_policies_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portfolio_policies_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portfolio_policies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portfolio_policies_id_seq OWNED BY public.portfolio_policies.id;


--
-- Name: portfolios; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portfolios (
    id bigint NOT NULL,
    owner_id text NOT NULL,
    name text NOT NULL,
    base_currency text DEFAULT 'KRW'::text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    created_by text NOT NULL,
    reason text NOT NULL,
    request_id text,
    idempotency_key text,
    requested_at timestamp with time zone,
    request_hash text,
    CONSTRAINT portfolios_api_command_all_or_none CHECK ((((request_id IS NULL) AND (idempotency_key IS NULL) AND (requested_at IS NULL) AND (request_hash IS NULL)) OR ((request_id IS NOT NULL) AND (idempotency_key IS NOT NULL) AND (requested_at IS NOT NULL) AND (request_hash IS NOT NULL)))),
    CONSTRAINT portfolios_api_command_non_blank CHECK ((((request_id IS NULL) OR (btrim(request_id) <> ''::text)) AND ((idempotency_key IS NULL) OR (btrim(idempotency_key) <> ''::text)))),
    CONSTRAINT portfolios_base_currency_check CHECK ((base_currency = ANY (ARRAY['KRW'::text, 'BTC'::text, 'USDT'::text]))),
    CONSTRAINT portfolios_created_by_check CHECK ((created_by <> ''::text)),
    CONSTRAINT portfolios_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT portfolios_request_hash_format CHECK (((request_hash IS NULL) OR (request_hash ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT portfolios_status_check CHECK ((status = ANY (ARRAY['active'::text, 'archived'::text])))
);


--
-- Name: portfolios_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.portfolios_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: portfolios_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.portfolios_id_seq OWNED BY public.portfolios.id;


--
-- Name: position_projections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.position_projections (
    id bigint NOT NULL,
    portfolio_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    quantity numeric(38,18) DEFAULT 0 NOT NULL,
    average_entry_price numeric(38,18),
    realized_pnl numeric(38,18) DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    source_fill_id bigint,
    CONSTRAINT position_projections_average_entry_price_check CHECK (((average_entry_price IS NULL) OR (average_entry_price >= (0)::numeric)))
);


--
-- Name: position_projections_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.position_projections_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: position_projections_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.position_projections_id_seq OWNED BY public.position_projections.id;


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
-- Name: realtime_connection_quality_intervals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.realtime_connection_quality_intervals (
    id bigint NOT NULL,
    connection_id uuid NOT NULL,
    market_id bigint,
    data_type text NOT NULL,
    range_start_at timestamp with time zone NOT NULL,
    range_end_at timestamp with time zone NOT NULL,
    quality text NOT NULL,
    reason_code text NOT NULL,
    fingerprint text NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    detected_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT realtime_connection_quality_intervals_check CHECK ((range_start_at < range_end_at)),
    CONSTRAINT realtime_connection_quality_intervals_data_type_check CHECK ((data_type = ANY (ARRAY['trade_event'::text, 'orderbook_snapshot'::text, 'ticker_snapshot'::text, 'source_candle'::text]))),
    CONSTRAINT realtime_connection_quality_intervals_fingerprint_check CHECK ((fingerprint ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT realtime_connection_quality_intervals_quality_check CHECK ((quality = ANY (ARRAY['available'::text, 'missing'::text, 'unavailable'::text, 'unverified'::text])))
);


--
-- Name: realtime_connection_quality_intervals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.realtime_connection_quality_intervals ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.realtime_connection_quality_intervals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: realtime_connection_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.realtime_connection_sessions (
    connection_id uuid NOT NULL,
    subscription_generation bigint DEFAULT 0 NOT NULL,
    subscription_scope jsonb DEFAULT '{}'::jsonb NOT NULL,
    connected_at timestamp with time zone NOT NULL,
    disconnected_at timestamp with time zone,
    status text DEFAULT 'active'::text NOT NULL,
    first_frame_sequence bigint,
    last_frame_sequence bigint DEFAULT 0 NOT NULL,
    last_received_at timestamp with time zone,
    disconnect_reason text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT realtime_connection_sessions_check CHECK (((disconnected_at IS NULL) OR (disconnected_at >= connected_at))),
    CONSTRAINT realtime_connection_sessions_first_frame_sequence_check CHECK (((first_frame_sequence IS NULL) OR (first_frame_sequence > 0))),
    CONSTRAINT realtime_connection_sessions_last_frame_sequence_check CHECK ((last_frame_sequence >= 0)),
    CONSTRAINT realtime_connection_sessions_status_check CHECK ((status = ANY (ARRAY['active'::text, 'closed'::text, 'disconnected'::text, 'failed'::text]))),
    CONSTRAINT realtime_connection_sessions_subscription_generation_check CHECK ((subscription_generation >= 0))
);


--
-- Name: reconciliation_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reconciliation_runs (
    id bigint NOT NULL,
    exchange_order_id bigint NOT NULL,
    run_key text NOT NULL,
    status text NOT NULL,
    observed_status text NOT NULL,
    observed_fill_count integer DEFAULT 0 NOT NULL,
    request_hash text NOT NULL,
    actor_id text NOT NULL,
    reason text NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    started_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    completed_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT reconciliation_runs_actor_id_check CHECK ((actor_id <> ''::text)),
    CONSTRAINT reconciliation_runs_check CHECK ((completed_at >= started_at)),
    CONSTRAINT reconciliation_runs_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text)),
    CONSTRAINT reconciliation_runs_observed_fill_count_check CHECK ((observed_fill_count >= 0)),
    CONSTRAINT reconciliation_runs_observed_status_check CHECK ((observed_status = ANY (ARRAY['done'::text, 'cancel'::text, 'prevented'::text, 'rejected'::text, 'outcome_unknown'::text, 'missing'::text]))),
    CONSTRAINT reconciliation_runs_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT reconciliation_runs_request_hash_check CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT reconciliation_runs_run_key_check CHECK ((run_key <> ''::text)),
    CONSTRAINT reconciliation_runs_status_check CHECK ((status = ANY (ARRAY['succeeded'::text, 'mismatch'::text, 'outcome_unknown'::text])))
);


--
-- Name: reconciliation_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.reconciliation_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: reconciliation_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.reconciliation_runs_id_seq OWNED BY public.reconciliation_runs.id;


--
-- Name: risk_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.risk_events (
    id bigint NOT NULL,
    order_intent_id bigint,
    bot_instance_id bigint,
    scope_type text NOT NULL,
    scope_key text NOT NULL,
    event_type text NOT NULL,
    severity text NOT NULL,
    fingerprint text NOT NULL,
    risk_policy_version integer,
    message text NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    occurred_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT risk_events_event_type_check CHECK ((event_type = ANY (ARRAY['policy_approved'::text, 'limit_rejected'::text, 'kill_switch_rejected'::text, 'reconciliation_mismatch'::text, 'outcome_unknown'::text]))),
    CONSTRAINT risk_events_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text)),
    CONSTRAINT risk_events_message_check CHECK ((message <> ''::text)),
    CONSTRAINT risk_events_risk_policy_version_check CHECK (((risk_policy_version IS NULL) OR (risk_policy_version >= 1))),
    CONSTRAINT risk_events_scope_key_check CHECK ((scope_key <> ''::text)),
    CONSTRAINT risk_events_scope_type_check CHECK ((scope_type = ANY (ARRAY['global'::text, 'portfolio'::text, 'bot'::text, 'instrument'::text]))),
    CONSTRAINT risk_events_severity_check CHECK ((severity = ANY (ARRAY['info'::text, 'warning'::text, 'critical'::text])))
);


--
-- Name: risk_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.risk_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: risk_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.risk_events_id_seq OWNED BY public.risk_events.id;


--
-- Name: risk_limits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.risk_limits (
    id bigint NOT NULL,
    scope_type text NOT NULL,
    scope_key text NOT NULL,
    limit_type text NOT NULL,
    version integer NOT NULL,
    limit_value numeric(38,18) NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    actor_id text NOT NULL,
    reason text NOT NULL,
    CONSTRAINT risk_limits_actor_id_check CHECK ((actor_id <> ''::text)),
    CONSTRAINT risk_limits_limit_type_check CHECK ((limit_type = ANY (ARRAY['max_order_notional'::text, 'max_daily_loss'::text, 'max_position_notional'::text, 'max_drawdown'::text, 'max_open_orders'::text]))),
    CONSTRAINT risk_limits_limit_value_check CHECK ((limit_value >= (0)::numeric)),
    CONSTRAINT risk_limits_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT risk_limits_scope_key_check CHECK ((scope_key <> ''::text)),
    CONSTRAINT risk_limits_scope_type_check CHECK ((scope_type = ANY (ARRAY['global'::text, 'portfolio'::text, 'bot'::text, 'instrument'::text]))),
    CONSTRAINT risk_limits_status_check CHECK ((status = ANY (ARRAY['active'::text, 'retired'::text]))),
    CONSTRAINT risk_limits_version_check CHECK ((version >= 1))
);


--
-- Name: risk_limits_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.risk_limits_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: risk_limits_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.risk_limits_id_seq OWNED BY public.risk_limits.id;


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying NOT NULL
);


--
-- Name: source_candle_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_candle_revisions (
    id bigint NOT NULL,
    source_candle_id bigint NOT NULL,
    revision_number integer NOT NULL,
    market_id bigint NOT NULL,
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
    source_as_of timestamp with time zone NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    input_content_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT source_candle_revisions_candle_unit_check CHECK ((candle_unit = ANY (ARRAY['1m'::text, '1d'::text]))),
    CONSTRAINT source_candle_revisions_input_content_hash_check CHECK ((input_content_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT source_candle_revisions_revision_number_check CHECK ((revision_number > 0)),
    CONSTRAINT source_candle_revisions_source_check CHECK ((source = 'UPBIT'::text))
);


--
-- Name: source_candle_revisions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.source_candle_revisions ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.source_candle_revisions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
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
    market_id bigint,
    occurred_at timestamp with time zone,
    received_at timestamp with time zone,
    stored_at timestamp with time zone,
    knowledge_at timestamp with time zone,
    fetch_manifest_id bigint,
    source_receipt_id bigint,
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
-- Name: source_receipts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_receipts (
    id bigint NOT NULL,
    data_type text NOT NULL,
    market_id bigint NOT NULL,
    instrument_id bigint NOT NULL,
    connection_id uuid NOT NULL,
    frame_sequence bigint NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone NOT NULL,
    payload_checksum text NOT NULL,
    raw_payload jsonb NOT NULL,
    fetch_manifest_id bigint,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    knowledge_at timestamp with time zone NOT NULL,
    collector_version text NOT NULL,
    schema_version text NOT NULL,
    CONSTRAINT source_receipts_data_type_ck CHECK ((data_type = ANY (ARRAY['source_candle'::text, 'trade_event'::text, 'orderbook_snapshot'::text, 'ticker_snapshot'::text]))),
    CONSTRAINT source_receipts_frame_sequence_ck CHECK ((frame_sequence > 0)),
    CONSTRAINT source_receipts_payload_checksum_ck CHECK ((length(payload_checksum) = 64))
);


--
-- Name: source_receipts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.source_receipts ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.source_receipts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: strategy_definitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.strategy_definitions (
    id bigint NOT NULL,
    owner_id text NOT NULL,
    name text NOT NULL,
    idempotency_key text NOT NULL,
    request_id text NOT NULL,
    actor_id text NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    reason text NOT NULL,
    request_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT strategy_definitions_actor_id_check CHECK ((btrim(actor_id) <> ''::text)),
    CONSTRAINT strategy_definitions_idempotency_key_check CHECK ((btrim(idempotency_key) <> ''::text)),
    CONSTRAINT strategy_definitions_name_check CHECK ((btrim(name) <> ''::text)),
    CONSTRAINT strategy_definitions_owner_id_check CHECK ((btrim(owner_id) <> ''::text)),
    CONSTRAINT strategy_definitions_reason_check CHECK ((btrim(reason) <> ''::text)),
    CONSTRAINT strategy_definitions_request_hash_check CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT strategy_definitions_request_id_check CHECK ((btrim(request_id) <> ''::text))
);


--
-- Name: strategy_definitions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.strategy_definitions ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.strategy_definitions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: strategy_graphs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.strategy_graphs (
    strategy_version_id bigint NOT NULL,
    graph_json jsonb NOT NULL,
    graph_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT strategy_graphs_graph_hash_check CHECK ((graph_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT strategy_graphs_graph_json_check CHECK (((graph_json ->> 'schema_version'::text) = 'strategy-graph-v1'::text))
);


--
-- Name: strategy_parameters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.strategy_parameters (
    id bigint NOT NULL,
    strategy_version_id bigint NOT NULL,
    name text NOT NULL,
    data_type text NOT NULL,
    default_value jsonb,
    constraints jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT strategy_parameters_data_type_check CHECK ((data_type = ANY (ARRAY['decimal'::text, 'integer'::text, 'boolean'::text, 'string'::text]))),
    CONSTRAINT strategy_parameters_name_check CHECK ((btrim(name) <> ''::text))
);


--
-- Name: strategy_parameters_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.strategy_parameters ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.strategy_parameters_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: strategy_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.strategy_versions (
    id bigint NOT NULL,
    strategy_id bigint NOT NULL,
    version integer NOT NULL,
    schema_version text DEFAULT 'strategy-graph-v1'::text NOT NULL,
    status text NOT NULL,
    graph_hash text NOT NULL,
    validation_result jsonb NOT NULL,
    idempotency_key text NOT NULL,
    request_id text NOT NULL,
    actor_id text NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    reason text NOT NULL,
    request_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    published_at timestamp with time zone,
    retired_at timestamp with time zone,
    CONSTRAINT strategy_versions_actor_id_check CHECK ((btrim(actor_id) <> ''::text)),
    CONSTRAINT strategy_versions_check CHECK (((status = 'published'::text) = (published_at IS NOT NULL))),
    CONSTRAINT strategy_versions_check1 CHECK (((status = 'retired'::text) = (retired_at IS NOT NULL))),
    CONSTRAINT strategy_versions_graph_hash_check CHECK ((graph_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT strategy_versions_idempotency_key_check CHECK ((btrim(idempotency_key) <> ''::text)),
    CONSTRAINT strategy_versions_reason_check CHECK ((btrim(reason) <> ''::text)),
    CONSTRAINT strategy_versions_request_hash_check CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT strategy_versions_request_id_check CHECK ((btrim(request_id) <> ''::text)),
    CONSTRAINT strategy_versions_schema_version_check CHECK ((schema_version = 'strategy-graph-v1'::text)),
    CONSTRAINT strategy_versions_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'validated'::text, 'published'::text, 'retired'::text]))),
    CONSTRAINT strategy_versions_version_check CHECK ((version > 0))
);


--
-- Name: strategy_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.strategy_versions ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.strategy_versions_id_seq
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
    market_id bigint,
    occurred_at timestamp with time zone,
    received_at timestamp with time zone,
    stored_at timestamp with time zone,
    knowledge_at timestamp with time zone,
    fetch_manifest_id bigint,
    source_receipt_id bigint,
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
    market_id bigint,
    occurred_at timestamp with time zone,
    received_at timestamp with time zone,
    stored_at timestamp with time zone,
    knowledge_at timestamp with time zone,
    fetch_manifest_id bigint,
    source_receipt_id bigint,
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
-- Name: trading_capabilities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trading_capabilities (
    id bigint NOT NULL,
    scope_type text DEFAULT 'global'::text NOT NULL,
    scope_key text DEFAULT 'global'::text NOT NULL,
    state text DEFAULT 'live_disabled'::text NOT NULL,
    deployment_sha text NOT NULL,
    approved_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    actor_id text NOT NULL,
    reason text NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    request_id text NOT NULL,
    idempotency_key text NOT NULL,
    CONSTRAINT trading_capabilities_actor_id_check CHECK ((actor_id <> ''::text)),
    CONSTRAINT trading_capabilities_actor_id_check1 CHECK ((actor_id !~ '^(ci|ai|service):'::text)),
    CONSTRAINT trading_capabilities_check CHECK ((expires_at > approved_at)),
    CONSTRAINT trading_capabilities_deployment_sha_check CHECK ((deployment_sha ~ '^[0-9a-f]{40}$'::text)),
    CONSTRAINT trading_capabilities_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text)),
    CONSTRAINT trading_capabilities_idempotency_key_check CHECK ((idempotency_key <> ''::text)),
    CONSTRAINT trading_capabilities_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT trading_capabilities_request_id_check CHECK ((request_id <> ''::text)),
    CONSTRAINT trading_capabilities_scope_key_check CHECK ((scope_key = 'global'::text)),
    CONSTRAINT trading_capabilities_scope_type_check CHECK ((scope_type = 'global'::text)),
    CONSTRAINT trading_capabilities_state_check CHECK ((state = ANY (ARRAY['live_disabled'::text, 'live_enabled'::text])))
);


--
-- Name: trading_capabilities_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.trading_capabilities_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: trading_capabilities_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.trading_capabilities_id_seq OWNED BY public.trading_capabilities.id;


--
-- Name: upbit_api_key_permission_attestations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.upbit_api_key_permission_attestations (
    id bigint NOT NULL,
    exchange_account_id bigint NOT NULL,
    has_order_permission boolean NOT NULL,
    has_order_read_permission boolean NOT NULL,
    has_withdraw_permission boolean NOT NULL,
    attested_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    actor_id text NOT NULL,
    reason text NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    request_id text NOT NULL,
    idempotency_key text NOT NULL,
    CONSTRAINT upbit_api_key_permission_attest_has_order_read_permission_check CHECK ((has_order_read_permission IS TRUE)),
    CONSTRAINT upbit_api_key_permission_attestat_has_withdraw_permission_check CHECK ((has_withdraw_permission IS FALSE)),
    CONSTRAINT upbit_api_key_permission_attestation_has_order_permission_check CHECK ((has_order_permission IS TRUE)),
    CONSTRAINT upbit_api_key_permission_attestations_actor_id_check CHECK ((actor_id <> ''::text)),
    CONSTRAINT upbit_api_key_permission_attestations_actor_id_check1 CHECK ((actor_id !~* '^(ci|ai|service):'::text)),
    CONSTRAINT upbit_api_key_permission_attestations_check CHECK ((expires_at > attested_at)),
    CONSTRAINT upbit_api_key_permission_attestations_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text)),
    CONSTRAINT upbit_api_key_permission_attestations_idempotency_key_check CHECK ((idempotency_key <> ''::text)),
    CONSTRAINT upbit_api_key_permission_attestations_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT upbit_api_key_permission_attestations_request_id_check CHECK ((request_id <> ''::text))
);


--
-- Name: upbit_api_key_permission_attestations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.upbit_api_key_permission_attestations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: upbit_api_key_permission_attestations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.upbit_api_key_permission_attestations_id_seq OWNED BY public.upbit_api_key_permission_attestations.id;


--
-- Name: upbit_live_exchange_order_bindings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.upbit_live_exchange_order_bindings (
    id bigint NOT NULL,
    exchange_account_id bigint NOT NULL,
    order_intent_id bigint NOT NULL,
    exchange_order_id bigint NOT NULL,
    live_order_identifier_id bigint NOT NULL,
    upbit_order_outbox_id bigint NOT NULL,
    upbit_order_uuid text NOT NULL,
    upbit_identifier text NOT NULL,
    source text NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    bound_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    actor_id text NOT NULL,
    reason text NOT NULL,
    request_id text NOT NULL,
    idempotency_key text NOT NULL,
    CONSTRAINT upbit_live_exchange_order_bindings_actor_id_check CHECK ((actor_id <> ''::text)),
    CONSTRAINT upbit_live_exchange_order_bindings_actor_id_check1 CHECK ((actor_id !~* '^(ci|ai|service):'::text)),
    CONSTRAINT upbit_live_exchange_order_bindings_check CHECK ((observed_at <= bound_at)),
    CONSTRAINT upbit_live_exchange_order_bindings_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text)),
    CONSTRAINT upbit_live_exchange_order_bindings_idempotency_key_check CHECK ((idempotency_key <> ''::text)),
    CONSTRAINT upbit_live_exchange_order_bindings_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT upbit_live_exchange_order_bindings_request_id_check CHECK ((request_id <> ''::text)),
    CONSTRAINT upbit_live_exchange_order_bindings_source_check CHECK ((source = ANY (ARRAY['order_submit_response'::text, 'rest_order_snapshot'::text, 'myorder_event'::text]))),
    CONSTRAINT upbit_live_exchange_order_bindings_upbit_identifier_check CHECK ((upbit_identifier ~ '^gm1_[a-z2-7]{52}$'::text)),
    CONSTRAINT upbit_live_exchange_order_bindings_upbit_order_uuid_check CHECK ((upbit_order_uuid ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'::text))
);


--
-- Name: upbit_live_exchange_order_bindings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.upbit_live_exchange_order_bindings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: upbit_live_exchange_order_bindings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.upbit_live_exchange_order_bindings_id_seq OWNED BY public.upbit_live_exchange_order_bindings.id;


--
-- Name: upbit_live_reconciliation_applications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.upbit_live_reconciliation_applications (
    id bigint NOT NULL,
    exchange_account_id bigint NOT NULL,
    order_intent_id bigint NOT NULL,
    exchange_order_id bigint NOT NULL,
    live_exchange_order_binding_id bigint NOT NULL,
    reconciliation_run_id bigint NOT NULL,
    source text NOT NULL,
    source_endpoint text NOT NULL,
    observed_upbit_order_uuid text NOT NULL,
    observed_upbit_identifier text NOT NULL,
    observed_state text NOT NULL,
    applied_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    request_hash text NOT NULL,
    can_resubmit boolean DEFAULT false NOT NULL,
    actual_request_sent boolean DEFAULT false NOT NULL,
    actual_order_cancel_sent boolean DEFAULT false NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    actor_id text NOT NULL,
    reason text NOT NULL,
    request_id text NOT NULL,
    idempotency_key text NOT NULL,
    CONSTRAINT upbit_live_reconciliation_appli_observed_upbit_identifier_check CHECK ((observed_upbit_identifier ~ '^gm1_[a-z2-7]{52}$'::text)),
    CONSTRAINT upbit_live_reconciliation_appli_observed_upbit_order_uuid_check CHECK ((observed_upbit_order_uuid ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'::text)),
    CONSTRAINT upbit_live_reconciliation_applic_actual_order_cancel_sent_check CHECK ((actual_order_cancel_sent IS FALSE)),
    CONSTRAINT upbit_live_reconciliation_application_actual_request_sent_check CHECK ((actual_request_sent IS FALSE)),
    CONSTRAINT upbit_live_reconciliation_applications_actor_id_check CHECK ((actor_id <> ''::text)),
    CONSTRAINT upbit_live_reconciliation_applications_actor_id_check1 CHECK ((actor_id !~* '^(ci|ai|service):'::text)),
    CONSTRAINT upbit_live_reconciliation_applications_can_resubmit_check CHECK ((can_resubmit IS FALSE)),
    CONSTRAINT upbit_live_reconciliation_applications_check CHECK ((applied_at <= recorded_at)),
    CONSTRAINT upbit_live_reconciliation_applications_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text)),
    CONSTRAINT upbit_live_reconciliation_applications_idempotency_key_check CHECK ((idempotency_key <> ''::text)),
    CONSTRAINT upbit_live_reconciliation_applications_observed_state_check CHECK ((observed_state = ANY (ARRAY['done'::text, 'cancel'::text, 'prevented'::text, 'rejected'::text]))),
    CONSTRAINT upbit_live_reconciliation_applications_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT upbit_live_reconciliation_applications_request_hash_check CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT upbit_live_reconciliation_applications_request_id_check CHECK ((request_id <> ''::text)),
    CONSTRAINT upbit_live_reconciliation_applications_source_check CHECK ((source = 'rest_order_snapshot'::text)),
    CONSTRAINT upbit_live_reconciliation_applications_source_endpoint_check CHECK ((source_endpoint = ANY (ARRAY['GET /v1/order'::text, 'GET /v1/orders/open'::text, 'GET /v1/orders/closed'::text, 'GET /v1/orders/uuids'::text])))
);


--
-- Name: upbit_live_reconciliation_applications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.upbit_live_reconciliation_applications_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: upbit_live_reconciliation_applications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.upbit_live_reconciliation_applications_id_seq OWNED BY public.upbit_live_reconciliation_applications.id;


--
-- Name: upbit_order_identifier_reservations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.upbit_order_identifier_reservations (
    id bigint NOT NULL,
    exchange_account_id bigint NOT NULL,
    identifier text NOT NULL,
    source_table text NOT NULL,
    source_column text NOT NULL,
    source_id bigint NOT NULL,
    reserved_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT upbit_order_identifier_reservations_identifier_check CHECK ((identifier <> ''::text)),
    CONSTRAINT upbit_order_identifier_reservations_source_column_check CHECK ((source_column = ANY (ARRAY['identifier'::text, 'response_uuid'::text, 'response_identifier'::text]))),
    CONSTRAINT upbit_order_identifier_reservations_source_table_check CHECK ((source_table = ANY (ARRAY['live_order_identifiers'::text, 'upbit_order_test_runs'::text])))
);


--
-- Name: upbit_order_identifier_reservations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.upbit_order_identifier_reservations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: upbit_order_identifier_reservations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.upbit_order_identifier_reservations_id_seq OWNED BY public.upbit_order_identifier_reservations.id;


--
-- Name: upbit_order_outbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.upbit_order_outbox (
    id bigint NOT NULL,
    exchange_account_id bigint NOT NULL,
    order_intent_id bigint NOT NULL,
    live_order_identifier_id bigint NOT NULL,
    permission_attestation_id bigint,
    status text NOT NULL,
    blocked_reason text,
    request_payload jsonb NOT NULL,
    request_hash text NOT NULL,
    submit_attempt_count integer DEFAULT 0 NOT NULL,
    actor_id text NOT NULL,
    reason text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    request_id text NOT NULL,
    idempotency_key text NOT NULL,
    CONSTRAINT upbit_order_outbox_actor_id_check CHECK ((actor_id <> ''::text)),
    CONSTRAINT upbit_order_outbox_actor_id_check1 CHECK ((actor_id !~* '^(ci|ai|service):'::text)),
    CONSTRAINT upbit_order_outbox_blocked_reason_check CHECK (((blocked_reason IS NULL) OR (blocked_reason = ANY (ARRAY['live_disabled'::text, 'permission_missing'::text, 'permission_not_ready'::text, 'permission_expired'::text, 'withdraw_permission_present'::text, 'kill_switch_armed'::text])))),
    CONSTRAINT upbit_order_outbox_check CHECK ((((status = 'ready'::text) AND (blocked_reason IS NULL) AND (permission_attestation_id IS NOT NULL)) OR ((status = 'blocked'::text) AND (blocked_reason IS NOT NULL)))),
    CONSTRAINT upbit_order_outbox_idempotency_key_check CHECK ((idempotency_key <> ''::text)),
    CONSTRAINT upbit_order_outbox_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT upbit_order_outbox_request_hash_check CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT upbit_order_outbox_request_id_check CHECK ((request_id <> ''::text)),
    CONSTRAINT upbit_order_outbox_request_payload_check CHECK ((jsonb_typeof(request_payload) = 'object'::text)),
    CONSTRAINT upbit_order_outbox_status_check CHECK ((status = ANY (ARRAY['ready'::text, 'blocked'::text]))),
    CONSTRAINT upbit_order_outbox_submit_attempt_count_check CHECK ((submit_attempt_count = 0))
);


--
-- Name: upbit_order_outbox_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.upbit_order_outbox_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: upbit_order_outbox_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.upbit_order_outbox_id_seq OWNED BY public.upbit_order_outbox.id;


--
-- Name: upbit_order_submit_rehearsals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.upbit_order_submit_rehearsals (
    id bigint NOT NULL,
    exchange_account_id bigint NOT NULL,
    order_intent_id bigint NOT NULL,
    live_order_identifier_id bigint NOT NULL,
    upbit_order_outbox_id bigint NOT NULL,
    permission_attestation_id bigint,
    rehearsal_status text NOT NULL,
    blocked_reason text,
    endpoint_key text NOT NULL,
    http_method text NOT NULL,
    request_path text NOT NULL,
    request_payload jsonb NOT NULL,
    request_hash text NOT NULL,
    query_string text NOT NULL,
    query_hash text NOT NULL,
    actual_request_sent boolean DEFAULT false NOT NULL,
    would_submit boolean DEFAULT false NOT NULL,
    can_bind_response boolean DEFAULT false NOT NULL,
    response_uuid text,
    response_identifier text,
    rehearsed_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    actor_id text NOT NULL,
    reason text NOT NULL,
    request_id text NOT NULL,
    idempotency_key text NOT NULL,
    CONSTRAINT upbit_order_submit_rehearsals_actor_id_check CHECK ((actor_id <> ''::text)),
    CONSTRAINT upbit_order_submit_rehearsals_actor_id_check1 CHECK ((actor_id !~* '^(ci|ai|service):'::text)),
    CONSTRAINT upbit_order_submit_rehearsals_actual_request_sent_check CHECK ((actual_request_sent IS FALSE)),
    CONSTRAINT upbit_order_submit_rehearsals_blocked_reason_check CHECK (((blocked_reason IS NULL) OR (blocked_reason = ANY (ARRAY['outbox_not_ready'::text, 'permission_expired'::text, 'live_identifier_not_reserved'::text, 'already_bound'::text, 'request_mismatch'::text, 'policy_blocked'::text])))),
    CONSTRAINT upbit_order_submit_rehearsals_can_bind_response_check CHECK ((can_bind_response IS FALSE)),
    CONSTRAINT upbit_order_submit_rehearsals_check CHECK ((((rehearsal_status = 'passed'::text) AND (blocked_reason IS NULL)) OR ((rehearsal_status = 'blocked'::text) AND (blocked_reason IS NOT NULL)))),
    CONSTRAINT upbit_order_submit_rehearsals_check1 CHECK ((rehearsed_at <= recorded_at)),
    CONSTRAINT upbit_order_submit_rehearsals_endpoint_key_check CHECK ((endpoint_key = 'rest.new-order'::text)),
    CONSTRAINT upbit_order_submit_rehearsals_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text)),
    CONSTRAINT upbit_order_submit_rehearsals_http_method_check CHECK ((http_method = 'POST'::text)),
    CONSTRAINT upbit_order_submit_rehearsals_idempotency_key_check CHECK ((idempotency_key <> ''::text)),
    CONSTRAINT upbit_order_submit_rehearsals_query_hash_check CHECK ((query_hash ~ '^[0-9a-f]{128}$'::text)),
    CONSTRAINT upbit_order_submit_rehearsals_query_string_check CHECK ((query_string <> ''::text)),
    CONSTRAINT upbit_order_submit_rehearsals_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT upbit_order_submit_rehearsals_rehearsal_status_check CHECK ((rehearsal_status = ANY (ARRAY['passed'::text, 'blocked'::text]))),
    CONSTRAINT upbit_order_submit_rehearsals_request_hash_check CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT upbit_order_submit_rehearsals_request_id_check CHECK ((request_id <> ''::text)),
    CONSTRAINT upbit_order_submit_rehearsals_request_path_check CHECK ((request_path = '/v1/orders'::text)),
    CONSTRAINT upbit_order_submit_rehearsals_request_payload_check CHECK ((jsonb_typeof(request_payload) = 'object'::text)),
    CONSTRAINT upbit_order_submit_rehearsals_response_identifier_check CHECK ((response_identifier IS NULL)),
    CONSTRAINT upbit_order_submit_rehearsals_response_uuid_check CHECK ((response_uuid IS NULL)),
    CONSTRAINT upbit_order_submit_rehearsals_would_submit_check CHECK ((would_submit IS FALSE))
);


--
-- Name: upbit_order_submit_rehearsals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.upbit_order_submit_rehearsals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: upbit_order_submit_rehearsals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.upbit_order_submit_rehearsals_id_seq OWNED BY public.upbit_order_submit_rehearsals.id;


--
-- Name: upbit_order_test_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.upbit_order_test_runs (
    id bigint NOT NULL,
    exchange_account_id bigint NOT NULL,
    request_id text NOT NULL,
    actor_id text NOT NULL,
    reason text NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    request_parameters jsonb NOT NULL,
    response_status_code integer NOT NULL,
    response_uuid text,
    response_identifier text,
    response_body jsonb NOT NULL,
    lookup_allowed boolean DEFAULT false NOT NULL,
    cancel_allowed boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT upbit_order_test_runs_actor_id_check CHECK ((actor_id <> ''::text)),
    CONSTRAINT upbit_order_test_runs_cancel_allowed_check CHECK ((cancel_allowed = false)),
    CONSTRAINT upbit_order_test_runs_check CHECK ((created_at >= requested_at)),
    CONSTRAINT upbit_order_test_runs_lookup_allowed_check CHECK ((lookup_allowed = false)),
    CONSTRAINT upbit_order_test_runs_reason_check CHECK ((reason <> ''::text)),
    CONSTRAINT upbit_order_test_runs_request_id_check CHECK ((request_id <> ''::text)),
    CONSTRAINT upbit_order_test_runs_request_parameters_check CHECK ((jsonb_typeof(request_parameters) = 'object'::text)),
    CONSTRAINT upbit_order_test_runs_response_body_check CHECK ((jsonb_typeof(response_body) = 'object'::text)),
    CONSTRAINT upbit_order_test_runs_response_status_code_check CHECK (((response_status_code >= 100) AND (response_status_code <= 599)))
);


--
-- Name: upbit_order_test_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.upbit_order_test_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: upbit_order_test_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.upbit_order_test_runs_id_seq OWNED BY public.upbit_order_test_runs.id;


--
-- Name: bot_definitions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_definitions ALTER COLUMN id SET DEFAULT nextval('public.bot_definitions_id_seq'::regclass);


--
-- Name: bot_instances id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_instances ALTER COLUMN id SET DEFAULT nextval('public.bot_instances_id_seq'::regclass);


--
-- Name: bot_state_transitions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_state_transitions ALTER COLUMN id SET DEFAULT nextval('public.bot_state_transitions_id_seq'::regclass);


--
-- Name: capital_allocations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capital_allocations ALTER COLUMN id SET DEFAULT nextval('public.capital_allocations_id_seq'::regclass);


--
-- Name: exchange_accounts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_accounts ALTER COLUMN id SET DEFAULT nextval('public.exchange_accounts_id_seq'::regclass);


--
-- Name: exchange_orders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_orders ALTER COLUMN id SET DEFAULT nextval('public.exchange_orders_id_seq'::regclass);


--
-- Name: kill_switches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kill_switches ALTER COLUMN id SET DEFAULT nextval('public.kill_switches_id_seq'::regclass);


--
-- Name: live_order_identifiers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.live_order_identifiers ALTER COLUMN id SET DEFAULT nextval('public.live_order_identifiers_id_seq'::regclass);


--
-- Name: order_fills id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_fills ALTER COLUMN id SET DEFAULT nextval('public.order_fills_id_seq'::regclass);


--
-- Name: order_intents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_intents ALTER COLUMN id SET DEFAULT nextval('public.order_intents_id_seq'::regclass);


--
-- Name: paper_execution_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paper_execution_jobs ALTER COLUMN id SET DEFAULT nextval('public.paper_execution_jobs_id_seq'::regclass);


--
-- Name: portfolio_policies id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_policies ALTER COLUMN id SET DEFAULT nextval('public.portfolio_policies_id_seq'::regclass);


--
-- Name: portfolios id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolios ALTER COLUMN id SET DEFAULT nextval('public.portfolios_id_seq'::regclass);


--
-- Name: position_projections id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.position_projections ALTER COLUMN id SET DEFAULT nextval('public.position_projections_id_seq'::regclass);


--
-- Name: reconciliation_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconciliation_runs ALTER COLUMN id SET DEFAULT nextval('public.reconciliation_runs_id_seq'::regclass);


--
-- Name: risk_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_events ALTER COLUMN id SET DEFAULT nextval('public.risk_events_id_seq'::regclass);


--
-- Name: risk_limits id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_limits ALTER COLUMN id SET DEFAULT nextval('public.risk_limits_id_seq'::regclass);


--
-- Name: trading_capabilities id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_capabilities ALTER COLUMN id SET DEFAULT nextval('public.trading_capabilities_id_seq'::regclass);


--
-- Name: upbit_api_key_permission_attestations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_api_key_permission_attestations ALTER COLUMN id SET DEFAULT nextval('public.upbit_api_key_permission_attestations_id_seq'::regclass);


--
-- Name: upbit_live_exchange_order_bindings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_exchange_order_bindings ALTER COLUMN id SET DEFAULT nextval('public.upbit_live_exchange_order_bindings_id_seq'::regclass);


--
-- Name: upbit_live_reconciliation_applications id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_reconciliation_applications ALTER COLUMN id SET DEFAULT nextval('public.upbit_live_reconciliation_applications_id_seq'::regclass);


--
-- Name: upbit_order_identifier_reservations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_identifier_reservations ALTER COLUMN id SET DEFAULT nextval('public.upbit_order_identifier_reservations_id_seq'::regclass);


--
-- Name: upbit_order_outbox id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_outbox ALTER COLUMN id SET DEFAULT nextval('public.upbit_order_outbox_id_seq'::regclass);


--
-- Name: upbit_order_submit_rehearsals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_submit_rehearsals ALTER COLUMN id SET DEFAULT nextval('public.upbit_order_submit_rehearsals_id_seq'::regclass);


--
-- Name: upbit_order_test_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_test_runs ALTER COLUMN id SET DEFAULT nextval('public.upbit_order_test_runs_id_seq'::regclass);


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
-- Name: backfill_jobs backfill_jobs_idempotency_key_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backfill_jobs
    ADD CONSTRAINT backfill_jobs_idempotency_key_uk UNIQUE (idempotency_key);


--
-- Name: backfill_jobs backfill_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backfill_jobs
    ADD CONSTRAINT backfill_jobs_pkey PRIMARY KEY (id);


--
-- Name: backfill_safety_gate backfill_safety_gate_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backfill_safety_gate
    ADD CONSTRAINT backfill_safety_gate_pkey PRIMARY KEY (singleton);


--
-- Name: backtest_artifacts backtest_artifacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_artifacts
    ADD CONSTRAINT backtest_artifacts_pkey PRIMARY KEY (id);


--
-- Name: backtest_artifacts backtest_artifacts_run_id_artifact_type_content_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_artifacts
    ADD CONSTRAINT backtest_artifacts_run_id_artifact_type_content_hash_key UNIQUE (run_id, artifact_type, content_hash);


--
-- Name: backtest_equity_points backtest_equity_points_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_equity_points
    ADD CONSTRAINT backtest_equity_points_pkey PRIMARY KEY (id);


--
-- Name: backtest_equity_points backtest_equity_points_run_id_occurred_at_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_equity_points
    ADD CONSTRAINT backtest_equity_points_run_id_occurred_at_key UNIQUE (run_id, occurred_at);


--
-- Name: backtest_equity_points backtest_equity_points_run_id_point_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_equity_points
    ADD CONSTRAINT backtest_equity_points_run_id_point_sequence_key UNIQUE (run_id, point_sequence);


--
-- Name: backtest_metrics backtest_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_metrics
    ADD CONSTRAINT backtest_metrics_pkey PRIMARY KEY (id);


--
-- Name: backtest_metrics backtest_metrics_run_id_metric_name_scope_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_metrics
    ADD CONSTRAINT backtest_metrics_run_id_metric_name_scope_key_key UNIQUE (run_id, metric_name, scope_key);


--
-- Name: backtest_runs backtest_runs_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_runs
    ADD CONSTRAINT backtest_runs_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: backtest_runs backtest_runs_input_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_runs
    ADD CONSTRAINT backtest_runs_input_hash_key UNIQUE (input_hash);


--
-- Name: backtest_runs backtest_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_runs
    ADD CONSTRAINT backtest_runs_pkey PRIMARY KEY (id);


--
-- Name: backtest_runs backtest_runs_strategy_version_id_dataset_version_id_engine_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_runs
    ADD CONSTRAINT backtest_runs_strategy_version_id_dataset_version_id_engine_key UNIQUE (strategy_version_id, dataset_version_id, engine_version, parameter_hash, seed);


--
-- Name: backtest_trades backtest_trades_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_trades
    ADD CONSTRAINT backtest_trades_pkey PRIMARY KEY (id);


--
-- Name: backtest_trades backtest_trades_run_id_trade_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_trades
    ADD CONSTRAINT backtest_trades_run_id_trade_sequence_key UNIQUE (run_id, trade_sequence);


--
-- Name: bot_definitions bot_definitions_owner_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_definitions
    ADD CONSTRAINT bot_definitions_owner_id_name_key UNIQUE (owner_id, name);


--
-- Name: bot_definitions bot_definitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_definitions
    ADD CONSTRAINT bot_definitions_pkey PRIMARY KEY (id);


--
-- Name: bot_instances bot_instances_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_instances
    ADD CONSTRAINT bot_instances_pkey PRIMARY KEY (id);


--
-- Name: bot_state_transitions bot_state_transitions_bot_instance_id_request_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_state_transitions
    ADD CONSTRAINT bot_state_transitions_bot_instance_id_request_id_key UNIQUE (bot_instance_id, request_id);


--
-- Name: bot_state_transitions bot_state_transitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_state_transitions
    ADD CONSTRAINT bot_state_transitions_pkey PRIMARY KEY (id);


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
-- Name: candle_rollup_invalidations candle_rollup_invalidations_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollup_invalidations
    ADD CONSTRAINT candle_rollup_invalidations_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: candle_rollup_invalidations candle_rollup_invalidations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollup_invalidations
    ADD CONSTRAINT candle_rollup_invalidations_pkey PRIMARY KEY (id);


--
-- Name: candle_rollup_recompute_jobs candle_rollup_recompute_jobs_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollup_recompute_jobs
    ADD CONSTRAINT candle_rollup_recompute_jobs_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: candle_rollup_recompute_jobs candle_rollup_recompute_jobs_invalidation_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollup_recompute_jobs
    ADD CONSTRAINT candle_rollup_recompute_jobs_invalidation_id_key UNIQUE (invalidation_id);


--
-- Name: candle_rollup_recompute_jobs candle_rollup_recompute_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollup_recompute_jobs
    ADD CONSTRAINT candle_rollup_recompute_jobs_pkey PRIMARY KEY (id);


--
-- Name: candle_rollups candle_rollups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollups
    ADD CONSTRAINT candle_rollups_pkey PRIMARY KEY (id);


--
-- Name: candle_rollups candle_rollups_revision_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollups
    ADD CONSTRAINT candle_rollups_revision_uk UNIQUE NULLS NOT DISTINCT (instrument_id, candle_unit, candle_start_at, calculation_version, input_content_hash, coverage_snapshot_hash, source_revision_through_id, quality_event_through_id);


--
-- Name: capital_allocations capital_allocations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capital_allocations
    ADD CONSTRAINT capital_allocations_pkey PRIMARY KEY (id);


--
-- Name: capital_allocations capital_allocations_portfolio_policy_id_scope_type_scope_ke_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capital_allocations
    ADD CONSTRAINT capital_allocations_portfolio_policy_id_scope_type_scope_ke_key UNIQUE (portfolio_policy_id, scope_type, scope_key);


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
-- Name: collection_policies collection_policies_natural_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_policies
    ADD CONSTRAINT collection_policies_natural_uk UNIQUE (exchange, quote_currency, name);


--
-- Name: collection_policies collection_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_policies
    ADD CONSTRAINT collection_policies_pkey PRIMARY KEY (id);


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
-- Name: collection_subscription_desires collection_subscription_desires_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_subscription_desires
    ADD CONSTRAINT collection_subscription_desires_pkey PRIMARY KEY (target_spec_id);


--
-- Name: collection_target_changes collection_target_changes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_target_changes
    ADD CONSTRAINT collection_target_changes_pkey PRIMARY KEY (id);


--
-- Name: collection_target_specs collection_target_specs_natural_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_target_specs
    ADD CONSTRAINT collection_target_specs_natural_uk UNIQUE NULLS NOT DISTINCT (policy_id, market_id, data_type, candle_unit);


--
-- Name: collection_target_specs collection_target_specs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_target_specs
    ADD CONSTRAINT collection_target_specs_pkey PRIMARY KEY (id);


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
-- Name: command_idempotency_records command_idempotency_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.command_idempotency_records
    ADD CONSTRAINT command_idempotency_records_pkey PRIMARY KEY (id);


--
-- Name: command_idempotency_records command_idempotency_records_scope_key_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.command_idempotency_records
    ADD CONSTRAINT command_idempotency_records_scope_key_uk UNIQUE (scope, idempotency_key);


--
-- Name: coverage_intervals coverage_intervals_natural_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coverage_intervals
    ADD CONSTRAINT coverage_intervals_natural_uk UNIQUE (target_spec_id, range_start_at, range_end_at, status);


--
-- Name: coverage_intervals coverage_intervals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coverage_intervals
    ADD CONSTRAINT coverage_intervals_pkey PRIMARY KEY (id);


--
-- Name: coverage_intervals coverage_intervals_target_spec_id_tstzrange_excl; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coverage_intervals
    ADD CONSTRAINT coverage_intervals_target_spec_id_tstzrange_excl EXCLUDE USING gist (target_spec_id WITH =, tstzrange(range_start_at, range_end_at, '[)'::text) WITH &&);


--
-- Name: data_quality_events data_quality_events_fingerprint_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_quality_events
    ADD CONSTRAINT data_quality_events_fingerprint_uk UNIQUE (target_spec_id, event_type, detected_at, fingerprint);


--
-- Name: data_quality_events data_quality_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_quality_events
    ADD CONSTRAINT data_quality_events_pkey PRIMARY KEY (id);


--
-- Name: dataset_build_coverage_snapshots dataset_build_coverage_snapsh_dataset_build_series_id_range_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_coverage_snapshots
    ADD CONSTRAINT dataset_build_coverage_snapsh_dataset_build_series_id_range_key UNIQUE (dataset_build_series_id, range_start_at, range_end_at);


--
-- Name: dataset_build_coverage_snapshots dataset_build_coverage_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_coverage_snapshots
    ADD CONSTRAINT dataset_build_coverage_snapshots_pkey PRIMARY KEY (id);


--
-- Name: dataset_build_market_status_snapshots dataset_build_market_status_s_dataset_build_id_source_marke_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_market_status_snapshots
    ADD CONSTRAINT dataset_build_market_status_s_dataset_build_id_source_marke_key UNIQUE (dataset_build_id, source_market_status_history_id);


--
-- Name: dataset_build_market_status_snapshots dataset_build_market_status_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_market_status_snapshots
    ADD CONSTRAINT dataset_build_market_status_snapshots_pkey PRIMARY KEY (id);


--
-- Name: dataset_build_series dataset_build_series_dataset_build_id_instrument_id_data_ki_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_dataset_build_id_instrument_id_data_ki_key UNIQUE NULLS NOT DISTINCT (dataset_build_id, instrument_id, data_kind, unit, definition_set_hash, calculation_version);


--
-- Name: dataset_build_series dataset_build_series_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_pkey PRIMARY KEY (id);


--
-- Name: dataset_builds dataset_builds_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_builds
    ADD CONSTRAINT dataset_builds_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: dataset_builds dataset_builds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_builds
    ADD CONSTRAINT dataset_builds_pkey PRIMARY KEY (id);


--
-- Name: dataset_version_candles dataset_version_candles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_candles
    ADD CONSTRAINT dataset_version_candles_pkey PRIMARY KEY (dataset_version_id, dataset_version_series_id, occurred_at);


--
-- Name: dataset_version_coverage_snapshots dataset_version_coverage_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_coverage_snapshots
    ADD CONSTRAINT dataset_version_coverage_snapshots_pkey PRIMARY KEY (dataset_version_id, source_build_coverage_snapshot_id);


--
-- Name: dataset_version_indicators dataset_version_indicators_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_indicators
    ADD CONSTRAINT dataset_version_indicators_pkey PRIMARY KEY (dataset_version_id, dataset_version_series_id, occurred_at);


--
-- Name: dataset_version_market_statistics dataset_version_market_statistics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_market_statistics
    ADD CONSTRAINT dataset_version_market_statistics_pkey PRIMARY KEY (dataset_version_id, dataset_version_series_id, occurred_at);


--
-- Name: dataset_version_market_status_snapshots dataset_version_market_status_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_market_status_snapshots
    ADD CONSTRAINT dataset_version_market_status_snapshots_pkey PRIMARY KEY (dataset_version_id, source_build_snapshot_id);


--
-- Name: dataset_version_microstructures dataset_version_microstructures_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_microstructures
    ADD CONSTRAINT dataset_version_microstructures_pkey PRIMARY KEY (dataset_version_id, dataset_version_series_id, occurred_at);


--
-- Name: dataset_version_series dataset_version_series_dataset_version_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_dataset_version_id_id_key UNIQUE (dataset_version_id, id);


--
-- Name: dataset_version_series dataset_version_series_dataset_version_id_instrument_id_dat_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_dataset_version_id_instrument_id_dat_key UNIQUE NULLS NOT DISTINCT (dataset_version_id, instrument_id, data_kind, unit, definition_set_hash, calculation_version);


--
-- Name: dataset_version_series dataset_version_series_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_pkey PRIMARY KEY (id);


--
-- Name: dataset_versions dataset_versions_content_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_versions
    ADD CONSTRAINT dataset_versions_content_hash_key UNIQUE (content_hash);


--
-- Name: dataset_versions dataset_versions_id_content_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_versions
    ADD CONSTRAINT dataset_versions_id_content_hash_key UNIQUE (id, content_hash);


--
-- Name: dataset_versions dataset_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_versions
    ADD CONSTRAINT dataset_versions_pkey PRIMARY KEY (id);


--
-- Name: exchange_accounts exchange_accounts_exchange_account_stable_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_accounts
    ADD CONSTRAINT exchange_accounts_exchange_account_stable_id_key UNIQUE (exchange, account_stable_id);


--
-- Name: exchange_accounts exchange_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_accounts
    ADD CONSTRAINT exchange_accounts_pkey PRIMARY KEY (id);


--
-- Name: exchange_orders exchange_orders_order_intent_id_simulated_order_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_orders
    ADD CONSTRAINT exchange_orders_order_intent_id_simulated_order_key_key UNIQUE (order_intent_id, simulated_order_key);


--
-- Name: exchange_orders exchange_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_orders
    ADD CONSTRAINT exchange_orders_pkey PRIMARY KEY (id);


--
-- Name: fetch_manifests fetch_manifests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fetch_manifests
    ADD CONSTRAINT fetch_manifests_pkey PRIMARY KEY (id);


--
-- Name: fetch_manifests fetch_manifests_request_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fetch_manifests
    ADD CONSTRAINT fetch_manifests_request_uk UNIQUE (source, request_fingerprint, requested_at);


--
-- Name: indicator_definition_versions indicator_definition_versions_definition_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_definition_versions
    ADD CONSTRAINT indicator_definition_versions_definition_hash_key UNIQUE (definition_hash);


--
-- Name: indicator_definition_versions indicator_definition_versions_definition_id_implementation__key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_definition_versions
    ADD CONSTRAINT indicator_definition_versions_definition_id_implementation__key UNIQUE (definition_id, implementation_version);


--
-- Name: indicator_definition_versions indicator_definition_versions_definition_id_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_definition_versions
    ADD CONSTRAINT indicator_definition_versions_definition_id_version_key UNIQUE (definition_id, version);


--
-- Name: indicator_definition_versions indicator_definition_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_definition_versions
    ADD CONSTRAINT indicator_definition_versions_pkey PRIMARY KEY (id);


--
-- Name: indicator_definitions indicator_definitions_indicator_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_definitions
    ADD CONSTRAINT indicator_definitions_indicator_key_key UNIQUE (indicator_key);


--
-- Name: indicator_definitions indicator_definitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_definitions
    ADD CONSTRAINT indicator_definitions_pkey PRIMARY KEY (id);


--
-- Name: indicator_invalidations indicator_invalidations_changed_quality_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_invalidations
    ADD CONSTRAINT indicator_invalidations_changed_quality_event_id_key UNIQUE (changed_quality_event_id);


--
-- Name: indicator_invalidations indicator_invalidations_changed_rollup_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_invalidations
    ADD CONSTRAINT indicator_invalidations_changed_rollup_id_key UNIQUE (changed_rollup_id);


--
-- Name: indicator_invalidations indicator_invalidations_changed_rollup_invalidation_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_invalidations
    ADD CONSTRAINT indicator_invalidations_changed_rollup_invalidation_id_key UNIQUE (changed_rollup_invalidation_id);


--
-- Name: indicator_invalidations indicator_invalidations_changed_source_revision_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_invalidations
    ADD CONSTRAINT indicator_invalidations_changed_source_revision_id_key UNIQUE (changed_source_revision_id);


--
-- Name: indicator_invalidations indicator_invalidations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_invalidations
    ADD CONSTRAINT indicator_invalidations_pkey PRIMARY KEY (id);


--
-- Name: indicator_materializations indicator_materializations_instrument_id_candle_unit_occurr_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_materializations
    ADD CONSTRAINT indicator_materializations_instrument_id_candle_unit_occurr_key UNIQUE NULLS NOT DISTINCT (instrument_id, candle_unit, occurred_at, definition_set_hash, current_rollup_id, current_source_revision_id, source_revision_through_id, quality_event_through_id, content_hash);


--
-- Name: indicator_materializations indicator_materializations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_materializations
    ADD CONSTRAINT indicator_materializations_pkey PRIMARY KEY (id);


--
-- Name: indicator_value_rollups indicator_value_rollups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_value_rollups
    ADD CONSTRAINT indicator_value_rollups_pkey PRIMARY KEY (indicator_value_id, candle_rollup_id);


--
-- Name: indicator_values indicator_values_materialization_id_definition_version_id_v_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_values
    ADD CONSTRAINT indicator_values_materialization_id_definition_version_id_v_key UNIQUE (materialization_id, definition_version_id, value_name);


--
-- Name: indicator_values indicator_values_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_values
    ADD CONSTRAINT indicator_values_pkey PRIMARY KEY (id);


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
-- Name: kill_switches kill_switches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kill_switches
    ADD CONSTRAINT kill_switches_pkey PRIMARY KEY (id);


--
-- Name: kill_switches kill_switches_scope_type_scope_key_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kill_switches
    ADD CONSTRAINT kill_switches_scope_type_scope_key_sequence_key UNIQUE (scope_type, scope_key, sequence);


--
-- Name: live_order_identifiers live_order_identifiers_exchange_account_id_identifier_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.live_order_identifiers
    ADD CONSTRAINT live_order_identifiers_exchange_account_id_identifier_key UNIQUE (exchange_account_id, identifier);


--
-- Name: live_order_identifiers live_order_identifiers_order_intent_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.live_order_identifiers
    ADD CONSTRAINT live_order_identifiers_order_intent_id_key UNIQUE (order_intent_id);


--
-- Name: live_order_identifiers live_order_identifiers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.live_order_identifiers
    ADD CONSTRAINT live_order_identifiers_pkey PRIMARY KEY (id);


--
-- Name: market_statistics market_statistics_market_id_interval_occurred_at_calculatio_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_statistics
    ADD CONSTRAINT market_statistics_market_id_interval_occurred_at_calculatio_key UNIQUE NULLS NOT DISTINCT (market_id, "interval", occurred_at, calculation_version, current_rollup_id, current_source_revision_id, source_revision_through_id, quality_event_through_id, content_hash);


--
-- Name: market_statistics market_statistics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_statistics
    ADD CONSTRAINT market_statistics_pkey PRIMARY KEY (id);


--
-- Name: market_status_history market_status_history_market_from_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_status_history
    ADD CONSTRAINT market_status_history_market_from_uk UNIQUE (market_id, valid_from);


--
-- Name: market_status_history market_status_history_market_id_tstzrange_excl; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_status_history
    ADD CONSTRAINT market_status_history_market_id_tstzrange_excl EXCLUDE USING gist (market_id WITH =, tstzrange(valid_from, valid_to, '[)'::text) WITH &&);


--
-- Name: market_status_history market_status_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_status_history
    ADD CONSTRAINT market_status_history_pkey PRIMARY KEY (id);


--
-- Name: markets markets_exchange_market_code_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.markets
    ADD CONSTRAINT markets_exchange_market_code_uk UNIQUE (exchange, market_code);


--
-- Name: markets markets_legacy_instrument_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.markets
    ADD CONSTRAINT markets_legacy_instrument_id_key UNIQUE (legacy_instrument_id);


--
-- Name: markets markets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.markets
    ADD CONSTRAINT markets_pkey PRIMARY KEY (id);


--
-- Name: microstructure_definition_versions microstructure_definition_versions_calculation_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_definition_versions
    ADD CONSTRAINT microstructure_definition_versions_calculation_version_key UNIQUE (calculation_version);


--
-- Name: microstructure_definition_versions microstructure_definition_versions_definition_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_definition_versions
    ADD CONSTRAINT microstructure_definition_versions_definition_hash_key UNIQUE (definition_hash);


--
-- Name: microstructure_definition_versions microstructure_definition_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_definition_versions
    ADD CONSTRAINT microstructure_definition_versions_pkey PRIMARY KEY (id);


--
-- Name: microstructure_invalidations microstructure_invalidations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_invalidations
    ADD CONSTRAINT microstructure_invalidations_pkey PRIMARY KEY (id);


--
-- Name: microstructure_materializations microstructure_materializatio_instrument_id_definition_vers_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materializations
    ADD CONSTRAINT microstructure_materializatio_instrument_id_definition_vers_key UNIQUE NULLS NOT DISTINCT (instrument_id, definition_version_id, bucket_start_at, orderbook_snapshot_through_id, trade_event_through_id, source_receipt_through_id, source_candle_revision_id, quality_event_through_id, connection_quality_through_id, content_hash);


--
-- Name: microstructure_materialization_orderbooks microstructure_materialization_orderbooks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materialization_orderbooks
    ADD CONSTRAINT microstructure_materialization_orderbooks_pkey PRIMARY KEY (materialization_id, orderbook_snapshot_id);


--
-- Name: microstructure_materialization_trades microstructure_materialization_trades_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materialization_trades
    ADD CONSTRAINT microstructure_materialization_trades_pkey PRIMARY KEY (materialization_id, trade_event_id);


--
-- Name: microstructure_materializations microstructure_materializations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materializations
    ADD CONSTRAINT microstructure_materializations_pkey PRIMARY KEY (id);


--
-- Name: microstructure_statistics microstructure_statistics_materialization_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_statistics
    ADD CONSTRAINT microstructure_statistics_materialization_id_key UNIQUE (materialization_id);


--
-- Name: microstructure_statistics microstructure_statistics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_statistics
    ADD CONSTRAINT microstructure_statistics_pkey PRIMARY KEY (id);


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
-- Name: order_fills order_fills_exchange_order_id_fill_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_fills
    ADD CONSTRAINT order_fills_exchange_order_id_fill_sequence_key UNIQUE (exchange_order_id, fill_sequence);


--
-- Name: order_fills order_fills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_fills
    ADD CONSTRAINT order_fills_pkey PRIMARY KEY (id);


--
-- Name: order_intents order_intents_bot_instance_id_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_intents
    ADD CONSTRAINT order_intents_bot_instance_id_idempotency_key_key UNIQUE (bot_instance_id, idempotency_key);


--
-- Name: order_intents order_intents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_intents
    ADD CONSTRAINT order_intents_pkey PRIMARY KEY (id);


--
-- Name: orderbook_snapshot_levels orderbook_snapshot_levels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_snapshot_levels
    ADD CONSTRAINT orderbook_snapshot_levels_pkey PRIMARY KEY (snapshot_id, level_index);


--
-- Name: orderbook_snapshots orderbook_snapshots_economic_state_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_snapshots
    ADD CONSTRAINT orderbook_snapshots_economic_state_uk UNIQUE (instrument_id, source, occurred_at, payload_checksum);


--
-- Name: orderbook_snapshots orderbook_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_snapshots
    ADD CONSTRAINT orderbook_snapshots_pkey PRIMARY KEY (id);


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
-- Name: p1_audit_recovery_gate p1_audit_recovery_gate_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.p1_audit_recovery_gate
    ADD CONSTRAINT p1_audit_recovery_gate_pkey PRIMARY KEY (singleton);


--
-- Name: paper_execution_jobs paper_execution_jobs_order_intent_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paper_execution_jobs
    ADD CONSTRAINT paper_execution_jobs_order_intent_id_key UNIQUE (order_intent_id);


--
-- Name: paper_execution_jobs paper_execution_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paper_execution_jobs
    ADD CONSTRAINT paper_execution_jobs_pkey PRIMARY KEY (id);


--
-- Name: portfolio_policies portfolio_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_policies
    ADD CONSTRAINT portfolio_policies_pkey PRIMARY KEY (id);


--
-- Name: portfolio_policies portfolio_policies_portfolio_id_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_policies
    ADD CONSTRAINT portfolio_policies_portfolio_id_version_key UNIQUE (portfolio_id, version);


--
-- Name: portfolios portfolios_owner_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolios
    ADD CONSTRAINT portfolios_owner_id_name_key UNIQUE (owner_id, name);


--
-- Name: portfolios portfolios_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolios
    ADD CONSTRAINT portfolios_pkey PRIMARY KEY (id);


--
-- Name: position_projections position_projections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.position_projections
    ADD CONSTRAINT position_projections_pkey PRIMARY KEY (id);


--
-- Name: position_projections position_projections_portfolio_id_instrument_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.position_projections
    ADD CONSTRAINT position_projections_portfolio_id_instrument_id_key UNIQUE (portfolio_id, instrument_id);


--
-- Name: raw_response_samples raw_response_samples_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_response_samples
    ADD CONSTRAINT raw_response_samples_pkey PRIMARY KEY (id);


--
-- Name: realtime_connection_quality_intervals realtime_connection_quality_inter_connection_id_fingerprint_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.realtime_connection_quality_intervals
    ADD CONSTRAINT realtime_connection_quality_inter_connection_id_fingerprint_key UNIQUE (connection_id, fingerprint);


--
-- Name: realtime_connection_quality_intervals realtime_connection_quality_intervals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.realtime_connection_quality_intervals
    ADD CONSTRAINT realtime_connection_quality_intervals_pkey PRIMARY KEY (id);


--
-- Name: realtime_connection_sessions realtime_connection_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.realtime_connection_sessions
    ADD CONSTRAINT realtime_connection_sessions_pkey PRIMARY KEY (connection_id);


--
-- Name: reconciliation_runs reconciliation_runs_exchange_order_id_run_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconciliation_runs
    ADD CONSTRAINT reconciliation_runs_exchange_order_id_run_key_key UNIQUE (exchange_order_id, run_key);


--
-- Name: reconciliation_runs reconciliation_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconciliation_runs
    ADD CONSTRAINT reconciliation_runs_pkey PRIMARY KEY (id);


--
-- Name: risk_events risk_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_events
    ADD CONSTRAINT risk_events_pkey PRIMARY KEY (id);


--
-- Name: risk_events risk_events_scope_type_scope_key_fingerprint_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_events
    ADD CONSTRAINT risk_events_scope_type_scope_key_fingerprint_key UNIQUE (scope_type, scope_key, fingerprint);


--
-- Name: risk_limits risk_limits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_limits
    ADD CONSTRAINT risk_limits_pkey PRIMARY KEY (id);


--
-- Name: risk_limits risk_limits_scope_type_scope_key_limit_type_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_limits
    ADD CONSTRAINT risk_limits_scope_type_scope_key_limit_type_version_key UNIQUE (scope_type, scope_key, limit_type, version);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: source_candle_revisions source_candle_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_candle_revisions
    ADD CONSTRAINT source_candle_revisions_pkey PRIMARY KEY (id);


--
-- Name: source_candle_revisions source_candle_revisions_source_candle_id_revision_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_candle_revisions
    ADD CONSTRAINT source_candle_revisions_source_candle_id_revision_number_key UNIQUE (source_candle_id, revision_number);


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
-- Name: source_receipts source_receipts_connection_frame_uk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_receipts
    ADD CONSTRAINT source_receipts_connection_frame_uk UNIQUE (connection_id, frame_sequence);


--
-- Name: source_receipts source_receipts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_receipts
    ADD CONSTRAINT source_receipts_pkey PRIMARY KEY (id);


--
-- Name: strategy_definitions strategy_definitions_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_definitions
    ADD CONSTRAINT strategy_definitions_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: strategy_definitions strategy_definitions_owner_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_definitions
    ADD CONSTRAINT strategy_definitions_owner_id_name_key UNIQUE (owner_id, name);


--
-- Name: strategy_definitions strategy_definitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_definitions
    ADD CONSTRAINT strategy_definitions_pkey PRIMARY KEY (id);


--
-- Name: strategy_graphs strategy_graphs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_graphs
    ADD CONSTRAINT strategy_graphs_pkey PRIMARY KEY (strategy_version_id);


--
-- Name: strategy_parameters strategy_parameters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_parameters
    ADD CONSTRAINT strategy_parameters_pkey PRIMARY KEY (id);


--
-- Name: strategy_parameters strategy_parameters_strategy_version_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_parameters
    ADD CONSTRAINT strategy_parameters_strategy_version_id_name_key UNIQUE (strategy_version_id, name);


--
-- Name: strategy_versions strategy_versions_id_graph_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_versions
    ADD CONSTRAINT strategy_versions_id_graph_hash_key UNIQUE (id, graph_hash);


--
-- Name: strategy_versions strategy_versions_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_versions
    ADD CONSTRAINT strategy_versions_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: strategy_versions strategy_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_versions
    ADD CONSTRAINT strategy_versions_pkey PRIMARY KEY (id);


--
-- Name: strategy_versions strategy_versions_strategy_id_graph_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_versions
    ADD CONSTRAINT strategy_versions_strategy_id_graph_hash_key UNIQUE (strategy_id, graph_hash);


--
-- Name: strategy_versions strategy_versions_strategy_id_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_versions
    ADD CONSTRAINT strategy_versions_strategy_id_version_key UNIQUE (strategy_id, version);


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
-- Name: trading_capabilities trading_capabilities_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_capabilities
    ADD CONSTRAINT trading_capabilities_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: trading_capabilities trading_capabilities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_capabilities
    ADD CONSTRAINT trading_capabilities_pkey PRIMARY KEY (id);


--
-- Name: trading_capabilities trading_capabilities_request_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_capabilities
    ADD CONSTRAINT trading_capabilities_request_id_key UNIQUE (request_id);


--
-- Name: upbit_api_key_permission_attestations upbit_api_key_permission_attestations_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_api_key_permission_attestations
    ADD CONSTRAINT upbit_api_key_permission_attestations_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: upbit_api_key_permission_attestations upbit_api_key_permission_attestations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_api_key_permission_attestations
    ADD CONSTRAINT upbit_api_key_permission_attestations_pkey PRIMARY KEY (id);


--
-- Name: upbit_api_key_permission_attestations upbit_api_key_permission_attestations_request_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_api_key_permission_attestations
    ADD CONSTRAINT upbit_api_key_permission_attestations_request_id_key UNIQUE (request_id);


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bin_exchange_account_id_upbit_ide_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_exchange_order_bindings
    ADD CONSTRAINT upbit_live_exchange_order_bin_exchange_account_id_upbit_ide_key UNIQUE (exchange_account_id, upbit_identifier);


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bin_exchange_account_id_upbit_ord_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_exchange_order_bindings
    ADD CONSTRAINT upbit_live_exchange_order_bin_exchange_account_id_upbit_ord_key UNIQUE (exchange_account_id, upbit_order_uuid);


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bindings_exchange_order_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_exchange_order_bindings
    ADD CONSTRAINT upbit_live_exchange_order_bindings_exchange_order_id_key UNIQUE (exchange_order_id);


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bindings_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_exchange_order_bindings
    ADD CONSTRAINT upbit_live_exchange_order_bindings_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bindings_live_order_identifier_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_exchange_order_bindings
    ADD CONSTRAINT upbit_live_exchange_order_bindings_live_order_identifier_id_key UNIQUE (live_order_identifier_id);


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bindings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_exchange_order_bindings
    ADD CONSTRAINT upbit_live_exchange_order_bindings_pkey PRIMARY KEY (id);


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bindings_request_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_exchange_order_bindings
    ADD CONSTRAINT upbit_live_exchange_order_bindings_request_id_key UNIQUE (request_id);


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bindings_upbit_order_outbox_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_exchange_order_bindings
    ADD CONSTRAINT upbit_live_exchange_order_bindings_upbit_order_outbox_id_key UNIQUE (upbit_order_outbox_id);


--
-- Name: upbit_live_reconciliation_applications upbit_live_reconciliation_application_reconciliation_run_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_reconciliation_applications
    ADD CONSTRAINT upbit_live_reconciliation_application_reconciliation_run_id_key UNIQUE (reconciliation_run_id);


--
-- Name: upbit_live_reconciliation_applications upbit_live_reconciliation_applications_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_reconciliation_applications
    ADD CONSTRAINT upbit_live_reconciliation_applications_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: upbit_live_reconciliation_applications upbit_live_reconciliation_applications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_reconciliation_applications
    ADD CONSTRAINT upbit_live_reconciliation_applications_pkey PRIMARY KEY (id);


--
-- Name: upbit_live_reconciliation_applications upbit_live_reconciliation_applications_request_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_reconciliation_applications
    ADD CONSTRAINT upbit_live_reconciliation_applications_request_id_key UNIQUE (request_id);


--
-- Name: upbit_order_identifier_reservations upbit_order_identifier_reserv_exchange_account_id_identifie_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_identifier_reservations
    ADD CONSTRAINT upbit_order_identifier_reserv_exchange_account_id_identifie_key UNIQUE (exchange_account_id, identifier);


--
-- Name: upbit_order_identifier_reservations upbit_order_identifier_reserv_source_table_source_column_so_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_identifier_reservations
    ADD CONSTRAINT upbit_order_identifier_reserv_source_table_source_column_so_key UNIQUE (source_table, source_column, source_id);


--
-- Name: upbit_order_identifier_reservations upbit_order_identifier_reservations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_identifier_reservations
    ADD CONSTRAINT upbit_order_identifier_reservations_pkey PRIMARY KEY (id);


--
-- Name: upbit_order_outbox upbit_order_outbox_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_outbox
    ADD CONSTRAINT upbit_order_outbox_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: upbit_order_outbox upbit_order_outbox_order_intent_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_outbox
    ADD CONSTRAINT upbit_order_outbox_order_intent_id_key UNIQUE (order_intent_id);


--
-- Name: upbit_order_outbox upbit_order_outbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_outbox
    ADD CONSTRAINT upbit_order_outbox_pkey PRIMARY KEY (id);


--
-- Name: upbit_order_outbox upbit_order_outbox_request_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_outbox
    ADD CONSTRAINT upbit_order_outbox_request_id_key UNIQUE (request_id);


--
-- Name: upbit_order_submit_rehearsals upbit_order_submit_rehearsals_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_submit_rehearsals
    ADD CONSTRAINT upbit_order_submit_rehearsals_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: upbit_order_submit_rehearsals upbit_order_submit_rehearsals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_submit_rehearsals
    ADD CONSTRAINT upbit_order_submit_rehearsals_pkey PRIMARY KEY (id);


--
-- Name: upbit_order_submit_rehearsals upbit_order_submit_rehearsals_request_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_submit_rehearsals
    ADD CONSTRAINT upbit_order_submit_rehearsals_request_id_key UNIQUE (request_id);


--
-- Name: upbit_order_submit_rehearsals upbit_order_submit_rehearsals_upbit_order_outbox_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_submit_rehearsals
    ADD CONSTRAINT upbit_order_submit_rehearsals_upbit_order_outbox_id_key UNIQUE (upbit_order_outbox_id);


--
-- Name: upbit_order_test_runs upbit_order_test_runs_exchange_account_id_request_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_test_runs
    ADD CONSTRAINT upbit_order_test_runs_exchange_account_id_request_id_key UNIQUE (exchange_account_id, request_id);


--
-- Name: upbit_order_test_runs upbit_order_test_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_test_runs
    ADD CONSTRAINT upbit_order_test_runs_pkey PRIMARY KEY (id);


--
-- Name: audit_logs_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX audit_logs_created_at_idx ON public.audit_logs USING btree (created_at DESC);


--
-- Name: backfill_jobs_lease_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX backfill_jobs_lease_idx ON public.backfill_jobs USING btree (status, next_retry_at, lease_expires_at, priority DESC, created_at);


--
-- Name: backfill_jobs_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX backfill_jobs_status_idx ON public.backfill_jobs USING btree (status, created_at DESC);


--
-- Name: backtest_runs_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX backtest_runs_status_idx ON public.backtest_runs USING btree (status, created_at, id);


--
-- Name: backtest_runs_strategy_dataset_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX backtest_runs_strategy_dataset_idx ON public.backtest_runs USING btree (strategy_version_id, dataset_version_id, created_at DESC, id DESC);


--
-- Name: backtest_runs_worker_lease_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX backtest_runs_worker_lease_idx ON public.backtest_runs USING btree (status, next_retry_at, lease_expires_at, requested_at, id);


--
-- Name: bot_instances_stage_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX bot_instances_stage_idx ON public.bot_instances USING btree (stage, execution_mode);


--
-- Name: candle_rollup_invalidations_range_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX candle_rollup_invalidations_range_idx ON public.candle_rollup_invalidations USING btree (instrument_id, candle_unit, calculation_version, range_start_at, range_end_at);


--
-- Name: candle_rollup_recompute_jobs_claim_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX candle_rollup_recompute_jobs_claim_idx ON public.candle_rollup_recompute_jobs USING btree (status, next_retry_at, lease_expires_at, priority DESC, created_at);


--
-- Name: candle_rollups_current_projection_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX candle_rollups_current_projection_idx ON public.candle_rollups USING btree (instrument_id, candle_unit, calculation_version, candle_start_at, source_revision_through_id DESC, quality_event_through_id DESC NULLS LAST, knowledge_at DESC, id DESC);


--
-- Name: candle_rollups_range_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX candle_rollups_range_idx ON public.candle_rollups USING btree (instrument_id, candle_unit, calculation_version, candle_start_at DESC);


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
-- Name: collection_runs_worker_run_key_uk; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX collection_runs_worker_run_key_uk ON public.collection_runs USING btree (worker_role, run_key) WHERE ((worker_role IS NOT NULL) AND (run_key IS NOT NULL));


--
-- Name: collection_subscription_desires_generation_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX collection_subscription_desires_generation_idx ON public.collection_subscription_desires USING btree (desired_state, generation, applied_generation);


--
-- Name: collection_target_specs_scheduler_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX collection_target_specs_scheduler_idx ON public.collection_target_specs USING btree (status, priority DESC, updated_at);


--
-- Name: collection_worker_heartbeats_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX collection_worker_heartbeats_status_idx ON public.collection_worker_heartbeats USING btree (status, last_heartbeat_at DESC);


--
-- Name: coverage_intervals_target_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX coverage_intervals_target_time_idx ON public.coverage_intervals USING btree (target_spec_id, range_start_at, range_end_at);


--
-- Name: dataset_builds_claim_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX dataset_builds_claim_idx ON public.dataset_builds USING btree (COALESCE(next_retry_at, created_at), id) WHERE (status = ANY (ARRAY['pending'::text, 'running'::text, 'retry_wait'::text]));


--
-- Name: dataset_versions_as_of_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX dataset_versions_as_of_idx ON public.dataset_versions USING btree (as_of, id);


--
-- Name: exchange_orders_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX exchange_orders_status_idx ON public.exchange_orders USING btree (status, submitted_at);


--
-- Name: indicator_invalidations_claim_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX indicator_invalidations_claim_idx ON public.indicator_invalidations USING btree (status, impact_start_at, created_at);


--
-- Name: indicator_materializations_projection_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX indicator_materializations_projection_idx ON public.indicator_materializations USING btree (instrument_id, candle_unit, occurred_at, knowledge_at, source_revision_through_id DESC, quality_event_through_id DESC NULLS LAST, id DESC);


--
-- Name: kill_switches_scope_state_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX kill_switches_scope_state_idx ON public.kill_switches USING btree (scope_type, scope_key, state, sequence DESC);


--
-- Name: live_order_identifiers_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX live_order_identifiers_status_idx ON public.live_order_identifiers USING btree (status, created_at, id);


--
-- Name: market_status_history_point_in_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX market_status_history_point_in_time_idx ON public.market_status_history USING btree (market_id, valid_from DESC, valid_to);


--
-- Name: markets_quote_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX markets_quote_status_idx ON public.markets USING btree (exchange, quote_currency, market_code);


--
-- Name: microstructure_invalidations_claim_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX microstructure_invalidations_claim_idx ON public.microstructure_invalidations USING btree (status, next_retry_at, bucket_start_at, id);


--
-- Name: microstructure_invalidations_pending_bucket_uk; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX microstructure_invalidations_pending_bucket_uk ON public.microstructure_invalidations USING btree (instrument_id, bucket_start_at, source_candle_revision_id, quality_event_through_id, connection_quality_through_id) NULLS NOT DISTINCT WHERE (status = ANY (ARRAY['pending'::text, 'retry_wait'::text]));


--
-- Name: microstructure_materializations_projection_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX microstructure_materializations_projection_idx ON public.microstructure_materializations USING btree (instrument_id, bucket_start_at, knowledge_at, orderbook_snapshot_through_id DESC, trade_event_through_id DESC, source_receipt_through_id DESC, id DESC);


--
-- Name: missing_ranges_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX missing_ranges_status_idx ON public.missing_ranges USING btree (status, instrument_id, data_type);


--
-- Name: notification_events_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notification_events_status_idx ON public.notification_events USING btree (status, created_at DESC);


--
-- Name: order_intents_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX order_intents_status_idx ON public.order_intents USING btree (status, created_at);


--
-- Name: orderbook_snapshots_market_occurred_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX orderbook_snapshots_market_occurred_idx ON public.orderbook_snapshots USING btree (market_id, occurred_at DESC);


--
-- Name: orderbook_snapshots_source_receipt_uk; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX orderbook_snapshots_source_receipt_uk ON public.orderbook_snapshots USING btree (source_receipt_id) WHERE (source_receipt_id IS NOT NULL);


--
-- Name: orderbook_summaries_collected_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX orderbook_summaries_collected_at_idx ON public.orderbook_summaries USING btree (collected_at DESC);


--
-- Name: orderbook_summaries_instrument_bucket_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX orderbook_summaries_instrument_bucket_idx ON public.orderbook_summaries USING btree (instrument_id, bucket_at DESC);


--
-- Name: paper_execution_jobs_claim_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX paper_execution_jobs_claim_idx ON public.paper_execution_jobs USING btree (status, next_retry_at, lease_expires_at, priority DESC, created_at, id);


--
-- Name: portfolios_idempotency_key_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX portfolios_idempotency_key_unique ON public.portfolios USING btree (idempotency_key) WHERE (idempotency_key IS NOT NULL);


--
-- Name: reconciliation_runs_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX reconciliation_runs_status_idx ON public.reconciliation_runs USING btree (status, completed_at DESC, id DESC);


--
-- Name: risk_limits_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX risk_limits_active_idx ON public.risk_limits USING btree (scope_type, scope_key, limit_type) WHERE (status = 'active'::text);


--
-- Name: source_candle_revisions_incremental_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX source_candle_revisions_incremental_lookup_idx ON public.source_candle_revisions USING btree (instrument_id, candle_unit, candle_start_at, id DESC);


--
-- Name: source_candle_revisions_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX source_candle_revisions_lookup_idx ON public.source_candle_revisions USING btree (instrument_id, candle_unit, candle_start_at, revision_number DESC);


--
-- Name: source_candles_collected_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX source_candles_collected_at_idx ON public.source_candles USING btree (collected_at DESC);


--
-- Name: source_candles_instrument_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX source_candles_instrument_time_idx ON public.source_candles USING btree (instrument_id, candle_unit, candle_start_at DESC);


--
-- Name: source_candles_source_receipt_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX source_candles_source_receipt_idx ON public.source_candles USING btree (source_receipt_id) WHERE (source_receipt_id IS NOT NULL);


--
-- Name: source_receipts_market_occurred_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX source_receipts_market_occurred_idx ON public.source_receipts USING btree (market_id, data_type, occurred_at DESC);


--
-- Name: source_receipts_payload_checksum_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX source_receipts_payload_checksum_idx ON public.source_receipts USING btree (payload_checksum);


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
-- Name: ticker_snapshots_source_receipt_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ticker_snapshots_source_receipt_idx ON public.ticker_snapshots USING btree (source_receipt_id) WHERE (source_receipt_id IS NOT NULL);


--
-- Name: trade_events_instrument_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trade_events_instrument_time_idx ON public.trade_events USING btree (instrument_id, trade_timestamp_at DESC);


--
-- Name: trade_events_source_receipt_uk; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trade_events_source_receipt_uk ON public.trade_events USING btree (source_receipt_id) WHERE (source_receipt_id IS NOT NULL);


--
-- Name: trading_capabilities_global_latest_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trading_capabilities_global_latest_idx ON public.trading_capabilities USING btree (scope_type, scope_key, created_at DESC, id DESC);


--
-- Name: upbit_api_key_permission_attestations_latest_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX upbit_api_key_permission_attestations_latest_idx ON public.upbit_api_key_permission_attestations USING btree (exchange_account_id, expires_at DESC, id DESC);


--
-- Name: upbit_live_exchange_order_bindings_observed_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX upbit_live_exchange_order_bindings_observed_idx ON public.upbit_live_exchange_order_bindings USING btree (source, observed_at, id);


--
-- Name: upbit_live_reconciliation_applications_observed_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX upbit_live_reconciliation_applications_observed_idx ON public.upbit_live_reconciliation_applications USING btree (source_endpoint, observed_state, applied_at, id);


--
-- Name: upbit_order_outbox_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX upbit_order_outbox_status_idx ON public.upbit_order_outbox USING btree (status, created_at, id);


--
-- Name: upbit_order_submit_rehearsals_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX upbit_order_submit_rehearsals_status_idx ON public.upbit_order_submit_rehearsals USING btree (rehearsal_status, rehearsed_at, id);


--
-- Name: upbit_order_test_runs_requested_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX upbit_order_test_runs_requested_idx ON public.upbit_order_test_runs USING btree (exchange_account_id, requested_at DESC, id DESC);


--
-- Name: backtest_artifacts backtest_artifacts_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER backtest_artifacts_append_only_delete BEFORE DELETE ON public.backtest_artifacts FOR EACH ROW EXECUTE FUNCTION public.reject_backtest_result_mutation();


--
-- Name: backtest_artifacts backtest_artifacts_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER backtest_artifacts_append_only_update BEFORE UPDATE ON public.backtest_artifacts FOR EACH ROW EXECUTE FUNCTION public.reject_backtest_result_mutation();


--
-- Name: backtest_equity_points backtest_equity_points_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER backtest_equity_points_append_only_delete BEFORE DELETE ON public.backtest_equity_points FOR EACH ROW EXECUTE FUNCTION public.reject_backtest_result_mutation();


--
-- Name: backtest_equity_points backtest_equity_points_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER backtest_equity_points_append_only_update BEFORE UPDATE ON public.backtest_equity_points FOR EACH ROW EXECUTE FUNCTION public.reject_backtest_result_mutation();


--
-- Name: backtest_metrics backtest_metrics_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER backtest_metrics_append_only_delete BEFORE DELETE ON public.backtest_metrics FOR EACH ROW EXECUTE FUNCTION public.reject_backtest_result_mutation();


--
-- Name: backtest_metrics backtest_metrics_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER backtest_metrics_append_only_update BEFORE UPDATE ON public.backtest_metrics FOR EACH ROW EXECUTE FUNCTION public.reject_backtest_result_mutation();


--
-- Name: backtest_runs backtest_runs_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER backtest_runs_append_only_delete BEFORE DELETE ON public.backtest_runs FOR EACH ROW EXECUTE FUNCTION public.enforce_backtest_run_terminal_seal();


--
-- Name: backtest_runs backtest_runs_terminal_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER backtest_runs_terminal_update BEFORE UPDATE ON public.backtest_runs FOR EACH ROW EXECUTE FUNCTION public.enforce_backtest_run_terminal_seal();


--
-- Name: backtest_runs backtest_runs_validate_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER backtest_runs_validate_insert BEFORE INSERT ON public.backtest_runs FOR EACH ROW EXECUTE FUNCTION public.validate_backtest_run_inputs();


--
-- Name: backtest_runs backtest_runs_validate_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER backtest_runs_validate_update BEFORE UPDATE ON public.backtest_runs FOR EACH ROW EXECUTE FUNCTION public.validate_backtest_run_inputs();


--
-- Name: backtest_trades backtest_trades_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER backtest_trades_append_only_delete BEFORE DELETE ON public.backtest_trades FOR EACH ROW EXECUTE FUNCTION public.reject_backtest_result_mutation();


--
-- Name: backtest_trades backtest_trades_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER backtest_trades_append_only_update BEFORE UPDATE ON public.backtest_trades FOR EACH ROW EXECUTE FUNCTION public.reject_backtest_result_mutation();


--
-- Name: bot_state_transitions bot_state_transitions_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER bot_state_transitions_append_only_delete BEFORE DELETE ON public.bot_state_transitions FOR EACH ROW EXECUTE FUNCTION public.reject_p5_append_only_mutation();


--
-- Name: bot_state_transitions bot_state_transitions_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER bot_state_transitions_append_only_update BEFORE UPDATE ON public.bot_state_transitions FOR EACH ROW EXECUTE FUNCTION public.reject_p5_append_only_mutation();


--
-- Name: candle_rollups candle_rollup_indicator_invalidation; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER candle_rollup_indicator_invalidation AFTER INSERT ON public.candle_rollups FOR EACH ROW EXECUTE FUNCTION public.enqueue_indicator_invalidation();


--
-- Name: candle_rollup_invalidations candle_rollup_invalidations_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER candle_rollup_invalidations_append_only_delete BEFORE DELETE ON public.candle_rollup_invalidations FOR EACH ROW EXECUTE FUNCTION public.reject_candle_rollup_invalidation_mutation();


--
-- Name: candle_rollup_invalidations candle_rollup_invalidations_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER candle_rollup_invalidations_append_only_update BEFORE UPDATE ON public.candle_rollup_invalidations FOR EACH ROW EXECUTE FUNCTION public.reject_candle_rollup_invalidation_mutation();


--
-- Name: candle_rollups candle_rollups_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER candle_rollups_append_only_delete BEFORE DELETE ON public.candle_rollups FOR EACH ROW EXECUTE FUNCTION public.reject_candle_rollup_mutation();


--
-- Name: candle_rollups candle_rollups_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER candle_rollups_append_only_update BEFORE UPDATE ON public.candle_rollups FOR EACH ROW EXECUTE FUNCTION public.reject_candle_rollup_mutation();


--
-- Name: candle_rollup_recompute_jobs completed_rollup_indicator_invalidation; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER completed_rollup_indicator_invalidation AFTER UPDATE OF status ON public.candle_rollup_recompute_jobs FOR EACH ROW EXECUTE FUNCTION public.enqueue_completed_rollup_indicator_invalidation();


--
-- Name: dataset_build_coverage_snapshots dataset_build_coverage_snapshots_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_build_coverage_snapshots_append_only_delete BEFORE DELETE ON public.dataset_build_coverage_snapshots FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_build_coverage_snapshots dataset_build_coverage_snapshots_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_build_coverage_snapshots_append_only_update BEFORE UPDATE ON public.dataset_build_coverage_snapshots FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_build_series dataset_build_series_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_build_series_append_only_delete BEFORE DELETE ON public.dataset_build_series FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_build_series dataset_build_series_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_build_series_append_only_update BEFORE UPDATE ON public.dataset_build_series FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_build_market_status_snapshots dataset_build_status_snapshots_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_build_status_snapshots_append_only_delete BEFORE DELETE ON public.dataset_build_market_status_snapshots FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_build_market_status_snapshots dataset_build_status_snapshots_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_build_status_snapshots_append_only_update BEFORE UPDATE ON public.dataset_build_market_status_snapshots FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_version_candles dataset_version_candles_a_sealed_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_candles_a_sealed_insert BEFORE INSERT ON public.dataset_version_candles FOR EACH ROW EXECUTE FUNCTION public.reject_sealed_dataset_version_child_insert();


--
-- Name: dataset_version_candles dataset_version_candles_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_candles_append_only_delete BEFORE DELETE ON public.dataset_version_candles FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_version_candles dataset_version_candles_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_candles_append_only_update BEFORE UPDATE ON public.dataset_version_candles FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_version_candles dataset_version_candles_identity; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_candles_identity BEFORE INSERT ON public.dataset_version_candles FOR EACH ROW EXECUTE FUNCTION public.validate_dataset_version_typed_member();


--
-- Name: dataset_version_coverage_snapshots dataset_version_coverage_snapshots_a_sealed_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_coverage_snapshots_a_sealed_insert BEFORE INSERT ON public.dataset_version_coverage_snapshots FOR EACH ROW EXECUTE FUNCTION public.reject_sealed_dataset_version_child_insert();


--
-- Name: dataset_version_coverage_snapshots dataset_version_coverage_snapshots_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_coverage_snapshots_append_only_delete BEFORE DELETE ON public.dataset_version_coverage_snapshots FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_version_coverage_snapshots dataset_version_coverage_snapshots_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_coverage_snapshots_append_only_update BEFORE UPDATE ON public.dataset_version_coverage_snapshots FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_version_indicators dataset_version_indicators_a_sealed_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_indicators_a_sealed_insert BEFORE INSERT ON public.dataset_version_indicators FOR EACH ROW EXECUTE FUNCTION public.reject_sealed_dataset_version_child_insert();


--
-- Name: dataset_version_indicators dataset_version_indicators_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_indicators_append_only_delete BEFORE DELETE ON public.dataset_version_indicators FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_version_indicators dataset_version_indicators_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_indicators_append_only_update BEFORE UPDATE ON public.dataset_version_indicators FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_version_indicators dataset_version_indicators_identity; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_indicators_identity BEFORE INSERT ON public.dataset_version_indicators FOR EACH ROW EXECUTE FUNCTION public.validate_dataset_version_typed_member();


--
-- Name: dataset_version_market_statistics dataset_version_market_statistics_a_sealed_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_market_statistics_a_sealed_insert BEFORE INSERT ON public.dataset_version_market_statistics FOR EACH ROW EXECUTE FUNCTION public.reject_sealed_dataset_version_child_insert();


--
-- Name: dataset_version_market_statistics dataset_version_market_statistics_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_market_statistics_append_only_delete BEFORE DELETE ON public.dataset_version_market_statistics FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_version_market_statistics dataset_version_market_statistics_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_market_statistics_append_only_update BEFORE UPDATE ON public.dataset_version_market_statistics FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_version_market_statistics dataset_version_market_statistics_identity; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_market_statistics_identity BEFORE INSERT ON public.dataset_version_market_statistics FOR EACH ROW EXECUTE FUNCTION public.validate_dataset_version_typed_member();


--
-- Name: dataset_version_market_status_snapshots dataset_version_market_status_snapshots_a_sealed_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_market_status_snapshots_a_sealed_insert BEFORE INSERT ON public.dataset_version_market_status_snapshots FOR EACH ROW EXECUTE FUNCTION public.reject_sealed_dataset_version_child_insert();


--
-- Name: dataset_version_market_status_snapshots dataset_version_market_status_snapshots_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_market_status_snapshots_append_only_delete BEFORE DELETE ON public.dataset_version_market_status_snapshots FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_version_market_status_snapshots dataset_version_market_status_snapshots_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_market_status_snapshots_append_only_update BEFORE UPDATE ON public.dataset_version_market_status_snapshots FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_version_microstructures dataset_version_microstructures_a_sealed_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_microstructures_a_sealed_insert BEFORE INSERT ON public.dataset_version_microstructures FOR EACH ROW EXECUTE FUNCTION public.reject_sealed_dataset_version_child_insert();


--
-- Name: dataset_version_microstructures dataset_version_microstructures_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_microstructures_append_only_delete BEFORE DELETE ON public.dataset_version_microstructures FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_version_microstructures dataset_version_microstructures_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_microstructures_append_only_update BEFORE UPDATE ON public.dataset_version_microstructures FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_version_microstructures dataset_version_microstructures_identity; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_microstructures_identity BEFORE INSERT ON public.dataset_version_microstructures FOR EACH ROW EXECUTE FUNCTION public.validate_dataset_version_typed_member();


--
-- Name: dataset_version_series dataset_version_series_a_sealed_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_series_a_sealed_insert BEFORE INSERT ON public.dataset_version_series FOR EACH ROW EXECUTE FUNCTION public.reject_sealed_dataset_version_child_insert();


--
-- Name: dataset_version_series dataset_version_series_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_series_append_only_delete BEFORE DELETE ON public.dataset_version_series FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_version_series dataset_version_series_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_version_series_append_only_update BEFORE UPDATE ON public.dataset_version_series FOR EACH ROW EXECUTE FUNCTION public.reject_dataset_version_mutation();


--
-- Name: dataset_versions dataset_versions_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_versions_append_only_delete BEFORE DELETE ON public.dataset_versions FOR EACH ROW EXECUTE FUNCTION public.enforce_dataset_version_seal();


--
-- Name: dataset_versions dataset_versions_seal_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER dataset_versions_seal_update BEFORE UPDATE ON public.dataset_versions FOR EACH ROW EXECUTE FUNCTION public.enforce_dataset_version_seal();


--
-- Name: exchange_orders exchange_orders_require_live_binding; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER exchange_orders_require_live_binding AFTER INSERT OR UPDATE ON public.exchange_orders DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.validate_p6_live_exchange_order_has_binding();


--
-- Name: indicator_definition_versions indicator_definition_versions_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER indicator_definition_versions_append_only BEFORE DELETE OR UPDATE ON public.indicator_definition_versions FOR EACH ROW EXECUTE FUNCTION public.reject_indicator_immutable_mutation();


--
-- Name: indicator_definitions indicator_definitions_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER indicator_definitions_append_only BEFORE DELETE OR UPDATE ON public.indicator_definitions FOR EACH ROW EXECUTE FUNCTION public.reject_indicator_immutable_mutation();


--
-- Name: indicator_materializations indicator_materializations_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER indicator_materializations_append_only BEFORE DELETE OR UPDATE ON public.indicator_materializations FOR EACH ROW EXECUTE FUNCTION public.reject_indicator_immutable_mutation();


--
-- Name: indicator_value_rollups indicator_value_rollups_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER indicator_value_rollups_append_only BEFORE DELETE OR UPDATE ON public.indicator_value_rollups FOR EACH ROW EXECUTE FUNCTION public.reject_indicator_immutable_mutation();


--
-- Name: indicator_values indicator_values_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER indicator_values_append_only BEFORE DELETE OR UPDATE ON public.indicator_values FOR EACH ROW EXECUTE FUNCTION public.reject_indicator_immutable_mutation();


--
-- Name: kill_switches kill_switches_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER kill_switches_append_only_delete BEFORE DELETE ON public.kill_switches FOR EACH ROW EXECUTE FUNCTION public.reject_p5_append_only_mutation();


--
-- Name: kill_switches kill_switches_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER kill_switches_append_only_update BEFORE UPDATE ON public.kill_switches FOR EACH ROW EXECUTE FUNCTION public.reject_p5_append_only_mutation();


--
-- Name: live_order_identifiers live_order_identifiers_reserve_identifier; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER live_order_identifiers_reserve_identifier AFTER INSERT OR UPDATE ON public.live_order_identifiers FOR EACH ROW EXECUTE FUNCTION public.reserve_p6_live_order_identifier();


--
-- Name: live_order_identifiers live_order_identifiers_validate_identity; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER live_order_identifiers_validate_identity BEFORE INSERT OR UPDATE ON public.live_order_identifiers FOR EACH ROW EXECUTE FUNCTION public.validate_p6_live_order_identifier();


--
-- Name: market_statistics market_statistics_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER market_statistics_append_only BEFORE DELETE OR UPDATE ON public.market_statistics FOR EACH ROW EXECUTE FUNCTION public.reject_indicator_immutable_mutation();


--
-- Name: microstructure_definition_versions microstructure_definition_versions_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER microstructure_definition_versions_append_only BEFORE DELETE OR UPDATE ON public.microstructure_definition_versions FOR EACH ROW EXECUTE FUNCTION public.reject_microstructure_immutable_mutation();


--
-- Name: microstructure_materialization_orderbooks microstructure_materialization_orderbooks_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER microstructure_materialization_orderbooks_append_only BEFORE DELETE OR UPDATE ON public.microstructure_materialization_orderbooks FOR EACH ROW EXECUTE FUNCTION public.reject_microstructure_immutable_mutation();


--
-- Name: microstructure_materialization_trades microstructure_materialization_trades_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER microstructure_materialization_trades_append_only BEFORE DELETE OR UPDATE ON public.microstructure_materialization_trades FOR EACH ROW EXECUTE FUNCTION public.reject_microstructure_immutable_mutation();


--
-- Name: microstructure_materializations microstructure_materializations_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER microstructure_materializations_append_only BEFORE DELETE OR UPDATE ON public.microstructure_materializations FOR EACH ROW EXECUTE FUNCTION public.reject_microstructure_immutable_mutation();


--
-- Name: microstructure_statistics microstructure_statistics_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER microstructure_statistics_append_only BEFORE DELETE OR UPDATE ON public.microstructure_statistics FOR EACH ROW EXECUTE FUNCTION public.reject_microstructure_immutable_mutation();


--
-- Name: order_fills order_fills_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER order_fills_append_only_delete BEFORE DELETE ON public.order_fills FOR EACH ROW EXECUTE FUNCTION public.reject_p5_append_only_mutation();


--
-- Name: order_fills order_fills_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER order_fills_append_only_update BEFORE UPDATE ON public.order_fills FOR EACH ROW EXECUTE FUNCTION public.reject_p5_append_only_mutation();


--
-- Name: orderbook_snapshots orderbook_snapshot_microstructure_invalidation; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER orderbook_snapshot_microstructure_invalidation AFTER INSERT ON public.orderbook_snapshots FOR EACH ROW WHEN ((new.source_receipt_id IS NOT NULL)) EXECUTE FUNCTION public.enqueue_microstructure_invalidation();


--
-- Name: realtime_connection_quality_intervals realtime_connection_quality_intervals_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER realtime_connection_quality_intervals_append_only BEFORE DELETE OR UPDATE ON public.realtime_connection_quality_intervals FOR EACH ROW EXECUTE FUNCTION public.reject_microstructure_immutable_mutation();


--
-- Name: realtime_connection_quality_intervals realtime_quality_microstructure_invalidation; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER realtime_quality_microstructure_invalidation AFTER INSERT ON public.realtime_connection_quality_intervals FOR EACH ROW EXECUTE FUNCTION public.enqueue_quality_microstructure_invalidation();


--
-- Name: reconciliation_runs reconciliation_runs_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER reconciliation_runs_append_only_delete BEFORE DELETE ON public.reconciliation_runs FOR EACH ROW EXECUTE FUNCTION public.reject_p5_append_only_mutation();


--
-- Name: reconciliation_runs reconciliation_runs_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER reconciliation_runs_append_only_update BEFORE UPDATE ON public.reconciliation_runs FOR EACH ROW EXECUTE FUNCTION public.reject_p5_append_only_mutation();


--
-- Name: reconciliation_runs reconciliation_runs_require_live_application; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER reconciliation_runs_require_live_application AFTER INSERT ON public.reconciliation_runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.validate_p6_live_reconciliation_run_has_application();


--
-- Name: risk_events risk_events_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER risk_events_append_only_delete BEFORE DELETE ON public.risk_events FOR EACH ROW EXECUTE FUNCTION public.reject_p5_append_only_mutation();


--
-- Name: risk_events risk_events_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER risk_events_append_only_update BEFORE UPDATE ON public.risk_events FOR EACH ROW EXECUTE FUNCTION public.reject_p5_append_only_mutation();


--
-- Name: data_quality_events source_candle_quality_microstructure_invalidation; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER source_candle_quality_microstructure_invalidation AFTER INSERT ON public.data_quality_events FOR EACH ROW EXECUTE FUNCTION public.enqueue_source_candle_microstructure_invalidation();


--
-- Name: source_candle_revisions source_candle_revision_microstructure_invalidation; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER source_candle_revision_microstructure_invalidation AFTER INSERT ON public.source_candle_revisions FOR EACH ROW EXECUTE FUNCTION public.enqueue_source_candle_microstructure_invalidation();


--
-- Name: source_candle_revisions source_candle_revisions_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER source_candle_revisions_append_only_delete BEFORE DELETE ON public.source_candle_revisions FOR EACH ROW EXECUTE FUNCTION public.reject_source_candle_revision_mutation();


--
-- Name: source_candle_revisions source_candle_revisions_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER source_candle_revisions_append_only_update BEFORE UPDATE ON public.source_candle_revisions FOR EACH ROW EXECUTE FUNCTION public.reject_source_candle_revision_mutation();


--
-- Name: source_receipts source_receipts_prepare_realtime_evidence; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER source_receipts_prepare_realtime_evidence BEFORE INSERT ON public.source_receipts FOR EACH ROW EXECUTE FUNCTION public.prepare_realtime_source_receipt();


--
-- Name: source_candle_revisions source_revision_indicator_invalidation; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER source_revision_indicator_invalidation AFTER INSERT ON public.source_candle_revisions FOR EACH ROW EXECUTE FUNCTION public.enqueue_source_indicator_invalidation();


--
-- Name: strategy_graphs strategy_graphs_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER strategy_graphs_append_only_delete BEFORE DELETE ON public.strategy_graphs FOR EACH ROW EXECUTE FUNCTION public.reject_strategy_version_mutation();


--
-- Name: strategy_graphs strategy_graphs_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER strategy_graphs_append_only_update BEFORE UPDATE ON public.strategy_graphs FOR EACH ROW EXECUTE FUNCTION public.reject_strategy_version_mutation();


--
-- Name: strategy_parameters strategy_parameters_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER strategy_parameters_append_only_delete BEFORE DELETE ON public.strategy_parameters FOR EACH ROW EXECUTE FUNCTION public.reject_strategy_version_mutation();


--
-- Name: strategy_parameters strategy_parameters_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER strategy_parameters_append_only_update BEFORE UPDATE ON public.strategy_parameters FOR EACH ROW EXECUTE FUNCTION public.reject_strategy_version_mutation();


--
-- Name: strategy_versions strategy_versions_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER strategy_versions_append_only_delete BEFORE DELETE ON public.strategy_versions FOR EACH ROW EXECUTE FUNCTION public.reject_strategy_version_mutation();


--
-- Name: strategy_versions strategy_versions_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER strategy_versions_append_only_update BEFORE UPDATE ON public.strategy_versions FOR EACH ROW EXECUTE FUNCTION public.reject_strategy_version_mutation();


--
-- Name: trade_events trade_event_microstructure_invalidation; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trade_event_microstructure_invalidation AFTER INSERT ON public.trade_events FOR EACH ROW WHEN ((new.source_receipt_id IS NOT NULL)) EXECUTE FUNCTION public.enqueue_microstructure_invalidation();


--
-- Name: trade_events trade_events_conflicting_duplicate_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trade_events_conflicting_duplicate_guard BEFORE INSERT ON public.trade_events FOR EACH ROW EXECUTE FUNCTION public.reject_conflicting_trade_event();


--
-- Name: trading_capabilities trading_capabilities_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trading_capabilities_append_only_delete BEFORE DELETE ON public.trading_capabilities FOR EACH ROW EXECUTE FUNCTION public.reject_p6_trading_capability_mutation();


--
-- Name: trading_capabilities trading_capabilities_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trading_capabilities_append_only_update BEFORE UPDATE ON public.trading_capabilities FOR EACH ROW EXECUTE FUNCTION public.reject_p6_trading_capability_mutation();


--
-- Name: upbit_api_key_permission_attestations upbit_api_key_permission_attestations_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_api_key_permission_attestations_append_only_delete BEFORE DELETE ON public.upbit_api_key_permission_attestations FOR EACH ROW EXECUTE FUNCTION public.reject_p6_upbit_permission_attestation_mutation();


--
-- Name: upbit_api_key_permission_attestations upbit_api_key_permission_attestations_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_api_key_permission_attestations_append_only_update BEFORE UPDATE ON public.upbit_api_key_permission_attestations FOR EACH ROW EXECUTE FUNCTION public.reject_p6_upbit_permission_attestation_mutation();


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bindings_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_live_exchange_order_bindings_append_only_delete BEFORE DELETE ON public.upbit_live_exchange_order_bindings FOR EACH ROW EXECUTE FUNCTION public.reject_p6_live_exchange_order_binding_mutation();


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bindings_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_live_exchange_order_bindings_append_only_update BEFORE UPDATE ON public.upbit_live_exchange_order_bindings FOR EACH ROW EXECUTE FUNCTION public.reject_p6_live_exchange_order_binding_mutation();


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bindings_mark_submitted; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_live_exchange_order_bindings_mark_submitted AFTER INSERT ON public.upbit_live_exchange_order_bindings FOR EACH ROW EXECUTE FUNCTION public.mark_p6_live_identifier_submitted();


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bindings_validate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_live_exchange_order_bindings_validate BEFORE INSERT ON public.upbit_live_exchange_order_bindings FOR EACH ROW EXECUTE FUNCTION public.validate_p6_live_exchange_order_binding();


--
-- Name: upbit_live_reconciliation_applications upbit_live_reconciliation_applications_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_live_reconciliation_applications_append_only_delete BEFORE DELETE ON public.upbit_live_reconciliation_applications FOR EACH ROW EXECUTE FUNCTION public.reject_p6_live_reconciliation_application_mutation();


--
-- Name: upbit_live_reconciliation_applications upbit_live_reconciliation_applications_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_live_reconciliation_applications_append_only_update BEFORE UPDATE ON public.upbit_live_reconciliation_applications FOR EACH ROW EXECUTE FUNCTION public.reject_p6_live_reconciliation_application_mutation();


--
-- Name: upbit_live_reconciliation_applications upbit_live_reconciliation_applications_validate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_live_reconciliation_applications_validate BEFORE INSERT ON public.upbit_live_reconciliation_applications FOR EACH ROW EXECUTE FUNCTION public.validate_p6_live_reconciliation_application();


--
-- Name: upbit_order_outbox upbit_order_outbox_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_order_outbox_append_only_delete BEFORE DELETE ON public.upbit_order_outbox FOR EACH ROW EXECUTE FUNCTION public.reject_p6_upbit_order_outbox_mutation();


--
-- Name: upbit_order_outbox upbit_order_outbox_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_order_outbox_append_only_update BEFORE UPDATE ON public.upbit_order_outbox FOR EACH ROW EXECUTE FUNCTION public.reject_p6_upbit_order_outbox_mutation();


--
-- Name: upbit_order_outbox upbit_order_outbox_validate_consistency; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_order_outbox_validate_consistency BEFORE INSERT ON public.upbit_order_outbox FOR EACH ROW EXECUTE FUNCTION public.validate_p6_upbit_order_outbox_consistency();


--
-- Name: upbit_order_submit_rehearsals upbit_order_submit_rehearsals_append_only_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_order_submit_rehearsals_append_only_delete BEFORE DELETE ON public.upbit_order_submit_rehearsals FOR EACH ROW EXECUTE FUNCTION public.reject_p6_order_submit_rehearsal_mutation();


--
-- Name: upbit_order_submit_rehearsals upbit_order_submit_rehearsals_append_only_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_order_submit_rehearsals_append_only_update BEFORE UPDATE ON public.upbit_order_submit_rehearsals FOR EACH ROW EXECUTE FUNCTION public.reject_p6_order_submit_rehearsal_mutation();


--
-- Name: upbit_order_submit_rehearsals upbit_order_submit_rehearsals_validate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_order_submit_rehearsals_validate BEFORE INSERT ON public.upbit_order_submit_rehearsals FOR EACH ROW EXECUTE FUNCTION public.validate_p6_order_submit_rehearsal();


--
-- Name: upbit_order_test_runs upbit_order_test_runs_reject_live_identifier; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_order_test_runs_reject_live_identifier BEFORE INSERT OR UPDATE ON public.upbit_order_test_runs FOR EACH ROW EXECUTE FUNCTION public.validate_p6_order_test_identifier_not_live();


--
-- Name: upbit_order_test_runs upbit_order_test_runs_reject_mutation; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_order_test_runs_reject_mutation BEFORE DELETE OR UPDATE ON public.upbit_order_test_runs FOR EACH ROW EXECUTE FUNCTION public.reject_p6_order_test_run_mutation();


--
-- Name: upbit_order_test_runs upbit_order_test_runs_reserve_identifiers; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER upbit_order_test_runs_reserve_identifiers AFTER INSERT ON public.upbit_order_test_runs FOR EACH ROW EXECUTE FUNCTION public.reserve_p6_order_test_identifier();


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
-- Name: backfill_job_targets backfill_job_targets_last_fetch_manifest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backfill_job_targets
    ADD CONSTRAINT backfill_job_targets_last_fetch_manifest_id_fkey FOREIGN KEY (last_fetch_manifest_id) REFERENCES public.fetch_manifests(id);


--
-- Name: backfill_job_targets backfill_job_targets_target_spec_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backfill_job_targets
    ADD CONSTRAINT backfill_job_targets_target_spec_id_fkey FOREIGN KEY (target_spec_id) REFERENCES public.collection_target_specs(id);


--
-- Name: backtest_artifacts backtest_artifacts_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_artifacts
    ADD CONSTRAINT backtest_artifacts_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.backtest_runs(id) ON DELETE RESTRICT;


--
-- Name: backtest_equity_points backtest_equity_points_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_equity_points
    ADD CONSTRAINT backtest_equity_points_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.backtest_runs(id) ON DELETE RESTRICT;


--
-- Name: backtest_metrics backtest_metrics_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_metrics
    ADD CONSTRAINT backtest_metrics_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.backtest_runs(id) ON DELETE RESTRICT;


--
-- Name: backtest_runs backtest_runs_dataset_version_id_dataset_content_hash_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_runs
    ADD CONSTRAINT backtest_runs_dataset_version_id_dataset_content_hash_fkey FOREIGN KEY (dataset_version_id, dataset_content_hash) REFERENCES public.dataset_versions(id, content_hash) ON DELETE RESTRICT;


--
-- Name: backtest_runs backtest_runs_dataset_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_runs
    ADD CONSTRAINT backtest_runs_dataset_version_id_fkey FOREIGN KEY (dataset_version_id) REFERENCES public.dataset_versions(id) ON DELETE RESTRICT;


--
-- Name: backtest_runs backtest_runs_strategy_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_runs
    ADD CONSTRAINT backtest_runs_strategy_version_id_fkey FOREIGN KEY (strategy_version_id) REFERENCES public.strategy_versions(id) ON DELETE RESTRICT;


--
-- Name: backtest_runs backtest_runs_strategy_version_id_strategy_graph_hash_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_runs
    ADD CONSTRAINT backtest_runs_strategy_version_id_strategy_graph_hash_fkey FOREIGN KEY (strategy_version_id, strategy_graph_hash) REFERENCES public.strategy_versions(id, graph_hash) ON DELETE RESTRICT;


--
-- Name: backtest_trades backtest_trades_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_trades
    ADD CONSTRAINT backtest_trades_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.backtest_runs(id) ON DELETE RESTRICT;


--
-- Name: bot_definitions bot_definitions_portfolio_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_definitions
    ADD CONSTRAINT bot_definitions_portfolio_id_fkey FOREIGN KEY (portfolio_id) REFERENCES public.portfolios(id) ON DELETE RESTRICT;


--
-- Name: bot_definitions bot_definitions_strategy_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_definitions
    ADD CONSTRAINT bot_definitions_strategy_version_id_fkey FOREIGN KEY (strategy_version_id) REFERENCES public.strategy_versions(id) ON DELETE RESTRICT;


--
-- Name: bot_instances bot_instances_backtest_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_instances
    ADD CONSTRAINT bot_instances_backtest_run_id_fkey FOREIGN KEY (backtest_run_id) REFERENCES public.backtest_runs(id) ON DELETE RESTRICT;


--
-- Name: bot_instances bot_instances_bot_definition_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_instances
    ADD CONSTRAINT bot_instances_bot_definition_id_fkey FOREIGN KEY (bot_definition_id) REFERENCES public.bot_definitions(id) ON DELETE RESTRICT;


--
-- Name: bot_instances bot_instances_portfolio_policy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_instances
    ADD CONSTRAINT bot_instances_portfolio_policy_id_fkey FOREIGN KEY (portfolio_policy_id) REFERENCES public.portfolio_policies(id) ON DELETE RESTRICT;


--
-- Name: bot_instances bot_instances_strategy_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_instances
    ADD CONSTRAINT bot_instances_strategy_version_id_fkey FOREIGN KEY (strategy_version_id) REFERENCES public.strategy_versions(id) ON DELETE RESTRICT;


--
-- Name: bot_state_transitions bot_state_transitions_bot_instance_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bot_state_transitions
    ADD CONSTRAINT bot_state_transitions_bot_instance_id_fkey FOREIGN KEY (bot_instance_id) REFERENCES public.bot_instances(id) ON DELETE RESTRICT;


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
-- Name: candle_rollup_invalidations candle_rollup_invalidations_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollup_invalidations
    ADD CONSTRAINT candle_rollup_invalidations_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: candle_rollup_invalidations candle_rollup_invalidations_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollup_invalidations
    ADD CONSTRAINT candle_rollup_invalidations_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: candle_rollup_invalidations candle_rollup_invalidations_quality_event_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollup_invalidations
    ADD CONSTRAINT candle_rollup_invalidations_quality_event_through_id_fkey FOREIGN KEY (quality_event_through_id) REFERENCES public.data_quality_events(id);


--
-- Name: candle_rollup_invalidations candle_rollup_invalidations_source_revision_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollup_invalidations
    ADD CONSTRAINT candle_rollup_invalidations_source_revision_through_id_fkey FOREIGN KEY (source_revision_through_id) REFERENCES public.source_candle_revisions(id);


--
-- Name: candle_rollup_recompute_jobs candle_rollup_recompute_jobs_invalidation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollup_recompute_jobs
    ADD CONSTRAINT candle_rollup_recompute_jobs_invalidation_id_fkey FOREIGN KEY (invalidation_id) REFERENCES public.candle_rollup_invalidations(id);


--
-- Name: candle_rollup_recompute_jobs candle_rollup_recompute_jobs_processing_quality_event_thro_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollup_recompute_jobs
    ADD CONSTRAINT candle_rollup_recompute_jobs_processing_quality_event_thro_fkey FOREIGN KEY (processing_quality_event_through_id) REFERENCES public.data_quality_events(id);


--
-- Name: candle_rollup_recompute_jobs candle_rollup_recompute_jobs_processing_source_revision_th_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollup_recompute_jobs
    ADD CONSTRAINT candle_rollup_recompute_jobs_processing_source_revision_th_fkey FOREIGN KEY (processing_source_revision_through_id) REFERENCES public.source_candle_revisions(id);


--
-- Name: candle_rollups candle_rollups_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollups
    ADD CONSTRAINT candle_rollups_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: candle_rollups candle_rollups_quality_event_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candle_rollups
    ADD CONSTRAINT candle_rollups_quality_event_through_id_fkey FOREIGN KEY (quality_event_through_id) REFERENCES public.data_quality_events(id);


--
-- Name: capital_allocations capital_allocations_portfolio_policy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capital_allocations
    ADD CONSTRAINT capital_allocations_portfolio_policy_id_fkey FOREIGN KEY (portfolio_policy_id) REFERENCES public.portfolio_policies(id) ON DELETE RESTRICT;


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
-- Name: collection_subscription_desires collection_subscription_desires_target_spec_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_subscription_desires
    ADD CONSTRAINT collection_subscription_desires_target_spec_id_fkey FOREIGN KEY (target_spec_id) REFERENCES public.collection_target_specs(id) ON DELETE CASCADE;


--
-- Name: collection_target_changes collection_target_changes_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_target_changes
    ADD CONSTRAINT collection_target_changes_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: collection_target_specs collection_target_specs_legacy_target_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_target_specs
    ADD CONSTRAINT collection_target_specs_legacy_target_id_fkey FOREIGN KEY (legacy_target_id) REFERENCES public.collection_targets(id);


--
-- Name: collection_target_specs collection_target_specs_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_target_specs
    ADD CONSTRAINT collection_target_specs_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: collection_target_specs collection_target_specs_policy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_target_specs
    ADD CONSTRAINT collection_target_specs_policy_id_fkey FOREIGN KEY (policy_id) REFERENCES public.collection_policies(id);


--
-- Name: collection_targets collection_targets_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collection_targets
    ADD CONSTRAINT collection_targets_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: coverage_intervals coverage_intervals_fetch_manifest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coverage_intervals
    ADD CONSTRAINT coverage_intervals_fetch_manifest_id_fkey FOREIGN KEY (fetch_manifest_id) REFERENCES public.fetch_manifests(id);


--
-- Name: coverage_intervals coverage_intervals_target_spec_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coverage_intervals
    ADD CONSTRAINT coverage_intervals_target_spec_id_fkey FOREIGN KEY (target_spec_id) REFERENCES public.collection_target_specs(id);


--
-- Name: data_quality_events data_quality_events_fetch_manifest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_quality_events
    ADD CONSTRAINT data_quality_events_fetch_manifest_id_fkey FOREIGN KEY (fetch_manifest_id) REFERENCES public.fetch_manifests(id);


--
-- Name: data_quality_events data_quality_events_target_spec_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_quality_events
    ADD CONSTRAINT data_quality_events_target_spec_id_fkey FOREIGN KEY (target_spec_id) REFERENCES public.collection_target_specs(id);


--
-- Name: dataset_build_coverage_snapshots dataset_build_coverage_snapsh_source_data_quality_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_coverage_snapshots
    ADD CONSTRAINT dataset_build_coverage_snapsh_source_data_quality_event_id_fkey FOREIGN KEY (source_data_quality_event_id) REFERENCES public.data_quality_events(id);


--
-- Name: dataset_build_coverage_snapshots dataset_build_coverage_snapshots_dataset_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_coverage_snapshots
    ADD CONSTRAINT dataset_build_coverage_snapshots_dataset_build_id_fkey FOREIGN KEY (dataset_build_id) REFERENCES public.dataset_builds(id) ON DELETE RESTRICT;


--
-- Name: dataset_build_coverage_snapshots dataset_build_coverage_snapshots_dataset_build_series_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_coverage_snapshots
    ADD CONSTRAINT dataset_build_coverage_snapshots_dataset_build_series_id_fkey FOREIGN KEY (dataset_build_series_id) REFERENCES public.dataset_build_series(id) ON DELETE RESTRICT;


--
-- Name: dataset_build_market_status_snapshots dataset_build_market_status_s_source_market_status_history_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_market_status_snapshots
    ADD CONSTRAINT dataset_build_market_status_s_source_market_status_history_fkey FOREIGN KEY (source_market_status_history_id) REFERENCES public.market_status_history(id);


--
-- Name: dataset_build_market_status_snapshots dataset_build_market_status_snapshots_dataset_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_market_status_snapshots
    ADD CONSTRAINT dataset_build_market_status_snapshots_dataset_build_id_fkey FOREIGN KEY (dataset_build_id) REFERENCES public.dataset_builds(id) ON DELETE RESTRICT;


--
-- Name: dataset_build_market_status_snapshots dataset_build_market_status_snapshots_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_market_status_snapshots
    ADD CONSTRAINT dataset_build_market_status_snapshots_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: dataset_build_series dataset_build_series_candle_rollup_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_candle_rollup_through_id_fkey FOREIGN KEY (candle_rollup_through_id) REFERENCES public.candle_rollups(id);


--
-- Name: dataset_build_series dataset_build_series_connection_quality_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_connection_quality_through_id_fkey FOREIGN KEY (connection_quality_through_id) REFERENCES public.realtime_connection_quality_intervals(id);


--
-- Name: dataset_build_series dataset_build_series_dataset_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_dataset_build_id_fkey FOREIGN KEY (dataset_build_id) REFERENCES public.dataset_builds(id) ON DELETE RESTRICT;


--
-- Name: dataset_build_series dataset_build_series_indicator_materialization_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_indicator_materialization_through_id_fkey FOREIGN KEY (indicator_materialization_through_id) REFERENCES public.indicator_materializations(id);


--
-- Name: dataset_build_series dataset_build_series_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id) ON DELETE RESTRICT;


--
-- Name: dataset_build_series dataset_build_series_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id) ON DELETE RESTRICT;


--
-- Name: dataset_build_series dataset_build_series_market_statistic_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_market_statistic_through_id_fkey FOREIGN KEY (market_statistic_through_id) REFERENCES public.market_statistics(id);


--
-- Name: dataset_build_series dataset_build_series_market_status_history_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_market_status_history_through_id_fkey FOREIGN KEY (market_status_history_through_id) REFERENCES public.market_status_history(id);


--
-- Name: dataset_build_series dataset_build_series_microstructure_materialization_throug_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_microstructure_materialization_throug_fkey FOREIGN KEY (microstructure_materialization_through_id) REFERENCES public.microstructure_materializations(id);


--
-- Name: dataset_build_series dataset_build_series_orderbook_snapshot_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_orderbook_snapshot_through_id_fkey FOREIGN KEY (orderbook_snapshot_through_id) REFERENCES public.orderbook_snapshots(id);


--
-- Name: dataset_build_series dataset_build_series_quality_event_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_quality_event_through_id_fkey FOREIGN KEY (quality_event_through_id) REFERENCES public.data_quality_events(id);


--
-- Name: dataset_build_series dataset_build_series_source_receipt_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_source_receipt_through_id_fkey FOREIGN KEY (source_receipt_through_id) REFERENCES public.source_receipts(id);


--
-- Name: dataset_build_series dataset_build_series_source_revision_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_source_revision_through_id_fkey FOREIGN KEY (source_revision_through_id) REFERENCES public.source_candle_revisions(id);


--
-- Name: dataset_build_series dataset_build_series_trade_event_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_build_series
    ADD CONSTRAINT dataset_build_series_trade_event_through_id_fkey FOREIGN KEY (trade_event_through_id) REFERENCES public.trade_events(id);


--
-- Name: dataset_builds dataset_builds_version_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_builds
    ADD CONSTRAINT dataset_builds_version_fk FOREIGN KEY (dataset_version_id) REFERENCES public.dataset_versions(id) ON DELETE RESTRICT;


--
-- Name: dataset_version_candles dataset_version_candles_candle_rollup_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_candles
    ADD CONSTRAINT dataset_version_candles_candle_rollup_id_fkey FOREIGN KEY (candle_rollup_id) REFERENCES public.candle_rollups(id);


--
-- Name: dataset_version_candles dataset_version_candles_dataset_version_id_dataset_version_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_candles
    ADD CONSTRAINT dataset_version_candles_dataset_version_id_dataset_version_fkey FOREIGN KEY (dataset_version_id, dataset_version_series_id) REFERENCES public.dataset_version_series(dataset_version_id, id) ON DELETE RESTRICT;


--
-- Name: dataset_version_candles dataset_version_candles_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_candles
    ADD CONSTRAINT dataset_version_candles_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: dataset_version_candles dataset_version_candles_source_candle_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_candles
    ADD CONSTRAINT dataset_version_candles_source_candle_revision_id_fkey FOREIGN KEY (source_candle_revision_id) REFERENCES public.source_candle_revisions(id);


--
-- Name: dataset_version_coverage_snapshots dataset_version_coverage_snap_dataset_version_id_dataset_v_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_coverage_snapshots
    ADD CONSTRAINT dataset_version_coverage_snap_dataset_version_id_dataset_v_fkey FOREIGN KEY (dataset_version_id, dataset_version_series_id) REFERENCES public.dataset_version_series(dataset_version_id, id) ON DELETE RESTRICT;


--
-- Name: dataset_version_coverage_snapshots dataset_version_coverage_snap_source_build_coverage_snapsh_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_coverage_snapshots
    ADD CONSTRAINT dataset_version_coverage_snap_source_build_coverage_snapsh_fkey FOREIGN KEY (source_build_coverage_snapshot_id) REFERENCES public.dataset_build_coverage_snapshots(id);


--
-- Name: dataset_version_coverage_snapshots dataset_version_coverage_snap_source_data_quality_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_coverage_snapshots
    ADD CONSTRAINT dataset_version_coverage_snap_source_data_quality_event_id_fkey FOREIGN KEY (source_data_quality_event_id) REFERENCES public.data_quality_events(id);


--
-- Name: dataset_version_coverage_snapshots dataset_version_coverage_snapshots_dataset_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_coverage_snapshots
    ADD CONSTRAINT dataset_version_coverage_snapshots_dataset_version_id_fkey FOREIGN KEY (dataset_version_id) REFERENCES public.dataset_versions(id) ON DELETE RESTRICT;


--
-- Name: dataset_version_indicators dataset_version_indicators_dataset_version_id_dataset_vers_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_indicators
    ADD CONSTRAINT dataset_version_indicators_dataset_version_id_dataset_vers_fkey FOREIGN KEY (dataset_version_id, dataset_version_series_id) REFERENCES public.dataset_version_series(dataset_version_id, id) ON DELETE RESTRICT;


--
-- Name: dataset_version_indicators dataset_version_indicators_indicator_materialization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_indicators
    ADD CONSTRAINT dataset_version_indicators_indicator_materialization_id_fkey FOREIGN KEY (indicator_materialization_id) REFERENCES public.indicator_materializations(id);


--
-- Name: dataset_version_indicators dataset_version_indicators_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_indicators
    ADD CONSTRAINT dataset_version_indicators_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: dataset_version_market_statistics dataset_version_market_statis_dataset_version_id_dataset_v_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_market_statistics
    ADD CONSTRAINT dataset_version_market_statis_dataset_version_id_dataset_v_fkey FOREIGN KEY (dataset_version_id, dataset_version_series_id) REFERENCES public.dataset_version_series(dataset_version_id, id) ON DELETE RESTRICT;


--
-- Name: dataset_version_market_statistics dataset_version_market_statistics_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_market_statistics
    ADD CONSTRAINT dataset_version_market_statistics_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: dataset_version_market_statistics dataset_version_market_statistics_market_statistic_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_market_statistics
    ADD CONSTRAINT dataset_version_market_statistics_market_statistic_id_fkey FOREIGN KEY (market_statistic_id) REFERENCES public.market_statistics(id);


--
-- Name: dataset_version_market_status_snapshots dataset_version_market_status_sna_source_build_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_market_status_snapshots
    ADD CONSTRAINT dataset_version_market_status_sna_source_build_snapshot_id_fkey FOREIGN KEY (source_build_snapshot_id) REFERENCES public.dataset_build_market_status_snapshots(id);


--
-- Name: dataset_version_market_status_snapshots dataset_version_market_status_snapshots_dataset_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_market_status_snapshots
    ADD CONSTRAINT dataset_version_market_status_snapshots_dataset_version_id_fkey FOREIGN KEY (dataset_version_id) REFERENCES public.dataset_versions(id) ON DELETE RESTRICT;


--
-- Name: dataset_version_market_status_snapshots dataset_version_market_status_snapshots_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_market_status_snapshots
    ADD CONSTRAINT dataset_version_market_status_snapshots_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: dataset_version_market_status_snapshots dataset_version_market_status_source_market_status_history_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_market_status_snapshots
    ADD CONSTRAINT dataset_version_market_status_source_market_status_history_fkey FOREIGN KEY (source_market_status_history_id) REFERENCES public.market_status_history(id);


--
-- Name: dataset_version_microstructures dataset_version_microstructur_dataset_version_id_dataset_v_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_microstructures
    ADD CONSTRAINT dataset_version_microstructur_dataset_version_id_dataset_v_fkey FOREIGN KEY (dataset_version_id, dataset_version_series_id) REFERENCES public.dataset_version_series(dataset_version_id, id) ON DELETE RESTRICT;


--
-- Name: dataset_version_microstructures dataset_version_microstructur_microstructure_materializati_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_microstructures
    ADD CONSTRAINT dataset_version_microstructur_microstructure_materializati_fkey FOREIGN KEY (microstructure_materialization_id) REFERENCES public.microstructure_materializations(id);


--
-- Name: dataset_version_microstructures dataset_version_microstructures_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_microstructures
    ADD CONSTRAINT dataset_version_microstructures_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: dataset_version_series dataset_version_series_candle_rollup_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_candle_rollup_through_id_fkey FOREIGN KEY (candle_rollup_through_id) REFERENCES public.candle_rollups(id);


--
-- Name: dataset_version_series dataset_version_series_connection_quality_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_connection_quality_through_id_fkey FOREIGN KEY (connection_quality_through_id) REFERENCES public.realtime_connection_quality_intervals(id);


--
-- Name: dataset_version_series dataset_version_series_dataset_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_dataset_version_id_fkey FOREIGN KEY (dataset_version_id) REFERENCES public.dataset_versions(id) ON DELETE RESTRICT;


--
-- Name: dataset_version_series dataset_version_series_indicator_materialization_through_i_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_indicator_materialization_through_i_fkey FOREIGN KEY (indicator_materialization_through_id) REFERENCES public.indicator_materializations(id);


--
-- Name: dataset_version_series dataset_version_series_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: dataset_version_series dataset_version_series_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: dataset_version_series dataset_version_series_market_statistic_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_market_statistic_through_id_fkey FOREIGN KEY (market_statistic_through_id) REFERENCES public.market_statistics(id);


--
-- Name: dataset_version_series dataset_version_series_market_status_history_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_market_status_history_through_id_fkey FOREIGN KEY (market_status_history_through_id) REFERENCES public.market_status_history(id);


--
-- Name: dataset_version_series dataset_version_series_microstructure_materialization_thro_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_microstructure_materialization_thro_fkey FOREIGN KEY (microstructure_materialization_through_id) REFERENCES public.microstructure_materializations(id);


--
-- Name: dataset_version_series dataset_version_series_orderbook_snapshot_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_orderbook_snapshot_through_id_fkey FOREIGN KEY (orderbook_snapshot_through_id) REFERENCES public.orderbook_snapshots(id);


--
-- Name: dataset_version_series dataset_version_series_quality_event_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_quality_event_through_id_fkey FOREIGN KEY (quality_event_through_id) REFERENCES public.data_quality_events(id);


--
-- Name: dataset_version_series dataset_version_series_source_build_series_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_source_build_series_id_fkey FOREIGN KEY (source_build_series_id) REFERENCES public.dataset_build_series(id) ON DELETE RESTRICT;


--
-- Name: dataset_version_series dataset_version_series_source_receipt_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_source_receipt_through_id_fkey FOREIGN KEY (source_receipt_through_id) REFERENCES public.source_receipts(id);


--
-- Name: dataset_version_series dataset_version_series_source_revision_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_source_revision_through_id_fkey FOREIGN KEY (source_revision_through_id) REFERENCES public.source_candle_revisions(id);


--
-- Name: dataset_version_series dataset_version_series_trade_event_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_version_series
    ADD CONSTRAINT dataset_version_series_trade_event_through_id_fkey FOREIGN KEY (trade_event_through_id) REFERENCES public.trade_events(id);


--
-- Name: exchange_orders exchange_orders_order_intent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_orders
    ADD CONSTRAINT exchange_orders_order_intent_id_fkey FOREIGN KEY (order_intent_id) REFERENCES public.order_intents(id) ON DELETE RESTRICT;


--
-- Name: fetch_manifests fetch_manifests_collection_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fetch_manifests
    ADD CONSTRAINT fetch_manifests_collection_run_id_fkey FOREIGN KEY (collection_run_id) REFERENCES public.collection_runs(id);


--
-- Name: fetch_manifests fetch_manifests_target_spec_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fetch_manifests
    ADD CONSTRAINT fetch_manifests_target_spec_id_fkey FOREIGN KEY (target_spec_id) REFERENCES public.collection_target_specs(id);


--
-- Name: indicator_definition_versions indicator_definition_versions_definition_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_definition_versions
    ADD CONSTRAINT indicator_definition_versions_definition_id_fkey FOREIGN KEY (definition_id) REFERENCES public.indicator_definitions(id);


--
-- Name: indicator_invalidations indicator_invalidations_changed_quality_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_invalidations
    ADD CONSTRAINT indicator_invalidations_changed_quality_event_id_fkey FOREIGN KEY (changed_quality_event_id) REFERENCES public.data_quality_events(id);


--
-- Name: indicator_invalidations indicator_invalidations_changed_rollup_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_invalidations
    ADD CONSTRAINT indicator_invalidations_changed_rollup_id_fkey FOREIGN KEY (changed_rollup_id) REFERENCES public.candle_rollups(id);


--
-- Name: indicator_invalidations indicator_invalidations_changed_rollup_invalidation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_invalidations
    ADD CONSTRAINT indicator_invalidations_changed_rollup_invalidation_id_fkey FOREIGN KEY (changed_rollup_invalidation_id) REFERENCES public.candle_rollup_invalidations(id);


--
-- Name: indicator_invalidations indicator_invalidations_changed_source_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_invalidations
    ADD CONSTRAINT indicator_invalidations_changed_source_revision_id_fkey FOREIGN KEY (changed_source_revision_id) REFERENCES public.source_candle_revisions(id);


--
-- Name: indicator_invalidations indicator_invalidations_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_invalidations
    ADD CONSTRAINT indicator_invalidations_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: indicator_materializations indicator_materializations_current_rollup_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_materializations
    ADD CONSTRAINT indicator_materializations_current_rollup_id_fkey FOREIGN KEY (current_rollup_id) REFERENCES public.candle_rollups(id) ON DELETE RESTRICT;


--
-- Name: indicator_materializations indicator_materializations_current_source_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_materializations
    ADD CONSTRAINT indicator_materializations_current_source_revision_id_fkey FOREIGN KEY (current_source_revision_id) REFERENCES public.source_candle_revisions(id) ON DELETE RESTRICT;


--
-- Name: indicator_materializations indicator_materializations_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_materializations
    ADD CONSTRAINT indicator_materializations_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: indicator_materializations indicator_materializations_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_materializations
    ADD CONSTRAINT indicator_materializations_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: indicator_materializations indicator_materializations_parent_materialization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_materializations
    ADD CONSTRAINT indicator_materializations_parent_materialization_id_fkey FOREIGN KEY (parent_materialization_id) REFERENCES public.indicator_materializations(id) ON DELETE RESTRICT;


--
-- Name: indicator_materializations indicator_materializations_quality_event_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_materializations
    ADD CONSTRAINT indicator_materializations_quality_event_through_id_fkey FOREIGN KEY (quality_event_through_id) REFERENCES public.data_quality_events(id);


--
-- Name: indicator_value_rollups indicator_value_rollups_candle_rollup_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_value_rollups
    ADD CONSTRAINT indicator_value_rollups_candle_rollup_id_fkey FOREIGN KEY (candle_rollup_id) REFERENCES public.candle_rollups(id) ON DELETE RESTRICT;


--
-- Name: indicator_value_rollups indicator_value_rollups_indicator_value_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_value_rollups
    ADD CONSTRAINT indicator_value_rollups_indicator_value_id_fkey FOREIGN KEY (indicator_value_id) REFERENCES public.indicator_values(id) ON DELETE RESTRICT;


--
-- Name: indicator_values indicator_values_definition_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_values
    ADD CONSTRAINT indicator_values_definition_version_id_fkey FOREIGN KEY (definition_version_id) REFERENCES public.indicator_definition_versions(id) ON DELETE RESTRICT;


--
-- Name: indicator_values indicator_values_materialization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_values
    ADD CONSTRAINT indicator_values_materialization_id_fkey FOREIGN KEY (materialization_id) REFERENCES public.indicator_materializations(id) ON DELETE RESTRICT;


--
-- Name: indicator_values indicator_values_parent_value_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.indicator_values
    ADD CONSTRAINT indicator_values_parent_value_id_fkey FOREIGN KEY (parent_value_id) REFERENCES public.indicator_values(id) ON DELETE RESTRICT;


--
-- Name: live_order_identifiers live_order_identifiers_exchange_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.live_order_identifiers
    ADD CONSTRAINT live_order_identifiers_exchange_account_id_fkey FOREIGN KEY (exchange_account_id) REFERENCES public.exchange_accounts(id) ON DELETE RESTRICT;


--
-- Name: live_order_identifiers live_order_identifiers_order_intent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.live_order_identifiers
    ADD CONSTRAINT live_order_identifiers_order_intent_id_fkey FOREIGN KEY (order_intent_id) REFERENCES public.order_intents(id) ON DELETE RESTRICT;


--
-- Name: market_statistics market_statistics_current_rollup_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_statistics
    ADD CONSTRAINT market_statistics_current_rollup_id_fkey FOREIGN KEY (current_rollup_id) REFERENCES public.candle_rollups(id) ON DELETE RESTRICT;


--
-- Name: market_statistics market_statistics_current_source_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_statistics
    ADD CONSTRAINT market_statistics_current_source_revision_id_fkey FOREIGN KEY (current_source_revision_id) REFERENCES public.source_candle_revisions(id) ON DELETE RESTRICT;


--
-- Name: market_statistics market_statistics_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_statistics
    ADD CONSTRAINT market_statistics_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: market_statistics market_statistics_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_statistics
    ADD CONSTRAINT market_statistics_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: market_statistics market_statistics_parent_statistic_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_statistics
    ADD CONSTRAINT market_statistics_parent_statistic_id_fkey FOREIGN KEY (parent_statistic_id) REFERENCES public.market_statistics(id) ON DELETE RESTRICT;


--
-- Name: market_statistics market_statistics_quality_event_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_statistics
    ADD CONSTRAINT market_statistics_quality_event_through_id_fkey FOREIGN KEY (quality_event_through_id) REFERENCES public.data_quality_events(id);


--
-- Name: market_status_history market_status_history_fetch_manifest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_status_history
    ADD CONSTRAINT market_status_history_fetch_manifest_id_fkey FOREIGN KEY (fetch_manifest_id) REFERENCES public.fetch_manifests(id);


--
-- Name: market_status_history market_status_history_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market_status_history
    ADD CONSTRAINT market_status_history_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: markets markets_legacy_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.markets
    ADD CONSTRAINT markets_legacy_instrument_id_fkey FOREIGN KEY (legacy_instrument_id) REFERENCES public.instruments(id);


--
-- Name: microstructure_invalidations microstructure_invalidations_changed_connection_quality_in_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_invalidations
    ADD CONSTRAINT microstructure_invalidations_changed_connection_quality_in_fkey FOREIGN KEY (changed_connection_quality_interval_id) REFERENCES public.realtime_connection_quality_intervals(id);


--
-- Name: microstructure_invalidations microstructure_invalidations_changed_orderbook_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_invalidations
    ADD CONSTRAINT microstructure_invalidations_changed_orderbook_snapshot_id_fkey FOREIGN KEY (changed_orderbook_snapshot_id) REFERENCES public.orderbook_snapshots(id);


--
-- Name: microstructure_invalidations microstructure_invalidations_changed_quality_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_invalidations
    ADD CONSTRAINT microstructure_invalidations_changed_quality_event_id_fkey FOREIGN KEY (changed_quality_event_id) REFERENCES public.data_quality_events(id);


--
-- Name: microstructure_invalidations microstructure_invalidations_changed_source_candle_revisio_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_invalidations
    ADD CONSTRAINT microstructure_invalidations_changed_source_candle_revisio_fkey FOREIGN KEY (changed_source_candle_revision_id) REFERENCES public.source_candle_revisions(id);


--
-- Name: microstructure_invalidations microstructure_invalidations_changed_trade_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_invalidations
    ADD CONSTRAINT microstructure_invalidations_changed_trade_event_id_fkey FOREIGN KEY (changed_trade_event_id) REFERENCES public.trade_events(id);


--
-- Name: microstructure_invalidations microstructure_invalidations_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_invalidations
    ADD CONSTRAINT microstructure_invalidations_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: microstructure_invalidations microstructure_invalidations_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_invalidations
    ADD CONSTRAINT microstructure_invalidations_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: microstructure_invalidations microstructure_invalidations_quality_event_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_invalidations
    ADD CONSTRAINT microstructure_invalidations_quality_event_through_id_fkey FOREIGN KEY (quality_event_through_id) REFERENCES public.data_quality_events(id);


--
-- Name: microstructure_invalidations microstructure_invalidations_source_candle_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_invalidations
    ADD CONSTRAINT microstructure_invalidations_source_candle_revision_id_fkey FOREIGN KEY (source_candle_revision_id) REFERENCES public.source_candle_revisions(id);


--
-- Name: microstructure_materializations microstructure_materializatio_connection_quality_through_i_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materializations
    ADD CONSTRAINT microstructure_materializatio_connection_quality_through_i_fkey FOREIGN KEY (connection_quality_through_id) REFERENCES public.realtime_connection_quality_intervals(id);


--
-- Name: microstructure_materialization_orderbooks microstructure_materialization_order_orderbook_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materialization_orderbooks
    ADD CONSTRAINT microstructure_materialization_order_orderbook_snapshot_id_fkey FOREIGN KEY (orderbook_snapshot_id) REFERENCES public.orderbook_snapshots(id) ON DELETE RESTRICT;


--
-- Name: microstructure_materialization_orderbooks microstructure_materialization_orderboo_materialization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materialization_orderbooks
    ADD CONSTRAINT microstructure_materialization_orderboo_materialization_id_fkey FOREIGN KEY (materialization_id) REFERENCES public.microstructure_materializations(id) ON DELETE RESTRICT;


--
-- Name: microstructure_materialization_orderbooks microstructure_materialization_orderbook_source_receipt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materialization_orderbooks
    ADD CONSTRAINT microstructure_materialization_orderbook_source_receipt_id_fkey FOREIGN KEY (source_receipt_id) REFERENCES public.source_receipts(id) ON DELETE RESTRICT;


--
-- Name: microstructure_materialization_trades microstructure_materialization_trades_materialization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materialization_trades
    ADD CONSTRAINT microstructure_materialization_trades_materialization_id_fkey FOREIGN KEY (materialization_id) REFERENCES public.microstructure_materializations(id) ON DELETE RESTRICT;


--
-- Name: microstructure_materialization_trades microstructure_materialization_trades_source_receipt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materialization_trades
    ADD CONSTRAINT microstructure_materialization_trades_source_receipt_id_fkey FOREIGN KEY (source_receipt_id) REFERENCES public.source_receipts(id) ON DELETE RESTRICT;


--
-- Name: microstructure_materialization_trades microstructure_materialization_trades_trade_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materialization_trades
    ADD CONSTRAINT microstructure_materialization_trades_trade_event_id_fkey FOREIGN KEY (trade_event_id) REFERENCES public.trade_events(id) ON DELETE RESTRICT;


--
-- Name: microstructure_materializations microstructure_materializations_definition_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materializations
    ADD CONSTRAINT microstructure_materializations_definition_version_id_fkey FOREIGN KEY (definition_version_id) REFERENCES public.microstructure_definition_versions(id);


--
-- Name: microstructure_materializations microstructure_materializations_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materializations
    ADD CONSTRAINT microstructure_materializations_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: microstructure_materializations microstructure_materializations_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materializations
    ADD CONSTRAINT microstructure_materializations_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: microstructure_materializations microstructure_materializations_parent_materialization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materializations
    ADD CONSTRAINT microstructure_materializations_parent_materialization_id_fkey FOREIGN KEY (parent_materialization_id) REFERENCES public.microstructure_materializations(id) ON DELETE RESTRICT;


--
-- Name: microstructure_materializations microstructure_materializations_quality_event_through_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materializations
    ADD CONSTRAINT microstructure_materializations_quality_event_through_id_fkey FOREIGN KEY (quality_event_through_id) REFERENCES public.data_quality_events(id);


--
-- Name: microstructure_materializations microstructure_materializations_source_candle_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_materializations
    ADD CONSTRAINT microstructure_materializations_source_candle_revision_id_fkey FOREIGN KEY (source_candle_revision_id) REFERENCES public.source_candle_revisions(id) ON DELETE RESTRICT;


--
-- Name: microstructure_statistics microstructure_statistics_closing_orderbook_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_statistics
    ADD CONSTRAINT microstructure_statistics_closing_orderbook_snapshot_id_fkey FOREIGN KEY (closing_orderbook_snapshot_id) REFERENCES public.orderbook_snapshots(id) ON DELETE RESTRICT;


--
-- Name: microstructure_statistics microstructure_statistics_materialization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_statistics
    ADD CONSTRAINT microstructure_statistics_materialization_id_fkey FOREIGN KEY (materialization_id) REFERENCES public.microstructure_materializations(id) ON DELETE RESTRICT;


--
-- Name: microstructure_statistics microstructure_statistics_parent_statistic_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.microstructure_statistics
    ADD CONSTRAINT microstructure_statistics_parent_statistic_id_fkey FOREIGN KEY (parent_statistic_id) REFERENCES public.microstructure_statistics(id) ON DELETE RESTRICT;


--
-- Name: missing_ranges missing_ranges_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.missing_ranges
    ADD CONSTRAINT missing_ranges_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: order_fills order_fills_exchange_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_fills
    ADD CONSTRAINT order_fills_exchange_order_id_fkey FOREIGN KEY (exchange_order_id) REFERENCES public.exchange_orders(id) ON DELETE RESTRICT;


--
-- Name: order_intents order_intents_bot_instance_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_intents
    ADD CONSTRAINT order_intents_bot_instance_id_fkey FOREIGN KEY (bot_instance_id) REFERENCES public.bot_instances(id) ON DELETE RESTRICT;


--
-- Name: order_intents order_intents_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_intents
    ADD CONSTRAINT order_intents_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: orderbook_snapshot_levels orderbook_snapshot_levels_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_snapshot_levels
    ADD CONSTRAINT orderbook_snapshot_levels_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES public.orderbook_snapshots(id) ON DELETE CASCADE;


--
-- Name: orderbook_snapshots orderbook_snapshots_fetch_manifest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_snapshots
    ADD CONSTRAINT orderbook_snapshots_fetch_manifest_id_fkey FOREIGN KEY (fetch_manifest_id) REFERENCES public.fetch_manifests(id);


--
-- Name: orderbook_snapshots orderbook_snapshots_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_snapshots
    ADD CONSTRAINT orderbook_snapshots_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: orderbook_snapshots orderbook_snapshots_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_snapshots
    ADD CONSTRAINT orderbook_snapshots_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: orderbook_snapshots orderbook_snapshots_source_receipt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_snapshots
    ADD CONSTRAINT orderbook_snapshots_source_receipt_id_fkey FOREIGN KEY (source_receipt_id) REFERENCES public.source_receipts(id);


--
-- Name: orderbook_summaries orderbook_summaries_collection_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_summaries
    ADD CONSTRAINT orderbook_summaries_collection_run_id_fkey FOREIGN KEY (collection_run_id) REFERENCES public.collection_runs(id);


--
-- Name: orderbook_summaries orderbook_summaries_fetch_manifest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_summaries
    ADD CONSTRAINT orderbook_summaries_fetch_manifest_id_fkey FOREIGN KEY (fetch_manifest_id) REFERENCES public.fetch_manifests(id);


--
-- Name: orderbook_summaries orderbook_summaries_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_summaries
    ADD CONSTRAINT orderbook_summaries_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: orderbook_summaries orderbook_summaries_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orderbook_summaries
    ADD CONSTRAINT orderbook_summaries_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: paper_execution_jobs paper_execution_jobs_order_intent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.paper_execution_jobs
    ADD CONSTRAINT paper_execution_jobs_order_intent_id_fkey FOREIGN KEY (order_intent_id) REFERENCES public.order_intents(id) ON DELETE RESTRICT;


--
-- Name: portfolio_policies portfolio_policies_portfolio_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_policies
    ADD CONSTRAINT portfolio_policies_portfolio_id_fkey FOREIGN KEY (portfolio_id) REFERENCES public.portfolios(id) ON DELETE RESTRICT;


--
-- Name: position_projections position_projections_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.position_projections
    ADD CONSTRAINT position_projections_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: position_projections position_projections_portfolio_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.position_projections
    ADD CONSTRAINT position_projections_portfolio_id_fkey FOREIGN KEY (portfolio_id) REFERENCES public.portfolios(id) ON DELETE RESTRICT;


--
-- Name: position_projections position_projections_source_fill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.position_projections
    ADD CONSTRAINT position_projections_source_fill_id_fkey FOREIGN KEY (source_fill_id) REFERENCES public.order_fills(id) ON DELETE RESTRICT;


--
-- Name: realtime_connection_quality_intervals realtime_connection_quality_intervals_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.realtime_connection_quality_intervals
    ADD CONSTRAINT realtime_connection_quality_intervals_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.realtime_connection_sessions(connection_id);


--
-- Name: realtime_connection_quality_intervals realtime_connection_quality_intervals_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.realtime_connection_quality_intervals
    ADD CONSTRAINT realtime_connection_quality_intervals_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: reconciliation_runs reconciliation_runs_exchange_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconciliation_runs
    ADD CONSTRAINT reconciliation_runs_exchange_order_id_fkey FOREIGN KEY (exchange_order_id) REFERENCES public.exchange_orders(id) ON DELETE RESTRICT;


--
-- Name: risk_events risk_events_bot_instance_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_events
    ADD CONSTRAINT risk_events_bot_instance_id_fkey FOREIGN KEY (bot_instance_id) REFERENCES public.bot_instances(id) ON DELETE RESTRICT;


--
-- Name: risk_events risk_events_order_intent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.risk_events
    ADD CONSTRAINT risk_events_order_intent_id_fkey FOREIGN KEY (order_intent_id) REFERENCES public.order_intents(id) ON DELETE RESTRICT;


--
-- Name: source_candle_revisions source_candle_revisions_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_candle_revisions
    ADD CONSTRAINT source_candle_revisions_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: source_candle_revisions source_candle_revisions_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_candle_revisions
    ADD CONSTRAINT source_candle_revisions_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: source_candle_revisions source_candle_revisions_source_candle_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_candle_revisions
    ADD CONSTRAINT source_candle_revisions_source_candle_id_fkey FOREIGN KEY (source_candle_id) REFERENCES public.source_candles(id);


--
-- Name: source_candles source_candles_collection_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_candles
    ADD CONSTRAINT source_candles_collection_run_id_fkey FOREIGN KEY (collection_run_id) REFERENCES public.collection_runs(id);


--
-- Name: source_candles source_candles_fetch_manifest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_candles
    ADD CONSTRAINT source_candles_fetch_manifest_id_fkey FOREIGN KEY (fetch_manifest_id) REFERENCES public.fetch_manifests(id);


--
-- Name: source_candles source_candles_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_candles
    ADD CONSTRAINT source_candles_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: source_candles source_candles_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_candles
    ADD CONSTRAINT source_candles_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: source_candles source_candles_source_receipt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_candles
    ADD CONSTRAINT source_candles_source_receipt_id_fkey FOREIGN KEY (source_receipt_id) REFERENCES public.source_receipts(id);


--
-- Name: source_receipts source_receipts_connection_session_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_receipts
    ADD CONSTRAINT source_receipts_connection_session_fk FOREIGN KEY (connection_id) REFERENCES public.realtime_connection_sessions(connection_id);


--
-- Name: source_receipts source_receipts_fetch_manifest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_receipts
    ADD CONSTRAINT source_receipts_fetch_manifest_id_fkey FOREIGN KEY (fetch_manifest_id) REFERENCES public.fetch_manifests(id);


--
-- Name: source_receipts source_receipts_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_receipts
    ADD CONSTRAINT source_receipts_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: source_receipts source_receipts_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_receipts
    ADD CONSTRAINT source_receipts_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: strategy_graphs strategy_graphs_strategy_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_graphs
    ADD CONSTRAINT strategy_graphs_strategy_version_id_fkey FOREIGN KEY (strategy_version_id) REFERENCES public.strategy_versions(id) ON DELETE RESTRICT;


--
-- Name: strategy_graphs strategy_graphs_strategy_version_id_graph_hash_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_graphs
    ADD CONSTRAINT strategy_graphs_strategy_version_id_graph_hash_fkey FOREIGN KEY (strategy_version_id, graph_hash) REFERENCES public.strategy_versions(id, graph_hash) ON DELETE RESTRICT;


--
-- Name: strategy_parameters strategy_parameters_strategy_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_parameters
    ADD CONSTRAINT strategy_parameters_strategy_version_id_fkey FOREIGN KEY (strategy_version_id) REFERENCES public.strategy_versions(id) ON DELETE RESTRICT;


--
-- Name: strategy_versions strategy_versions_strategy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_versions
    ADD CONSTRAINT strategy_versions_strategy_id_fkey FOREIGN KEY (strategy_id) REFERENCES public.strategy_definitions(id) ON DELETE RESTRICT;


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
-- Name: ticker_snapshots ticker_snapshots_fetch_manifest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticker_snapshots
    ADD CONSTRAINT ticker_snapshots_fetch_manifest_id_fkey FOREIGN KEY (fetch_manifest_id) REFERENCES public.fetch_manifests(id);


--
-- Name: ticker_snapshots ticker_snapshots_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticker_snapshots
    ADD CONSTRAINT ticker_snapshots_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: ticker_snapshots ticker_snapshots_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticker_snapshots
    ADD CONSTRAINT ticker_snapshots_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: ticker_snapshots ticker_snapshots_source_receipt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticker_snapshots
    ADD CONSTRAINT ticker_snapshots_source_receipt_id_fkey FOREIGN KEY (source_receipt_id) REFERENCES public.source_receipts(id);


--
-- Name: trade_events trade_events_collection_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trade_events
    ADD CONSTRAINT trade_events_collection_run_id_fkey FOREIGN KEY (collection_run_id) REFERENCES public.collection_runs(id);


--
-- Name: trade_events trade_events_fetch_manifest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trade_events
    ADD CONSTRAINT trade_events_fetch_manifest_id_fkey FOREIGN KEY (fetch_manifest_id) REFERENCES public.fetch_manifests(id);


--
-- Name: trade_events trade_events_instrument_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trade_events
    ADD CONSTRAINT trade_events_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES public.instruments(id);


--
-- Name: trade_events trade_events_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trade_events
    ADD CONSTRAINT trade_events_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.markets(id);


--
-- Name: trade_events trade_events_source_receipt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trade_events
    ADD CONSTRAINT trade_events_source_receipt_id_fkey FOREIGN KEY (source_receipt_id) REFERENCES public.source_receipts(id);


--
-- Name: upbit_api_key_permission_attestations upbit_api_key_permission_attestations_exchange_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_api_key_permission_attestations
    ADD CONSTRAINT upbit_api_key_permission_attestations_exchange_account_id_fkey FOREIGN KEY (exchange_account_id) REFERENCES public.exchange_accounts(id) ON DELETE RESTRICT;


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_binding_live_order_identifier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_exchange_order_bindings
    ADD CONSTRAINT upbit_live_exchange_order_binding_live_order_identifier_id_fkey FOREIGN KEY (live_order_identifier_id) REFERENCES public.live_order_identifiers(id) ON DELETE RESTRICT;


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bindings_exchange_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_exchange_order_bindings
    ADD CONSTRAINT upbit_live_exchange_order_bindings_exchange_account_id_fkey FOREIGN KEY (exchange_account_id) REFERENCES public.exchange_accounts(id) ON DELETE RESTRICT;


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bindings_exchange_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_exchange_order_bindings
    ADD CONSTRAINT upbit_live_exchange_order_bindings_exchange_order_id_fkey FOREIGN KEY (exchange_order_id) REFERENCES public.exchange_orders(id) ON DELETE RESTRICT;


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bindings_order_intent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_exchange_order_bindings
    ADD CONSTRAINT upbit_live_exchange_order_bindings_order_intent_id_fkey FOREIGN KEY (order_intent_id) REFERENCES public.order_intents(id) ON DELETE RESTRICT;


--
-- Name: upbit_live_exchange_order_bindings upbit_live_exchange_order_bindings_upbit_order_outbox_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_exchange_order_bindings
    ADD CONSTRAINT upbit_live_exchange_order_bindings_upbit_order_outbox_id_fkey FOREIGN KEY (upbit_order_outbox_id) REFERENCES public.upbit_order_outbox(id) ON DELETE RESTRICT;


--
-- Name: upbit_live_reconciliation_applications upbit_live_reconciliation_app_live_exchange_order_binding__fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_reconciliation_applications
    ADD CONSTRAINT upbit_live_reconciliation_app_live_exchange_order_binding__fkey FOREIGN KEY (live_exchange_order_binding_id) REFERENCES public.upbit_live_exchange_order_bindings(id) ON DELETE RESTRICT;


--
-- Name: upbit_live_reconciliation_applications upbit_live_reconciliation_applicatio_reconciliation_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_reconciliation_applications
    ADD CONSTRAINT upbit_live_reconciliation_applicatio_reconciliation_run_id_fkey FOREIGN KEY (reconciliation_run_id) REFERENCES public.reconciliation_runs(id) ON DELETE RESTRICT;


--
-- Name: upbit_live_reconciliation_applications upbit_live_reconciliation_applications_exchange_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_reconciliation_applications
    ADD CONSTRAINT upbit_live_reconciliation_applications_exchange_account_id_fkey FOREIGN KEY (exchange_account_id) REFERENCES public.exchange_accounts(id) ON DELETE RESTRICT;


--
-- Name: upbit_live_reconciliation_applications upbit_live_reconciliation_applications_exchange_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_reconciliation_applications
    ADD CONSTRAINT upbit_live_reconciliation_applications_exchange_order_id_fkey FOREIGN KEY (exchange_order_id) REFERENCES public.exchange_orders(id) ON DELETE RESTRICT;


--
-- Name: upbit_live_reconciliation_applications upbit_live_reconciliation_applications_order_intent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_live_reconciliation_applications
    ADD CONSTRAINT upbit_live_reconciliation_applications_order_intent_id_fkey FOREIGN KEY (order_intent_id) REFERENCES public.order_intents(id) ON DELETE RESTRICT;


--
-- Name: upbit_order_identifier_reservations upbit_order_identifier_reservations_exchange_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_identifier_reservations
    ADD CONSTRAINT upbit_order_identifier_reservations_exchange_account_id_fkey FOREIGN KEY (exchange_account_id) REFERENCES public.exchange_accounts(id) ON DELETE RESTRICT;


--
-- Name: upbit_order_outbox upbit_order_outbox_exchange_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_outbox
    ADD CONSTRAINT upbit_order_outbox_exchange_account_id_fkey FOREIGN KEY (exchange_account_id) REFERENCES public.exchange_accounts(id) ON DELETE RESTRICT;


--
-- Name: upbit_order_outbox upbit_order_outbox_live_order_identifier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_outbox
    ADD CONSTRAINT upbit_order_outbox_live_order_identifier_id_fkey FOREIGN KEY (live_order_identifier_id) REFERENCES public.live_order_identifiers(id) ON DELETE RESTRICT;


--
-- Name: upbit_order_outbox upbit_order_outbox_order_intent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_outbox
    ADD CONSTRAINT upbit_order_outbox_order_intent_id_fkey FOREIGN KEY (order_intent_id) REFERENCES public.order_intents(id) ON DELETE RESTRICT;


--
-- Name: upbit_order_outbox upbit_order_outbox_permission_attestation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_outbox
    ADD CONSTRAINT upbit_order_outbox_permission_attestation_id_fkey FOREIGN KEY (permission_attestation_id) REFERENCES public.upbit_api_key_permission_attestations(id) ON DELETE RESTRICT;


--
-- Name: upbit_order_submit_rehearsals upbit_order_submit_rehearsals_exchange_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_submit_rehearsals
    ADD CONSTRAINT upbit_order_submit_rehearsals_exchange_account_id_fkey FOREIGN KEY (exchange_account_id) REFERENCES public.exchange_accounts(id) ON DELETE RESTRICT;


--
-- Name: upbit_order_submit_rehearsals upbit_order_submit_rehearsals_live_order_identifier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_submit_rehearsals
    ADD CONSTRAINT upbit_order_submit_rehearsals_live_order_identifier_id_fkey FOREIGN KEY (live_order_identifier_id) REFERENCES public.live_order_identifiers(id) ON DELETE RESTRICT;


--
-- Name: upbit_order_submit_rehearsals upbit_order_submit_rehearsals_order_intent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_submit_rehearsals
    ADD CONSTRAINT upbit_order_submit_rehearsals_order_intent_id_fkey FOREIGN KEY (order_intent_id) REFERENCES public.order_intents(id) ON DELETE RESTRICT;


--
-- Name: upbit_order_submit_rehearsals upbit_order_submit_rehearsals_permission_attestation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_submit_rehearsals
    ADD CONSTRAINT upbit_order_submit_rehearsals_permission_attestation_id_fkey FOREIGN KEY (permission_attestation_id) REFERENCES public.upbit_api_key_permission_attestations(id) ON DELETE RESTRICT;


--
-- Name: upbit_order_submit_rehearsals upbit_order_submit_rehearsals_upbit_order_outbox_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_submit_rehearsals
    ADD CONSTRAINT upbit_order_submit_rehearsals_upbit_order_outbox_id_fkey FOREIGN KEY (upbit_order_outbox_id) REFERENCES public.upbit_order_outbox(id) ON DELETE RESTRICT;


--
-- Name: upbit_order_test_runs upbit_order_test_runs_exchange_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upbit_order_test_runs
    ADD CONSTRAINT upbit_order_test_runs_exchange_account_id_fkey FOREIGN KEY (exchange_account_id) REFERENCES public.exchange_accounts(id) ON DELETE RESTRICT;


--
-- PostgreSQL database dump complete
--

\unrestrict dbmate


--
-- Dbmate schema migrations
--

INSERT INTO public.schema_migrations (version) VALUES
    ('20260715000100'),
    ('20260717000100'),
    ('20260717000200'),
    ('20260717000300'),
    ('20260717000400'),
    ('20260717000500'),
    ('20260717000600'),
    ('20260717000700'),
    ('20260717000800'),
    ('20260717000900'),
    ('20260717001000'),
    ('20260717001100'),
    ('20260717001200'),
    ('20260717001300'),
    ('20260718000100'),
    ('20260718000200'),
    ('20260718000300'),
    ('20260718000400'),
    ('20260718000500'),
    ('20260718000600'),
    ('20260718000700'),
    ('20260718000800'),
    ('20260718000900'),
    ('20260718001000'),
    ('20260718001100'),
    ('20260718001200'),
    ('20260718001300'),
    ('20260718001400');
