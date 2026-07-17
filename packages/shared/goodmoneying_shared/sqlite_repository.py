from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

from goodmoneying_shared.aggregation import (
    AGGREGATION_UNITS,
    CALCULATION_VERSION,
    MATERIALIZED_AGGREGATION_UNITS,
    SOURCE_FETCH_BATCH_SIZE,
    CoverageSlice,
    aggregate_candles,
    rollup_bucket_start,
)
from goodmoneying_shared.incremental_aggregation import (
    affected_rollup_ranges,
    affected_rollup_ranges_for_interval,
    rollup_bucket_end,
    rollup_result_content_hash,
    rollup_result_content_hash_values,
)
from goodmoneying_shared.models import (
    AuditLogSummary,
    BackfillJob,
    BackfillJobDetail,
    BackfillJobTarget,
    BackfillPlan,
    BackfillWorkerStatus,
    CandidateUniverseEntry,
    CandleAggregationJob,
    CandleAggregationJobTarget,
    CandleRollupRecomputeJob,
    CandleView,
    CollectionActivityBucket,
    CollectionDashboardTarget,
    CollectionDataStatus,
    CollectionPlan,
    CollectionRun,
    CollectionWorkerDiagnostic,
    CollectionWorkerError,
    CollectionWorkerHeartbeatStatus,
    CollectionWorkerRuntimeStatus,
    CollectionWorkerStatus,
    CollectionWorkerStatusSummary,
    CollectionWorkerType,
    CoverageSegment,
    CoverageStatus,
    DashboardSummary,
    HealthCheck,
    Instrument,
    MarketListRow,
    MissingRangeSummary,
    NotificationEvent,
    OperationsTrendPoint,
    OrderbookSummary,
    RealtimeCollectionHeatmapBucket,
    RealtimeCollectionHeatmapRow,
    RealtimeSourceFrame,
    RealtimeWorkerStatus,
    SourceCandle,
    SourceCandleRevisionCreated,
    StorageBreakdownItem,
    TickerSnapshot,
    TradeEvent,
    TradeFrequencyStatus,
    TradeSummary,
)
from goodmoneying_shared.time import KST, isoformat_kst, minute_bucket, now_kst


def _to_db_time(value: datetime) -> str:
    return isoformat_kst(value)


def _from_db_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(KST)


def _diagnostic_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return isoformat_kst(value)


def _decimal(value: str | int | float | Decimal | None) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _progress_decimal(value: str | int | float | Decimal | None) -> Decimal:
    return _decimal(value).normalize()


def _required_lastrowid(cursor: sqlite3.Cursor) -> int:
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite insert did not return lastrowid.")
    return cursor.lastrowid


def _format_storage_bytes(value: int) -> str:
    if value >= 1024**3:
        return f"{value / 1024**3:.1f}GB"
    if value > 0:
        return f"{value / 1024**2:.1f}MB"
    return f"{value}B"


def _source_candle_content_hash(item: SourceCandle) -> str:
    return _source_candle_content_hash_values(
        item.open_price,
        item.high_price,
        item.low_price,
        item.close_price,
        item.trade_volume,
        item.trade_amount,
    )


def _source_candle_content_hash_values(*values: object) -> str:
    payload = "|".join(format(Decimal(str(value)).normalize(), "f") for value in values)
    return hashlib.sha256(payload.encode()).hexdigest()


def _max_revision_id(values: object) -> int:
    return max((int(value) for value in str(values or "").split(",") if value), default=0)


