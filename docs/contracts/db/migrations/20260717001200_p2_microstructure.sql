-- migrate:up

SET TIME ZONE 'UTC';

-- 실시간 연결과 원문 receipt의 수명·품질을 보존한다.
CREATE TABLE realtime_connection_sessions (
  connection_id UUID PRIMARY KEY,
  subscription_generation BIGINT NOT NULL DEFAULT 0 CHECK (subscription_generation >= 0),
  subscription_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  connected_at TIMESTAMPTZ NOT NULL,
  disconnected_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','closed','disconnected','failed')),
  first_frame_sequence BIGINT CHECK (first_frame_sequence IS NULL OR first_frame_sequence > 0),
  last_frame_sequence BIGINT NOT NULL DEFAULT 0 CHECK (last_frame_sequence >= 0),
  last_received_at TIMESTAMPTZ,
  disconnect_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CHECK (disconnected_at IS NULL OR disconnected_at >= connected_at)
);

INSERT INTO realtime_connection_sessions (
  connection_id, connected_at, disconnected_at, status,
  first_frame_sequence, last_frame_sequence, last_received_at
)
SELECT connection_id, MIN(received_at), MAX(received_at), 'closed',
       MIN(frame_sequence), MAX(frame_sequence), MAX(received_at)
FROM source_receipts
GROUP BY connection_id
ON CONFLICT (connection_id) DO NOTHING;

CREATE TABLE realtime_connection_quality_intervals (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  connection_id UUID NOT NULL REFERENCES realtime_connection_sessions(connection_id),
  market_id BIGINT REFERENCES markets(id),
  data_type TEXT NOT NULL
    CHECK (data_type IN ('trade_event','orderbook_snapshot','ticker_snapshot','source_candle')),
  range_start_at TIMESTAMPTZ NOT NULL,
  range_end_at TIMESTAMPTZ NOT NULL,
  quality TEXT NOT NULL CHECK (quality IN ('available','missing','unavailable','unverified')),
  reason_code TEXT NOT NULL,
  fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  detected_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (connection_id, fingerprint),
  CHECK (range_start_at < range_end_at)
);

ALTER TABLE source_receipts
  ADD COLUMN IF NOT EXISTS knowledge_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS collector_version TEXT,
  ADD COLUMN IF NOT EXISTS schema_version TEXT;
UPDATE source_receipts
SET knowledge_at = COALESCE(knowledge_at, received_at),
    collector_version = COALESCE(collector_version, 'realtime-collector-v1'),
    schema_version = COALESCE(schema_version, 'upbit-websocket-default-v1');
ALTER TABLE source_receipts
  ALTER COLUMN knowledge_at SET NOT NULL,
  ALTER COLUMN collector_version SET NOT NULL,
  ALTER COLUMN schema_version SET NOT NULL;
ALTER TABLE source_receipts
  ADD CONSTRAINT source_receipts_connection_session_fk
  FOREIGN KEY (connection_id) REFERENCES realtime_connection_sessions(connection_id);

CREATE OR REPLACE FUNCTION prepare_realtime_source_receipt()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog, public AS $$
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
CREATE TRIGGER source_receipts_prepare_realtime_evidence
  BEFORE INSERT ON source_receipts FOR EACH ROW
  EXECUTE FUNCTION prepare_realtime_source_receipt();

ALTER TABLE orderbook_snapshots
  ADD COLUMN IF NOT EXISTS source_receipt_id BIGINT REFERENCES source_receipts(id);
ALTER TABLE trade_events
  ADD COLUMN IF NOT EXISTS source_receipt_id BIGINT REFERENCES source_receipts(id);
ALTER TABLE ticker_snapshots
  ADD COLUMN IF NOT EXISTS source_receipt_id BIGINT REFERENCES source_receipts(id);
ALTER TABLE source_candles
  ADD COLUMN IF NOT EXISTS source_receipt_id BIGINT REFERENCES source_receipts(id);
CREATE UNIQUE INDEX orderbook_snapshots_source_receipt_uk
  ON orderbook_snapshots (source_receipt_id) WHERE source_receipt_id IS NOT NULL;
CREATE UNIQUE INDEX trade_events_source_receipt_uk
  ON trade_events (source_receipt_id) WHERE source_receipt_id IS NOT NULL;
CREATE INDEX ticker_snapshots_source_receipt_idx
  ON ticker_snapshots (source_receipt_id) WHERE source_receipt_id IS NOT NULL;
