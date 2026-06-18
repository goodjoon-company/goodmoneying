from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

from goodmoneying_shared.models import (
    BackfillJob,
    BackfillJobDetail,
    BackfillJobTarget,
    BackfillPlan,
    CandidateUniverseEntry,
    CandleView,
    CollectionDashboardTarget,
    CollectionDataStatus,
    CollectionPlan,
    CollectionRun,
    CoverageSegment,
    CoverageStatus,
    DashboardSummary,
    Instrument,
    MarketListRow,
    NotificationEvent,
    OrderbookSummary,
    SourceCandle,
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
        active_targets = self.list_active_targets()
        coverage = [
            status for instrument in active_targets for status in self.coverage_for(instrument.id)
        ]
        delayed_targets = sum(1 for status in coverage if status.status != "normal")
        missing_ranges_open = sum(1 for status in coverage if status.status == "incident")
        failed_runs_24h = self._failed_runs_24h()
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
            active_targets=len(active_targets),
            failed_runs_24h=failed_runs_24h,
            delayed_targets=delayed_targets,
            missing_ranges_open=missing_ranges_open,
            coverage=coverage,
            targets=self.collection_dashboard_targets(),
            alerts=alerts,
            refreshed_at=now_utc(),
        )

    def collection_dashboard_targets(self) -> list[CollectionDashboardTarget]:
        targets: list[CollectionDashboardTarget] = []
        for instrument in self.list_active_targets():
            coverage = sorted(
                self.coverage_for(instrument.id),
                key=lambda item: {
                    "source_candle": 0,
                    "ticker_snapshot": 1,
                    "orderbook_summary": 2,
                }[item.data_type],
            )
            data_statuses = [self._collection_data_status(item) for item in coverage]
            overall_status: Literal["latest_collecting", "warning"] = (
                "latest_collecting"
                if all(item.status == "normal" for item in data_statuses)
                else "warning"
            )
            targets.append(
                CollectionDashboardTarget(
                    instrument=instrument,
                    overall_status=overall_status,
                    overall_status_label="최신수집중"
                    if overall_status == "latest_collecting"
                    else "주의",
                    plan=self._collection_plan_for(instrument.id),
                    data_statuses=data_statuses,
                    coverage_segments=[
                        segment
                        for data_status in data_statuses
                        for segment in self._coverage_segments_for(
                            instrument.id, data_status.data_type
                        )
                    ],
                )
            )
        return targets

    def market_list(self) -> list[MarketListRow]:
        rows: list[MarketListRow] = []
        for instrument in self.list_active_targets():
            ticker = self.latest_ticker(instrument.id)
            orderbook = self.latest_orderbook(instrument.id)
            if ticker is None or orderbook is None:
                continue
            rows.append(
                MarketListRow(
                    instrument=instrument,
                    trade_price=ticker.trade_price,
                    acc_trade_price_24h=ticker.acc_trade_price_24h,
                    acc_trade_price_24h_display=str(int(ticker.acc_trade_price_24h)),
                    change_rate=ticker.change_rate,
                    ticker_collected_at=ticker.collected_at,
                    orderbook_collected_at=orderbook.collected_at,
                    quality_status="normal",
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
        checks: list[
            tuple[
                Literal["source_candle", "ticker_snapshot", "orderbook_summary"],
                datetime | None,
            ]
        ] = [
            ("ticker_snapshot", latest_ticker.collected_at if latest_ticker else None),
            ("orderbook_summary", latest_orderbook.collected_at if latest_orderbook else None),
            ("source_candle", self._latest_candle_time(instrument_id)),
        ]
        statuses: list[CoverageStatus] = []
        for data_type, latest_at in checks:
            if latest_at is None:
                statuses.append(
                    CoverageStatus(
                        instrument_id=instrument_id,
                        data_type=data_type,
                        status="incident",
                        progress_percent=Decimal("0"),
                        last_successful_at=now_utc() - timedelta(days=365),
                    )
                )
                continue
            age = now_utc() - latest_at
            coverage_status: Literal["normal", "warning"] = (
                "normal" if age <= timedelta(minutes=3) else "warning"
            )
            statuses.append(
                CoverageStatus(
                    instrument_id=instrument_id,
                    data_type=data_type,
                    status=coverage_status,
                    progress_percent=Decimal("100"),
                    last_successful_at=latest_at,
                )
            )
        return statuses

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
        with self._lock, self._conn:
            counts = self._upsert_candles(candles)
            self._execute(
                """
                UPDATE backfill_job_targets
                SET status = 'running', updated_at = ?
                WHERE backfill_job_id = ? AND instrument_id = ? AND status = 'pending'
                """,
                (_to_db_time(now_utc()), job_id, instrument_id),
            )
        return counts.get(instrument_id, 0)

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
            display_range="2026-01-01 00:00 KST ~ 현재(지속)"
            if bool(row["is_continuous"])
            else "2026-01-01 00:00 KST ~ 2026-02-01 00:00 KST",
            range_time_zone="KST",
            progress_basis="현재(지속)은 KST 전일 23:59:59까지 기준",
        )

    def _collection_data_status(self, item: CoverageStatus) -> CollectionDataStatus:
        labels = {
            "source_candle": "캔들",
            "ticker_snapshot": "현재가",
            "orderbook_summary": "호가 요약",
        }
        return CollectionDataStatus(
            data_type=item.data_type,
            label=labels[item.data_type],
            status=item.status,
            status_label="정상" if item.status == "normal" else "주의",
            last_successful_at=item.last_successful_at,
            progress_percent=item.progress_percent,
            missing_segment_count=1 if item.data_type == "source_candle" else 0,
        )

    def _coverage_segments_for(
        self,
        instrument_id: int,
        data_type: Literal["source_candle", "ticker_snapshot", "orderbook_summary"],
    ) -> list[CoverageSegment]:
        plan = self._collection_plan_for(instrument_id)
        segment_end = now_utc()
        if data_type == "source_candle":
            return [
                CoverageSegment(
                    data_type=data_type,
                    status="collected",
                    offset_percent=Decimal("0"),
                    width_percent=Decimal("64"),
                    segment_start_at=plan.range_start_at,
                    segment_end_at=segment_end,
                    label="수집 완료",
                ),
                CoverageSegment(
                    data_type=data_type,
                    status="missing",
                    offset_percent=Decimal("64"),
                    width_percent=Decimal("8"),
                    segment_start_at=plan.range_start_at,
                    segment_end_at=segment_end,
                    label="결측",
                ),
                CoverageSegment(
                    data_type=data_type,
                    status="collected",
                    offset_percent=Decimal("72"),
                    width_percent=Decimal("28"),
                    segment_start_at=plan.range_start_at,
                    segment_end_at=segment_end,
                    label="수집 완료",
                ),
            ]
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
            SELECT collected_at FROM source_candles
            WHERE instrument_id = ?
            ORDER BY candle_start_at DESC
            LIMIT 1
            """,
            (instrument_id,),
        ).fetchone()
        return _from_db_time(row["collected_at"]) if row else None

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
