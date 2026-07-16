-- migrate:up

-- 웹소켓 전달 이력과 경제적 호가 상태를 분리해 감사 가능한 원천 증거로 보존한다.
SET TIME ZONE 'UTC';

CREATE TABLE IF NOT EXISTS source_receipts (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  data_type TEXT NOT NULL,
  market_id BIGINT NOT NULL REFERENCES markets(id),
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  connection_id UUID NOT NULL,
  frame_sequence BIGINT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  received_at TIMESTAMPTZ NOT NULL,
  payload_checksum TEXT NOT NULL,
  raw_payload JSONB NOT NULL,
  fetch_manifest_id BIGINT REFERENCES fetch_manifests(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT source_receipts_connection_frame_uk
    UNIQUE (connection_id, frame_sequence),
  CONSTRAINT source_receipts_data_type_ck CHECK (
    data_type IN ('source_candle', 'trade_event', 'orderbook_snapshot', 'ticker_snapshot')
  ),
  CONSTRAINT source_receipts_frame_sequence_ck CHECK (frame_sequence > 0),
  CONSTRAINT source_receipts_payload_checksum_ck CHECK (length(payload_checksum) = 64)
);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  market_id BIGINT NOT NULL REFERENCES markets(id),
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  source TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  received_at TIMESTAMPTZ NOT NULL,
  stored_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  knowledge_at TIMESTAMPTZ NOT NULL,
  total_ask_size NUMERIC(38, 18) NOT NULL,
  total_bid_size NUMERIC(38, 18) NOT NULL,
  level_count INTEGER NOT NULL,
  level NUMERIC(38, 18),
  stream_type TEXT,
  payload_checksum TEXT NOT NULL,
  fetch_manifest_id BIGINT REFERENCES fetch_manifests(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT orderbook_snapshots_economic_state_uk
    UNIQUE (instrument_id, source, occurred_at, payload_checksum),
  CONSTRAINT orderbook_snapshots_source_ck CHECK (source = 'UPBIT'),
  CONSTRAINT orderbook_snapshots_level_count_ck CHECK (level_count > 0),
  CONSTRAINT orderbook_snapshots_total_size_ck CHECK (
    total_ask_size >= 0 AND total_bid_size >= 0
  ),
  CONSTRAINT orderbook_snapshots_payload_checksum_ck CHECK (length(payload_checksum) = 64)
);

CREATE TABLE IF NOT EXISTS orderbook_snapshot_levels (
  snapshot_id BIGINT NOT NULL REFERENCES orderbook_snapshots(id) ON DELETE CASCADE,
  level_index INTEGER NOT NULL,
  ask_price NUMERIC(38, 18) NOT NULL,
  ask_size NUMERIC(38, 18) NOT NULL,
  bid_price NUMERIC(38, 18) NOT NULL,
  bid_size NUMERIC(38, 18) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (snapshot_id, level_index),
  CONSTRAINT orderbook_snapshot_levels_index_ck CHECK (level_index >= 0),
  CONSTRAINT orderbook_snapshot_levels_value_ck CHECK (
    ask_price >= 0 AND ask_size >= 0 AND bid_price >= 0 AND bid_size >= 0
  )
);

CREATE INDEX IF NOT EXISTS source_receipts_market_occurred_idx
  ON source_receipts (market_id, data_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS source_receipts_payload_checksum_idx
  ON source_receipts (payload_checksum);
CREATE INDEX IF NOT EXISTS orderbook_snapshots_market_occurred_idx
  ON orderbook_snapshots (market_id, occurred_at DESC);

-- migrate:down

-- 원천 증거는 감사 데이터이므로 자동 수축에서 삭제하지 않는다.