CREATE INDEX source_candles_source_receipt_idx
  ON source_candles (source_receipt_id) WHERE source_receipt_id IS NOT NULL;

CREATE TABLE microstructure_definition_versions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  calculation_version TEXT NOT NULL UNIQUE,
  definition_hash TEXT NOT NULL UNIQUE CHECK (definition_hash ~ '^[0-9a-f]{64}$'),
  bucket_unit TEXT NOT NULL CHECK (bucket_unit = '1m'),
  algorithms JSONB NOT NULL,
  decimal_precision INTEGER NOT NULL CHECK (decimal_precision = 50),
  rounding TEXT NOT NULL CHECK (rounding = 'ROUND_HALF_EVEN'),
  implementation_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

INSERT INTO microstructure_definition_versions (
  calculation_version, definition_hash, bucket_unit, algorithms,
  decimal_precision, rounding, implementation_version
) VALUES (
  'microstructure-v1',
  '7485c044b6945f406b67de71892ed362a97318261577c2c81cf02da33c674491',
  '1m',
  '{"orderbook":{"level":"0","depth":10,"spreadBpsDenominator":"midpoint"},"trade":{"directionLabels":["BID","ASK"],"strength":"bidVolume/askVolume*100","reconcileWith":"sourceCandleRevisionVolumeAndAmount"}}'::jsonb,
  50, 'ROUND_HALF_EVEN', 'microstructure-engine-v1'
);

CREATE TABLE microstructure_materializations (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  market_id BIGINT NOT NULL REFERENCES markets(id),
  definition_version_id BIGINT NOT NULL REFERENCES microstructure_definition_versions(id),
  bucket_start_at TIMESTAMPTZ NOT NULL,
  parent_materialization_id BIGINT REFERENCES microstructure_materializations(id) ON DELETE RESTRICT,
  source_candle_revision_id BIGINT REFERENCES source_candle_revisions(id) ON DELETE RESTRICT,
  orderbook_snapshot_through_id BIGINT NOT NULL CHECK (orderbook_snapshot_through_id >= 0),
  trade_event_through_id BIGINT NOT NULL CHECK (trade_event_through_id >= 0),
  source_receipt_through_id BIGINT NOT NULL CHECK (source_receipt_through_id >= 0),
  quality_event_through_id BIGINT REFERENCES data_quality_events(id),
  connection_quality_through_id BIGINT NOT NULL
    REFERENCES realtime_connection_quality_intervals(id),
  knowledge_at TIMESTAMPTZ NOT NULL,
  source_as_of TIMESTAMPTZ NOT NULL,
  input_lineage_hash TEXT NOT NULL CHECK (input_lineage_hash ~ '^[0-9a-f]{64}$'),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE NULLS NOT DISTINCT (
    instrument_id, definition_version_id, bucket_start_at,
    orderbook_snapshot_through_id, trade_event_through_id,
    source_receipt_through_id, source_candle_revision_id,
    quality_event_through_id, connection_quality_through_id, content_hash
  )
);

