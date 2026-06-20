from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

from goodmoneying_shared.models import (
    AuditLogSummary,
    BackfillJob,
    BackfillJobDetail,
    BackfillJobTarget,
    BackfillPlan,
    CandidateUniverseEntry,
    CandleView,
    CollectionActivityBucket,
    CollectionDashboardTarget,
    CollectionDataStatus,
    CollectionPlan,
    CollectionRun,
    CoverageSegment,
    CoverageStatus,
    DashboardSummary,
    HealthCheck,
    Instrument,
    RealtimeCollectionHeatmapBucket,
    RealtimeCollectionHeatmapRow,
    MarketListRow,
    MissingRangeSummary,
    NotificationEvent,
    OperationsTrendPoint,
    OrderbookSummary,
    SourceCandle,
    StorageBreakdownItem,
    TickerSnapshot,
)
from goodmoneying_shared.time import minute_bucket, now_utc


def _to_db_time(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def _from_db_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _decimal(value: str | int | float | Decimal | None) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


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


class SQLiteOperationsRepository:
    """테스트와 로컬 데모용 저장소.

    런타임 계약은 PostgreSQL이지만, 이 어댑터는 같은 repository interface로
    M1 동작을 빠르게 검증하기 위한 SQLite 기반 구현이다.
    """

    def __init__(self, database_url: str = ":memory:") -> None:
        self._database_url = database_url
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(database_url, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    @classmethod
    def from_path(cls, path: str | Path) -> SQLiteOperationsRepository:
        return cls(str(path))

    def close(self) -> None:
        self._conn.close()

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def _create_schema(self) -> None:
        with self._conn:
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

    def upsert_instrument(self, market_code: str, display_name: str) -> Instrument:
        quote_currency, base_asset = market_code.split("-", maxsplit=1)
        timestamp = _to_db_time(now_utc())
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
        ranked_at = _to_db_time(now_utc())
        with self._lock, self._conn:
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
                for row in rows:
                    self._activate_target(row["instrument_id"], "system", "default_top_50")
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
            if not set(instrument_ids).issubset(candidate_ids):
                raise ValueError("활성 수집 대상은 후보 유니버스 안에서만 선택할 수 있다.")
            current_ids = {
                row["instrument_id"]
                for row in self._execute(
                    "SELECT instrument_id FROM collection_targets WHERE status = 'active'"
                )
            }
            next_ids = set(instrument_ids)
            for instrument_id in sorted(current_ids - next_ids):
                self._deactivate_target(instrument_id, "local_user", reason)
            for instrument_id in instrument_ids:
                self._activate_target(instrument_id, "local_user", reason)
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
                  COALESCE(ct.candidate_status, 'in_universe') AS candidate_status
                FROM candidate_universe_entries cue
                JOIN instruments i ON i.id = cue.instrument_id
                LEFT JOIN collection_targets ct ON ct.instrument_id = i.id
                ORDER BY cue.rank
                """
            ).fetchall()
        ranked_at = _from_db_time(rows[0]["ranked_at"]) if rows else now_utc()
        entries = [
            CandidateUniverseEntry(
                instrument=self._instrument_from_row(row),
                rank=row["rank"],
                acc_trade_price_24h=_decimal(row["acc_trade_price_24h"]),
                selected=row["target_status"] == "active",
                candidate_status=row["candidate_status"],
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
                ORDER BY i.market_code
                """
            ).fetchall()
        return [self._instrument_from_row(row) for row in rows]

    def record_incremental_collection(
        self,
        tickers: list[TickerSnapshot],
        orderbooks: list[OrderbookSummary],
        candles: list[SourceCandle],
    ) -> CollectionRun:
        started_at = now_utc()
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
                        _to_db_time(now_utc()),
                    ),
                )
            finished_at = now_utc()
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
        targets = self.collection_dashboard_targets()
        coverage = [
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
            Decimal(failed_runs_24h) / Decimal(recent_runs)
            if recent_runs > 0
            else Decimal("0")
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
            collection_activity=self._collection_activity_buckets(),
            realtime_collection_heatmap=self._realtime_collection_heatmap(),
            storage_breakdown=self._storage_breakdown_today(storage_bytes_today),
            operations_trend=self._operations_trend(
                coverage,
                storage_bytes_today,
                warning_targets,
                incident_targets,
            ),
            missing_range_top=self._missing_range_top(targets),
            audit_log_summary=self._audit_log_summary(),
            refreshed_at=now_utc(),
        )

    def collection_dashboard_targets(
        self, include_segments: bool = False
    ) -> list[CollectionDashboardTarget]:
        with self._lock:
            targets: list[CollectionDashboardTarget] = []
            active_targets = self.list_active_targets()
            source_candle_counts = self._table_counts_by_instrument(
                "source_candles",
                [instrument.id for instrument in active_targets],
            )
            for instrument in active_targets:
                ticker = self.latest_ticker(instrument.id)
                coverage = sorted(
                    self.coverage_for(instrument.id),
                    key=lambda item: {
                        "source_candle": 0,
                        "ticker_snapshot": 1,
                        "orderbook_summary": 2,
                    }[item.data_type],
                )
                data_statuses = [
                    self._collection_data_status(item, source_candle_counts)
                    for item in coverage
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
                        plan=self._collection_plan_for(instrument.id),
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
                        ticker_collected_at=ticker.collected_at if ticker else now_utc(),
                        coverage_percent=candle_status.progress_percent,
                        storage_row_count=self._instrument_storage_row_count(instrument.id),
                        storage_bytes_display=_format_storage_bytes(
                            self._instrument_storage_bytes(instrument.id)
                        ),
                    )
                )
            return targets

    def coverage_segments_for(self, instrument_id: int) -> list[CoverageSegment]:
        return [
            segment
            for status in self.coverage_for(instrument_id)
            for segment in self._coverage_segments_for(instrument_id, status.data_type)
        ]

    def market_list(self) -> list[MarketListRow]:
        rows: list[MarketListRow] = []
        for instrument in self.list_active_targets():
            ticker = self.latest_ticker(instrument.id)
            orderbook = self.latest_orderbook(instrument.id)
            if ticker is None or orderbook is None:
                continue
            coverage = self.coverage_for(instrument.id)
            rows.append(
                MarketListRow(
                    instrument=instrument,
                    trade_price=ticker.trade_price,
                    acc_trade_price_24h=ticker.acc_trade_price_24h,
                    acc_trade_price_24h_display=f"₩{int(ticker.acc_trade_price_24h):,}",
                    change_rate=ticker.change_rate,
                    ticker_collected_at=ticker.collected_at,
                    orderbook_collected_at=orderbook.collected_at,
                    quality_status=self._quality_status_from_coverage(coverage),
                    coverage_percent=self._market_coverage_percent_from_statuses(coverage),
                    storage_bytes=self._instrument_storage_bytes(instrument.id),
                    storage_row_count=self._instrument_storage_row_count(instrument.id),
                    storage_bytes_display=_format_storage_bytes(
                        self._instrument_storage_bytes(instrument.id)
                    ),
                )
            )
        return rows

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
        if unit in {"1m", "1d"}:
            return [
                CandleView(
                    started_at=item.candle_start_at,
                    open=item.open_price,
                    high=item.high_price,
                    low=item.low_price,
                    close=item.close_price,
                    volume=item.trade_volume,
                    trade_amount=item.trade_amount,
                    completeness="complete",
                )
                for item in source
                if item.candle_unit == unit
            ]
        return self._derive_candles(unit, source)

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

    def collection_runs(self, limit: int) -> list[CollectionRun]:
        rows = self._execute(
            """
            SELECT * FROM collection_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._collection_run_from_row(row) for row in rows]

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
                    _to_db_time(now_utc()),
                ),
            )
        return plan

    def approve_backfill_job(self, plan_id: str) -> BackfillJob:
        row = self._execute("SELECT * FROM backfill_plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if row is None:
            raise ValueError("존재하지 않는 백필 계획이다.")
        created_at = _to_db_time(now_utc())
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
                    (_to_db_time(now_utc()), row["id"]),
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
        self, job_id: int, instrument_id: int, candles: list[SourceCandle]
    ) -> int:
        if not candles:
            return 0
        if any(item.instrument_id != instrument_id for item in candles):
            raise ValueError("백필 캔들 대상 instrument_id가 작업 대상과 다르다.")
        started_at = now_utc()
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
                (run_id, instrument_id, rows_written, _to_db_time(now_utc())),
            )
            self._execute(
                """
                UPDATE collection_runs
                SET status = 'succeeded', finished_at = ?
                WHERE id = ?
                """,
                (_to_db_time(now_utc()), run_id),
            )
            self._execute(
                """
                UPDATE backfill_job_targets
                SET status = 'running', updated_at = ?
                WHERE backfill_job_id = ? AND instrument_id = ? AND status = 'pending'
                """,
                (_to_db_time(now_utc()), job_id, instrument_id),
            )
        return rows_written

    def mark_backfill_target(
        self,
        job_id: int,
        instrument_id: int,
        status: str,
        last_completed_at: datetime | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
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
                    _to_db_time(now_utc()),
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
            if current.status in {"succeeded", "failed", "stopped"} and action != "safe-restart":
                raise ValueError("완료 또는 중지된 백필 작업은 해당 명령을 수행할 수 없다.")
            self._execute(
                "UPDATE backfill_jobs SET status = ?, updated_at = ? WHERE id = ?",
                (transitions[action], _to_db_time(now_utc()), job_id),
            )
        return self._backfill_job_by_id(job_id)

    def backfill_jobs(self) -> list[BackfillJob]:
        rows = self._execute("SELECT * FROM backfill_jobs ORDER BY created_at DESC").fetchall()
        return [self._backfill_job_from_row(row) for row in rows]

    def notification_events(self) -> list[NotificationEvent]:
        rows = self._execute(
            "SELECT * FROM notification_events ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        return [self._notification_from_row(row) for row in rows]

    def _recent_run_count(self) -> int:
        since = _to_db_time(now_utc() - timedelta(hours=24))
        row = self._execute(
            "SELECT COUNT(*) AS count FROM collection_runs WHERE started_at >= ?",
            (since,),
        ).fetchone()
        return int(row["count"]) if row else 0

    def _recent_collection_result_count(self) -> int:
        since = _to_db_time(now_utc() - timedelta(hours=24))
        row = self._execute(
            "SELECT COUNT(*) AS count FROM target_collection_results WHERE created_at >= ?",
            (since,),
        ).fetchone()
        return int(row["count"]) if row else 0

    def _collection_rows_last_minute(self, run_type: str) -> int:
        since = _to_db_time(now_utc() - timedelta(minutes=1))
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
        day_start = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            self._table_count_since("source_candles", "collected_at", day_start)
            + self._table_count_since("ticker_snapshots", "collected_at", day_start)
            + self._table_count_since("orderbook_summaries", "collected_at", day_start)
            + self._table_count_since("target_collection_results", "created_at", day_start)
        )

    def _storage_bytes_today_estimate(self) -> int:
        day_start = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            self._table_count_since("source_candles", "collected_at", day_start) * 256
            + self._table_count_since("ticker_snapshots", "collected_at", day_start) * 160
            + self._table_count_since("orderbook_summaries", "collected_at", day_start) * 224
            + self._table_count_since("target_collection_results", "created_at", day_start) * 128
        )

    def _realtime_collection_heatmap(self) -> list[RealtimeCollectionHeatmapRow]:
        active_targets = self.list_active_targets()[:50]
        if not active_targets:
            return []
        expected_rows_by_type = {
            "source_candle": 60,
            "ticker_snapshot": 60,
            "orderbook_summary": 60,
        }
        now = now_utc()
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        first_hour = current_hour - timedelta(hours=23)
        target_ids = [target.id for target in active_targets]
        placeholders = ", ".join(["?"] * len(target_ids))

        source_counts: dict[tuple[int, datetime], int] = {}
        for row in self._execute(
            f"""
            SELECT instrument_id, collected_at
            FROM source_candles
            WHERE candle_unit = '1m' AND collected_at >= ? AND instrument_id IN ({placeholders})
            """,
            (_to_db_time(first_hour), *target_ids),
        ).fetchall():
            bucket = _from_db_time(row["collected_at"]).replace(
                minute=0, second=0, microsecond=0
            )
            source_counts[(row["instrument_id"], bucket)] = source_counts.get(
                (row["instrument_id"], bucket),
                0,
            ) + 1

        ticker_counts: dict[tuple[int, datetime], int] = {}
        for row in self._execute(
            f"""
            SELECT instrument_id, collected_at
            FROM ticker_snapshots
            WHERE collected_at >= ? AND instrument_id IN ({placeholders})
            """,
            (_to_db_time(first_hour), *target_ids),
        ).fetchall():
            bucket = _from_db_time(row["collected_at"]).replace(
                minute=0, second=0, microsecond=0
            )
            ticker_counts[(row["instrument_id"], bucket)] = ticker_counts.get(
                (row["instrument_id"], bucket),
                0,
            ) + 1

        orderbook_counts: dict[tuple[int, datetime], int] = {}
        for row in self._execute(
            f"""
            SELECT instrument_id, collected_at
            FROM orderbook_summaries
            WHERE collected_at >= ? AND instrument_id IN ({placeholders})
            """,
            (_to_db_time(first_hour), *target_ids),
        ).fetchall():
            bucket = _from_db_time(row["collected_at"]).replace(
                minute=0, second=0, microsecond=0
            )
            orderbook_counts[(row["instrument_id"], bucket)] = orderbook_counts.get(
                (row["instrument_id"], bucket),
                0,
            ) + 1

        heatmap: list[RealtimeCollectionHeatmapRow] = []
        for target in active_targets:
            hourly_buckets: list[RealtimeCollectionHeatmapBucket] = []
            for offset in range(24):
                bucket_start = first_hour + timedelta(hours=offset)
                actual_rows_by_type = {
                    "source_candle": source_counts.get((target.id, bucket_start), 0),
                    "ticker_snapshot": ticker_counts.get((target.id, bucket_start), 0),
                    "orderbook_summary": orderbook_counts.get((target.id, bucket_start), 0),
                }
                actual_rows_all = sum(actual_rows_by_type.values())
                expected_rows_all = sum(expected_rows_by_type.values())
                actual_ratio_percent = (
                    Decimal(actual_rows_all) / Decimal(expected_rows_all) * Decimal("100")
                    if expected_rows_all > 0
                    else Decimal("0")
                )
                hourly_buckets.append(
                    RealtimeCollectionHeatmapBucket(
                        bucket_start_at=bucket_start,
                        expected_rows_all=expected_rows_all,
                        actual_rows_all=actual_rows_all,
                        expected_rows_by_type=expected_rows_by_type,
                        actual_rows_by_type=actual_rows_by_type,
                        actual_ratio_percent=actual_ratio_percent,
                        status=self._realtime_collection_heatmap_status(actual_ratio_percent),
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
        current_hour = now_utc().replace(minute=0, second=0, microsecond=0)
        first_hour = current_hour - timedelta(hours=(7 * 24) - 1)
        run_counts: dict[datetime, int] = {}
        for row in self._execute(
            "SELECT started_at FROM collection_runs WHERE started_at >= ?",
            (_to_db_time(first_hour),),
        ).fetchall():
            bucket = _from_db_time(row["started_at"]).replace(minute=0, second=0, microsecond=0)
            run_counts[bucket] = run_counts.get(bucket, 0) + 1
        result_counts: dict[datetime, int] = {}
        for row in self._execute(
            "SELECT created_at FROM target_collection_results WHERE created_at >= ?",
            (_to_db_time(first_hour),),
        ).fetchall():
            bucket = _from_db_time(row["created_at"]).replace(minute=0, second=0, microsecond=0)
            result_counts[bucket] = result_counts.get(bucket, 0) + 1
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
        day_start = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
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
            (
                "quality_result",
                "품질/결과",
                self._table_count_since(
                    "target_collection_results", "created_at", day_start
                ),
                128,
            ),
        ]
        return [
            StorageBreakdownItem(
                data_type=cast(
                    Literal[
                        "source_candle",
                        "ticker_snapshot",
                        "orderbook_summary",
                        "quality_result",
                    ],
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
        today = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
        coverage_percent = self._average_coverage_percent(coverage)
        points = []
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            next_day = day + timedelta(days=1)
            storage_bytes = (
                storage_bytes_today
                if offset == 0
                else self._storage_bytes_for_range(day, next_day)
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
        since = now_utc() - timedelta(hours=24)
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
            SELECT created_at AS changed_at, '백필 승인' AS label
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

    def _storage_bytes_for_range(self, start_at: datetime, end_at: datetime) -> int:
        return (
            self._table_count_between("source_candles", "collected_at", start_at, end_at) * 256
            + self._table_count_between("ticker_snapshots", "collected_at", start_at, end_at)
            * 160
            + self._table_count_between("orderbook_summaries", "collected_at", start_at, end_at)
            * 224
            + self._table_count_between(
                "target_collection_results", "created_at", start_at, end_at
            )
            * 128
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
        actual_ratio_percent: Decimal,
    ) -> Literal["none", "low", "collecting", "high"]:
        if actual_ratio_percent <= Decimal("0"):
            return "none"
        if actual_ratio_percent < Decimal("24"):
            return "low"
        if actual_ratio_percent < Decimal("50"):
            return "collecting"
        if actual_ratio_percent < Decimal("90"):
            return "collecting"
        return "high"


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
        return (
            Decimal(bytes_value) / Decimal(total_bytes) * Decimal("100")
        ).quantize(Decimal("0.01"))

    def _storage_bytes_estimate(self) -> int:
        return sum(
            self._table_count(table) * row_size
            for table, row_size in (
                ("source_candles", 256),
                ("ticker_snapshots", 160),
                ("orderbook_summaries", 224),
                ("target_collection_results", 128),
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
        created_at = _to_db_time(now_utc())
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

    def _activate_target(self, instrument_id: int, actor: str, reason: str | None) -> None:
        timestamp = _to_db_time(now_utc())
        previous = self._execute(
            "SELECT status FROM collection_targets WHERE instrument_id = ?",
            (instrument_id,),
        ).fetchone()
        self._execute(
            """
            INSERT INTO collection_targets (
              instrument_id, status, candidate_status, activated_at, deactivated_at, updated_at
            )
            VALUES (?, 'active', 'in_universe', ?, NULL, ?)
            ON CONFLICT(instrument_id) DO UPDATE SET
              status = 'active',
              candidate_status = 'in_universe',
              activated_at = COALESCE(collection_targets.activated_at, excluded.activated_at),
              deactivated_at = NULL,
              updated_at = excluded.updated_at
            """,
            (instrument_id, timestamp, timestamp),
        )
        self._record_target_change(
            instrument_id, previous["status"] if previous else None, "active", actor, reason
        )
        self._ensure_collection_plan(instrument_id)

    def _deactivate_target(self, instrument_id: int, actor: str, reason: str | None) -> None:
        timestamp = _to_db_time(now_utc())
        previous = self._execute(
            "SELECT status FROM collection_targets WHERE instrument_id = ?",
            (instrument_id,),
        ).fetchone()
        self._execute(
            """
            UPDATE collection_targets
            SET status = 'inactive', deactivated_at = ?, updated_at = ?
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
            (instrument_id, previous_status, new_status, actor, reason, _to_db_time(now_utc())),
        )

    def _ensure_collection_plan(self, instrument_id: int) -> None:
        plan_start = datetime(2025, 12, 31, 15, 0, tzinfo=UTC)
        timestamp = _to_db_time(now_utc())
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
                last_successful_at=now_utc() - timedelta(days=365),
            )
        age = now_utc() - latest_at
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
            last_successful_at = now_utc() - timedelta(days=365)
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
        return minute_bucket(now_utc())

    def _expected_minutes(self, start_at: datetime, end_at: datetime) -> int:
        return max(1, int((end_at - start_at).total_seconds() // 60))

    def _upsert_tickers(self, tickers: list[TickerSnapshot]) -> dict[int, int]:
        counts: dict[int, int] = {}
        for item in tickers:
            self._execute(
                """
                INSERT INTO ticker_snapshots (
                  instrument_id, bucket_at, trade_price, acc_trade_price_24h,
                  change_rate, collected_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, bucket_at) DO UPDATE SET
                  trade_price = excluded.trade_price,
                  acc_trade_price_24h = excluded.acc_trade_price_24h,
                  change_rate = excluded.change_rate,
                  collected_at = excluded.collected_at
                WHERE excluded.collected_at > ticker_snapshots.collected_at
                """,
                (
                    item.instrument_id,
                    _to_db_time(minute_bucket(item.bucket_at)),
                    str(item.trade_price),
                    str(item.acc_trade_price_24h),
                    str(item.change_rate),
                    _to_db_time(item.collected_at),
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
                  ask_depth_10, imbalance_10, collected_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, bucket_at) DO UPDATE SET
                  best_bid_price = excluded.best_bid_price,
                  best_bid_size = excluded.best_bid_size,
                  best_ask_price = excluded.best_ask_price,
                  best_ask_size = excluded.best_ask_size,
                  spread = excluded.spread,
                  bid_depth_10 = excluded.bid_depth_10,
                  ask_depth_10 = excluded.ask_depth_10,
                  imbalance_10 = excluded.imbalance_10,
                  collected_at = excluded.collected_at
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
                ),
            )
            counts[item.instrument_id] = counts.get(item.instrument_id, 0) + 1
        return counts

    def _upsert_candles(self, candles: list[SourceCandle]) -> dict[int, int]:
        counts: dict[int, int] = {}
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
                WHERE excluded.collected_at > source_candles.collected_at
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
        return counts

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
        cutoff = _to_db_time(now_utc() - timedelta(hours=24))
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
        minute_units = {
            "3m": 3,
            "5m": 5,
            "10m": 10,
            "15m": 15,
            "30m": 30,
            "60m": 60,
            "240m": 240,
        }
        bucket_size = minute_units.get(unit)
        source_1m = [item for item in source if item.candle_unit == "1m"]
        if bucket_size is None:
            return [
                CandleView(
                    started_at=item.candle_start_at,
                    open=item.open_price,
                    high=item.high_price,
                    low=item.low_price,
                    close=item.close_price,
                    volume=item.trade_volume,
                    trade_amount=item.trade_amount,
                    completeness="complete",
                )
                for item in source
            ]
        grouped: dict[datetime, list[SourceCandle]] = {}
        for item in source_1m:
            minute = item.candle_start_at.minute - (item.candle_start_at.minute % bucket_size)
            bucket = item.candle_start_at.replace(minute=minute, second=0, microsecond=0)
            grouped.setdefault(bucket, []).append(item)
        result: list[CandleView] = []
        for bucket, items in sorted(grouped.items()):
            ordered = sorted(items, key=lambda item: item.candle_start_at)
            result.append(
                CandleView(
                    started_at=bucket,
                    open=ordered[0].open_price,
                    high=max(item.high_price for item in ordered),
                    low=min(item.low_price for item in ordered),
                    close=ordered[-1].close_price,
                    volume=sum((item.trade_volume for item in ordered), Decimal("0")),
                    trade_amount=sum((item.trade_amount for item in ordered), Decimal("0")),
                    completeness="complete" if len(ordered) == bucket_size else "partial",
                )
            )
        return result

    def _backfill_job_by_id(self, job_id: int) -> BackfillJob:
        row = self._execute("SELECT * FROM backfill_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("존재하지 않는 백필 작업이다.")
        return self._backfill_job_from_row(row)

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
            collected_at=_from_db_time(row["collected_at"]),
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
            collected_at=_from_db_time(row["collected_at"]),
        )

    def _candle_from_row(self, row: sqlite3.Row) -> SourceCandle:
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
            progress_percent=_decimal(row["progress_percent"]),
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
            (status, str(progress.normalize()), _to_db_time(now_utc()), job_id),
        )
