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
-- Name: source_candle_content_hash(numeric, numeric, numeric, numeric, numeric, numeric); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.source_candle_content_hash(p_open numeric, p_high numeric, p_low numeric, p_close numeric, p_volume numeric, p_trade_amount numeric) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    RETURN encode(sha256(convert_to(concat_ws('|'::text, (trim_scale(p_open))::text, (trim_scale(p_high))::text, (trim_scale(p_low))::text, (trim_scale(p_close))::text, (trim_scale(p_volume))::text, (trim_scale(p_trade_amount))::text), 'UTF8'::name)), 'hex'::text);


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
-- Name: dataset_versions dataset_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dataset_versions
    ADD CONSTRAINT dataset_versions_pkey PRIMARY KEY (id);


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
-- Name: backfill_jobs_lease_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX backfill_jobs_lease_idx ON public.backfill_jobs USING btree (status, next_retry_at, lease_expires_at, priority DESC, created_at);


--
-- Name: backfill_jobs_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX backfill_jobs_status_idx ON public.backfill_jobs USING btree (status, created_at DESC);


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
-- Name: indicator_invalidations_claim_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX indicator_invalidations_claim_idx ON public.indicator_invalidations USING btree (status, impact_start_at, created_at);


--
-- Name: indicator_materializations_projection_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX indicator_materializations_projection_idx ON public.indicator_materializations USING btree (instrument_id, candle_unit, occurred_at, knowledge_at, source_revision_through_id DESC, quality_event_through_id DESC NULLS LAST, id DESC);


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
-- Name: trade_events trade_event_microstructure_invalidation; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trade_event_microstructure_invalidation AFTER INSERT ON public.trade_events FOR EACH ROW WHEN ((new.source_receipt_id IS NOT NULL)) EXECUTE FUNCTION public.enqueue_microstructure_invalidation();


--
-- Name: trade_events trade_events_conflicting_duplicate_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trade_events_conflicting_duplicate_guard BEFORE INSERT ON public.trade_events FOR EACH ROW EXECUTE FUNCTION public.reject_conflicting_trade_event();


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
    ('20260717001300');