CREATE TABLE microstructure_statistics (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  materialization_id BIGINT NOT NULL UNIQUE
    REFERENCES microstructure_materializations(id) ON DELETE RESTRICT,
  parent_statistic_id BIGINT REFERENCES microstructure_statistics(id) ON DELETE RESTRICT,
  closing_orderbook_snapshot_id BIGINT REFERENCES orderbook_snapshots(id) ON DELETE RESTRICT,
  spread NUMERIC,
  spread_bps NUMERIC,
  bid_depth_10 NUMERIC,
  ask_depth_10 NUMERIC,
  orderbook_imbalance_10 NUMERIC,
  trade_count INTEGER CHECK (trade_count IS NULL OR trade_count >= 0),
  trade_intensity_per_minute NUMERIC,
  volume_intensity_per_minute NUMERIC,
  bid_count INTEGER CHECK (bid_count IS NULL OR bid_count >= 0),
  ask_count INTEGER CHECK (ask_count IS NULL OR ask_count >= 0),
  bid_volume NUMERIC,
  ask_volume NUMERIC,
  bid_ask_imbalance NUMERIC,
  execution_strength NUMERIC,
  orderbook_status TEXT NOT NULL
    CHECK (orderbook_status IN ('ready','missing','partial','invalid','undefined')),
  orderbook_quality TEXT NOT NULL
    CHECK (orderbook_quality IN ('available','no_trade','missing','unavailable','unverified')),
  trade_status TEXT NOT NULL
    CHECK (trade_status IN ('ready','missing','partial','invalid','undefined')),
  trade_quality TEXT NOT NULL
    CHECK (trade_quality IN ('available','no_trade','missing','unavailable','unverified')),
  execution_strength_status TEXT NOT NULL
    CHECK (execution_strength_status IN ('ready','missing','partial','invalid','undefined')),
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE microstructure_materialization_orderbooks (
  materialization_id BIGINT NOT NULL REFERENCES microstructure_materializations(id) ON DELETE RESTRICT,
  orderbook_snapshot_id BIGINT NOT NULL REFERENCES orderbook_snapshots(id) ON DELETE RESTRICT,
  source_receipt_id BIGINT NOT NULL REFERENCES source_receipts(id) ON DELETE RESTRICT,
  PRIMARY KEY (materialization_id, orderbook_snapshot_id)
);

CREATE TABLE microstructure_materialization_trades (
  materialization_id BIGINT NOT NULL REFERENCES microstructure_materializations(id) ON DELETE RESTRICT,
  trade_event_id BIGINT NOT NULL REFERENCES trade_events(id) ON DELETE RESTRICT,
  source_receipt_id BIGINT NOT NULL REFERENCES source_receipts(id) ON DELETE RESTRICT,
  PRIMARY KEY (materialization_id, trade_event_id)
);

CREATE TABLE microstructure_invalidations (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  market_id BIGINT NOT NULL REFERENCES markets(id),
  bucket_start_at TIMESTAMPTZ NOT NULL,
  changed_orderbook_snapshot_id BIGINT REFERENCES orderbook_snapshots(id),
  changed_trade_event_id BIGINT REFERENCES trade_events(id),
  changed_source_candle_revision_id BIGINT REFERENCES source_candle_revisions(id),
  changed_quality_event_id BIGINT REFERENCES data_quality_events(id),
  changed_connection_quality_interval_id BIGINT
    REFERENCES realtime_connection_quality_intervals(id),
  orderbook_snapshot_through_id BIGINT NOT NULL CHECK (orderbook_snapshot_through_id >= 0),
  trade_event_through_id BIGINT NOT NULL CHECK (trade_event_through_id >= 0),
  source_receipt_through_id BIGINT NOT NULL CHECK (source_receipt_through_id >= 0),
  source_candle_revision_id BIGINT REFERENCES source_candle_revisions(id),
  quality_event_through_id BIGINT REFERENCES data_quality_events(id),
  connection_quality_through_id BIGINT NOT NULL DEFAULT 0
    CHECK (connection_quality_through_id >= 0),
  knowledge_at TIMESTAMPTZ NOT NULL,
  source_as_of TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','running','succeeded','retry_wait','dead_letter')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
  next_retry_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  lease_owner TEXT,
  lease_expires_at TIMESTAMPTZ,
  lease_generation INTEGER NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
  last_error_code TEXT,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CHECK ((status = 'running') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)),
  CHECK (
    (changed_orderbook_snapshot_id IS NOT NULL)::integer +
    (changed_trade_event_id IS NOT NULL)::integer +
    (changed_source_candle_revision_id IS NOT NULL)::integer +
    (changed_quality_event_id IS NOT NULL)::integer +
    (changed_connection_quality_interval_id IS NOT NULL)::integer >= 1
  )
);

CREATE UNIQUE INDEX microstructure_invalidations_pending_bucket_uk
  ON microstructure_invalidations (
    instrument_id, bucket_start_at,
    source_candle_revision_id, quality_event_through_id,
    connection_quality_through_id
  )
  NULLS NOT DISTINCT
  WHERE status IN ('pending','retry_wait');
CREATE INDEX microstructure_invalidations_claim_idx
  ON microstructure_invalidations (status, next_retry_at, bucket_start_at, id);
CREATE INDEX microstructure_materializations_projection_idx
  ON microstructure_materializations (
    instrument_id, bucket_start_at, knowledge_at,
    orderbook_snapshot_through_id DESC, trade_event_through_id DESC,
    source_receipt_through_id DESC, id DESC
  );

CREATE OR REPLACE FUNCTION reject_microstructure_immutable_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END;
$$;