def _coverage_snapshot_hash(coverage: list[CoverageSlice]) -> str:
    payload = [
        {
            "startAt": item.start_at.astimezone(UTC).isoformat(),
            "endAt": item.end_at.astimezone(UTC).isoformat(),
            "status": item.status,
        }
        for item in coverage
    ]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class SQLiteOperationsRepository:
    """테스트와 로컬 데모용 저장소.

    런타임 계약은 PostgreSQL이지만, 이 어댑터는 같은 repository interface로
    M1 동작을 빠르게 검증하기 위한 SQLite 기반 구현이다.
    """

    def __init__(
        self,
        database_url: str = ":memory:",
        *,
        busy_timeout_seconds: float = 5.0,
    ) -> None:
        self._database_url = database_url
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            database_url,
            check_same_thread=False,
            timeout=busy_timeout_seconds,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.create_function(
            "source_candle_content_hash",
            6,
            _source_candle_content_hash_values,
            deterministic=True,
        )
        self._conn.create_function(
            "rollup_result_content_hash",
            9,
            rollup_result_content_hash_values,
            deterministic=True,
        )
        self._conn.create_function(
            "max_revision_id",
            1,
            _max_revision_id,
            deterministic=True,
        )
        self._create_schema()

    @classmethod
    def from_path(cls, path: str | Path) -> SQLiteOperationsRepository:
        return cls(str(path))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def _create_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS instruments (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  exchange TEXT NOT NULL,
                  market_code TEXT NOT NULL UNIQUE,
                  quote_currency TEXT NOT NULL,
                  base_asset TEXT NOT NULL,
                  display_name TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'active',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS candidate_universe_entries (
                  instrument_id INTEGER NOT NULL PRIMARY KEY,
                  rank INTEGER NOT NULL UNIQUE,
                  acc_trade_price_24h TEXT NOT NULL,
                  ranked_at TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS collection_targets (
                  instrument_id INTEGER NOT NULL PRIMARY KEY,
                  status TEXT NOT NULL,
                  candidate_status TEXT NOT NULL DEFAULT 'in_universe',
                  target_order INTEGER,
                  activated_at TEXT,
                  deactivated_at TEXT,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS collection_target_changes (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  instrument_id INTEGER NOT NULL,
                  previous_status TEXT,
                  new_status TEXT NOT NULL,
                  actor TEXT NOT NULL,
                  reason TEXT,
                  changed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS collection_plans (
                  instrument_id INTEGER NOT NULL PRIMARY KEY,
                  preset TEXT NOT NULL,
                  range_start_at TEXT NOT NULL,
                  range_end_at TEXT,
                  is_continuous INTEGER NOT NULL,
                  method TEXT NOT NULL,
                  status TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS collection_coverage_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  instrument_id INTEGER NOT NULL,
                  data_type TEXT NOT NULL,
                  range_start_at TEXT NOT NULL,
                  range_end_at TEXT,
                  status TEXT NOT NULL,
                  progress_percent TEXT NOT NULL,
                  last_successful_at TEXT NOT NULL,
                  missing_segment_count INTEGER NOT NULL,
                  calculated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS collection_coverage_segments (
                  snapshot_id INTEGER NOT NULL,
                  data_type TEXT NOT NULL,
                  status TEXT NOT NULL,
                  offset_percent TEXT NOT NULL,
                  width_percent TEXT NOT NULL,
                  segment_start_at TEXT NOT NULL,
                  segment_end_at TEXT NOT NULL,
                  label TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS collection_runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_type TEXT NOT NULL,
                  data_type TEXT NOT NULL,
                  status TEXT NOT NULL,
                  trigger_type TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  finished_at TEXT,
                  error_code TEXT,
                  error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS collection_worker_heartbeats (
                  worker_type TEXT PRIMARY KEY,
                  status TEXT NOT NULL,
                  last_heartbeat_at TEXT NOT NULL,
                  last_started_at TEXT,
                  last_successful_at TEXT,
                  last_error_at TEXT,
                  last_error_message TEXT,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS target_collection_results (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  collection_run_id INTEGER NOT NULL,
                  instrument_id INTEGER,
                  data_type TEXT NOT NULL,
                  status TEXT NOT NULL,
                  latency_ms INTEGER,
                  rows_written INTEGER NOT NULL,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ticker_snapshots (
                  instrument_id INTEGER NOT NULL,
                  bucket_at TEXT NOT NULL,
                  trade_price TEXT NOT NULL,
                  acc_trade_price_24h TEXT NOT NULL,
                  change_rate TEXT NOT NULL,
                  collected_at TEXT NOT NULL,
                  occurred_at TEXT,
                  received_at TEXT,
                  PRIMARY KEY (instrument_id, bucket_at)
                );

                CREATE TABLE IF NOT EXISTS orderbook_summaries (
                  instrument_id INTEGER NOT NULL,
                  bucket_at TEXT NOT NULL,
                  best_bid_price TEXT NOT NULL,
                  best_bid_size TEXT NOT NULL,
                  best_ask_price TEXT NOT NULL,
                  best_ask_size TEXT NOT NULL,
                  spread TEXT NOT NULL,
                  bid_depth_10 TEXT NOT NULL,
                  ask_depth_10 TEXT NOT NULL,
                  imbalance_10 TEXT NOT NULL,
                  collected_at TEXT NOT NULL,
                  occurred_at TEXT,
                  received_at TEXT,
                  PRIMARY KEY (instrument_id, bucket_at)
                );

                CREATE TABLE IF NOT EXISTS source_candles (
                  instrument_id INTEGER NOT NULL,
                  candle_unit TEXT NOT NULL,
                  candle_start_at TEXT NOT NULL,
                  open_price TEXT NOT NULL,
                  high_price TEXT NOT NULL,
                  low_price TEXT NOT NULL,
                  close_price TEXT NOT NULL,
                  trade_volume TEXT NOT NULL,
                  trade_amount TEXT NOT NULL,
                  collected_at TEXT NOT NULL,
                  PRIMARY KEY (instrument_id, candle_unit, candle_start_at)
                );

                CREATE TABLE IF NOT EXISTS source_candle_revisions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  instrument_id INTEGER NOT NULL,
                  candle_unit TEXT NOT NULL,
                  candle_start_at TEXT NOT NULL,
                  revision_number INTEGER NOT NULL,
                  open_price TEXT NOT NULL,
                  high_price TEXT NOT NULL,
                  low_price TEXT NOT NULL,
                  close_price TEXT NOT NULL,
                  trade_volume TEXT NOT NULL,
                  trade_amount TEXT NOT NULL,
                  source_as_of TEXT NOT NULL,
                  knowledge_at TEXT NOT NULL,
                  input_content_hash TEXT NOT NULL,
                  UNIQUE (instrument_id, candle_unit, candle_start_at, revision_number)
                );

                CREATE TRIGGER IF NOT EXISTS source_candle_revisions_append_only_update
                BEFORE UPDATE ON source_candle_revisions
                BEGIN SELECT RAISE(ABORT, 'source_candle_revisions is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS source_candle_revisions_append_only_delete
                BEFORE DELETE ON source_candle_revisions
                BEGIN SELECT RAISE(ABORT, 'source_candle_revisions is append-only'); END;

                CREATE TABLE IF NOT EXISTS candle_rollup_invalidations (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  idempotency_key TEXT NOT NULL UNIQUE,
                  market_id INTEGER NOT NULL,
                  instrument_id INTEGER NOT NULL,
                  candle_unit TEXT NOT NULL,
                  calculation_version TEXT NOT NULL,
                  range_start_at TEXT NOT NULL,
                  range_end_at TEXT NOT NULL,
                  output_bucket_count INTEGER NOT NULL CHECK (
                    output_bucket_count BETWEEN 1 AND 512
                  ),
                  source_revision_ids TEXT NOT NULL,
                  source_revision_through_id INTEGER NOT NULL,
                  quality_event_through_id INTEGER,
                  coverage_snapshot TEXT NOT NULL,
                  coverage_snapshot_hash TEXT NOT NULL,
                  knowledge_at TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS candle_rollup_invalidations_append_only_update
                BEFORE UPDATE ON candle_rollup_invalidations
                BEGIN SELECT RAISE(ABORT, 'candle_rollup_invalidations is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS candle_rollup_invalidations_append_only_delete
                BEFORE DELETE ON candle_rollup_invalidations
                BEGIN SELECT RAISE(ABORT, 'candle_rollup_invalidations is append-only'); END;

                CREATE TABLE IF NOT EXISTS candle_rollup_recompute_jobs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  invalidation_id INTEGER NOT NULL UNIQUE,
                  idempotency_key TEXT NOT NULL UNIQUE,
                  status TEXT NOT NULL DEFAULT 'pending',
                  priority INTEGER NOT NULL DEFAULT 100,
                  attempt_count INTEGER NOT NULL DEFAULT 0,
                  max_attempts INTEGER NOT NULL DEFAULT 5,
                  next_retry_at TEXT NOT NULL,
                  lease_owner TEXT,
                  lease_expires_at TEXT,
                  processing_source_revision_through_id INTEGER,
                  processing_quality_event_through_id INTEGER,
                  rows_written INTEGER NOT NULL DEFAULT 0,
                  last_error_code TEXT,
                  dead_letter_reason TEXT,
                  started_at TEXT,
                  finished_at TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS candle_rollup_recompute_jobs_claim_idx
                  ON candle_rollup_recompute_jobs (
                    status, next_retry_at, lease_expires_at, priority, created_at
                  );

                CREATE TABLE IF NOT EXISTS coverage_intervals (
                  instrument_id INTEGER NOT NULL,
                  candle_unit TEXT NOT NULL,
                  range_start_at TEXT NOT NULL,
                  range_end_at TEXT NOT NULL,
                  status TEXT NOT NULL,
                  PRIMARY KEY (instrument_id, candle_unit, range_start_at, range_end_at, status)
                );

                CREATE TABLE IF NOT EXISTS candle_rollups (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  instrument_id INTEGER NOT NULL,
                  candle_unit TEXT NOT NULL,
                  candle_start_at TEXT NOT NULL,
                  open_price TEXT NOT NULL,
                  high_price TEXT NOT NULL,
                  low_price TEXT NOT NULL,
                  close_price TEXT NOT NULL,
                  trade_volume TEXT NOT NULL,
                  trade_amount TEXT NOT NULL,
                  completeness TEXT NOT NULL,
                  calculation_version TEXT NOT NULL DEFAULT 'candle-rollup-v2',
                  source_as_of TEXT,
                  knowledge_at TEXT,
                  input_content_hash TEXT NOT NULL DEFAULT '',
                  input_revision_ids TEXT NOT NULL DEFAULT '',
                  source_revision_through_id INTEGER NOT NULL DEFAULT 0,
                  quality_event_through_id INTEGER NOT NULL DEFAULT 0,
                  coverage_snapshot_hash TEXT NOT NULL,
                  result_content_hash TEXT NOT NULL,
                  quality TEXT NOT NULL DEFAULT 'unverified',
                  materialized_at TEXT NOT NULL,
                  UNIQUE (
                    instrument_id, candle_unit, candle_start_at,
                    calculation_version, input_content_hash, coverage_snapshot_hash,
                    source_revision_through_id, quality_event_through_id
                  )
                );

                CREATE TABLE IF NOT EXISTS candle_aggregation_jobs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  status TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  started_at TEXT,
                  finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS candle_aggregation_job_targets (
                  job_id INTEGER NOT NULL,
                  instrument_id INTEGER NOT NULL,
                  candle_unit TEXT NOT NULL,
                  status TEXT NOT NULL,
                  rows_written INTEGER NOT NULL DEFAULT 0,
                  error_message TEXT,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (job_id, instrument_id, candle_unit)
                );

                CREATE TABLE IF NOT EXISTS trade_events (
                  instrument_id INTEGER NOT NULL,
                  sequential_id INTEGER NOT NULL,
                  trade_timestamp_at TEXT NOT NULL,
                  trade_price TEXT NOT NULL,
                  trade_volume TEXT NOT NULL,
                  trade_amount TEXT NOT NULL,
                  ask_bid TEXT NOT NULL,
                  collected_at TEXT NOT NULL,
                  PRIMARY KEY (instrument_id, sequential_id)
                );

                CREATE TABLE IF NOT EXISTS backfill_plans (
                  plan_id TEXT PRIMARY KEY,
                  data_type TEXT NOT NULL,
                  target_start_at TEXT NOT NULL,
                  target_end_at TEXT NOT NULL,
                  estimated_request_count INTEGER NOT NULL,
                  estimated_row_count INTEGER NOT NULL,
                  estimated_storage_bytes INTEGER NOT NULL,
                  targets TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS backfill_jobs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  plan_id TEXT NOT NULL,
                  status TEXT NOT NULL,
                  data_type TEXT NOT NULL,
                  progress_percent TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS backfill_job_targets (
                  backfill_job_id INTEGER NOT NULL,
                  instrument_id INTEGER NOT NULL,
                  status TEXT NOT NULL,
                  last_completed_at TEXT,
                  processed_missing_range_count INTEGER NOT NULL DEFAULT 0,
                  estimated_missing_range_count INTEGER NOT NULL DEFAULT 0,
                  rows_written_count INTEGER NOT NULL DEFAULT 0,
                  error_code TEXT,
                  error_message TEXT,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (backfill_job_id, instrument_id)
                );

                CREATE TABLE IF NOT EXISTS notification_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  severity TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  title TEXT NOT NULL,
                  message TEXT NOT NULL,
                  status TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"] for row in self._conn.execute("PRAGMA table_info(collection_targets)")
            }
            if "target_order" not in columns:
                self._conn.execute("ALTER TABLE collection_targets ADD COLUMN target_order INTEGER")
            for table_name in ("ticker_snapshots", "orderbook_summaries"):
                snapshot_columns = {
                    row["name"] for row in self._conn.execute(f"PRAGMA table_info({table_name})")
                }
                if "occurred_at" not in snapshot_columns:
                    self._conn.execute(f"ALTER TABLE {table_name} ADD COLUMN occurred_at TEXT")
                if "received_at" not in snapshot_columns:
                    self._conn.execute(f"ALTER TABLE {table_name} ADD COLUMN received_at TEXT")
            self._upgrade_candle_rollups_to_append_only()

    def _upgrade_candle_rollups_to_append_only(self) -> None:
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(candle_rollups)")}
        if "id" not in columns:
            self._conn.executescript(
                """
                ALTER TABLE candle_rollups RENAME TO candle_rollups_legacy;
                CREATE TABLE candle_rollups (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  instrument_id INTEGER NOT NULL,
                  candle_unit TEXT NOT NULL,
                  candle_start_at TEXT NOT NULL,
                  open_price TEXT NOT NULL,
                  high_price TEXT NOT NULL,
                  low_price TEXT NOT NULL,
                  close_price TEXT NOT NULL,
                  trade_volume TEXT NOT NULL,
                  trade_amount TEXT NOT NULL,
                  completeness TEXT NOT NULL,
                  calculation_version TEXT NOT NULL DEFAULT 'candle-rollup-v2',
                  source_as_of TEXT,
                  knowledge_at TEXT,
                  input_content_hash TEXT NOT NULL DEFAULT '',
                  input_revision_ids TEXT NOT NULL DEFAULT '',
                  source_revision_through_id INTEGER NOT NULL DEFAULT 0,
                  quality_event_through_id INTEGER NOT NULL DEFAULT 0,
                  coverage_snapshot_hash TEXT NOT NULL,
                  result_content_hash TEXT NOT NULL,
                  quality TEXT NOT NULL DEFAULT 'unverified',
                  materialized_at TEXT NOT NULL,
                  UNIQUE (
                    instrument_id, candle_unit, candle_start_at,
                    calculation_version, input_content_hash, coverage_snapshot_hash,
                    source_revision_through_id, quality_event_through_id
                  )
                );
                INSERT INTO candle_rollups (
                  instrument_id, candle_unit, candle_start_at, open_price, high_price,
                  low_price, close_price, trade_volume, trade_amount, completeness,
                  calculation_version, source_as_of, knowledge_at, input_content_hash,
                  input_revision_ids, source_revision_through_id,
                  coverage_snapshot_hash, result_content_hash,
                  quality, materialized_at
                )
                SELECT instrument_id, candle_unit, candle_start_at, open_price, high_price,
                       low_price, close_price, trade_volume, trade_amount, completeness,
                       calculation_version, source_as_of, knowledge_at, input_content_hash,
                       input_revision_ids, max_revision_id(input_revision_ids),
                       '3d9a6f3d6f8b6d0dca2bff8a6dcb8cc2cebd52cb0d80bb11d5fe35a342e944c3',
                       rollup_result_content_hash(
                         calculation_version, open_price, high_price, low_price,
                         close_price, trade_volume, trade_amount, completeness, quality
                       ),
                       quality, materialized_at
                FROM candle_rollups_legacy;
                DROP TABLE candle_rollups_legacy;
                """
            )
        self._conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS candle_rollups_current_projection_idx
              ON candle_rollups (
                instrument_id, candle_unit, calculation_version, candle_start_at,
                source_revision_through_id DESC,
                quality_event_through_id DESC, knowledge_at DESC, id DESC
              );
            CREATE TRIGGER IF NOT EXISTS candle_rollups_append_only_update
            BEFORE UPDATE ON candle_rollups
            BEGIN SELECT RAISE(ABORT, 'candle_rollups is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS candle_rollups_append_only_delete
            BEFORE DELETE ON candle_rollups
            BEGIN SELECT RAISE(ABORT, 'candle_rollups is append-only'); END;
            """
        )

    def upsert_instrument(self, market_code: str, display_name: str) -> Instrument:
        quote_currency, base_asset = market_code.split("-", maxsplit=1)
        timestamp = _to_db_time(now_kst())
        with self._lock, self._conn:
            self._execute(
                """
                INSERT INTO instruments (
                  exchange, market_code, quote_currency, base_asset,
                  display_name, created_at, updated_at
                )
                VALUES ('UPBIT', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_code) DO UPDATE SET
                  display_name = excluded.display_name,
                  updated_at = excluded.updated_at
                """,
                (market_code, quote_currency, base_asset, display_name, timestamp, timestamp),
            )
            row = self._execute(
                "SELECT * FROM instruments WHERE market_code = ?",
                (market_code,),
            ).fetchone()
        return self._instrument_from_row(row)

    def refresh_candidate_universe(
        self, entries: list[tuple[str, str, str]]
    ) -> list[CandidateUniverseEntry]:
        started_at = now_kst()
        ranked_at = _to_db_time(started_at)
        with self._lock, self._conn:
            cursor = self._execute(
                """
                INSERT INTO collection_runs (run_type, data_type, status, trigger_type, started_at)
                VALUES ('candidate_refresh', 'candidate_universe', 'running', 'schedule', ?)
                """,
                (_to_db_time(started_at),),
            )
            run_id = _required_lastrowid(cursor)
            self._execute("DELETE FROM candidate_universe_entries")
            for rank, (market_code, display_name, acc_trade_price_24h) in enumerate(
                entries[:100], start=1
            ):
                instrument = self.upsert_instrument(market_code, display_name)
                self._execute(
                    """
                    INSERT INTO candidate_universe_entries (
                      instrument_id, rank, acc_trade_price_24h, ranked_at, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (instrument.id, rank, acc_trade_price_24h, ranked_at, ranked_at),
                )
            self._execute(
                """
                UPDATE collection_targets
                SET candidate_status = CASE
                  WHEN instrument_id IN (SELECT instrument_id FROM candidate_universe_entries)
                  THEN 'in_universe'
                  ELSE 'out_of_universe'
                END
                """
            )
            finished_at = now_kst()
            self._execute(
                """
                UPDATE collection_runs
                SET status = 'succeeded', finished_at = ?
                WHERE id = ?
                """,
                (_to_db_time(finished_at), run_id),
            )
        return self.list_candidate_universe()[1]

    def ensure_default_active_targets(self, limit: int = 50) -> list[Instrument]:
        with self._lock, self._conn:
            active_count = self._execute(
                "SELECT COUNT(*) AS count FROM collection_targets WHERE status = 'active'"
            ).fetchone()["count"]
            if active_count == 0:
                rows = self._execute(
                    """
                    SELECT instrument_id
                    FROM candidate_universe_entries
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                for target_order, row in enumerate(rows, start=1):
                    self._activate_target(
                        row["instrument_id"], "system", "default_top_50", target_order
                    )
        return self.list_active_targets()

    def update_active_targets(
        self, instrument_ids: list[int], reason: str | None
    ) -> list[Instrument]:
        if len(instrument_ids) > 50:
            raise ValueError("활성 수집 대상은 최대 50개까지 선택할 수 있다.")
        if len(set(instrument_ids)) != len(instrument_ids):
            raise ValueError("활성 수집 대상은 중복될 수 없다.")
        with self._lock, self._conn:
            candidate_ids = {
                row["instrument_id"]
                for row in self._execute("SELECT instrument_id FROM candidate_universe_entries")
            }
            current_ids = {
                row["instrument_id"]
                for row in self._execute(
                    "SELECT instrument_id FROM collection_targets WHERE status = 'active'"
                )
            }
            next_ids = set(instrument_ids)
            newly_selected_ids = next_ids - current_ids
            if not newly_selected_ids.issubset(candidate_ids):
                raise ValueError("활성 수집 대상은 수집 후보군 안에서만 선택할 수 있다.")
            for instrument_id in sorted(current_ids - next_ids):
                self._deactivate_target(instrument_id, "local_user", reason)
            for target_order, instrument_id in enumerate(instrument_ids, start=1):
                self._activate_target(instrument_id, "local_user", reason, target_order)
        return self.list_active_targets()

    def list_candidate_universe(self) -> tuple[datetime, list[CandidateUniverseEntry]]:
        with self._lock:
            rows = self._execute(
                """
                SELECT
                  cue.rank,
                  cue.acc_trade_price_24h,
                  cue.ranked_at,
                  i.*,
                  COALESCE(ct.status, 'inactive') AS target_status,
                  ct.target_order AS favorite_order,
                  COALESCE(ct.candidate_status, 'in_universe') AS candidate_status
                FROM candidate_universe_entries cue
                JOIN instruments i ON i.id = cue.instrument_id
                LEFT JOIN collection_targets ct ON ct.instrument_id = i.id
                ORDER BY cue.rank
                """
            ).fetchall()
        ranked_at = _from_db_time(rows[0]["ranked_at"]) if rows else now_kst()
        entries = [
            CandidateUniverseEntry(
                instrument=self._instrument_from_row(row),
                rank=row["rank"],
                acc_trade_price_24h=_decimal(row["acc_trade_price_24h"]),
                selected=row["target_status"] == "active",
                candidate_status=row["candidate_status"],
                favorite_order=row["favorite_order"],
            )
            for row in rows
        ]
        return ranked_at, entries

    def list_active_targets(self) -> list[Instrument]:
        with self._lock:
            rows = self._execute(
                """
                SELECT i.*
                FROM collection_targets ct
                JOIN instruments i ON i.id = ct.instrument_id
                WHERE ct.status = 'active'
                ORDER BY ct.target_order, i.market_code
                """
            ).fetchall()
        return [self._instrument_from_row(row) for row in rows]

    def record_incremental_collection(
        self,
        tickers: list[TickerSnapshot],
        orderbooks: list[OrderbookSummary],
        candles: list[SourceCandle],
    ) -> CollectionRun:
        started_at = now_kst()
        with self._lock, self._conn:
            cursor = self._execute(
                """
                INSERT INTO collection_runs (run_type, data_type, status, trigger_type, started_at)
                VALUES ('incremental', 'ticker_snapshot', 'running', 'schedule', ?)
                """,
                (_to_db_time(started_at),),
            )
            run_id = _required_lastrowid(cursor)
            ticker_rows = self._upsert_tickers(tickers)
            orderbook_rows = self._upsert_orderbooks(orderbooks)
            candle_rows = self._upsert_candles(candles)
            all_instrument_ids = sorted(
                {item.instrument_id for item in tickers}
                | {item.instrument_id for item in orderbooks}
                | {item.instrument_id for item in candles}
            )
            for instrument_id in all_instrument_ids:
                self._execute(
                    """
                    INSERT INTO target_collection_results (
                      collection_run_id, instrument_id, data_type, status,
                      latency_ms, rows_written, created_at
                    )
                    VALUES (?, ?, 'ticker_snapshot', 'succeeded', 0, ?, ?)
                    """,
                    (
                        run_id,
                        instrument_id,
                        ticker_rows.get(instrument_id, 0)
                        + orderbook_rows.get(instrument_id, 0)
                        + candle_rows.get(instrument_id, 0),
                        _to_db_time(now_kst()),
                    ),
                )
            finished_at = now_kst()
            self._execute(
                """
                UPDATE collection_runs
                SET status = 'succeeded', finished_at = ?
                WHERE id = ?
                """,
                (_to_db_time(finished_at), run_id),
            )
        return CollectionRun(
            id=run_id,
            run_type="incremental",
            data_type="ticker_snapshot",
            status="succeeded",
            started_at=started_at,
            finished_at=finished_at,
        )

    def dashboard_summary(self) -> DashboardSummary:
        with self._lock:
            return self._dashboard_summary()

    def record_trade_events(self, trades: list[TradeEvent]) -> int:
        if not trades:
            return 0
        started_at = now_kst()
        with self._lock, self._conn:
            run_id = _required_lastrowid(
                self._execute(
                    """
                    INSERT INTO collection_runs (
                      run_type, data_type, status, trigger_type, started_at
                    )
                    VALUES ('incremental', 'trade_event', 'running', 'schedule', ?)
                    """,
                    (_to_db_time(started_at),),
                )
            )
            inserted_by_instrument: dict[int, int] = {}
            for trade in trades:
                cursor = self._execute(
                    """
                    INSERT OR IGNORE INTO trade_events (
                      instrument_id, sequential_id, trade_timestamp_at,
                      trade_price, trade_volume, trade_amount, ask_bid, collected_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade.instrument_id,
                        trade.sequential_id,
                        _to_db_time(trade.trade_timestamp_at),
                        str(trade.trade_price),
                        str(trade.trade_volume),
                        str(trade.trade_amount),
                        trade.ask_bid,
                        _to_db_time(trade.collected_at),
                    ),
                )
                if cursor.rowcount > 0:
                    inserted_by_instrument[trade.instrument_id] = (
                        inserted_by_instrument.get(trade.instrument_id, 0) + 1
                    )
            for instrument_id, rows_written in inserted_by_instrument.items():
                self._execute(
                    """
                    INSERT INTO target_collection_results (
                      collection_run_id, instrument_id, data_type, status,
                      latency_ms, rows_written, created_at
                    )
                    VALUES (?, ?, 'trade_event', 'succeeded', 0, ?, ?)
                    """,
                    (run_id, instrument_id, rows_written, _to_db_time(now_kst())),
                )
            self._execute(
                "UPDATE collection_runs SET status = 'succeeded', finished_at = ? WHERE id = ?",
                (_to_db_time(now_kst()), run_id),
            )
        return sum(inserted_by_instrument.values())

    def record_realtime_source_frames(self, frames: list[RealtimeSourceFrame]) -> int:
        summaries = [frame.summary for frame in frames if frame.summary is not None]
        with self._lock, self._conn:
            self._upsert_orderbooks(summaries)
        return len(frames)

    def purge_expired_source_evidence(self, *, as_of: datetime | None = None) -> tuple[int, int]:
        del as_of
        return (0, 0)

    def _dashboard_summary(self) -> DashboardSummary:
        targets = self.collection_dashboard_targets()
        coverage = self._dashboard_coverage_from_targets(targets)
        normal_targets = sum(
            1 for target in targets if target.overall_status == "latest_collecting"
        )
        warning_targets = sum(1 for target in targets if target.overall_status == "warning")
        incident_targets = sum(1 for target in targets if target.overall_status == "incident")
        delayed_targets = sum(1 for status in coverage if status.status != "normal")
        missing_ranges_open = sum(
            status.missing_segment_count
            for status in coverage
            if status.data_type == "source_candle"
        )
        failed_runs_24h = self._failed_runs_24h()
        recent_runs = self._recent_run_count()
        failure_rate_24h = (
            Decimal(failed_runs_24h) / Decimal(recent_runs) if recent_runs > 0 else Decimal("0")
        )
        storage_bytes_today = self._storage_bytes_today_estimate()
        storage_rows_today = self._storage_rows_today()
        alerts = self.notification_events()
        if any(
            alert.severity in {"error", "critical"} and alert.status == "open" for alert in alerts
        ):
            status: Literal["normal", "warning", "incident"] = "incident"
        elif delayed_targets > 0 or failed_runs_24h > 0:
            status = "warning"
        else:
            status = "normal"
        return DashboardSummary(
            status=status,
            active_targets=len(targets),
            active_target_limit=50,
            normal_targets=normal_targets,
            warning_targets=warning_targets,
            incident_targets=incident_targets,
            failed_runs_24h=failed_runs_24h,
            failure_rate_24h=failure_rate_24h,
            delayed_targets=delayed_targets,
            missing_ranges_open=missing_ranges_open,
            storage_bytes_today=storage_bytes_today,
            storage_bytes_today_display=_format_storage_bytes(storage_bytes_today),
            storage_rows_today=storage_rows_today,
            realtime_rows_last_minute=self._collection_rows_last_minute("incremental"),
            backfill_rows_last_minute=self._collection_rows_last_minute("backfill"),
            recent_request_count=self._recent_collection_result_count(),
            coverage=coverage,
            targets=targets,
            alerts=alerts,
            health_checks=self._health_checks(coverage, alerts),
            collection_activity=self.dashboard_collection_activity(),
            realtime_collection_heatmap=self.dashboard_realtime_heatmap(),
            storage_breakdown=self._storage_breakdown_today(storage_bytes_today),
            operations_trend=self._operations_trend(
                coverage, storage_bytes_today, warning_targets, incident_targets
            ),
            missing_range_top=self._missing_range_top(targets),
            audit_log_summary=self.dashboard_audit_log_summary(),
            worker_status=self.dashboard_worker_status(),
            refreshed_at=now_kst(),
        )

    def dashboard_coverage(self) -> list[CoverageStatus]:
        return self._dashboard_coverage_from_targets(self.collection_dashboard_targets())

    def dashboard_collection_activity(self) -> list[CollectionActivityBucket]:
        return self._collection_activity_buckets()

    def dashboard_realtime_heatmap(self) -> list[RealtimeCollectionHeatmapRow]:
        return self._realtime_collection_heatmap()

    def dashboard_storage_breakdown(self) -> list[StorageBreakdownItem]:
        return self._storage_breakdown_today(self._storage_bytes_today_estimate())

    def dashboard_operations_trend(self) -> list[OperationsTrendPoint]:
        targets = self.collection_dashboard_targets()
        coverage = self._dashboard_coverage_from_targets(targets)
        warning_targets = sum(1 for target in targets if target.overall_status == "warning")
        incident_targets = sum(1 for target in targets if target.overall_status == "incident")
        return self._operations_trend(
            coverage,
            self._storage_bytes_today_estimate(),
            warning_targets,
            incident_targets,
        )

    def dashboard_missing_ranges(self) -> list[MissingRangeSummary]:
        return self._missing_range_top(self.collection_dashboard_targets())

    def dashboard_audit_log_summary(self) -> AuditLogSummary:
        return self._audit_log_summary()

    def dashboard_worker_status(self) -> CollectionWorkerStatusSummary:
        return CollectionWorkerStatusSummary(
            realtime=self._realtime_worker_status(),
            backfill=self._backfill_worker_status(),
        )

    def _dashboard_coverage_from_targets(
        self, targets: list[CollectionDashboardTarget]
    ) -> list[CoverageStatus]:
        return [
            CoverageStatus(
                instrument_id=target.instrument.id,
                data_type=status.data_type,
                status=status.status,
                progress_percent=status.progress_percent,
                last_successful_at=status.last_successful_at,
                missing_segment_count=status.missing_segment_count,
            )
            for target in targets
            for status in target.data_statuses
        ]

    def collection_dashboard_targets(
        self, include_segments: bool = False
    ) -> list[CollectionDashboardTarget]:
        with self._lock:
            targets: list[CollectionDashboardTarget] = []
            active_targets = self.list_active_targets()
            instrument_ids = [instrument.id for instrument in active_targets]
            source_candle_counts = self._table_counts_by_instrument(
                "source_candles",
                instrument_ids,
            )
            source_candle_ranges = self._source_candle_ranges_by_instrument(instrument_ids)
            storage_bytes_by_instrument = self._instrument_storage_bytes_by_instrument(
                instrument_ids
            )
            storage_rows_by_instrument = self._instrument_storage_row_counts_by_instrument(
                instrument_ids
            )
            plans_by_instrument = self._collection_plans_by_instrument(instrument_ids)
            for instrument in active_targets:
                ticker = self.latest_ticker(instrument.id)
                orderbook = self.latest_orderbook(instrument.id)
                plan = plans_by_instrument[instrument.id]
                coverage = sorted(
                    self._dashboard_target_coverage(
                        instrument.id,
                        plan,
                        source_candle_counts.get(instrument.id, 0),
                        source_candle_ranges.get(instrument.id, (None, None)),
                        ticker,
                        orderbook,
                    ),
                    key=lambda item: {
                        "source_candle": 0,
                        "ticker_snapshot": 1,
                        "orderbook_summary": 2,
                    }[item.data_type],
                )
                data_statuses = [
                    self._collection_data_status(item, source_candle_counts) for item in coverage
                ]
                candle_status = next(
                    item for item in data_statuses if item.data_type == "source_candle"
                )
                overall_status: Literal["latest_collecting", "warning", "incident"]
                if any(item.status == "incident" for item in data_statuses):
                    overall_status = "incident"
                elif all(item.status == "normal" for item in data_statuses):
                    overall_status = "latest_collecting"
                else:
                    overall_status = "warning"
                targets.append(
                    CollectionDashboardTarget(
                        instrument=instrument,
                        overall_status=overall_status,
                        overall_status_label="최신수집중"
                        if overall_status == "latest_collecting"
                        else "장애"
                        if overall_status == "incident"
                        else "주의",
                        plan=plan,
                        data_statuses=data_statuses,
                        coverage_segments=[
                            segment
                            for data_status in data_statuses
                            for segment in self._coverage_segments_for(
                                instrument.id, data_status.data_type
                            )
                        ]
                        if include_segments
                        else [],
                        change_rate=ticker.change_rate if ticker else Decimal("0"),
                        acc_trade_price_24h_display=(
                            f"₩{int(ticker.acc_trade_price_24h):,}" if ticker else "₩0"
                        ),
                        ticker_collected_at=ticker.collected_at if ticker else now_kst(),
                        coverage_percent=candle_status.progress_percent,
                        storage_row_count=storage_rows_by_instrument.get(instrument.id, 0),
                        storage_bytes_display=_format_storage_bytes(
                            storage_bytes_by_instrument.get(instrument.id, 0)
                        ),
                        collected_start_at=source_candle_ranges.get(instrument.id, (None, None))[0],
                        collected_end_at=source_candle_ranges.get(instrument.id, (None, None))[1],
                    )
                )
            return targets

    def _source_candle_ranges_by_instrument(
        self, instrument_ids: list[int]
    ) -> dict[int, tuple[datetime | None, datetime | None]]:
        if not instrument_ids:
            return {}
        placeholders = ",".join("?" for _ in instrument_ids)
        rows = self._execute(
            f"""
            SELECT instrument_id,
                   min(candle_start_at) AS collected_start_at,
                   max(candle_start_at) AS collected_end_at
            FROM source_candles
            WHERE candle_unit = '1m'
              AND instrument_id IN ({placeholders})
            GROUP BY instrument_id
            """,
            tuple(instrument_ids),
        ).fetchall()
        return {
            row["instrument_id"]: (
                _from_db_time(row["collected_start_at"]) if row["collected_start_at"] else None,
                _from_db_time(row["collected_end_at"]) if row["collected_end_at"] else None,
            )
            for row in rows
        }

    def coverage_segments_for(self, instrument_id: int) -> list[CoverageSegment]:
        return [
            segment
            for status in self.coverage_for(instrument_id)
            for segment in self._coverage_segments_for(instrument_id, status.data_type)
        ]

    def market_list(self) -> list[MarketListRow]:
        rows: list[MarketListRow] = []
        _, candidate_entries = self.list_candidate_universe()
        instrument_ids = [entry.instrument.id for entry in candidate_entries]
        source_candle_counts = self._table_counts_by_instrument(
            "source_candles",
            instrument_ids,
        )
        source_candle_ranges = self._source_candle_ranges_by_instrument(instrument_ids)
        storage_bytes_by_instrument = self._instrument_storage_bytes_by_instrument(instrument_ids)
        collection_plans = self._collection_plans_by_instrument(instrument_ids)
        current_at = now_kst()
        for entry in candidate_entries:
            instrument = entry.instrument
            ticker = self.latest_ticker(instrument.id)
            orderbook = self.latest_orderbook(instrument.id) if ticker else None
            storage_bytes = storage_bytes_by_instrument.get(instrument.id, 0)
            one_minute_candle_count = source_candle_counts.get(instrument.id, 0)
            stored_candle_start_at, candle_coverage_end_at = source_candle_ranges.get(
                instrument.id,
                (None, None),
            )
            plan = collection_plans[instrument.id]
            candle_coverage = self._source_candle_coverage_status_from_summary(
                instrument.id,
                plan,
                one_minute_candle_count,
                (stored_candle_start_at, candle_coverage_end_at),
            )
            acc_trade_price_24h = (
                ticker.acc_trade_price_24h if ticker else entry.acc_trade_price_24h
            )
            rows.append(
                MarketListRow(
                    instrument=instrument,
                    asset_type="coin",
                    is_favorite=entry.selected,
                    favorite_order=entry.favorite_order,
                    trade_price=ticker.trade_price if ticker else None,
                    price_currency=instrument.quote_currency,
                    acc_trade_price_24h=acc_trade_price_24h,
                    acc_trade_price_24h_display=f"₩{int(acc_trade_price_24h):,}",
                    trade_amount_currency=instrument.quote_currency,
                    change_rate=ticker.change_rate if ticker else None,
                    change_rate_basis="전일 종가 대비",
                    ticker_collected_at=ticker.collected_at if ticker else None,
                    orderbook_collected_at=orderbook.collected_at if orderbook else None,
                    quality_status=self._quality_status_from_coverage([candle_coverage]),
                    coverage_percent=candle_coverage.progress_percent,
                    candle_coverage_start_at=plan.range_start_at,
                    candle_coverage_end_at=candle_coverage_end_at,
                    candle_coverage_current_at=current_at,
                    one_minute_candle_count=one_minute_candle_count,
                    storage_bytes=storage_bytes,
                    storage_row_count=one_minute_candle_count,
                    storage_bytes_display=_format_storage_bytes(storage_bytes),
                )
            )
        return sorted(
            rows,
            key=lambda row: (
                0 if row.is_favorite else 1,
                row.favorite_order if row.favorite_order is not None else row.instrument.id,
                row.instrument.id,
            ),
        )

    def get_instrument(self, instrument_id: int) -> Instrument | None:
        row = self._execute("SELECT * FROM instruments WHERE id = ?", (instrument_id,)).fetchone()
        return self._instrument_from_row(row) if row else None

    def latest_ticker(self, instrument_id: int) -> TickerSnapshot | None:
        row = self._execute(
            """
            SELECT * FROM ticker_snapshots
            WHERE instrument_id = ?
            ORDER BY bucket_at DESC
            LIMIT 1
            """,
            (instrument_id,),
        ).fetchone()
        return self._ticker_from_row(row) if row else None

    def latest_orderbook(self, instrument_id: int) -> OrderbookSummary | None:
        row = self._execute(
            """
            SELECT * FROM orderbook_summaries
            WHERE instrument_id = ?
            ORDER BY bucket_at DESC
            LIMIT 1
            """,
            (instrument_id,),
        ).fetchone()
        return self._orderbook_from_row(row) if row else None

    def coverage_for(self, instrument_id: int) -> list[CoverageStatus]:
        latest_ticker = self.latest_ticker(instrument_id)
        latest_orderbook = self.latest_orderbook(instrument_id)
        candle_status = self._source_candle_coverage_status(instrument_id)
        return [
            candle_status,
            self._freshness_coverage_status(
                instrument_id,
                "ticker_snapshot",
                latest_ticker.collected_at if latest_ticker else None,
            ),
            self._freshness_coverage_status(
                instrument_id,
                "orderbook_summary",
                latest_orderbook.collected_at if latest_orderbook else None,
            ),
        ]

    def _dashboard_target_coverage(
        self,
        instrument_id: int,
        plan: CollectionPlan,
        source_candle_count: int,
        source_candle_range: tuple[datetime | None, datetime | None],
        latest_ticker: TickerSnapshot | None,
        latest_orderbook: OrderbookSummary | None,
    ) -> list[CoverageStatus]:
        return [
            self._source_candle_coverage_status_from_summary(
                instrument_id, plan, source_candle_count, source_candle_range
            ),
            self._freshness_coverage_status(
                instrument_id,
                "ticker_snapshot",
                latest_ticker.collected_at if latest_ticker else None,
            ),
            self._freshness_coverage_status(
                instrument_id,
                "orderbook_summary",
                latest_orderbook.collected_at if latest_orderbook else None,
            ),
        ]

    def candles(
        self, instrument_id: int, unit: str, start_at: datetime, end_at: datetime
    ) -> list[CandleView]:
        rows = self._execute(
            """
            SELECT * FROM source_candles
            WHERE instrument_id = ?
              AND candle_start_at >= ?
              AND candle_start_at <= ?
            ORDER BY candle_start_at
            """,
            (instrument_id, _to_db_time(start_at), _to_db_time(end_at)),
        ).fetchall()
        source = [self._candle_from_row(row) for row in rows]
        if unit == "1m":
            return aggregate_candles("1m", source)
        if unit in AGGREGATION_UNITS:
            rollups = self.candle_rollups(instrument_id, unit, start_at, end_at)
            if rollups:
                return rollups
        return self._derive_candles(unit, source)

    def candle_page(
        self,
        instrument_id: int,
        unit: str,
        start_at: datetime,
        end_at: datetime,
        page_size: int,
        cursor: datetime | None,
    ) -> tuple[list[CandleView], datetime | None]:
        rows = self.candles(instrument_id, unit, start_at, end_at)
        if cursor is not None:
            rows = [item for item in rows if item.started_at > cursor]
        page = rows[:page_size]
        next_cursor = page[-1].started_at if len(rows) > page_size and page else None
        return page, next_cursor

    def materialize_candle_rollups(self, instrument_id: int, unit: str) -> int:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._execute(
                    """
                    SELECT candle.*,
                           revision.id AS revision_id,
                           revision.input_content_hash,
                           revision.knowledge_at AS revision_knowledge_at
                    FROM source_candles candle
                    LEFT JOIN source_candle_revisions revision
                      ON revision.id = (
                       SELECT latest.id
                       FROM source_candle_revisions latest
                       WHERE latest.instrument_id = candle.instrument_id
                         AND latest.candle_unit = candle.candle_unit
                         AND latest.candle_start_at = candle.candle_start_at
                       ORDER BY latest.source_as_of DESC,
                                latest.revision_number DESC, latest.id DESC
                       LIMIT 1
                     )
                    WHERE candle.instrument_id = ?
                    ORDER BY candle.candle_start_at
                    """,
                    (instrument_id,),
                )
                source: list[SourceCandle] = []
                while rows := cursor.fetchmany(SOURCE_FETCH_BATCH_SIZE):
                    source.extend(self._candle_from_row(row) for row in rows)
                coverage = self._candle_coverage(instrument_id)
                rollups = aggregate_candles(unit, source, coverage=coverage)
                materialized_at = _to_db_time(now_kst())
                for item in rollups:
                    bucket_coverage = self._candle_coverage(
                        instrument_id,
                        item.started_at,
                        rollup_bucket_end(unit, item.started_at),
                    )
                    coverage_hash = _coverage_snapshot_hash(bucket_coverage)
                    quality_ceiling = self._execute(
                        """
                        SELECT MAX(quality_event_through_id) AS quality_event_through_id,
                               MAX(knowledge_at) AS knowledge_at
                        FROM candle_rollup_invalidations
                        WHERE instrument_id = ? AND quality_event_through_id IS NOT NULL
                          AND range_start_at < ? AND range_end_at > ?
                        """,
                        (
                            instrument_id,
                            _to_db_time(rollup_bucket_end(unit, item.started_at)),
                            _to_db_time(item.started_at),
                        ),
                    ).fetchone()
                    quality_event_through_id = (
                        int(quality_ceiling["quality_event_through_id"])
                        if quality_ceiling
                        and quality_ceiling["quality_event_through_id"] is not None
                        else None
                    )
                    quality_knowledge_at = (
                        _from_db_time(quality_ceiling["knowledge_at"])
                        if quality_ceiling and quality_ceiling["knowledge_at"] is not None
                        else None
                    )
                    knowledge_at = max(
                        item.knowledge_at or datetime.min.replace(tzinfo=UTC),
                        quality_knowledge_at or datetime.min.replace(tzinfo=UTC),
                    )
                    self._execute(
                        """
                        INSERT INTO candle_rollups (
                          instrument_id, candle_unit, candle_start_at, open_price, high_price,
                          low_price, close_price, trade_volume, trade_amount, completeness,
                          calculation_version, source_as_of, knowledge_at, input_content_hash,
                          input_revision_ids, source_revision_through_id,
                          quality_event_through_id, coverage_snapshot_hash,
                          result_content_hash, quality,
                          materialized_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(
                          instrument_id, candle_unit, candle_start_at,
                          calculation_version, input_content_hash, coverage_snapshot_hash,
                          source_revision_through_id, quality_event_through_id
                        ) DO NOTHING
                        """,
                        (
                            instrument_id,
                            unit,
                            _to_db_time(item.started_at),
                            str(item.open),
                            str(item.high),
                            str(item.low),
                            str(item.close),
                            str(item.volume),
                            str(item.trade_amount),
                            item.completeness,
                            item.calculation_version,
                            _to_db_time(item.source_as_of) if item.source_as_of else None,
                            _to_db_time(knowledge_at),
                            item.input_content_hash,
                            ",".join(str(value) for value in item.input_revision_ids),
                            max(item.input_revision_ids, default=0),
                            quality_event_through_id or 0,
                            coverage_hash,
                            rollup_result_content_hash(item),
                            item.quality,
                            materialized_at,
                        ),
                    )
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()
        return len(rollups)

    def _candle_coverage(
        self,
        instrument_id: int,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[CoverageSlice]:
        rows = self._execute(
            """
            SELECT range_start_at, range_end_at, status
            FROM coverage_intervals
            WHERE instrument_id = ? AND candle_unit IN ('1m', '1d')
              AND (? IS NULL OR range_end_at > ?)
              AND (? IS NULL OR range_start_at < ?)
            ORDER BY range_start_at
            """,
            (
                instrument_id,
                _to_db_time(start_at) if start_at else None,
                _to_db_time(start_at) if start_at else None,
                _to_db_time(end_at) if end_at else None,
                _to_db_time(end_at) if end_at else None,
            ),
        ).fetchall()
        return [
            CoverageSlice(
                (
                    max(_from_db_time(row["range_start_at"]), start_at)
                    if start_at
                    else _from_db_time(row["range_start_at"])
                ),
                (
                    min(_from_db_time(row["range_end_at"]), end_at)
                    if end_at
                    else _from_db_time(row["range_end_at"])
                ),
                cast(
                    Literal["available", "no_trade", "missing", "unavailable", "unverified"],
                    row["status"],
                ),
            )
            for row in rows
        ]

    def replace_candle_coverage_classification(
        self,
        instrument_id: int,
        start_at: datetime,
        end_at: datetime,
        status: Literal["available", "no_trade", "missing", "unavailable", "unverified"],
    ) -> None:
        """SQLite parity용 커버리지 전이와 품질 전용 재계산 전파다."""

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                overlaps = self._execute(
                    """
                    SELECT * FROM coverage_intervals
                    WHERE instrument_id = ? AND candle_unit = '1m'
                      AND range_start_at < ? AND range_end_at > ?
                    ORDER BY range_start_at
                    """,
                    (instrument_id, _to_db_time(end_at), _to_db_time(start_at)),
                ).fetchall()
                if (
                    len(overlaps) == 1
                    and overlaps[0]["range_start_at"] == _to_db_time(start_at)
                    and overlaps[0]["range_end_at"] == _to_db_time(end_at)
                    and overlaps[0]["status"] == status
                ):
                    self._conn.commit()
                    return
                self._execute(
                    """
                    DELETE FROM coverage_intervals
                    WHERE instrument_id = ? AND candle_unit = '1m'
                      AND range_start_at < ? AND range_end_at > ?
                    """,
                    (instrument_id, _to_db_time(end_at), _to_db_time(start_at)),
                )
                for overlap in overlaps:
                    overlap_start = _from_db_time(overlap["range_start_at"])
                    overlap_end = _from_db_time(overlap["range_end_at"])
                    for preserved_start, preserved_end in (
                        (overlap_start, min(overlap_end, start_at)),
                        (max(overlap_start, end_at), overlap_end),
                    ):
                        if preserved_start >= preserved_end:
                            continue
                        self._execute(
                            """
                            INSERT INTO coverage_intervals (
                              instrument_id, candle_unit, range_start_at, range_end_at, status
                            ) VALUES (?, '1m', ?, ?, ?)
                            """,
                            (
                                instrument_id,
                                _to_db_time(preserved_start),
                                _to_db_time(preserved_end),
                                overlap["status"],
                            ),
                        )
                self._execute(
                    """
                    INSERT INTO coverage_intervals (
                      instrument_id, candle_unit, range_start_at, range_end_at, status
                    ) VALUES (?, '1m', ?, ?, ?)
                    """,
                    (instrument_id, _to_db_time(start_at), _to_db_time(end_at), status),
                )
                ceiling_row = self._execute(
                    "SELECT MAX(id) AS id FROM source_candle_revisions WHERE instrument_id = ?",
                    (instrument_id,),
                ).fetchone()
                if ceiling_row is None or ceiling_row["id"] is None:
                    self._conn.commit()
                    return
                now = now_kst()
                quality_event_id = int(
                    self._execute(
                        """
                        SELECT COALESCE(MAX(quality_event_through_id), 0) + 1
                        FROM candle_rollup_invalidations
                        """
                    ).fetchone()[0]
                )
                for affected in affected_rollup_ranges_for_interval(
                    start_at.astimezone(UTC), end_at.astimezone(UTC)
                ):
                    contains_source = self._execute(
                        """
                        SELECT 1 FROM source_candle_revisions
                        WHERE instrument_id = ?
                          AND candle_start_at >= ? AND candle_start_at < ?
                        LIMIT 1
                        """,
                        (
                            instrument_id,
                            _to_db_time(affected.start_at),
                            _to_db_time(affected.end_at),
                        ),
                    ).fetchone()
                    if contains_source is None:
                        continue
                    coverage = self._candle_coverage(
                        instrument_id, affected.start_at, affected.end_at
                    )
                    coverage_hash = _coverage_snapshot_hash(coverage)
                    coverage_payload = [
                        {
                            "startAt": item.start_at.astimezone(UTC).isoformat(),
                            "endAt": item.end_at.astimezone(UTC).isoformat(),
                            "status": item.status,
                        }
                        for item in coverage
                    ]
                    fingerprint = hashlib.sha256(
                        "|".join(
                            (
                                "quality",
                                str(instrument_id),
                                affected.unit,
                                affected.start_at.isoformat(),
                                affected.end_at.isoformat(),
                                coverage_hash,
                            )
                        ).encode()
                    ).hexdigest()
                    invalidation = self._execute(
                        """
                        INSERT INTO candle_rollup_invalidations (
                          idempotency_key, market_id, instrument_id, candle_unit,
                          calculation_version, range_start_at, range_end_at,
                          output_bucket_count, source_revision_ids,
                          source_revision_through_id, quality_event_through_id,
                          coverage_snapshot, coverage_snapshot_hash, knowledge_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(idempotency_key) DO NOTHING
                        """,
                        (
                            fingerprint,
                            instrument_id,
                            instrument_id,
                            affected.unit,
                            CALCULATION_VERSION,
                            _to_db_time(affected.start_at),
                            _to_db_time(affected.end_at),
                            affected.output_bucket_count,
                            int(ceiling_row["id"]),
                            quality_event_id,
                            json.dumps(coverage_payload, ensure_ascii=False, sort_keys=True),
                            coverage_hash,
                            _to_db_time(now),
                            _to_db_time(now),
                        ),
                    )
                    if invalidation.rowcount == 0:
                        continue
                    self._execute(
                        """
                        INSERT INTO candle_rollup_recompute_jobs (
                          invalidation_id, idempotency_key, status, next_retry_at,
                          created_at, updated_at
                        ) VALUES (?, ?, 'pending', ?, ?, ?)
                        """,
                        (
                            _required_lastrowid(invalidation),
                            fingerprint,
                            _to_db_time(now),
                            _to_db_time(now),
                            _to_db_time(now),
                        ),
                    )
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def candle_rollups(
        self,
        instrument_id: int,
        unit: str,
        start_at: datetime,
        end_at: datetime,
        *,
        knowledge_at: datetime | None = None,
        source_revision_through_id: int | None = None,
        quality_event_through_id: int | None = None,
    ) -> list[CandleView]:
        rows = self._execute(
            """
            SELECT * FROM (
              SELECT rollup.*,
                     ROW_NUMBER() OVER (
                       PARTITION BY instrument_id, candle_unit, candle_start_at,
                                    calculation_version
                       ORDER BY source_revision_through_id DESC,
                                quality_event_through_id DESC,
                                knowledge_at DESC, id DESC
                     ) AS projection_rank
              FROM candle_rollups rollup
              WHERE instrument_id = ? AND candle_unit = ?
                AND calculation_version = ?
                AND candle_start_at >= ? AND candle_start_at < ?
                AND (? IS NULL OR knowledge_at <= ?)
                AND (? IS NULL OR source_revision_through_id <= ?)
                AND (? IS NULL OR COALESCE(quality_event_through_id, 0) <= ?)
            ) projection
            WHERE projection_rank = 1
            ORDER BY candle_start_at
            """,
            (
                instrument_id,
                unit,
                CALCULATION_VERSION,
                _to_db_time(start_at),
                _to_db_time(end_at),
                _to_db_time(knowledge_at) if knowledge_at else None,
                _to_db_time(knowledge_at) if knowledge_at else None,
                source_revision_through_id,
                source_revision_through_id,
                quality_event_through_id,
                quality_event_through_id,
            ),
        ).fetchall()
        return [
            CandleView(
                started_at=_from_db_time(row["candle_start_at"]),
                open=_decimal(row["open_price"]),
                high=_decimal(row["high_price"]),
                low=_decimal(row["low_price"]),
                close=_decimal(row["close_price"]),
                volume=_decimal(row["trade_volume"]),
                trade_amount=_decimal(row["trade_amount"]),
                completeness=cast(Literal["complete", "partial", "empty"], row["completeness"]),
                calculation_version=str(row["calculation_version"]),
                source_as_of=(_from_db_time(row["source_as_of"]) if row["source_as_of"] else None),
                knowledge_at=(_from_db_time(row["knowledge_at"]) if row["knowledge_at"] else None),
                input_content_hash=str(row["input_content_hash"]),
                quality=cast(
                    Literal["available", "no_trade", "missing", "unavailable", "unverified"],
                    row["quality"],
                ),
                input_revision_ids=tuple(
                    int(value) for value in str(row["input_revision_ids"]).split(",") if value
                ),
                rollup_id=int(row["id"]),
                source_revision_through_id=int(row["source_revision_through_id"]),
                quality_event_through_id=(
                    int(row["quality_event_through_id"])
                    if row["quality_event_through_id"] is not None
                    else None
                ),
                coverage_snapshot_hash=str(row["coverage_snapshot_hash"]),
            )
            for row in rows
        ]

    def candle_rollup_recompute_job(self, job_id: int) -> CandleRollupRecomputeJob:
        row = self._execute(
            """
            SELECT job.*, invalidation.market_id, invalidation.instrument_id,
                   invalidation.candle_unit, invalidation.calculation_version,
                   invalidation.range_start_at, invalidation.range_end_at,
                   invalidation.source_revision_through_id,
                   invalidation.quality_event_through_id
            FROM candle_rollup_recompute_jobs job
            JOIN candle_rollup_invalidations invalidation
              ON invalidation.id = job.invalidation_id
            WHERE job.id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"집계 재계산 작업을 찾을 수 없다: {job_id}")
        return self._candle_rollup_recompute_job_from_row(row)

    def latest_candle_rollup_recompute_job(self) -> CandleRollupRecomputeJob | None:
        row = self._execute(
            "SELECT id FROM candle_rollup_recompute_jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return self.candle_rollup_recompute_job(int(row["id"])) if row else None

    def claim_next_candle_rollup_recompute_job(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
        lease_seconds: int = 120,
    ) -> CandleRollupRecomputeJob | None:
        claimed_at = now or now_kst()
        with self._lock, self._conn:
            self._dead_letter_expired_candle_rollup_jobs_locked(claimed_at)
            row = self._execute(
                """
                SELECT id FROM candle_rollup_recompute_jobs
                WHERE (
                  status IN ('pending', 'retry_wait') AND next_retry_at <= ?
                  AND attempt_count < max_attempts
                ) OR (
                  status = 'running' AND lease_expires_at < ?
                  AND attempt_count < max_attempts
                )
                ORDER BY priority DESC, created_at, id LIMIT 1
                """,
                (_to_db_time(claimed_at), _to_db_time(claimed_at)),
            ).fetchone()
            if row is None:
                return None
            return self._claim_candle_rollup_recompute_job_locked(
                int(row["id"]), worker_id, claimed_at, lease_seconds
            )

    def claim_candle_rollup_recompute_job(
        self,
        job_id: int,
        worker_id: str,
        *,
        now: datetime | None = None,
        lease_seconds: int = 120,
    ) -> CandleRollupRecomputeJob | None:
        claimed_at = now or now_kst()
        with self._lock, self._conn:
            self._dead_letter_expired_candle_rollup_jobs_locked(claimed_at, job_id=job_id)
            return self._claim_candle_rollup_recompute_job_locked(
                job_id, worker_id, claimed_at, lease_seconds
            )

    def _dead_letter_expired_candle_rollup_jobs_locked(
        self, expired_at: datetime, *, job_id: int | None = None
    ) -> None:
        job_filter = "AND id = ?" if job_id is not None else ""
        self._execute(
            f"""
            UPDATE candle_rollup_recompute_jobs
            SET status = 'dead_letter', lease_owner = NULL, lease_expires_at = NULL,
                last_error_code = COALESCE(last_error_code, 'LEASE_EXPIRED'),
                dead_letter_reason = 'LEASE_EXPIRED', finished_at = ?, updated_at = ?
            WHERE status = 'running' AND lease_expires_at < ?
              AND attempt_count >= max_attempts {job_filter}
            """,
            (
                _to_db_time(expired_at),
                _to_db_time(expired_at),
                _to_db_time(expired_at),
                *(tuple([job_id]) if job_id is not None else ()),
            ),
        )

    def _claim_candle_rollup_recompute_job_locked(
        self, job_id: int, worker_id: str, claimed_at: datetime, lease_seconds: int
    ) -> CandleRollupRecomputeJob | None:
        cursor = self._execute(
            """
            UPDATE candle_rollup_recompute_jobs
            SET status = 'running', lease_owner = ?, lease_expires_at = ?,
                attempt_count = attempt_count + 1,
                processing_source_revision_through_id = (
                  SELECT source_revision_through_id FROM candle_rollup_invalidations
                  WHERE id = candle_rollup_recompute_jobs.invalidation_id
                ),
                processing_quality_event_through_id = (
                  SELECT quality_event_through_id FROM candle_rollup_invalidations
                  WHERE id = candle_rollup_recompute_jobs.invalidation_id
                ),
                started_at = COALESCE(started_at, ?), updated_at = ?
            WHERE id = ? AND (
              (status IN ('pending', 'retry_wait') AND next_retry_at <= ?
               AND attempt_count < max_attempts)
              OR (status = 'running' AND lease_expires_at < ?
                  AND attempt_count < max_attempts)
            )
            """,
            (
                worker_id,
                _to_db_time(claimed_at + timedelta(seconds=lease_seconds)),
                _to_db_time(claimed_at),
                _to_db_time(claimed_at),
                job_id,
                _to_db_time(claimed_at),
                _to_db_time(claimed_at),
            ),
        )
        return self.candle_rollup_recompute_job(job_id) if cursor.rowcount == 1 else None

    def run_candle_rollup_recompute_job(
        self, job_id: int, worker_id: str, *, now: datetime | None = None
    ) -> int:
        completed_at = now or now_kst()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._execute(
                    """
                    SELECT job.*, invalidation.*,
                           job.id AS job_id, invalidation.id AS invalidation_id
                    FROM candle_rollup_recompute_jobs job
                    JOIN candle_rollup_invalidations invalidation
                      ON invalidation.id = job.invalidation_id
                    WHERE job.id = ?
                    """,
                    (job_id,),
                ).fetchone()
                if (
                    row is None
                    or row["status"] != "running"
                    or row["lease_owner"] != worker_id
                    or _from_db_time(row["lease_expires_at"]) < completed_at
                ):
                    raise RuntimeError("집계 재계산 작업의 유효한 임대가 아니다.")
                source_unit_filter = (
                    "revision.candle_unit IN ('1m', '1d')"
                    if row["candle_unit"] in {"1d", "1w", "1M"}
                    else "revision.candle_unit = '1m'"
                )
                source_rows = self._execute(
                    f"""
                    SELECT revision.*, revision.source_as_of AS collected_at,
                           revision.id AS revision_id,
                           revision.knowledge_at AS revision_knowledge_at
                    FROM source_candle_revisions revision
                    WHERE revision.instrument_id = ? AND {source_unit_filter}
                      AND revision.candle_start_at >= ? AND revision.candle_start_at < ?
                      AND revision.id <= ?
                      AND revision.id = (
                        SELECT latest.id
                        FROM source_candle_revisions latest
                        WHERE latest.instrument_id = revision.instrument_id
                          AND latest.candle_unit = revision.candle_unit
                          AND latest.candle_start_at = revision.candle_start_at
                          AND latest.id <= ?
                        ORDER BY latest.source_as_of DESC,
                                 latest.revision_number DESC, latest.id DESC
                        LIMIT 1
                      )
                    ORDER BY revision.candle_start_at
                    """,
                    (
                        row["instrument_id"],
                        row["range_start_at"],
                        row["range_end_at"],
                        row["source_revision_through_id"],
                        row["source_revision_through_id"],
                    ),
                ).fetchall()
                coverage = [
                    CoverageSlice(
                        datetime.fromisoformat(item["startAt"]),
                        datetime.fromisoformat(item["endAt"]),
                        cast(Any, item["status"]),
                    )
                    for item in json.loads(row["coverage_snapshot"])
                ]
                rollups = aggregate_candles(
                    str(row["candle_unit"]),
                    [self._candle_from_row(item) for item in source_rows],
                    coverage=coverage,
                )
                rows_written = 0
                for item in rollups:
                    if not (
                        _from_db_time(row["range_start_at"])
                        <= item.started_at
                        < _from_db_time(row["range_end_at"])
                    ):
                        continue
                    existing = self._execute(
                        """
                        SELECT MAX(source_revision_through_id) FROM candle_rollups
                        WHERE instrument_id = ? AND candle_unit = ?
                          AND candle_start_at = ? AND calculation_version = ?
                        """,
                        (
                            row["instrument_id"],
                            row["candle_unit"],
                            _to_db_time(item.started_at),
                            row["calculation_version"],
                        ),
                    ).fetchone()
                    existing_ceiling = int(existing[0] or 0) if existing else 0
                    if existing_ceiling > int(row["source_revision_through_id"]):
                        continue
                    rows_written += self._insert_incremental_rollup(row, item, completed_at)
                finished_at = now_kst()
                cursor = self._execute(
                    """
                    UPDATE candle_rollup_recompute_jobs
                    SET status = 'succeeded', rows_written = ?, lease_owner = NULL,
                        lease_expires_at = NULL, finished_at = ?, updated_at = ?,
                        last_error_code = NULL, dead_letter_reason = NULL
                    WHERE id = ? AND status = 'running' AND lease_owner = ?
                      AND lease_expires_at >= ?
                    """,
                    (
                        rows_written,
                        _to_db_time(finished_at),
                        _to_db_time(finished_at),
                        job_id,
                        worker_id,
                        _to_db_time(finished_at),
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("집계 재계산 작업의 임대 fencing에 실패했다.")
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()
        return rows_written

    def _insert_incremental_rollup(
        self, invalidation: sqlite3.Row, item: CandleView, materialized_at: datetime
    ) -> int:
        cursor = self._execute(
            """
            INSERT INTO candle_rollups (
              instrument_id, candle_unit, candle_start_at, open_price, high_price,
              low_price, close_price, trade_volume, trade_amount, completeness,
              calculation_version, source_as_of, knowledge_at, input_content_hash,
              input_revision_ids, source_revision_through_id,
              quality_event_through_id, coverage_snapshot_hash,
              result_content_hash, quality, materialized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
              instrument_id, candle_unit, candle_start_at,
              calculation_version, input_content_hash, coverage_snapshot_hash,
              source_revision_through_id, quality_event_through_id
            ) DO NOTHING
            """,
            (
                invalidation["instrument_id"], invalidation["candle_unit"],
                _to_db_time(item.started_at), str(item.open), str(item.high), str(item.low),
                str(item.close), str(item.volume), str(item.trade_amount), item.completeness,
                invalidation["calculation_version"],
                (
                    _to_db_time(item.source_as_of)
                    if item.source_as_of
                    else invalidation["knowledge_at"]
                ),
                _to_db_time(
                    max(
                        item.knowledge_at or datetime.min.replace(tzinfo=UTC),
                        _from_db_time(invalidation["knowledge_at"]),
                    )
                ),
                item.input_content_hash,
                ",".join(str(value) for value in item.input_revision_ids),
                int(invalidation["source_revision_through_id"]),
                invalidation["quality_event_through_id"] or 0,
                invalidation["coverage_snapshot_hash"],
                rollup_result_content_hash(item), item.quality,
                _to_db_time(materialized_at),
            ),
        )
        return cursor.rowcount

    def fail_candle_rollup_recompute_job(
        self,
        job_id: int,
        worker_id: str,
        error_code: str,
        *,
        now: datetime | None = None,
    ) -> CandleRollupRecomputeJob:
        failed_at = now or now_kst()
        with self._lock, self._conn:
            row = self._execute(
                "SELECT * FROM candle_rollup_recompute_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if (
                row is None
                or row["status"] != "running"
                or row["lease_owner"] != worker_id
            ):
                raise RuntimeError("집계 재계산 작업의 유효한 임대가 아니다.")
            attempts = int(row["attempt_count"])
            exhausted = attempts >= int(row["max_attempts"])
            retry_at = failed_at + timedelta(seconds=min(300, 5 * (2 ** (attempts - 1))))
            fenced_at = now_kst()
            cursor = self._execute(
                """
                UPDATE candle_rollup_recompute_jobs
                SET status = ?, next_retry_at = ?, lease_owner = NULL,
                    lease_expires_at = NULL, last_error_code = ?, dead_letter_reason = ?,
                    finished_at = CASE WHEN ? THEN ? ELSE NULL END, updated_at = ?
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                  AND lease_expires_at >= ?
                """,
                (
                    "dead_letter" if exhausted else "retry_wait", _to_db_time(retry_at), error_code,
                    error_code if exhausted else None, exhausted, _to_db_time(failed_at),
                    _to_db_time(failed_at), job_id, worker_id, _to_db_time(fenced_at),
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("집계 재계산 작업의 유효한 임대가 아니다.")
        return self.candle_rollup_recompute_job(job_id)

    def safe_restart_candle_rollup_recompute_job(
        self, job_id: int
    ) -> CandleRollupRecomputeJob:
        restarted_at = now_kst()
        with self._lock, self._conn:
            cursor = self._execute(
                """
                UPDATE candle_rollup_recompute_jobs
                SET status = 'pending', attempt_count = 0, next_retry_at = ?,
                    lease_owner = NULL, lease_expires_at = NULL, last_error_code = NULL,
                    dead_letter_reason = NULL, started_at = NULL, finished_at = NULL,
                    updated_at = ?
                WHERE id = ? AND status = 'dead_letter'
                """,
                (_to_db_time(restarted_at), _to_db_time(restarted_at), job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("dead_letter 작업만 safe-restart할 수 있다.")
        return self.candle_rollup_recompute_job(job_id)

    @staticmethod
    def _candle_rollup_recompute_job_from_row(row: sqlite3.Row) -> CandleRollupRecomputeJob:
        return CandleRollupRecomputeJob(
            id=int(row["id"]), invalidation_id=int(row["invalidation_id"]),
            status=cast(Any, row["status"]), market_id=int(row["market_id"]),
            instrument_id=int(row["instrument_id"]), candle_unit=str(row["candle_unit"]),
            calculation_version=str(row["calculation_version"]),
            range_start_at=_from_db_time(row["range_start_at"]),
            range_end_at=_from_db_time(row["range_end_at"]),
            source_revision_through_id=int(row["source_revision_through_id"]),
            quality_event_through_id=(
                int(row["quality_event_through_id"])
                if row["quality_event_through_id"] is not None else None
            ),
            attempt_count=int(row["attempt_count"]), max_attempts=int(row["max_attempts"]),
            next_retry_at=_from_db_time(row["next_retry_at"]), lease_owner=row["lease_owner"],
            lease_expires_at=(
                _from_db_time(row["lease_expires_at"]) if row["lease_expires_at"] else None
            ),
            rows_written=int(row["rows_written"]), last_error_code=row["last_error_code"],
            dead_letter_reason=row["dead_letter_reason"],
        )

    def schedule_candle_aggregation(self) -> CandleAggregationJob | None:
        existing = self._execute(
            """
            SELECT id FROM candle_aggregation_jobs
            WHERE status IN ('pending', 'running')
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        if existing:
            return self._candle_aggregation_job(int(existing["id"]))
        stale_targets: list[tuple[int, str]] = []
        for instrument in self.list_active_targets():
            source_latest = self._execute(
                """
                SELECT MAX(candle_start_at) AS candle_start_at
                FROM source_candles WHERE instrument_id = ?
                """,
                (instrument.id,),
            ).fetchone()["candle_start_at"]
            if source_latest is None:
                continue
            latest = _from_db_time(source_latest)
            for unit in MATERIALIZED_AGGREGATION_UNITS:
                rollup_latest = self._execute(
                    """
                    SELECT MAX(candle_start_at) AS candle_start_at
                    FROM candle_rollups WHERE instrument_id = ? AND candle_unit = ?
                      AND calculation_version = ?
                    """,
                    (instrument.id, unit, CALCULATION_VERSION),
                ).fetchone()["candle_start_at"]
                if rollup_latest is None or _from_db_time(rollup_latest) < rollup_bucket_start(
                    unit, latest
                ):
                    stale_targets.append((instrument.id, unit))
        if not stale_targets:
            return None
        created_at = _to_db_time(now_kst())
        cursor = self._execute(
            "INSERT INTO candle_aggregation_jobs (status, created_at) VALUES ('pending', ?)",
            (created_at,),
        )
        job_id = _required_lastrowid(cursor)
        for instrument_id, unit in stale_targets:
            self._execute(
                """
                INSERT INTO candle_aggregation_job_targets (
                  job_id, instrument_id, candle_unit, status, updated_at
                ) VALUES (?, ?, ?, 'pending', ?)
                """,
                (job_id, instrument_id, unit, created_at),
            )
        self._conn.commit()
        return self._candle_aggregation_job(job_id)

    def claim_next_candle_aggregation_job(self) -> CandleAggregationJob | None:
        row = self._execute(
            """
            SELECT id FROM candle_aggregation_jobs WHERE status = 'pending'
            ORDER BY id LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        job_id = int(row["id"])
        self._execute(
            """
            UPDATE candle_aggregation_jobs
            SET status = 'running', started_at = ?, finished_at = NULL
            WHERE id = ?
            """,
            (_to_db_time(now_kst()), job_id),
        )
        self._conn.commit()
        return self._candle_aggregation_job(job_id)

    def candle_aggregation_job_targets(self, job_id: int) -> list[CandleAggregationJobTarget]:
        rows = self._execute(
            """
            SELECT * FROM candle_aggregation_job_targets
            WHERE job_id = ? ORDER BY instrument_id, candle_unit
            """,
            (job_id,),
        ).fetchall()
        return [
            CandleAggregationJobTarget(
                job_id=job_id,
                instrument_id=int(row["instrument_id"]),
                candle_unit=str(row["candle_unit"]),
                status=cast(Literal["pending", "running", "succeeded", "failed"], row["status"]),
                rows_written=int(row["rows_written"]),
            )
            for row in rows
        ]

    def mark_candle_aggregation_target(
        self, job_id: int, instrument_id: int, unit: str, status: str, rows_written: int
    ) -> None:
        self._execute(
            """
            UPDATE candle_aggregation_job_targets
            SET status = ?, rows_written = ?, updated_at = ?
            WHERE job_id = ? AND instrument_id = ? AND candle_unit = ?
            """,
            (status, rows_written, _to_db_time(now_kst()), job_id, instrument_id, unit),
        )
        remaining = self._execute(
            """
            SELECT COUNT(*) AS count FROM candle_aggregation_job_targets
            WHERE job_id = ? AND status NOT IN ('succeeded', 'failed')
            """,
            (job_id,),
        ).fetchone()["count"]
        if int(remaining) == 0:
            failed = self._execute(
                """
                SELECT COUNT(*) AS count FROM candle_aggregation_job_targets
                WHERE job_id = ? AND status = 'failed'
                """,
                (job_id,),
            ).fetchone()["count"]
            self._execute(
                """
                UPDATE candle_aggregation_jobs
                SET status = ?, finished_at = ? WHERE id = ?
                """,
                ("failed" if int(failed) else "succeeded", _to_db_time(now_kst()), job_id),
            )
        self._conn.commit()

    def latest_candle_aggregation_job(self) -> CandleAggregationJob | None:
        row = self._execute(
            "SELECT id FROM candle_aggregation_jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return self._candle_aggregation_job(int(row["id"])) if row else None

    def _candle_aggregation_job(self, job_id: int) -> CandleAggregationJob:
        row = self._execute(
            """
            SELECT j.id, j.status, j.created_at,
                   COUNT(t.candle_unit) AS total_target_count,
                   SUM(CASE WHEN t.status = 'succeeded' THEN 1 ELSE 0 END)
                     AS completed_target_count,
                   SUM(CASE WHEN t.status = 'running' THEN 1 ELSE 0 END) AS running_target_count,
                   SUM(CASE WHEN t.status = 'pending' THEN 1 ELSE 0 END) AS pending_target_count,
                   SUM(CASE WHEN t.status = 'failed' THEN 1 ELSE 0 END) AS failed_target_count
            FROM candle_aggregation_jobs j
            LEFT JOIN candle_aggregation_job_targets t ON t.job_id = j.id
            WHERE j.id = ? GROUP BY j.id
            """,
            (job_id,),
        ).fetchone()
        total = int(row["total_target_count"])
        completed = int(row["completed_target_count"] or 0)
        return CandleAggregationJob(
            id=int(row["id"]),
            status=cast(Literal["pending", "running", "succeeded", "failed"], row["status"]),
            progress_percent=(
                Decimal(completed) * Decimal("100") / Decimal(total) if total else Decimal("0")
            ),
            total_target_count=total,
            completed_target_count=completed,
            running_target_count=int(row["running_target_count"] or 0),
            pending_target_count=int(row["pending_target_count"] or 0),
            failed_target_count=int(row["failed_target_count"] or 0),
            created_at=_from_db_time(row["created_at"]),
        )

    def ticker_snapshots(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> list[TickerSnapshot]:
        rows = self._execute(
            """
            SELECT * FROM ticker_snapshots
            WHERE instrument_id = ? AND bucket_at >= ? AND bucket_at <= ?
            ORDER BY bucket_at
            """,
            (instrument_id, _to_db_time(start_at), _to_db_time(end_at)),
        ).fetchall()
        return [self._ticker_from_row(row) for row in rows]

    def orderbook_summaries(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> list[OrderbookSummary]:
        rows = self._execute(
            """
            SELECT * FROM orderbook_summaries
            WHERE instrument_id = ? AND bucket_at >= ? AND bucket_at <= ?
            ORDER BY bucket_at
            """,
            (instrument_id, _to_db_time(start_at), _to_db_time(end_at)),
        ).fetchall()
        return [self._orderbook_from_row(row) for row in rows]

    def trade_summary(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> TradeSummary:
        row = self._execute(
            """
            SELECT COUNT(*) AS trade_count,
                   COALESCE(SUM(CASE WHEN ask_bid = 'BID' THEN trade_volume ELSE 0 END), 0)
                     AS buy_volume,
                   COALESCE(SUM(CASE WHEN ask_bid = 'ASK' THEN trade_volume ELSE 0 END), 0)
                     AS sell_volume,
                   MAX(trade_timestamp_at) AS last_trade_at
            FROM trade_events
            WHERE instrument_id = ? AND trade_timestamp_at >= ? AND trade_timestamp_at <= ?
            """,
            (instrument_id, _to_db_time(start_at), _to_db_time(end_at)),
        ).fetchone()
        return TradeSummary(
            trade_count=int(row["trade_count"]),
            buy_volume=Decimal(str(row["buy_volume"])),
            sell_volume=Decimal(str(row["sell_volume"])),
            last_trade_at=_from_db_time(row["last_trade_at"]) if row["last_trade_at"] else None,
        )

    def collection_runs(self, limit: int) -> list[CollectionRun]:
        rows = self._execute(
            """
            SELECT * FROM collection_runs
            ORDER BY started_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._collection_run_from_row(row) for row in rows]

    def record_collection_worker_heartbeat(
        self,
        worker_type: CollectionWorkerType,
        status: CollectionWorkerHeartbeatStatus,
        error_message: str | None = None,
    ) -> None:
        if worker_type not in {
            "realtime_collection",
            "backfill_collection",
            "candle_aggregation",
        }:
            raise ValueError("지원하지 않는 수집 워커 유형이다.")
        if status not in {"running", "gated", "failed"}:
            raise ValueError("지원하지 않는 수집 워커 상태다.")
        now = now_kst()
        with self._lock, self._conn:
            self._execute(
                """
                INSERT INTO collection_worker_heartbeats (
                  worker_type, status, last_heartbeat_at, last_started_at,
                  last_successful_at, last_error_at, last_error_message, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_type) DO UPDATE SET
                  status = excluded.status,
                  last_heartbeat_at = excluded.last_heartbeat_at,
                  last_started_at = CASE
                    WHEN excluded.status = 'running'
                    THEN excluded.last_started_at
                    ELSE collection_worker_heartbeats.last_started_at
                  END,
                  last_successful_at = CASE
                    WHEN excluded.status = 'running'
                    THEN excluded.last_successful_at
                    ELSE collection_worker_heartbeats.last_successful_at
                  END,
                  last_error_at = CASE
                    WHEN excluded.status = 'failed'
                    THEN excluded.last_error_at
                    ELSE collection_worker_heartbeats.last_error_at
                  END,
                  last_error_message = CASE
                    WHEN excluded.status IN ('failed', 'gated')
                    THEN excluded.last_error_message
                    ELSE collection_worker_heartbeats.last_error_message
                  END,
                  updated_at = excluded.updated_at
                """,
                (
                    worker_type,
                    status,
                    _to_db_time(now),
                    _to_db_time(now) if status == "running" else None,
                    _to_db_time(now) if status == "running" else None,
                    _to_db_time(now) if status == "failed" else None,
                    error_message,
                    _to_db_time(now),
                ),
            )

    def record_collection_run_failure(
        self,
        run_type: str,
        data_type: str,
        started_at: datetime,
        error_code: str,
        error_message: str,
    ) -> CollectionRun:
        finished_at = now_kst()
        with self._lock, self._conn:
            cursor = self._execute(
                """
                INSERT INTO collection_runs (
                  run_type, data_type, status, trigger_type, started_at,
                  finished_at, error_code, error_message
                )
                VALUES (?, ?, 'failed', 'system', ?, ?, ?, ?)
                """,
                (
                    run_type,
                    data_type,
                    _to_db_time(started_at),
                    _to_db_time(finished_at),
                    error_code,
                    error_message,
                ),
            )
            run_id = _required_lastrowid(cursor)
        return CollectionRun(
            id=run_id,
            run_type=run_type,
            data_type=data_type,
            status="failed",
            started_at=started_at,
            finished_at=finished_at,
        )

    def create_backfill_plan(
        self,
        data_type: str,
        target_start_at: datetime,
        target_end_at: datetime,
        instrument_ids: list[int],
    ) -> BackfillPlan:
        if data_type != "source_candle":
            raise ValueError("M1 백필 계획은 source_candle만 지원한다.")
        if target_start_at >= target_end_at:
            raise ValueError("백필 종료 시각은 시작 시각보다 뒤여야 한다.")
        duration_minutes = max(1, int((target_end_at - target_start_at).total_seconds() // 60))
        estimated_request_count = len(instrument_ids) * max(1, duration_minutes // 200 + 1)
        estimated_row_count = len(instrument_ids) * duration_minutes
        plan = BackfillPlan(
            plan_id=str(uuid.uuid4()),
            data_type="source_candle",
            target_start_at=target_start_at,
            target_end_at=target_end_at,
            estimated_request_count=estimated_request_count,
            estimated_row_count=estimated_row_count,
            estimated_storage_bytes=estimated_row_count * 256,
            targets=instrument_ids,
        )
        with self._lock, self._conn:
            self._execute(
                """
                INSERT INTO backfill_plans (
                  plan_id, data_type, target_start_at, target_end_at,
                  estimated_request_count, estimated_row_count, estimated_storage_bytes,
                  targets, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.plan_id,
                    plan.data_type,
                    _to_db_time(plan.target_start_at),
                    _to_db_time(plan.target_end_at),
                    plan.estimated_request_count,
                    plan.estimated_row_count,
                    plan.estimated_storage_bytes,
                    ",".join(str(item) for item in plan.targets),
                    _to_db_time(now_kst()),
                ),
            )
        return plan

    def approve_backfill_job(self, plan_id: str) -> BackfillJob:
        row = self._execute("SELECT * FROM backfill_plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if row is None:
            raise ValueError("존재하지 않는 백필 계획이다.")
        created_at = _to_db_time(now_kst())
        with self._lock, self._conn:
            cursor = self._execute(
                """
                INSERT INTO backfill_jobs (
                  plan_id, status, data_type, progress_percent, created_at, updated_at
                )
                VALUES (?, 'pending', ?, '0', ?, ?)
                """,
                (plan_id, row["data_type"], created_at, created_at),
            )
            job_id = _required_lastrowid(cursor)
            targets = [int(item) for item in str(row["targets"]).split(",") if item]
            for instrument_id in targets:
                self._execute(
                    """
                    INSERT INTO backfill_job_targets (
                      backfill_job_id, instrument_id, status, updated_at
                    )
                    VALUES (?, ?, 'pending', ?)
                    """,
                    (job_id, instrument_id, created_at),
                )
        return self._backfill_job_by_id(job_id)

    def claim_next_backfill_job(self) -> BackfillJobDetail | None:
        with self._lock, self._conn:
            row = self._execute(
                """
                SELECT
                  bj.id, bj.status, bj.data_type, bj.created_at,
                  bp.target_start_at, bp.target_end_at,
                  bp.estimated_request_count, bp.estimated_row_count
                FROM backfill_jobs bj
                JOIN backfill_plans bp ON bp.plan_id = bj.plan_id
                WHERE bj.status IN ('pending', 'running')
                ORDER BY bj.created_at
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            if row["status"] == "pending":
                self._execute(
                    "UPDATE backfill_jobs SET status = 'running', updated_at = ? WHERE id = ?",
                    (_to_db_time(now_kst()), row["id"]),
                )
                row = self._execute(
                    """
                    SELECT
                      bj.id, bj.status, bj.data_type, bj.created_at,
                      bp.target_start_at, bp.target_end_at,
                      bp.estimated_request_count, bp.estimated_row_count
                    FROM backfill_jobs bj
                    JOIN backfill_plans bp ON bp.plan_id = bj.plan_id
                    WHERE bj.id = ?
                    """,
                    (row["id"],),
                ).fetchone()
        return self._backfill_job_detail_from_row(row)

    def backfill_job_targets(self, job_id: int) -> list[BackfillJobTarget]:
        rows = self._execute(
            """
            SELECT * FROM backfill_job_targets
            WHERE backfill_job_id = ?
            ORDER BY instrument_id
            """,
            (job_id,),
        ).fetchall()
        return [self._backfill_target_from_row(row) for row in rows]

    def record_backfill_candles(
        self,
        job_id: int,
        instrument_id: int,
        candles: list[SourceCandle],
        *,
        fetch_evidence: object | None = None,
    ) -> int:
        del fetch_evidence
        if not candles:
            return 0
        if any(item.instrument_id != instrument_id for item in candles):
            raise ValueError("백필 캔들 대상 instrument_id가 작업 대상과 다르다.")
        started_at = now_kst()
        with self._lock, self._conn:
            cursor = self._execute(
                """
                INSERT INTO collection_runs (run_type, data_type, status, trigger_type, started_at)
                VALUES ('backfill', 'source_candle', 'running', 'backfill_job', ?)
                """,
                (_to_db_time(started_at),),
            )
            run_id = _required_lastrowid(cursor)
            counts = self._upsert_candles(candles)
            rows_written = counts.get(instrument_id, 0)
            self._execute(
                """
                INSERT INTO target_collection_results (
                  collection_run_id, instrument_id, data_type, status,
                  latency_ms, rows_written, created_at
                )
                VALUES (?, ?, 'source_candle', 'succeeded', 0, ?, ?)
                """,
                (run_id, instrument_id, rows_written, _to_db_time(now_kst())),
            )
            self._execute(
                """
                UPDATE collection_runs
                SET status = 'succeeded', finished_at = ?
                WHERE id = ?
                """,
                (_to_db_time(now_kst()), run_id),
            )
            self._execute(
                """
                UPDATE backfill_job_targets
                SET status = 'running', updated_at = ?
                WHERE backfill_job_id = ? AND instrument_id = ? AND status = 'pending'
                """,
                (_to_db_time(now_kst()), job_id, instrument_id),
            )
        return rows_written

    def record_backfill_target_progress(
        self,
        job_id: int,
        instrument_id: int,
        processed_missing_range_count: int,
        estimated_missing_range_count: int,
        rows_written_count: int,
        last_completed_at: datetime | None,
    ) -> None:
        with self._lock, self._conn:
            self._execute(
                """
                UPDATE backfill_job_targets
                SET processed_missing_range_count = ?,
                    estimated_missing_range_count = ?,
                    rows_written_count = ?,
                    last_completed_at = ?,
                    updated_at = ?
                WHERE backfill_job_id = ? AND instrument_id = ?
                """,
                (
                    max(0, processed_missing_range_count),
                    max(0, estimated_missing_range_count),
                    max(0, rows_written_count),
                    _to_db_time(last_completed_at) if last_completed_at else None,
                    _to_db_time(now_kst()),
                    job_id,
                    instrument_id,
                ),
            )

    def mark_backfill_target(
        self,
        job_id: int,
        instrument_id: int,
        status: str,
        last_completed_at: datetime | None,
        error_code: str | None = None,
        error_message: str | None = None,
        retry_after_seconds: float | None = None,
        *,
        fetch_evidence: object | None = None,
    ) -> None:
        del retry_after_seconds, fetch_evidence
        if status not in {"pending", "running", "paused", "stopped", "succeeded", "failed"}:
            raise ValueError("지원하지 않는 백필 대상 상태다.")
        with self._lock, self._conn:
            self._execute(
                """
                UPDATE backfill_job_targets
                SET status = ?, last_completed_at = ?, error_code = ?,
                    error_message = ?, updated_at = ?
                WHERE backfill_job_id = ? AND instrument_id = ?
                """,
                (
                    status,
                    _to_db_time(last_completed_at) if last_completed_at else None,
                    error_code,
                    error_message,
                    _to_db_time(now_kst()),
                    job_id,
                    instrument_id,
                ),
            )
            self._refresh_backfill_job_progress(job_id)

    def control_backfill_job(self, job_id: int, action: str) -> BackfillJob:
        transitions = {
            "pause": "paused",
            "stop": "stopped",
            "resume": "running",
            "safe-restart": "pending",
        }
        if action not in transitions:
            raise ValueError("지원하지 않는 백필 제어 명령이다.")
        with self._lock, self._conn:
            current = self._backfill_job_by_id(job_id)
            is_terminal_action_allowed = action == "safe-restart" or (
                current.status == "failed" and action == "resume"
            )
            if (
                current.status in {"succeeded", "failed", "stopped"}
                and not is_terminal_action_allowed
            ):
                raise ValueError("완료 또는 중지된 백필 작업은 해당 명령을 수행할 수 없다.")
            self._execute(
                "UPDATE backfill_jobs SET status = ?, updated_at = ? WHERE id = ?",
                (transitions[action], _to_db_time(now_kst()), job_id),
            )
        return self._backfill_job_by_id(job_id)

    def delete_backfill_job(self, job_id: int) -> None:
        with self._lock, self._conn:
            current = self._execute(
                "SELECT status FROM backfill_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if current is None:
                raise ValueError("존재하지 않는 백필 작업이다.")
            if current["status"] == "running":
                raise ValueError("실행 중인 백필 작업은 먼저 중지해야 한다.")
            self._execute("DELETE FROM backfill_job_targets WHERE backfill_job_id = ?", (job_id,))
            self._execute("DELETE FROM backfill_jobs WHERE id = ?", (job_id,))

    def backfill_jobs(self) -> list[BackfillJob]:
        stopped_since = _to_db_time(now_kst() - timedelta(days=30))
        rows = self._execute(
            """
            SELECT
              bj.*,
              bp.target_start_at,
              bp.target_end_at,
              bp.estimated_request_count,
              COALESCE(
                ROUND(
                  100.0 * SUM(
                    CASE
                      WHEN bjt.status = 'succeeded' THEN 1.0
                      WHEN bjt.estimated_missing_range_count > 0 THEN
                        MIN(
                          1.0,
                          CAST(bjt.processed_missing_range_count AS REAL)
                            / bjt.estimated_missing_range_count
                        )
                      ELSE 0
                    END
                  ) / NULLIF(COUNT(bjt.instrument_id), 0),
                  2
                ),
                0
              ) AS live_progress_percent,
              COUNT(bjt.instrument_id) AS total_target_count,
              COUNT(CASE WHEN bjt.status = 'succeeded' THEN 1 END) AS completed_target_count,
              running.instrument_id AS current_target_id,
              running.processed_missing_range_count,
              running.estimated_missing_range_count,
              running.rows_written_count AS current_target_backfill_row_count,
              CASE
                WHEN running.instrument_id IS NULL THEN NULL
                ELSE (
                  SELECT COUNT(*)
                  FROM backfill_job_targets bjt_index
                  WHERE bjt_index.backfill_job_id = bj.id
                    AND bjt_index.instrument_id <= running.instrument_id
                )
              END AS running_target_index
            FROM backfill_jobs bj
            JOIN backfill_plans bp ON bp.plan_id = bj.plan_id
            LEFT JOIN backfill_job_targets bjt ON bjt.backfill_job_id = bj.id
            LEFT JOIN backfill_job_targets running
              ON running.backfill_job_id = bj.id
             AND running.instrument_id = (
               SELECT MIN(instrument_id)
               FROM backfill_job_targets
               WHERE backfill_job_id = bj.id AND status = 'running'
             )
            WHERE bj.status != 'stopped' OR bj.created_at >= ?
            GROUP BY bj.id
            ORDER BY bj.created_at DESC
            """,
            (stopped_since,),
        ).fetchall()
        return [self._backfill_job_from_row(row) for row in rows]

    def notification_events(self) -> list[NotificationEvent]:
        rows = self._execute(
            "SELECT * FROM notification_events ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        return [self._notification_from_row(row) for row in rows]

    def _recent_run_count(self) -> int:
        since = _to_db_time(now_kst() - timedelta(hours=24))
        row = self._execute(
            "SELECT COUNT(*) AS count FROM collection_runs WHERE started_at >= ?",
            (since,),
        ).fetchone()
        return int(row["count"]) if row else 0

    def _recent_collection_result_count(self) -> int:
        since = _to_db_time(now_kst() - timedelta(hours=24))
        row = self._execute(
            "SELECT COUNT(*) AS count FROM target_collection_results WHERE created_at >= ?",
            (since,),
        ).fetchone()
        return int(row["count"]) if row else 0

    def _collection_rows_last_minute(self, run_type: str) -> int:
        since = _to_db_time(now_kst() - timedelta(minutes=1))
        row = self._execute(
            """
            SELECT COALESCE(SUM(tcr.rows_written), 0) AS count
            FROM target_collection_results tcr
            JOIN collection_runs cr ON cr.id = tcr.collection_run_id
            WHERE cr.run_type = ? AND tcr.created_at >= ?
            """,
            (run_type, since),
        ).fetchone()
        return int(row["count"]) if row else 0

    def _storage_rows_today(self) -> int:
        day_start = now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            self._table_count_since("source_candles", "collected_at", day_start)
            + self._table_count_since("ticker_snapshots", "collected_at", day_start)
            + self._table_count_since("orderbook_summaries", "collected_at", day_start)
        )

    def _storage_bytes_today_estimate(self) -> int:
        day_start = now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            self._table_count_since("source_candles", "collected_at", day_start) * 256
            + self._table_count_since("ticker_snapshots", "collected_at", day_start) * 160
            + self._table_count_since("orderbook_summaries", "collected_at", day_start) * 224
        )

    def _realtime_collection_heatmap(self) -> list[RealtimeCollectionHeatmapRow]:
        active_targets = self.list_active_targets()[:50]
        if not active_targets:
            return []
        now = now_kst()
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        first_hour = current_hour - timedelta(hours=23)
        target_ids = [target.id for target in active_targets]
        placeholders = ", ".join(["?"] * len(target_ids))
        aggregates: dict[tuple[int, datetime], dict[str, Decimal | int]] = {}
        for row in self._execute(
            f"""
            SELECT instrument_id, trade_timestamp_at, trade_volume, trade_amount, ask_bid
            FROM trade_events
            WHERE trade_timestamp_at >= ? AND instrument_id IN ({placeholders})
            """,
            (_to_db_time(first_hour), *target_ids),
        ).fetchall():
            bucket = _from_db_time(row["trade_timestamp_at"]).replace(
                minute=0, second=0, microsecond=0
            )
            key = (row["instrument_id"], bucket)
            aggregate = aggregates.setdefault(
                key,
                {
                    "trade_count": 0,
                    "bid_volume": Decimal("0"),
                    "ask_volume": Decimal("0"),
                    "trade_volume": Decimal("0"),
                    "trade_amount": Decimal("0"),
                },
            )
            trade_volume = _decimal(row["trade_volume"])
            aggregate["trade_count"] = int(aggregate["trade_count"]) + 1
            aggregate["trade_volume"] = cast(Decimal, aggregate["trade_volume"]) + trade_volume
            aggregate["trade_amount"] = cast(Decimal, aggregate["trade_amount"]) + _decimal(
                row["trade_amount"]
            )
            if row["ask_bid"] == "BID":
                aggregate["bid_volume"] = cast(Decimal, aggregate["bid_volume"]) + trade_volume
            else:
                aggregate["ask_volume"] = cast(Decimal, aggregate["ask_volume"]) + trade_volume

        heatmap: list[RealtimeCollectionHeatmapRow] = []
        for target in active_targets:
            hourly_buckets: list[RealtimeCollectionHeatmapBucket] = []
            for offset in range(24):
                bucket_start = first_hour + timedelta(hours=offset)
                aggregate = aggregates.get(
                    (target.id, bucket_start),
                    {
                        "trade_count": 0,
                        "bid_volume": Decimal("0"),
                        "ask_volume": Decimal("0"),
                        "trade_volume": Decimal("0"),
                        "trade_amount": Decimal("0"),
                    },
                )
                trade_count = int(aggregate["trade_count"])
                average_trades_per_minute = Decimal(trade_count) / Decimal("60")
                ask_volume = cast(Decimal, aggregate["ask_volume"])
                trade_strength = (
                    cast(Decimal, aggregate["bid_volume"]) / ask_volume * Decimal("100")
                    if ask_volume > 0
                    else Decimal("0")
                )
                hourly_buckets.append(
                    RealtimeCollectionHeatmapBucket(
                        bucket_start_at=bucket_start,
                        trade_count=trade_count,
                        average_trades_per_minute=average_trades_per_minute,
                        trade_strength=trade_strength,
                        trade_volume=cast(Decimal, aggregate["trade_volume"]),
                        trade_amount=cast(Decimal, aggregate["trade_amount"]),
                        status=self._realtime_collection_heatmap_status(average_trades_per_minute),
                    )
                )
            heatmap.append(
                RealtimeCollectionHeatmapRow(
                    instrument=target,
                    instrument_display_name=target.display_name,
                    hourly_buckets=hourly_buckets,
                )
            )
        return heatmap

    def _collection_activity_buckets(self) -> list[CollectionActivityBucket]:
        current_hour = now_kst().replace(minute=0, second=0, microsecond=0)
        first_hour = current_hour - timedelta(hours=(7 * 24) - 1)
        run_counts = {
            _from_db_time(row["bucket_start_at"]).replace(minute=0, second=0, microsecond=0): int(
                row["run_count"]
            )
            for row in self._execute(
                """
                SELECT substr(started_at, 1, 13) || ':00:00+09:00' AS bucket_start_at,
                       COUNT(*) AS run_count
                FROM collection_runs
                WHERE started_at >= ?
                GROUP BY bucket_start_at
                """,
                (_to_db_time(first_hour),),
            ).fetchall()
        }
        result_counts = {
            _from_db_time(row["bucket_start_at"]).replace(minute=0, second=0, microsecond=0): int(
                row["result_count"]
            )
            for row in self._execute(
                """
                SELECT substr(created_at, 1, 13) || ':00:00+09:00' AS bucket_start_at,
                       COUNT(*) AS result_count
                FROM target_collection_results
                WHERE created_at >= ?
                GROUP BY bucket_start_at
                """,
                (_to_db_time(first_hour),),
            ).fetchall()
        }
        buckets = []
        for offset in range(7 * 24):
            bucket_start = first_hour + timedelta(hours=offset)
            run_count = run_counts.get(bucket_start, 0)
            result_count = result_counts.get(bucket_start, 0)
            buckets.append(
                CollectionActivityBucket(
                    bucket_start_at=bucket_start,
                    run_count=run_count,
                    result_count=result_count,
                    status=self._activity_status(run_count, result_count),
                )
            )
        return buckets

    def _storage_breakdown_today(self, total_bytes: int) -> list[StorageBreakdownItem]:
        day_start = now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = [
            (
                "source_candle",
                "캔들",
                self._table_count_since("source_candles", "collected_at", day_start),
                256,
            ),
            (
                "ticker_snapshot",
                "현재가",
                self._table_count_since("ticker_snapshots", "collected_at", day_start),
                160,
            ),
            (
                "orderbook_summary",
                "호가",
                self._table_count_since("orderbook_summaries", "collected_at", day_start),
                224,
            ),
        ]
        return [
            StorageBreakdownItem(
                data_type=cast(
                    Literal["source_candle", "ticker_snapshot", "orderbook_summary"],
                    data_type,
                ),
                label=label,
                row_count=row_count,
                bytes=row_count * row_size,
                bytes_display=_format_storage_bytes(row_count * row_size),
                share_percent=self._storage_share_percent(row_count * row_size, total_bytes),
            )
            for data_type, label, row_count, row_size in rows
        ]

    def _operations_trend(
        self,
        coverage: list[CoverageStatus],
        storage_bytes_today: int,
        warning_targets: int,
        incident_targets: int,
    ) -> list[OperationsTrendPoint]:
        today = now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
        coverage_percent = self._average_coverage_percent(coverage)
        points = []
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            next_day = day + timedelta(days=1)
            storage_bytes = (
                storage_bytes_today if offset == 0 else self._storage_bytes_for_range(day, next_day)
            )
            points.append(
                OperationsTrendPoint(
                    bucket_date=day,
                    coverage_percent=coverage_percent if offset == 0 else Decimal("0"),
                    storage_bytes=storage_bytes,
                    warning_targets=warning_targets if offset == 0 else 0,
                    incident_targets=incident_targets if offset == 0 else 0,
                )
            )
        return points

    def _missing_range_top(
        self, targets: list[CollectionDashboardTarget]
    ) -> list[MissingRangeSummary]:
        summaries = []
        for target in targets:
            candle_status = next(
                status for status in target.data_statuses if status.data_type == "source_candle"
            )
            summaries.append(
                MissingRangeSummary(
                    instrument=target.instrument,
                    missing_segment_count=candle_status.missing_segment_count,
                    coverage_percent=candle_status.progress_percent,
                    last_successful_at=candle_status.last_successful_at,
                )
            )
        return sorted(
            summaries,
            key=lambda item: (item.missing_segment_count, Decimal("100") - item.coverage_percent),
            reverse=True,
        )[:5]

    def _audit_log_summary(self) -> AuditLogSummary:
        since = now_kst() - timedelta(hours=24)
        target_count_row = self._execute(
            """
            SELECT COUNT(*) AS count
            FROM collection_target_changes
            WHERE changed_at >= ?
            """,
            (_to_db_time(since),),
        ).fetchone()
        backfill_count_row = self._execute(
            """
            SELECT COUNT(*) AS count
            FROM backfill_jobs
            WHERE created_at >= ?
            """,
            (_to_db_time(since),),
        ).fetchone()
        latest_target = self._execute(
            """
            SELECT changed_at AS changed_at, '대상 변경' AS label
            FROM collection_target_changes
            ORDER BY changed_at DESC
            LIMIT 1
            """
        ).fetchone()
        latest_backfill = self._execute(
            """
            SELECT created_at AS changed_at, '백필 시작' AS label
            FROM backfill_jobs
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        latest_rows = [row for row in [latest_target, latest_backfill] if row is not None]
        latest_row = max(
            latest_rows,
            key=lambda row: _from_db_time(row["changed_at"]),
            default=None,
        )
        return AuditLogSummary(
            target_change_count_24h=int(target_count_row["count"]) if target_count_row else 0,
            backfill_change_count_24h=int(backfill_count_row["count"]) if backfill_count_row else 0,
            latest_change_at=_from_db_time(latest_row["changed_at"]) if latest_row else None,
            latest_change_label=str(latest_row["label"]) if latest_row else "기록 없음",
        )

    def _realtime_worker_status(self) -> RealtimeWorkerStatus:
        runtime = self.collection_worker_runtime_status("realtime_collection")
        status = runtime.status
        status_label = runtime.status_label
        status_detail = runtime.status_detail
        last_heartbeat_at = runtime.last_heartbeat_at
        error_count_24h = self._realtime_error_count_24h()
        run_count_24h = self._realtime_run_count_24h()
        collected_row_count_24h = self._realtime_collected_row_count_24h()
        failure_rate_24h = (
            Decimal(error_count_24h) / Decimal(run_count_24h) * Decimal("100")
            if run_count_24h > 0
            else Decimal("0")
        )
        last_collected_at = self._latest_collection_finished_at("incremental")
        return RealtimeWorkerStatus(
            status=status,
            status_label=status_label,
            status_detail=status_detail,
            last_heartbeat_at=last_heartbeat_at,
            last_collected_at=last_collected_at,
            collected_row_count_24h=collected_row_count_24h,
            error_count_24h=error_count_24h,
            failure_rate_24h=failure_rate_24h,
            diagnostics=self._realtime_worker_diagnostics(
                status_detail,
                last_heartbeat_at,
                last_collected_at,
                collected_row_count_24h,
                error_count_24h,
                failure_rate_24h,
            ),
            recent_errors=self._recent_realtime_errors(),
        )

    def _backfill_worker_status(self) -> BackfillWorkerStatus:
        runtime = self.collection_worker_runtime_status("backfill_collection")
        status = runtime.status
        status_label = runtime.status_label
        status_detail = runtime.status_detail
        last_heartbeat_at = runtime.last_heartbeat_at
        total_error_count = self._backfill_error_count_all()
        total_target_count_all = self._backfill_target_count_all()
        failure_rate_all = (
            Decimal(total_error_count) / Decimal(total_target_count_all) * Decimal("100")
            if total_target_count_all > 0
            else Decimal("0")
        )
        (
            running_target_count,
            total_target_count,
            queued_job_count,
            queued_target_count,
        ) = self._active_backfill_target_summary()
        last_collected_at = self._latest_collection_finished_at("backfill")
        return BackfillWorkerStatus(
            status=status,
            status_label=status_label,
            status_detail=status_detail,
            last_heartbeat_at=last_heartbeat_at,
            last_collected_at=last_collected_at,
            total_error_count=total_error_count,
            failure_rate_all=failure_rate_all,
            running_target_count=running_target_count,
            total_target_count=total_target_count,
            queued_job_count=queued_job_count,
            queued_target_count=queued_target_count,
            diagnostics=self._backfill_worker_diagnostics(
                status_detail,
                last_heartbeat_at,
                last_collected_at,
                total_error_count,
                failure_rate_all,
                running_target_count,
                total_target_count,
                queued_job_count,
                queued_target_count,
            ),
            recent_errors=self._recent_backfill_errors(),
        )

    def _realtime_worker_diagnostics(
        self,
        status_detail: str,
        last_heartbeat_at: datetime | None,
        last_collected_at: datetime | None,
        collected_row_count: int,
        error_count: int,
        failure_rate: Decimal,
    ) -> list[CollectionWorkerDiagnostic]:
        return [
            CollectionWorkerDiagnostic(
                "마지막 heartbeat",
                _diagnostic_datetime(last_heartbeat_at),
                status_detail,
            ),
            CollectionWorkerDiagnostic(
                "마지막 저장 성공",
                _diagnostic_datetime(last_collected_at),
                "최근 성공 또는 부분 성공한 실시간 저장 시각",
            ),
            CollectionWorkerDiagnostic(
                "24시간 수집 row",
                f"{collected_row_count:,} rows",
                "최근 24시간 실시간 수집이 저장한 ticker/orderbook/candle row 합계",
            ),
            CollectionWorkerDiagnostic(
                "24시간 오류",
                f"{error_count:,}건",
                f"24시간 실패율 {failure_rate:.2f}%",
            ),
        ]

    def _backfill_worker_diagnostics(
        self,
        status_detail: str,
        last_heartbeat_at: datetime | None,
        last_collected_at: datetime | None,
        error_count: int,
        failure_rate: Decimal,
        running_target_count: int,
        total_target_count: int,
        queued_job_count: int,
        queued_target_count: int,
    ) -> list[CollectionWorkerDiagnostic]:
        return [
            CollectionWorkerDiagnostic(
                "마지막 heartbeat",
                _diagnostic_datetime(last_heartbeat_at),
                status_detail,
            ),
            CollectionWorkerDiagnostic(
                "마지막 저장 성공",
                _diagnostic_datetime(last_collected_at),
                "최근 성공 또는 부분 성공한 백필 저장 시각",
            ),
            CollectionWorkerDiagnostic(
                "전체 오류",
                f"{error_count:,}건",
                f"전체 실패율 {failure_rate:.2f}%",
            ),
            CollectionWorkerDiagnostic(
                "동작중 코인",
                f"{running_target_count:,}/{total_target_count:,}개",
                "현재 실행 중인 백필 계획의 running 대상 수",
            ),
            CollectionWorkerDiagnostic(
                "대기 백필",
                f"{queued_job_count:,}건 / {queued_target_count:,}개",
                "현재 계획 이후 대기 중인 백필 job/target",
            ),
        ]

    def _worker_runtime_status(
        self,
        worker_type: CollectionWorkerType,
        stale_after: timedelta,
    ) -> tuple[CollectionWorkerStatus, str, str, datetime | None]:
        row = self._execute(
            """
            SELECT * FROM collection_worker_heartbeats
            WHERE worker_type = ?
            """,
            (worker_type,),
        ).fetchone()
        if row is None:
            return "stale", "중지 추정", "worker heartbeat 기록이 없습니다.", None
        last_heartbeat_at = _from_db_time(row["last_heartbeat_at"])
        if row["status"] == "failed":
            return (
                "failed",
                "오류",
                str(row["last_error_message"] or "마지막 heartbeat가 실패 상태입니다."),
                last_heartbeat_at,
            )
        if row["status"] == "gated":
            return (
                "gated",
                "승인 대기",
                str(row["last_error_message"] or "백필 안전 게이트가 닫혀 있습니다."),
                last_heartbeat_at,
            )
        if now_kst() - last_heartbeat_at > stale_after:
            return (
                "stale",
                "지연",
                "마지막 heartbeat가 허용 지연 시간을 넘었습니다.",
                last_heartbeat_at,
            )
        return "running", "동작 중", "최근 heartbeat 정상", last_heartbeat_at

    def collection_worker_runtime_status(
        self, worker_type: CollectionWorkerType
    ) -> CollectionWorkerRuntimeStatus:
        stale_after = {
            "realtime_collection": timedelta(minutes=2),
            "backfill_collection": timedelta(seconds=30),
            "candle_aggregation": timedelta(seconds=30),
        }[worker_type]
        return CollectionWorkerRuntimeStatus(
            *self._worker_runtime_status(worker_type, stale_after=stale_after)
        )

    def _latest_collection_finished_at(self, run_type: str) -> datetime | None:
        row = self._execute(
            """
            SELECT finished_at, started_at FROM collection_runs
            WHERE run_type = ? AND status IN ('succeeded', 'partial')
            ORDER BY COALESCE(finished_at, started_at) DESC
            LIMIT 1
            """,
            (run_type,),
        ).fetchone()
        if row is None:
            return None
        return _from_db_time(row["finished_at"] or row["started_at"])

    def _realtime_error_count_24h(self) -> int:
        cutoff = _to_db_time(now_kst() - timedelta(hours=24))
        row = self._execute(
            """
            SELECT COUNT(*) AS count
            FROM collection_runs
            WHERE run_type = 'incremental' AND status = 'failed' AND started_at >= ?
            """,
            (cutoff,),
        ).fetchone()
        return int(row["count"]) if row else 0

    def _realtime_run_count_24h(self) -> int:
        cutoff = _to_db_time(now_kst() - timedelta(hours=24))
        row = self._execute(
            """
            SELECT COUNT(*) AS count
            FROM collection_runs
            WHERE run_type = 'incremental' AND started_at >= ?
            """,
            (cutoff,),
        ).fetchone()
        return int(row["count"]) if row else 0

    def _realtime_collected_row_count_24h(self) -> int:
        cutoff = _to_db_time(now_kst() - timedelta(hours=24))
        row = self._execute(
            """
            SELECT COALESCE(SUM(tcr.rows_written), 0) AS count
            FROM target_collection_results tcr
            JOIN collection_runs cr ON cr.id = tcr.collection_run_id
            WHERE cr.run_type = 'incremental' AND tcr.created_at >= ?
            """,
            (cutoff,),
        ).fetchone()
        return int(row["count"]) if row else 0

    def _recent_realtime_errors(self) -> list[CollectionWorkerError]:
        cutoff = _to_db_time(now_kst() - timedelta(hours=24))
        rows = self._execute(
            """
            SELECT started_at, error_code, error_message
            FROM collection_runs
            WHERE run_type = 'incremental' AND status = 'failed' AND started_at >= ?
            ORDER BY started_at DESC
            LIMIT 10
            """,
            (cutoff,),
        ).fetchall()
        return [
            CollectionWorkerError(
                occurred_at=_from_db_time(row["started_at"]),
                code=str(row["error_code"] or "CollectionRunFailed"),
                message=str(row["error_message"] or "실시간 수집 실행이 실패했습니다."),
            )
            for row in rows
        ]

    def _backfill_error_count_all(self) -> int:
        row = self._execute(
            """
            SELECT COUNT(*) AS count
            FROM backfill_job_targets
            WHERE status = 'failed'
            """
        ).fetchone()
        return int(row["count"]) if row else 0

    def _backfill_target_count_all(self) -> int:
        row = self._execute(
            """
            SELECT COUNT(*) AS count
            FROM backfill_job_targets
            """
        ).fetchone()
        return int(row["count"]) if row else 0

    def _active_backfill_target_summary(self) -> tuple[int, int, int, int]:
        active_job = self._execute(
            """
            SELECT id
            FROM backfill_jobs
            WHERE status IN ('running', 'pending')
            ORDER BY CASE WHEN status = 'running' THEN 0 ELSE 1 END, created_at
            LIMIT 1
            """
        ).fetchone()
        if active_job is None:
            return 0, 0, 0, 0
        active_job_id = int(active_job["id"])
        active_counts = self._execute(
            """
            SELECT
              SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_count,
              COUNT(*) AS total_count
            FROM backfill_job_targets
            WHERE backfill_job_id = ?
            """,
            (active_job_id,),
        ).fetchone()
        queued_counts = self._execute(
            """
            SELECT
              COUNT(DISTINCT bj.id) AS queued_job_count,
              COUNT(bjt.instrument_id) AS queued_target_count
            FROM backfill_jobs bj
            LEFT JOIN backfill_job_targets bjt ON bjt.backfill_job_id = bj.id
            WHERE bj.status = 'pending' AND bj.id <> ?
            """,
            (active_job_id,),
        ).fetchone()
        return (
            int(active_counts["running_count"] or 0) if active_counts else 0,
            int(active_counts["total_count"] or 0) if active_counts else 0,
            int(queued_counts["queued_job_count"] or 0) if queued_counts else 0,
            int(queued_counts["queued_target_count"] or 0) if queued_counts else 0,
        )

    def _active_backfill_target_counts(self) -> tuple[int, int]:
        running_count, total_count, _, _ = self._active_backfill_target_summary()
        return running_count, total_count

    def _recent_backfill_errors(self) -> list[CollectionWorkerError]:
        rows = self._execute(
            """
            SELECT bjt.updated_at, bjt.error_code, bjt.error_message, i.market_code
            FROM backfill_job_targets bjt
            JOIN instruments i ON i.id = bjt.instrument_id
            WHERE bjt.status = 'failed'
            ORDER BY bjt.updated_at DESC
            LIMIT 10
            """
        ).fetchall()
        return [
            CollectionWorkerError(
                occurred_at=_from_db_time(row["updated_at"]),
                code=str(row["error_code"] or "BackfillTargetFailed"),
                message=f"{row['market_code']}: {row['error_message'] or '백필 대상 수집 실패'}",
            )
            for row in rows
        ]

    def _storage_bytes_for_range(self, start_at: datetime, end_at: datetime) -> int:
        return (
            self._table_count_between("source_candles", "collected_at", start_at, end_at) * 256
            + self._table_count_between("ticker_snapshots", "collected_at", start_at, end_at) * 160
            + self._table_count_between("orderbook_summaries", "collected_at", start_at, end_at)
            * 224
        )

    def _table_count_between(
        self, table: str, time_column: str, start_at: datetime, end_at: datetime
    ) -> int:
        row = self._execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE {time_column} >= ? AND {time_column} < ?",
            (_to_db_time(start_at), _to_db_time(end_at)),
        ).fetchone()
        return int(row["count"]) if row else 0

    @staticmethod
    def _activity_status(
        run_count: int,
        result_count: int,
    ) -> Literal["none", "low", "collecting", "high"]:
        if run_count == 0 and result_count == 0:
            return "none"
        if result_count >= 50:
            return "high"
        if run_count > 0:
            return "collecting"
        return "low"

    @staticmethod
    def _realtime_collection_heatmap_status(
        average_trades_per_minute: Decimal,
    ) -> TradeFrequencyStatus:
        if average_trades_per_minute < Decimal("10"):
            return "red"
        if average_trades_per_minute < Decimal("50"):
            return "orange"
        if average_trades_per_minute < Decimal("100"):
            return "yellow"
        if average_trades_per_minute < Decimal("200"):
            return "blue"
        return "green"

    @staticmethod
    def _average_coverage_percent(coverage: list[CoverageStatus]) -> Decimal:
        source = [item.progress_percent for item in coverage if item.data_type == "source_candle"]
        if not source:
            return Decimal("0")
        return (sum(source) / Decimal(len(source))).quantize(Decimal("0.01"))

    @staticmethod
    def _storage_share_percent(bytes_value: int, total_bytes: int) -> Decimal:
        if total_bytes <= 0:
            return Decimal("0")
        return (Decimal(bytes_value) / Decimal(total_bytes) * Decimal("100")).quantize(
            Decimal("0.01")
        )

    def _storage_bytes_estimate(self) -> int:
        return sum(
            self._table_count(table) * row_size
            for table, row_size in (
                ("source_candles", 256),
                ("ticker_snapshots", 160),
                ("orderbook_summaries", 224),
            )
        )

    def _instrument_storage_bytes(self, instrument_id: int) -> int:
        counts = [
            self._table_count("source_candles", instrument_id) * 256,
            self._table_count("ticker_snapshots", instrument_id) * 160,
            self._table_count("orderbook_summaries", instrument_id) * 224,
        ]
        return sum(counts)

    def _instrument_storage_row_count(self, instrument_id: int) -> int:
        return (
            self._table_count("source_candles", instrument_id)
            + self._table_count("ticker_snapshots", instrument_id)
            + self._table_count("orderbook_summaries", instrument_id)
        )

    def _instrument_storage_bytes_by_instrument(self, instrument_ids: list[int]) -> dict[int, int]:
        source_counts = self._table_counts_by_instrument("source_candles", instrument_ids)
        ticker_counts = self._table_counts_by_instrument("ticker_snapshots", instrument_ids)
        orderbook_counts = self._table_counts_by_instrument("orderbook_summaries", instrument_ids)
        return {
            instrument_id: source_counts.get(instrument_id, 0) * 256
            + ticker_counts.get(instrument_id, 0) * 160
            + orderbook_counts.get(instrument_id, 0) * 224
            for instrument_id in instrument_ids
        }

    def _instrument_storage_row_counts_by_instrument(
        self, instrument_ids: list[int]
    ) -> dict[int, int]:
        source_counts = self._table_counts_by_instrument("source_candles", instrument_ids)
        ticker_counts = self._table_counts_by_instrument("ticker_snapshots", instrument_ids)
        orderbook_counts = self._table_counts_by_instrument("orderbook_summaries", instrument_ids)
        return {
            instrument_id: source_counts.get(instrument_id, 0)
            + ticker_counts.get(instrument_id, 0)
            + orderbook_counts.get(instrument_id, 0)
            for instrument_id in instrument_ids
        }

    def _table_count(self, table: str, instrument_id: int | None = None) -> int:
        if instrument_id is None:
            row = self._execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        else:
            row = self._execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE instrument_id = ?",
                (instrument_id,),
            ).fetchone()
        return int(row["count"]) if row else 0

    def _table_count_since(self, table: str, time_column: str, since: datetime) -> int:
        row = self._execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE {time_column} >= ?",
            (_to_db_time(since),),
        ).fetchone()
        return int(row["count"]) if row else 0

    def _table_counts_by_instrument(self, table: str, instrument_ids: list[int]) -> dict[int, int]:
        if not instrument_ids:
            return {}
        placeholders = ", ".join(["?"] * len(instrument_ids))
        rows = self._execute(
            f"""
            SELECT instrument_id, COUNT(*) AS count
            FROM {table}
            WHERE instrument_id IN ({placeholders})
            GROUP BY instrument_id
            """,
            tuple(instrument_ids),
        ).fetchall()
        return {int(row["instrument_id"]): int(row["count"]) for row in rows}

    def _market_coverage_percent(self, instrument_id: int) -> Decimal:
        coverage = self.coverage_for(instrument_id)
        return self._market_coverage_percent_from_statuses(coverage)

    def _market_coverage_percent_from_statuses(self, coverage: list[CoverageStatus]) -> Decimal:
        if not coverage:
            return Decimal("0")
        return sum((item.progress_percent for item in coverage), Decimal("0")) / Decimal(
            len(coverage)
        )

    def _quality_status_from_coverage(
        self, coverage: list[CoverageStatus]
    ) -> Literal["normal", "warning", "incident"]:
        if any(item.status == "incident" for item in coverage):
            return "incident"
        if any(item.status != "normal" for item in coverage):
            return "warning"
        return "normal"

    def _health_checks(
        self, coverage: list[CoverageStatus], alerts: list[NotificationEvent]
    ) -> list[HealthCheck]:
        ticker_warnings = sum(
            1
            for item in coverage
            if item.data_type == "ticker_snapshot" and item.status != "normal"
        )
        candle_warnings = sum(
            1 for item in coverage if item.data_type == "source_candle" and item.status != "normal"
        )
        orderbook_warnings = sum(
            1
            for item in coverage
            if item.data_type == "orderbook_summary" and item.status != "normal"
        )
        open_alerts = [alert for alert in alerts if alert.status == "open"]
        return [
            HealthCheck(
                title="현재가·거래대금",
                status="normal" if ticker_warnings == 0 else "warning",
                status_label="정상" if ticker_warnings == 0 else "주의",
                detail="최근 1-3분 정상" if ticker_warnings == 0 else f"지연 {ticker_warnings}구간",
            ),
            HealthCheck(
                title="캔들 상태",
                status="normal" if candle_warnings == 0 else "warning",
                status_label="정상" if candle_warnings == 0 else "주의",
                detail="직전 완성 1분봉 저장"
                if candle_warnings == 0
                else f"결측 {candle_warnings}구간",
            ),
            HealthCheck(
                title="호가 상태",
                status="normal" if orderbook_warnings == 0 else "warning",
                status_label="정상" if orderbook_warnings == 0 else "주의",
                detail="매수 잔량 우세"
                if orderbook_warnings == 0
                else f"지연 {orderbook_warnings}구간",
            ),
            HealthCheck(
                title="완전성 검사",
                status="normal" if not open_alerts else "warning",
                status_label="정상" if not open_alerts else "주의",
                detail="결측 0구간" if not open_alerts else f"알림 {len(open_alerts)}건",
            ),
        ]

    def add_notification(
        self,
        severity: str,
        event_type: str,
        title: str,
        message: str,
    ) -> NotificationEvent:
        created_at = _to_db_time(now_kst())
        with self._lock, self._conn:
            cursor = self._execute(
                """
                INSERT INTO notification_events (
                  severity, event_type, title, message, status, created_at
                )
                VALUES (?, ?, ?, ?, 'open', ?)
                """,
                (severity, event_type, title, message, created_at),
            )
            row = self._execute(
                "SELECT * FROM notification_events WHERE id = ?",
                (_required_lastrowid(cursor),),
            ).fetchone()
        return self._notification_from_row(row)

    def _activate_target(
        self, instrument_id: int, actor: str, reason: str | None, target_order: int
    ) -> None:
        timestamp = _to_db_time(now_kst())
        previous = self._execute(
            "SELECT status FROM collection_targets WHERE instrument_id = ?",
            (instrument_id,),
        ).fetchone()
        self._execute(
            """
            INSERT INTO collection_targets (
              instrument_id,
              status,
              candidate_status,
              target_order,
              activated_at,
              deactivated_at,
              updated_at
            )
            VALUES (?, 'active', 'in_universe', ?, ?, NULL, ?)
            ON CONFLICT(instrument_id) DO UPDATE SET
              status = 'active',
              candidate_status = 'in_universe',
              target_order = excluded.target_order,
              activated_at = COALESCE(collection_targets.activated_at, excluded.activated_at),
              deactivated_at = NULL,
              updated_at = excluded.updated_at
            """,
            (instrument_id, target_order, timestamp, timestamp),
        )
        self._record_target_change(
            instrument_id, previous["status"] if previous else None, "active", actor, reason
        )
        self._ensure_collection_plan(instrument_id)

    def _deactivate_target(self, instrument_id: int, actor: str, reason: str | None) -> None:
        timestamp = _to_db_time(now_kst())
        previous = self._execute(
            "SELECT status FROM collection_targets WHERE instrument_id = ?",
            (instrument_id,),
        ).fetchone()
        self._execute(
            """
            UPDATE collection_targets
            SET status = 'inactive', target_order = NULL, deactivated_at = ?, updated_at = ?
            WHERE instrument_id = ?
            """,
            (timestamp, timestamp, instrument_id),
        )
        self._record_target_change(
            instrument_id, previous["status"] if previous else None, "inactive", actor, reason
        )

    def _record_target_change(
        self,
        instrument_id: int,
        previous_status: str | None,
        new_status: str,
        actor: str,
        reason: str | None,
    ) -> None:
        self._execute(
            """
            INSERT INTO collection_target_changes (
              instrument_id, previous_status, new_status, actor, reason, changed_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (instrument_id, previous_status, new_status, actor, reason, _to_db_time(now_kst())),
        )

    def _ensure_collection_plan(self, instrument_id: int) -> None:
        plan_start = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
        timestamp = _to_db_time(now_kst())
        self._execute(
            """
            INSERT INTO collection_plans (
              instrument_id, preset, range_start_at, range_end_at,
              is_continuous, method, status, updated_at
            )
            VALUES (?, '2026년 1월 1분봉', ?, NULL, 1, 'safe_restart', 'latest_collecting', ?)
            ON CONFLICT(instrument_id) DO NOTHING
            """,
            (instrument_id, _to_db_time(plan_start), timestamp),
        )

    def _collection_plan_for(self, instrument_id: int) -> CollectionPlan:
        row = self._execute(
            "SELECT * FROM collection_plans WHERE instrument_id = ?",
            (instrument_id,),
        ).fetchone()
        if row is None:
            self._ensure_collection_plan(instrument_id)
            row = self._execute(
                "SELECT * FROM collection_plans WHERE instrument_id = ?",
                (instrument_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"수집 계획을 생성하지 못했다: instrument_id={instrument_id}")
        return self._collection_plan_from_row(instrument_id, row)

    def _collection_plans_by_instrument(
        self, instrument_ids: list[int]
    ) -> dict[int, CollectionPlan]:
        if not instrument_ids:
            return {}
        placeholders = ",".join("?" for _ in instrument_ids)
        rows = self._execute(
            f"""
            SELECT *
            FROM collection_plans
            WHERE instrument_id IN ({placeholders})
            """,
            tuple(instrument_ids),
        ).fetchall()
        plans = {
            row["instrument_id"]: self._collection_plan_from_row(row["instrument_id"], row)
            for row in rows
        }
        missing_ids = [
            instrument_id for instrument_id in instrument_ids if instrument_id not in plans
        ]
        for instrument_id in missing_ids:
            self._ensure_collection_plan(instrument_id)
        if missing_ids:
            missing_placeholders = ",".join("?" for _ in missing_ids)
            rows = self._execute(
                f"""
                SELECT *
                FROM collection_plans
                WHERE instrument_id IN ({missing_placeholders})
                """,
                tuple(missing_ids),
            ).fetchall()
            plans.update(
                {
                    row["instrument_id"]: self._collection_plan_from_row(row["instrument_id"], row)
                    for row in rows
                }
            )
        return plans

    def _collection_plan_from_row(self, instrument_id: int, row: sqlite3.Row) -> CollectionPlan:
        return CollectionPlan(
            instrument_id=instrument_id,
            preset=str(row["preset"]),
            range_start_at=_from_db_time(row["range_start_at"]),
            range_end_at=_from_db_time(row["range_end_at"]) if row["range_end_at"] else None,
            is_continuous=bool(row["is_continuous"]),
            method=str(row["method"]),
            display_range="2026-01-01 00:00 KST ~ NOW"
            if bool(row["is_continuous"])
            else "2026-01-01 00:00 KST ~ 2026-02-01 00:00 KST",
            range_time_zone="KST",
            progress_basis="현재(지속)은 KST 전일 23:59:59까지 기준",
        )

    def _collection_data_status(
        self,
        item: CoverageStatus,
        source_candle_counts: dict[int, int] | None = None,
    ) -> CollectionDataStatus:
        labels = {
            "source_candle": "캔들",
            "ticker_snapshot": "현재가",
            "orderbook_summary": "호가 요약",
        }
        stored_row_count = 0
        if item.data_type == "source_candle":
            stored_row_count = (
                source_candle_counts.get(item.instrument_id)
                if source_candle_counts is not None
                else self._table_count("source_candles", item.instrument_id)
            ) or 0
        return CollectionDataStatus(
            data_type=item.data_type,
            label=labels[item.data_type],
            status=item.status,
            status_label="정상"
            if item.status == "normal"
            else "장애"
            if item.status == "incident"
            else "주의",
            last_successful_at=item.last_successful_at,
            progress_percent=item.progress_percent,
            missing_segment_count=item.missing_segment_count,
            stored_row_count=stored_row_count,
        )

    def _coverage_segments_for(
        self,
        instrument_id: int,
        data_type: Literal["source_candle", "ticker_snapshot", "orderbook_summary"],
    ) -> list[CoverageSegment]:
        plan = self._collection_plan_for(instrument_id)
        if data_type == "source_candle":
            return self._source_candle_coverage_segments(instrument_id, plan)
        segment_end = self._coverage_range_end(plan)
        return [
            CoverageSegment(
                data_type=data_type,
                status="collected",
                offset_percent=Decimal("0"),
                width_percent=Decimal("100"),
                segment_start_at=plan.range_start_at,
                segment_end_at=segment_end,
                label="수집 완료",
            )
        ]

    def _freshness_coverage_status(
        self,
        instrument_id: int,
        data_type: Literal["ticker_snapshot", "orderbook_summary"],
        latest_at: datetime | None,
    ) -> CoverageStatus:
        if latest_at is None:
            return CoverageStatus(
                instrument_id=instrument_id,
                data_type=data_type,
                status="incident",
                progress_percent=Decimal("0"),
                last_successful_at=now_kst() - timedelta(days=365),
            )
        age = now_kst() - latest_at
        return CoverageStatus(
            instrument_id=instrument_id,
            data_type=data_type,
            status="normal" if age <= timedelta(minutes=3) else "warning",
            progress_percent=Decimal("100"),
            last_successful_at=latest_at,
        )

    def _source_candle_coverage_status(self, instrument_id: int) -> CoverageStatus:
        plan = self._collection_plan_for(instrument_id)
        range_end = self._coverage_range_end(plan)
        expected_minutes = self._expected_minutes(plan.range_start_at, range_end)
        stored_starts = sorted(
            self._source_candle_starts(instrument_id, plan.range_start_at, range_end)
        )
        stored_count = len(stored_starts)
        progress = (Decimal(stored_count) * Decimal("100") / Decimal(expected_minutes)).quantize(
            Decimal("0.01")
        )
        missing_segments = self._missing_segment_count_from_starts(
            stored_starts, plan.range_start_at, range_end
        )
        latest_at = stored_starts[-1] if stored_starts else None
        if latest_at is None:
            status: Literal["normal", "warning", "incident"] = "incident"
            last_successful_at = now_kst() - timedelta(days=365)
        elif missing_segments == 0 and progress == Decimal("100.00"):
            status = "normal"
            last_successful_at = latest_at
        else:
            status = "warning"
            last_successful_at = latest_at
        return CoverageStatus(
            instrument_id=instrument_id,
            data_type="source_candle",
            status=status,
            progress_percent=progress.normalize(),
            last_successful_at=last_successful_at,
            missing_segment_count=missing_segments,
        )

    def _source_candle_coverage_status_from_summary(
        self,
        instrument_id: int,
        plan: CollectionPlan,
        stored_count: int,
        source_candle_range: tuple[datetime | None, datetime | None],
    ) -> CoverageStatus:
        range_end = self._coverage_range_end(plan)
        expected_minutes = self._expected_minutes(plan.range_start_at, range_end)
        first_start_at, latest_start_at = source_candle_range
        progress = (Decimal(stored_count) * Decimal("100") / Decimal(expected_minutes)).quantize(
            Decimal("0.01")
        )
        if latest_start_at is None:
            return CoverageStatus(
                instrument_id=instrument_id,
                data_type="source_candle",
                status="incident",
                progress_percent=Decimal("0"),
                last_successful_at=now_kst() - timedelta(days=365),
                missing_segment_count=1,
            )
        missing_segments = 0
        if first_start_at is not None and first_start_at > plan.range_start_at:
            missing_segments += 1
        if latest_start_at + timedelta(minutes=1) < range_end:
            missing_segments += 1
        if stored_count < expected_minutes and missing_segments == 0:
            missing_segments = 1
        status: Literal["normal", "warning", "incident"] = (
            "normal" if missing_segments == 0 and progress == Decimal("100.00") else "warning"
        )
        return CoverageStatus(
            instrument_id=instrument_id,
            data_type="source_candle",
            status=status,
            progress_percent=progress.normalize(),
            last_successful_at=latest_start_at,
            missing_segment_count=missing_segments,
        )

    def _missing_segment_count_from_starts(
        self,
        stored_starts: list[datetime],
        start_at: datetime,
        end_at: datetime,
    ) -> int:
        if not stored_starts:
            return 1
        missing_segments = 0
        if stored_starts[0] > start_at:
            missing_segments += 1
        previous = stored_starts[0]
        for bucket in stored_starts[1:]:
            if bucket > previous + timedelta(minutes=1):
                missing_segments += 1
            previous = bucket
        if stored_starts[-1] + timedelta(minutes=1) < end_at:
            missing_segments += 1
        return missing_segments

    def _source_candle_coverage_segments(
        self,
        instrument_id: int,
        plan: CollectionPlan,
    ) -> list[CoverageSegment]:
        range_start = plan.range_start_at
        range_end = self._coverage_range_end(plan)
        expected_minutes = self._expected_minutes(range_start, range_end)
        stored_starts = sorted(self._source_candle_starts(instrument_id, range_start, range_end))
        segments: list[CoverageSegment] = []
        cursor = range_start
        collected_start: datetime | None = None
        collected_end: datetime | None = None
        for bucket in stored_starts:
            bucket_end = min(bucket + timedelta(minutes=1), range_end)
            if collected_start is None:
                if cursor < bucket:
                    segments.append(
                        self._coverage_segment(
                            "source_candle",
                            "missing",
                            cursor,
                            bucket,
                            range_start,
                            expected_minutes,
                        )
                    )
                collected_start = bucket
                collected_end = bucket_end
                cursor = bucket_end
                continue
            if collected_end is not None and bucket == collected_end:
                collected_end = bucket_end
                cursor = bucket_end
                continue
            segments.append(
                self._coverage_segment(
                    "source_candle",
                    "collected",
                    collected_start,
                    collected_end or bucket,
                    range_start,
                    expected_minutes,
                )
            )
            if collected_end is not None and collected_end < bucket:
                segments.append(
                    self._coverage_segment(
                        "source_candle",
                        "missing",
                        collected_end,
                        bucket,
                        range_start,
                        expected_minutes,
                    )
                )
            collected_start = bucket
            collected_end = bucket_end
            cursor = bucket_end
        if collected_start is not None:
            segments.append(
                self._coverage_segment(
                    "source_candle",
                    "collected",
                    collected_start,
                    collected_end or range_end,
                    range_start,
                    expected_minutes,
                )
            )
        if cursor < range_end:
            segments.append(
                self._coverage_segment(
                    "source_candle",
                    "missing",
                    cursor,
                    range_end,
                    range_start,
                    expected_minutes,
                )
            )
        return segments

    def _coverage_segment(
        self,
        data_type: Literal["source_candle", "ticker_snapshot", "orderbook_summary"],
        status: Literal["collected", "missing"],
        segment_start_at: datetime,
        segment_end_at: datetime,
        range_start_at: datetime,
        expected_minutes: int,
    ) -> CoverageSegment:
        offset_minutes = int((segment_start_at - range_start_at).total_seconds() // 60)
        width_minutes = max(1, int((segment_end_at - segment_start_at).total_seconds() // 60))
        return CoverageSegment(
            data_type=data_type,
            status=status,
            offset_percent=(Decimal(offset_minutes) * Decimal("100") / Decimal(expected_minutes))
            .quantize(Decimal("0.01"))
            .normalize(),
            width_percent=(Decimal(width_minutes) * Decimal("100") / Decimal(expected_minutes))
            .quantize(Decimal("0.01"))
            .normalize(),
            segment_start_at=segment_start_at,
            segment_end_at=segment_end_at,
            label="수집 완료" if status == "collected" else "결측",
        )

    def _source_candle_starts(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> set[datetime]:
        rows = self._execute(
            """
            SELECT candle_start_at
            FROM source_candles
            WHERE instrument_id = ?
              AND candle_unit = '1m'
              AND candle_start_at >= ?
              AND candle_start_at < ?
            ORDER BY candle_start_at
            """,
            (instrument_id, _to_db_time(start_at), _to_db_time(end_at)),
        ).fetchall()
        return {_from_db_time(row["candle_start_at"]) for row in rows}

    def _coverage_range_end(self, plan: CollectionPlan) -> datetime:
        if plan.range_end_at is not None:
            return plan.range_end_at
        return minute_bucket(now_kst())

    def _expected_minutes(self, start_at: datetime, end_at: datetime) -> int:
        return max(1, int((end_at - start_at).total_seconds() // 60))

    def _upsert_tickers(self, tickers: list[TickerSnapshot]) -> dict[int, int]:
        counts: dict[int, int] = {}
        for item in tickers:
            self._execute(
                """
                INSERT INTO ticker_snapshots (
                  instrument_id, bucket_at, trade_price, acc_trade_price_24h,
                  change_rate, collected_at, occurred_at, received_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, bucket_at) DO UPDATE SET
                  trade_price = excluded.trade_price,
                  acc_trade_price_24h = excluded.acc_trade_price_24h,
                  change_rate = excluded.change_rate,
                  collected_at = excluded.collected_at,
                  occurred_at = excluded.occurred_at,
                  received_at = excluded.received_at
                WHERE excluded.collected_at > ticker_snapshots.collected_at
                """,
                (
                    item.instrument_id,
                    _to_db_time(minute_bucket(item.bucket_at)),
                    str(item.trade_price),
                    str(item.acc_trade_price_24h),
                    str(item.change_rate),
                    _to_db_time(item.collected_at),
                    item.occurred_at.astimezone(KST).isoformat(),
                    item.received_at.astimezone(KST).isoformat(),
                ),
            )
            counts[item.instrument_id] = counts.get(item.instrument_id, 0) + 1
        return counts

    def _upsert_orderbooks(self, orderbooks: list[OrderbookSummary]) -> dict[int, int]:
        counts: dict[int, int] = {}
        for item in orderbooks:
            self._execute(
                """
                INSERT INTO orderbook_summaries (
                  instrument_id, bucket_at, best_bid_price, best_bid_size,
                  best_ask_price, best_ask_size, spread, bid_depth_10,
                  ask_depth_10, imbalance_10, collected_at, occurred_at, received_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, bucket_at) DO UPDATE SET
                  best_bid_price = excluded.best_bid_price,
                  best_bid_size = excluded.best_bid_size,
                  best_ask_price = excluded.best_ask_price,
                  best_ask_size = excluded.best_ask_size,
                  spread = excluded.spread,
                  bid_depth_10 = excluded.bid_depth_10,
                  ask_depth_10 = excluded.ask_depth_10,
                  imbalance_10 = excluded.imbalance_10,
                  collected_at = excluded.collected_at,
                  occurred_at = excluded.occurred_at,
                  received_at = excluded.received_at
                WHERE excluded.collected_at > orderbook_summaries.collected_at
                """,
                (
                    item.instrument_id,
                    _to_db_time(minute_bucket(item.bucket_at)),
                    str(item.best_bid_price),
                    str(item.best_bid_size),
                    str(item.best_ask_price),
                    str(item.best_ask_size),
                    str(item.spread),
                    str(item.bid_depth_10),
                    str(item.ask_depth_10),
                    str(item.imbalance_10),
                    _to_db_time(item.collected_at),
                    item.occurred_at.astimezone(KST).isoformat(),
                    item.received_at.astimezone(KST).isoformat(),
                ),
            )
            counts[item.instrument_id] = counts.get(item.instrument_id, 0) + 1
        return counts

    def _upsert_candles(self, candles: list[SourceCandle]) -> dict[int, int]:
        counts: dict[int, int] = {}
        created_revisions: list[SourceCandleRevisionCreated] = []
        for item in candles:
            self._execute(
                """
                INSERT INTO source_candles (
                  instrument_id, candle_unit, candle_start_at, open_price,
                  high_price, low_price, close_price, trade_volume,
                  trade_amount, collected_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, candle_unit, candle_start_at) DO UPDATE SET
                  open_price = excluded.open_price,
                  high_price = excluded.high_price,
                  low_price = excluded.low_price,
                  close_price = excluded.close_price,
                  trade_volume = excluded.trade_volume,
                  trade_amount = excluded.trade_amount,
                  collected_at = excluded.collected_at
                WHERE excluded.collected_at >= source_candles.collected_at
                """,
                (
                    item.instrument_id,
                    item.candle_unit,
                    _to_db_time(item.candle_start_at),
                    str(item.open_price),
                    str(item.high_price),
                    str(item.low_price),
                    str(item.close_price),
                    str(item.trade_volume),
                    str(item.trade_amount),
                    _to_db_time(item.collected_at),
                ),
            )
            counts[item.instrument_id] = counts.get(item.instrument_id, 0) + 1
            created_revisions.extend(self._append_source_candle_revisions([item]))
        self._source_candle_revisions_created(created_revisions)
        return counts

    def _append_source_candle_revisions(
        self, candles: list[SourceCandle]
    ) -> list[SourceCandleRevisionCreated]:
        """현재 투영과 내용이 달라 새로 추가된 개정만 반환한다."""
        created: list[SourceCandleRevisionCreated] = []
        for item in candles:
            row = self._execute(
                """
                SELECT * FROM source_candles
                WHERE instrument_id = ? AND candle_unit = ? AND candle_start_at = ?
                """,
                (item.instrument_id, item.candle_unit, _to_db_time(item.candle_start_at)),
            ).fetchone()
            if row is None:
                continue
            content_hash = _source_candle_content_hash(item)
            latest = self._execute(
                """
                SELECT revision_number, input_content_hash
                FROM source_candle_revisions
                WHERE instrument_id = ? AND candle_unit = ? AND candle_start_at = ?
                ORDER BY revision_number DESC LIMIT 1
                """,
                (item.instrument_id, item.candle_unit, row["candle_start_at"]),
            ).fetchone()
            if latest is not None and latest["input_content_hash"] == content_hash:
                continue
            revision = 1 if latest is None else int(latest["revision_number"]) + 1
            cursor = self._execute(
                """
                INSERT OR IGNORE INTO source_candle_revisions (
                  instrument_id, candle_unit, candle_start_at, revision_number,
                  open_price, high_price, low_price, close_price, trade_volume, trade_amount,
                  source_as_of, knowledge_at, input_content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.instrument_id,
                    item.candle_unit,
                    row["candle_start_at"],
                    revision,
                    str(item.open_price),
                    str(item.high_price),
                    str(item.low_price),
                    str(item.close_price),
                    str(item.trade_volume),
                    str(item.trade_amount),
                    _to_db_time(item.collected_at),
                    _to_db_time(item.knowledge_at or item.collected_at),
                    content_hash,
                ),
            )
            if cursor.rowcount == 1:
                created.append(
                    SourceCandleRevisionCreated(
                        id=_required_lastrowid(cursor),
                        revision_number=int(revision),
                        market_id=item.instrument_id,
                        candle_start_at=_from_db_time(row["candle_start_at"]),
                        knowledge_at=item.knowledge_at or item.collected_at,
                        input_content_hash=content_hash,
                    )
                )
        return created

    def _source_candle_revisions_created(self, created: list[SourceCandleRevisionCreated]) -> None:
        """P2-2가 같은 트랜잭션에서 신규 개정을 소비하는 내부 훅이다."""

        if not created:
            return
        by_instrument: dict[tuple[int, str], list[SourceCandleRevisionCreated]] = {}
        for item in created:
            row = self._execute(
                "SELECT candle_unit FROM source_candle_revisions WHERE id = ?", (item.id,)
            ).fetchone()
            if row is not None and row["candle_unit"] in {"1m", "1d"}:
                by_instrument.setdefault((item.market_id, str(row["candle_unit"])), []).append(item)
        for (instrument_id, source_unit), revisions in by_instrument.items():
            revision_ids = sorted(item.id for item in revisions)
            revision_ids_text = ",".join(str(value) for value in revision_ids)
            source_knowledge_at = max(item.knowledge_at for item in revisions)
            created_at = _to_db_time(now_kst())
            for affected in affected_rollup_ranges(
                [item.candle_start_at.astimezone(UTC) for item in revisions],
                units=("1d", "1w", "1M") if source_unit == "1d" else MATERIALIZED_AGGREGATION_UNITS,
            ):
                quality_ceiling = self._execute(
                    """
                    SELECT MAX(quality_event_through_id) AS quality_event_through_id,
                           MAX(knowledge_at) AS knowledge_at
                    FROM candle_rollup_invalidations
                    WHERE instrument_id = ? AND quality_event_through_id IS NOT NULL
                      AND range_start_at < ? AND range_end_at > ?
                    """,
                    (
                        instrument_id,
                        _to_db_time(affected.end_at),
                        _to_db_time(affected.start_at),
                    ),
                ).fetchone()
                quality_event_through_id = (
                    int(quality_ceiling["quality_event_through_id"])
                    if quality_ceiling
                    and quality_ceiling["quality_event_through_id"] is not None
                    else None
                )
                quality_knowledge_at = (
                    _from_db_time(quality_ceiling["knowledge_at"])
                    if quality_ceiling and quality_ceiling["knowledge_at"] is not None
                    else source_knowledge_at
                )
                knowledge_at = max(source_knowledge_at, quality_knowledge_at)
                coverage_payload = [
                    {
                        "startAt": item.start_at.astimezone(UTC).isoformat(),
                        "endAt": item.end_at.astimezone(UTC).isoformat(),
                        "status": item.status,
                    }
                    for item in self._candle_coverage(
                        instrument_id, affected.start_at, affected.end_at
                    )
                ]
                coverage_json = json.dumps(
                    coverage_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                coverage_hash = hashlib.sha256(coverage_json.encode()).hexdigest()
                fingerprint = hashlib.sha256(
                    "|".join(
                        (
                            str(instrument_id),
                            affected.unit,
                            affected.start_at.isoformat(),
                            affected.end_at.isoformat(),
                            CALCULATION_VERSION,
                            revision_ids_text,
                            coverage_hash,
                        )
                    ).encode()
                ).hexdigest()
                cursor = self._execute(
                    """
                    INSERT OR IGNORE INTO candle_rollup_invalidations (
                      idempotency_key, market_id, instrument_id, candle_unit,
                      calculation_version, range_start_at, range_end_at,
                      output_bucket_count, source_revision_ids,
                      source_revision_through_id, quality_event_through_id,
                      coverage_snapshot, coverage_snapshot_hash, knowledge_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fingerprint,
                        instrument_id,
                        instrument_id,
                        affected.unit,
                        CALCULATION_VERSION,
                        _to_db_time(affected.start_at),
                        _to_db_time(affected.end_at),
                        affected.output_bucket_count,
                        revision_ids_text,
                        max(revision_ids),
                        quality_event_through_id,
                        coverage_json,
                        coverage_hash,
                        _to_db_time(knowledge_at),
                        created_at,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                invalidation_id = _required_lastrowid(cursor)
                self._execute(
                    """
                    INSERT INTO candle_rollup_recompute_jobs (
                      invalidation_id, idempotency_key, status, next_retry_at,
                      created_at, updated_at
                    ) VALUES (?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        invalidation_id,
                        f"rollup:{fingerprint}",
                        created_at,
                        created_at,
                        created_at,
                    ),
                )

    def _latest_candle_time(self, instrument_id: int) -> datetime | None:
        row = self._execute(
            """
            SELECT candle_start_at FROM source_candles
            WHERE instrument_id = ?
            ORDER BY candle_start_at DESC
            LIMIT 1
            """,
            (instrument_id,),
        ).fetchone()
        return _from_db_time(row["candle_start_at"]) if row else None

    def _failed_runs_24h(self) -> int:
        cutoff = _to_db_time(now_kst() - timedelta(hours=24))
        return int(
            self._execute(
                """
                SELECT COUNT(*) AS count FROM collection_runs
                WHERE status = 'failed' AND started_at >= ?
                """,
                (cutoff,),
            ).fetchone()["count"]
        )

    def _derive_candles(self, unit: str, source: list[SourceCandle]) -> list[CandleView]:
        return aggregate_candles(unit, source)

    def _backfill_job_by_id(self, job_id: int) -> BackfillJob:
        row = self._execute(
            """
            SELECT
              bj.*,
              bp.target_start_at,
              bp.target_end_at,
              bp.estimated_request_count,
              (
                SELECT COALESCE(
                  ROUND(
                    100.0 * SUM(
                      CASE
                        WHEN progress_target.status = 'succeeded' THEN 1.0
                        WHEN progress_target.estimated_missing_range_count > 0 THEN
                          MIN(
                            1.0,
                            CAST(progress_target.processed_missing_range_count AS REAL)
                              / progress_target.estimated_missing_range_count
                          )
                        ELSE 0
                      END
                    ) / NULLIF(COUNT(progress_target.instrument_id), 0),
                    2
                  ),
                  0
                )
                FROM backfill_job_targets progress_target
                WHERE progress_target.backfill_job_id = bj.id
              ) AS live_progress_percent,
              (SELECT COUNT(*) FROM backfill_job_targets WHERE backfill_job_id = bj.id)
                AS total_target_count,
              (
                SELECT COUNT(*)
                FROM backfill_job_targets
                WHERE backfill_job_id = bj.id AND status = 'succeeded'
              ) AS completed_target_count,
              running.instrument_id AS current_target_id,
              running.processed_missing_range_count,
              running.estimated_missing_range_count,
              running.rows_written_count AS current_target_backfill_row_count,
              CASE
                WHEN running.instrument_id IS NULL THEN NULL
                ELSE (
                  SELECT COUNT(*)
                  FROM backfill_job_targets bjt_index
                  WHERE bjt_index.backfill_job_id = bj.id
                    AND bjt_index.instrument_id <= running.instrument_id
                )
              END AS running_target_index
            FROM backfill_jobs bj
            JOIN backfill_plans bp ON bp.plan_id = bj.plan_id
            LEFT JOIN backfill_job_targets running
              ON running.backfill_job_id = bj.id
             AND running.instrument_id = (
               SELECT MIN(instrument_id)
               FROM backfill_job_targets
               WHERE backfill_job_id = bj.id AND status = 'running'
             )
            WHERE bj.id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            raise ValueError("존재하지 않는 백필 작업이다.")
        return self._backfill_job_from_row(row)

    def _backfill_job_target_instruments(self, job_id: int) -> list[Instrument]:
        rows = self._execute(
            """
            SELECT i.*
            FROM backfill_job_targets bjt
            JOIN instruments i ON i.id = bjt.instrument_id
            WHERE bjt.backfill_job_id = ?
            ORDER BY i.market_code
            """,
            (job_id,),
        ).fetchall()
        return [self._instrument_from_row(row) for row in rows]

    def _instrument_from_row(self, row: sqlite3.Row) -> Instrument:
        return Instrument(
            id=int(row["id"]),
            exchange="UPBIT",
            market_code=str(row["market_code"]),
            quote_currency=str(row["quote_currency"]),
            base_asset=str(row["base_asset"]),
            display_name=str(row["display_name"]),
        )

    def _ticker_from_row(self, row: sqlite3.Row) -> TickerSnapshot:
        return TickerSnapshot(
            instrument_id=int(row["instrument_id"]),
            bucket_at=_from_db_time(row["bucket_at"]),
            trade_price=_decimal(row["trade_price"]),
            acc_trade_price_24h=_decimal(row["acc_trade_price_24h"]),
            change_rate=_decimal(row["change_rate"]),
            occurred_at=_from_db_time(row["occurred_at"] or row["bucket_at"]),
            received_at=_from_db_time(row["received_at"] or row["collected_at"]),
        )

    def _orderbook_from_row(self, row: sqlite3.Row) -> OrderbookSummary:
        return OrderbookSummary(
            instrument_id=int(row["instrument_id"]),
            bucket_at=_from_db_time(row["bucket_at"]),
            best_bid_price=_decimal(row["best_bid_price"]),
            best_bid_size=_decimal(row["best_bid_size"]),
            best_ask_price=_decimal(row["best_ask_price"]),
            best_ask_size=_decimal(row["best_ask_size"]),
            spread=_decimal(row["spread"]),
            bid_depth_10=_decimal(row["bid_depth_10"]),
            ask_depth_10=_decimal(row["ask_depth_10"]),
            imbalance_10=_decimal(row["imbalance_10"]),
            occurred_at=_from_db_time(row["occurred_at"] or row["bucket_at"]),
            received_at=_from_db_time(row["received_at"] or row["collected_at"]),
        )

    def _candle_from_row(self, row: sqlite3.Row) -> SourceCandle:
        columns = set(row.keys())
        return SourceCandle(
            instrument_id=int(row["instrument_id"]),
            candle_unit=cast(Literal["1m", "1d"], row["candle_unit"]),
            candle_start_at=_from_db_time(row["candle_start_at"]),
            open_price=_decimal(row["open_price"]),
            high_price=_decimal(row["high_price"]),
            low_price=_decimal(row["low_price"]),
            close_price=_decimal(row["close_price"]),
            trade_volume=_decimal(row["trade_volume"]),
            trade_amount=_decimal(row["trade_amount"]),
            collected_at=_from_db_time(row["collected_at"]),
            revision_id=(
                int(row["revision_id"])
                if "revision_id" in columns and row["revision_id"] is not None
                else None
            ),
            input_content_hash=(
                str(row["input_content_hash"])
                if "input_content_hash" in columns and row["input_content_hash"]
                else None
            ),
            knowledge_at=(
                _from_db_time(row["revision_knowledge_at"])
                if "revision_knowledge_at" in columns and row["revision_knowledge_at"]
                else None
            ),
        )

    def _collection_run_from_row(self, row: sqlite3.Row) -> CollectionRun:
        return CollectionRun(
            id=int(row["id"]),
            run_type=str(row["run_type"]),
            data_type=str(row["data_type"]),
            status=cast(
                Literal["running", "succeeded", "partial", "failed", "cancelled"],
                row["status"],
            ),
            started_at=_from_db_time(row["started_at"]),
            finished_at=_from_db_time(row["finished_at"]) if row["finished_at"] else None,
        )

    def _notification_from_row(self, row: sqlite3.Row) -> NotificationEvent:
        return NotificationEvent(
            id=int(row["id"]),
            severity=cast(Literal["info", "warning", "error", "critical"], row["severity"]),
            event_type=str(row["event_type"]),
            title=str(row["title"]),
            message=str(row["message"]),
            status=cast(Literal["open", "acknowledged", "resolved"], row["status"]),
            created_at=_from_db_time(row["created_at"]),
        )

    def _backfill_job_from_row(self, row: sqlite3.Row) -> BackfillJob:
        row_keys = set(row.keys())
        current_target_id = row["current_target_id"] if "current_target_id" in row_keys else None
        current_target = None
        if current_target_id is not None:
            instrument_row = self._execute(
                "SELECT * FROM instruments WHERE id = ?",
                (int(current_target_id),),
            ).fetchone()
            current_target = (
                self._instrument_from_row(instrument_row) if instrument_row is not None else None
            )
        return BackfillJob(
            id=int(row["id"]),
            status=cast(
                Literal[
                    "planned",
                    "pending",
                    "running",
                    "paused",
                    "stopped",
                    "succeeded",
                    "failed",
                ],
                row["status"],
            ),
            data_type=str(row["data_type"]),
            progress_percent=_progress_decimal(
                row["live_progress_percent"]
                if "live_progress_percent" in row_keys
                else row["progress_percent"]
            ),
            estimated_request_count=int(row["estimated_request_count"]),
            total_target_count=int(row["total_target_count"] or 0),
            completed_target_count=int(row["completed_target_count"] or 0),
            running_target_index=(
                int(row["running_target_index"])
                if row["running_target_index"] is not None
                else None
            ),
            current_target=current_target,
            current_target_backfill_row_count=int(row["current_target_backfill_row_count"] or 0),
            processed_missing_range_count=int(row["processed_missing_range_count"] or 0),
            estimated_missing_range_count=int(row["estimated_missing_range_count"] or 0),
            target_start_at=_from_db_time(row["target_start_at"]),
            target_end_at=_from_db_time(row["target_end_at"]),
            targets=self._backfill_job_target_instruments(int(row["id"])),
            created_at=_from_db_time(row["created_at"]),
        )

    def _backfill_job_detail_from_row(self, row: sqlite3.Row | None) -> BackfillJobDetail | None:
        if row is None:
            return None
        return BackfillJobDetail(
            id=int(row["id"]),
            status=cast(
                Literal[
                    "planned",
                    "pending",
                    "running",
                    "paused",
                    "stopped",
                    "succeeded",
                    "failed",
                ],
                row["status"],
            ),
            data_type=str(row["data_type"]),
            target_start_at=_from_db_time(row["target_start_at"]),
            target_end_at=_from_db_time(row["target_end_at"]),
            estimated_request_count=int(row["estimated_request_count"]),
            estimated_row_count=int(row["estimated_row_count"]),
            created_at=_from_db_time(row["created_at"]),
        )

    def _backfill_target_from_row(self, row: sqlite3.Row) -> BackfillJobTarget:
        return BackfillJobTarget(
            job_id=int(row["backfill_job_id"]),
            instrument_id=int(row["instrument_id"]),
            status=cast(
                Literal["pending", "running", "paused", "stopped", "succeeded", "failed"],
                row["status"],
            ),
            last_completed_at=_from_db_time(row["last_completed_at"])
            if row["last_completed_at"]
            else None,
            error_code=cast(str | None, row["error_code"]),
            error_message=cast(str | None, row["error_message"]),
        )

    def _refresh_backfill_job_progress(self, job_id: int) -> None:
        current = self._execute(
            "SELECT status FROM backfill_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if current is None:
            raise ValueError("존재하지 않는 백필 작업이다.")
        rows = self._execute(
            "SELECT status FROM backfill_job_targets WHERE backfill_job_id = ?",
            (job_id,),
        ).fetchall()
        total = len(rows)
        succeeded = sum(1 for row in rows if row["status"] == "succeeded")
        failed = any(row["status"] == "failed" for row in rows)
        if total == 0:
            progress = Decimal("0")
            status = "failed"
        else:
            progress = (Decimal(succeeded) / Decimal(total) * Decimal("100")).quantize(
                Decimal("0.01")
            )
            if failed:
                status = "failed"
            elif succeeded == total:
                status = "succeeded"
            else:
                status = "running"
        if current["status"] in {"paused", "stopped"}:
            status = current["status"]
        self._execute(
            """
            UPDATE backfill_jobs
            SET status = ?, progress_percent = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, str(progress.normalize()), _to_db_time(now_kst()), job_id),
        )