CREATE TRIGGER microstructure_definition_versions_append_only
  BEFORE UPDATE OR DELETE ON microstructure_definition_versions
  FOR EACH ROW EXECUTE FUNCTION reject_microstructure_immutable_mutation();
CREATE TRIGGER realtime_connection_quality_intervals_append_only
  BEFORE UPDATE OR DELETE ON realtime_connection_quality_intervals
  FOR EACH ROW EXECUTE FUNCTION reject_microstructure_immutable_mutation();
CREATE TRIGGER microstructure_materializations_append_only
  BEFORE UPDATE OR DELETE ON microstructure_materializations
  FOR EACH ROW EXECUTE FUNCTION reject_microstructure_immutable_mutation();
CREATE TRIGGER microstructure_statistics_append_only
  BEFORE UPDATE OR DELETE ON microstructure_statistics
  FOR EACH ROW EXECUTE FUNCTION reject_microstructure_immutable_mutation();
CREATE TRIGGER microstructure_materialization_orderbooks_append_only
  BEFORE UPDATE OR DELETE ON microstructure_materialization_orderbooks
  FOR EACH ROW EXECUTE FUNCTION reject_microstructure_immutable_mutation();
CREATE TRIGGER microstructure_materialization_trades_append_only
  BEFORE UPDATE OR DELETE ON microstructure_materialization_trades
  FOR EACH ROW EXECUTE FUNCTION reject_microstructure_immutable_mutation();

CREATE OR REPLACE FUNCTION reject_conflicting_trade_event()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
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
CREATE TRIGGER trade_events_conflicting_duplicate_guard
  BEFORE INSERT ON trade_events FOR EACH ROW
  EXECUTE FUNCTION reject_conflicting_trade_event();

CREATE OR REPLACE FUNCTION enqueue_microstructure_invalidation()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog, public AS $$
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

CREATE TRIGGER orderbook_snapshot_microstructure_invalidation
  AFTER INSERT ON orderbook_snapshots FOR EACH ROW
  WHEN (NEW.source_receipt_id IS NOT NULL)
  EXECUTE FUNCTION enqueue_microstructure_invalidation();
CREATE TRIGGER trade_event_microstructure_invalidation
  AFTER INSERT ON trade_events FOR EACH ROW
  WHEN (NEW.source_receipt_id IS NOT NULL)
  EXECUTE FUNCTION enqueue_microstructure_invalidation();

CREATE OR REPLACE FUNCTION enqueue_source_candle_microstructure_invalidation()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog, public AS $$
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

CREATE TRIGGER source_candle_revision_microstructure_invalidation
  AFTER INSERT ON source_candle_revisions FOR EACH ROW
  EXECUTE FUNCTION enqueue_source_candle_microstructure_invalidation();
CREATE TRIGGER source_candle_quality_microstructure_invalidation
  AFTER INSERT ON data_quality_events FOR EACH ROW
  EXECUTE FUNCTION enqueue_source_candle_microstructure_invalidation();

CREATE OR REPLACE FUNCTION enqueue_quality_microstructure_invalidation()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog, public AS $$
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
CREATE TRIGGER realtime_quality_microstructure_invalidation
  AFTER INSERT ON realtime_connection_quality_intervals FOR EACH ROW
  EXECUTE FUNCTION enqueue_quality_microstructure_invalidation();

REVOKE ALL ON FUNCTION enqueue_microstructure_invalidation() FROM PUBLIC;
REVOKE ALL ON FUNCTION enqueue_source_candle_microstructure_invalidation() FROM PUBLIC;
REVOKE ALL ON FUNCTION enqueue_quality_microstructure_invalidation() FROM PUBLIC;
GRANT SELECT ON microstructure_definition_versions TO CURRENT_USER;
GRANT SELECT, INSERT ON microstructure_materializations, microstructure_statistics,
  microstructure_materialization_orderbooks, microstructure_materialization_trades TO CURRENT_USER;
GRANT SELECT, INSERT, UPDATE ON microstructure_invalidations,
  realtime_connection_sessions, realtime_connection_quality_intervals TO CURRENT_USER;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO CURRENT_USER;
REVOKE UPDATE, DELETE ON microstructure_definition_versions,
  microstructure_materializations, microstructure_statistics,
  microstructure_materialization_orderbooks, microstructure_materialization_trades
  FROM CURRENT_USER;

-- migrate:down
-- 불변 원천 계보와 물질화는 자동 삭제하지 않는다.
SELECT 1;
