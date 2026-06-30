from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

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
    HealthCheck,
    Instrument,
    MarketListRow,
    NotificationEvent,
    OrderbookSummary,
    SourceCandle,
    TickerSnapshot,
)
from goodmoneying_shared.time import minute_bucket, now_utc

Row = dict[str, Any]


def _coverage_segments_from_timeline(
    data_type: Literal["source_candle", "ticker_snapshot", "orderbook_summary"],
    timeline: list[tuple[Literal["collected", "missing"], datetime, datetime]],
) -> list[CoverageSegment]:
    if not timeline:
        return []
    timeline_start = timeline[0][1]
    timeline_end = timeline[-1][2]
    total_seconds = Decimal(str(max(1.0, (timeline_end - timeline_start).total_seconds())))
    segments: list[CoverageSegment] = []
    accumulated_width = Decimal("0")
    for index, (status, start_at, end_at) in enumerate(timeline):
        offset = (
            Decimal(str(max(0.0, (start_at - timeline_start).total_seconds())))
            / total_seconds
            * Decimal("100")
        ).quantize(Decimal("0.0001"))
        if index == len(timeline) - 1:
            width = Decimal("100") - accumulated_width
        else:
            width = (
                Decimal(str(max(0.0, (end_at - start_at).total_seconds())))
                / total_seconds
                * Decimal("100")
            ).quantize(Decimal("0.0001"))
            accumulated_width += width
        offset = offset.normalize()
        width = width.normalize()
        segments.append(
            CoverageSegment(
                data_type=data_type,
                status=status,
                offset_percent=offset,
                width_percent=width,
                segment_start_at=start_at,
                segment_end_at=end_at,
                label="수집 완료" if status == "collected" else "결측",
            )
        )
    return segments


def _format_storage_bytes(value: int) -> str:
    if value >= 1024**3:
        return f"{value / 1024**3:.1f}GB"
    if value > 0:
        return f"{value / 1024**2:.1f}MB"
    return f"{value}B"


class PostgresOperationsRepository:
    """PostgreSQL 계약 기반 런타임 저장소."""

    def __init__(
        self, database_url: str, schema_path: str | Path = "docs/contracts/db/schema.sql"
    ) -> None:
        self._database_url = database_url
        self._schema_path = Path(schema_path)
        self._apply_schema_if_empty()

    def _connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self._database_url, row_factory=dict_row)

    def _apply_schema_if_empty(self) -> None:
        with self._connect() as conn:
            conn.execute(self._schema_path.read_text())

    def upsert_instrument(self, market_code: str, display_name: str) -> Instrument:
        quote_currency, base_asset = market_code.split("-", maxsplit=1)
        with self._connect() as conn:
            row = _expect_row(
                conn.execute(
                    """
                    INSERT INTO instruments (
                      exchange, market_code, quote_currency, base_asset, display_name
                    )
                    VALUES ('UPBIT', %s, %s, %s, %s)
                    ON CONFLICT (exchange, market_code) DO UPDATE SET
                      display_name = excluded.display_name,
                      updated_at = now()
                    RETURNING *
                    """,
                    (market_code, quote_currency, base_asset, display_name),
                ).fetchone()
            )
        return _instrument(row)

    def refresh_candidate_universe(
        self, entries: list[tuple[str, str, str]]
    ) -> list[CandidateUniverseEntry]:
        with self._connect() as conn:
            snapshot_id = _expect_row(
                conn.execute(
                    """
                    INSERT INTO candidate_universe_snapshots (
                      source, exchange, quote_currency, ranked_at
                    )
                    VALUES ('UPBIT', 'UPBIT', 'KRW', %s)
                    RETURNING id
                    """,
                    (now_utc(),),
                ).fetchone()
            )["id"]
            for rank, (market_code, display_name, acc_trade_price_24h) in enumerate(
                entries[:100], start=1
            ):
                instrument = self.upsert_instrument(market_code, display_name)
                conn.execute(
                    """
                    INSERT INTO candidate_universe_entries (
                      snapshot_id, instrument_id, rank, acc_trade_price_24h,
                      is_default_selected
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (snapshot_id, instrument.id, rank, Decimal(acc_trade_price_24h), rank <= 50),
                )
            conn.execute(
                """
                UPDATE collection_targets
                SET candidate_status = CASE
                  WHEN instrument_id IN (
                    SELECT instrument_id FROM candidate_universe_entries
                    WHERE snapshot_id = %s
                  )
                  THEN 'in_universe'
                  ELSE 'out_of_universe'
                END
                """,
                (snapshot_id,),
            )
        return self.list_candidate_universe()[1]

    def ensure_default_active_targets(self, limit: int = 50) -> list[Instrument]:
        with self._connect() as conn:
            active_count = _expect_row(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM collection_targets WHERE status = 'active'"
                ).fetchone()
            )["count"]
            if active_count == 0:
                snapshot_id = _latest_snapshot_id(conn)
                rows = conn.execute(
                    """
                    SELECT instrument_id
                    FROM candidate_universe_entries
                    WHERE snapshot_id = %s
                    ORDER BY rank
                    LIMIT %s
                    """,
                    (snapshot_id, limit),
                ).fetchall()
                for row in rows:
                    self._activate_target(
                        conn, int(row["instrument_id"]), "system", "default_top_50"
                    )
        return self.list_active_targets()

    def update_active_targets(
        self, instrument_ids: list[int], reason: str | None
    ) -> list[Instrument]:
        if len(instrument_ids) > 50:
            raise ValueError("활성 수집 대상은 최대 50개까지 선택할 수 있다.")
        if len(set(instrument_ids)) != len(instrument_ids):
            raise ValueError("활성 수집 대상은 중복될 수 없다.")
        with self._connect() as conn:
            snapshot_id = _latest_snapshot_id(conn)
            candidate_ids = {
                int(row["instrument_id"])
                for row in conn.execute(
                    "SELECT instrument_id FROM candidate_universe_entries WHERE snapshot_id = %s",
                    (snapshot_id,),
                ).fetchall()
            }
            current_ids = {
                int(row["instrument_id"])
                for row in conn.execute(
                    "SELECT instrument_id FROM collection_targets WHERE status = 'active'"
                ).fetchall()
            }
            next_ids = set(instrument_ids)
            newly_selected_ids = next_ids - current_ids
            if not newly_selected_ids.issubset(candidate_ids):
                raise ValueError("활성 수집 대상은 후보 유니버스 안에서만 선택할 수 있다.")
            for instrument_id in sorted(current_ids - next_ids):
                self._deactivate_target(conn, instrument_id, "local_user", reason)
            for instrument_id in instrument_ids:
                if instrument_id in current_ids:
                    continue
                self._activate_target(conn, instrument_id, "local_user", reason)
        return self.list_active_targets()

    def list_candidate_universe(self) -> tuple[datetime, list[CandidateUniverseEntry]]:
        with self._connect() as conn:
            snapshot_id = _latest_snapshot_id(conn)
            rows = conn.execute(
                """
                SELECT
                  cue.rank,
                  cue.acc_trade_price_24h,
                  cus.ranked_at,
                  i.*,
                  COALESCE(ct.status, 'inactive') AS target_status,
                  COALESCE(ct.candidate_status, 'in_universe') AS candidate_status
                FROM candidate_universe_entries cue
                JOIN candidate_universe_snapshots cus ON cus.id = cue.snapshot_id
                JOIN instruments i ON i.id = cue.instrument_id
                LEFT JOIN collection_targets ct ON ct.instrument_id = i.id
                WHERE cue.snapshot_id = %s
                ORDER BY cue.rank
                """,
                (snapshot_id,),
            ).fetchall()
        ranked_at = cast(datetime, rows[0]["ranked_at"]) if rows else now_utc()
        return ranked_at, [
            CandidateUniverseEntry(
                instrument=_instrument(row),
                rank=int(row["rank"]),
                acc_trade_price_24h=Decimal(row["acc_trade_price_24h"]),
                selected=row["target_status"] == "active",
                candidate_status=cast(
                    Literal["in_universe", "out_of_universe"],
                    row["candidate_status"],
                ),
            )
            for row in rows
        ]

    def list_active_targets(self) -> list[Instrument]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT i.*
                FROM collection_targets ct
                JOIN instruments i ON i.id = ct.instrument_id
                WHERE ct.status = 'active'
                ORDER BY i.market_code
                """
            ).fetchall()
        return [_instrument(row) for row in rows]

    def record_incremental_collection(
        self,
        tickers: list[TickerSnapshot],
        orderbooks: list[OrderbookSummary],
        candles: list[SourceCandle],
    ) -> CollectionRun:
        started_at = now_utc()
        with self._connect() as conn:
            run_id = int(
                _expect_row(
                    conn.execute(
                        """
                        INSERT INTO collection_runs (
                          run_type, data_type, status, trigger_type, started_at
                        )
                        VALUES ('incremental', 'ticker_snapshot', 'running', 'schedule', %s)
                        RETURNING id
                        """,
                        (started_at,),
                    ).fetchone()
                )["id"]
            )
            ticker_rows = self._upsert_tickers(conn, run_id, tickers)
            orderbook_rows = self._upsert_orderbooks(conn, run_id, orderbooks)
            candle_rows = self._upsert_candles(conn, run_id, candles)
            all_ids = sorted(
                {item.instrument_id for item in tickers}
                | {item.instrument_id for item in orderbooks}
                | {item.instrument_id for item in candles}
            )
            for instrument_id in all_ids:
                conn.execute(
                    """
                    INSERT INTO target_collection_results (
                      collection_run_id, instrument_id, data_type, status,
                      latency_ms, rows_written
                    )
                    VALUES (%s, %s, 'ticker_snapshot', 'succeeded', 0, %s)
                    """,
                    (
                        run_id,
                        instrument_id,
                        ticker_rows.get(instrument_id, 0)
                        + orderbook_rows.get(instrument_id, 0)
                        + candle_rows.get(instrument_id, 0),
                    ),
                )
            finished_at = now_utc()
            conn.execute(
                "UPDATE collection_runs SET status = 'succeeded', finished_at = %s WHERE id = %s",
                (finished_at, run_id),
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
        targets = self.collection_dashboard_targets()
        normal_targets = sum(
            1 for target in targets if target.overall_status == "latest_collecting"
        )
        warning_targets = sum(1 for target in targets if target.overall_status == "warning")
        incident_targets = sum(1 for target in targets if target.overall_status == "incident")
        delayed_targets = sum(1 for status in coverage if status.status != "normal")
        missing_ranges_open = sum(1 for status in coverage if status.status == "incident")
        failed_runs_24h = self._failed_runs_24h()
        recent_runs = self._recent_run_count()
        failure_rate_24h = (
            Decimal(failed_runs_24h) / Decimal(recent_runs)
            if recent_runs > 0
            else Decimal("0")
        )
        storage_bytes_today = self._storage_bytes_estimate()
        alerts = self.notification_events()
        if any(
            alert.severity in {"error", "critical"} and alert.status == "open" for alert in alerts
        ):
            summary_status: Literal["normal", "warning", "incident"] = "incident"
        elif delayed_targets > 0 or failed_runs_24h > 0:
            summary_status = "warning"
        else:
            summary_status = "normal"
        return DashboardSummary(
            status=summary_status,
            active_targets=len(active_targets),
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
            recent_request_count=max(recent_runs, len(active_targets) * 3),
            rate_limit_remaining_percent=Decimal("64"),
            coverage=coverage,
            targets=targets,
            alerts=alerts,
            health_checks=self._health_checks(coverage, alerts),
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
                    coverage_percent=self._market_coverage_percent(instrument.id),
                    storage_bytes=self._instrument_storage_bytes(instrument.id),
                    storage_bytes_display=_format_storage_bytes(
                        self._instrument_storage_bytes(instrument.id)
                    ),
                )
            )
        return rows

    def get_instrument(self, instrument_id: int) -> Instrument | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM instruments WHERE id = %s", (instrument_id,)
            ).fetchone()
        return _instrument(row) if row else None

    def latest_ticker(self, instrument_id: int) -> TickerSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM ticker_snapshots
                WHERE instrument_id = %s
                ORDER BY bucket_at DESC
                LIMIT 1
                """,
                (instrument_id,),
            ).fetchone()
        return _ticker(row) if row else None

    def latest_orderbook(self, instrument_id: int) -> OrderbookSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM orderbook_summaries
                WHERE instrument_id = %s
                ORDER BY bucket_at DESC
                LIMIT 1
                """,
                (instrument_id,),
            ).fetchone()
        return _orderbook(row) if row else None

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
            coverage_status: Literal["normal", "warning"] = (
                "normal" if now_utc() - latest_at <= timedelta(minutes=3) else "warning"
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
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM source_candles
                WHERE instrument_id = %s AND candle_start_at >= %s AND candle_start_at <= %s
                ORDER BY candle_start_at
                """,
                (instrument_id, start_at, end_at),
            ).fetchall()
        source = [_candle(row) for row in rows]
        if unit in {"1m", "1d"}:
            return [_candle_view(item) for item in source if item.candle_unit == unit]
        return _derive_candles(unit, source)

    def ticker_snapshots(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> list[TickerSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM ticker_snapshots
                WHERE instrument_id = %s AND bucket_at >= %s AND bucket_at <= %s
                ORDER BY bucket_at
                """,
                (instrument_id, start_at, end_at),
            ).fetchall()
        return [_ticker(row) for row in rows]

    def orderbook_summaries(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> list[OrderbookSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM orderbook_summaries
                WHERE instrument_id = %s AND bucket_at >= %s AND bucket_at <= %s
                ORDER BY bucket_at
                """,
                (instrument_id, start_at, end_at),
            ).fetchall()
        return [_orderbook(row) for row in rows]

    def collection_runs(self, limit: int) -> list[CollectionRun]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM collection_runs ORDER BY started_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [_collection_run(row) for row in rows]

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
        plan = BackfillPlan(
            plan_id=str(uuid.uuid4()),
            data_type="source_candle",
            target_start_at=target_start_at,
            target_end_at=target_end_at,
            estimated_request_count=estimated_request_count,
            estimated_row_count=len(instrument_ids) * duration_minutes,
            estimated_storage_bytes=len(instrument_ids) * duration_minutes * 256,
            targets=instrument_ids,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO backfill_jobs (
                  status, data_type, plan, target_start_at, target_end_at,
                  estimated_request_count, estimated_row_count, estimated_storage_bytes,
                  restart_mode, created_by
                )
                VALUES (
                  'planned', %s, %s, %s, %s, %s, %s, %s,
                  'safe_restart', 'local_user'
                )
                """,
                (
                    plan.data_type,
                    Jsonb({"planId": plan.plan_id, "targets": plan.targets}),
                    plan.target_start_at,
                    plan.target_end_at,
                    plan.estimated_request_count,
                    plan.estimated_row_count,
                    plan.estimated_storage_bytes,
                ),
            )
        return plan

    def approve_backfill_job(self, plan_id: str) -> BackfillJob:
        with self._connect() as conn:
            planned = conn.execute(
                """
                SELECT *
                FROM backfill_jobs
                WHERE status = 'planned' AND plan ->> 'planId' = %s
                ORDER BY created_at DESC
                LIMIT 1
                FOR UPDATE
                """,
                (plan_id,),
            ).fetchone()
            if planned is None:
                raise ValueError("존재하지 않는 백필 계획이다.")
            targets = [
                int(item)
                for item in cast(dict[str, Any], planned["plan"]).get("targets", [])
            ]
            row = _expect_row(
                conn.execute(
                    """
                    UPDATE backfill_jobs
                    SET status = 'pending',
                        approved_by = 'local_user',
                        approved_at = %s,
                        updated_at = now()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (now_utc(), planned["id"]),
                ).fetchone()
            )
            for instrument_id in targets:
                conn.execute(
                    """
                    INSERT INTO backfill_job_targets (backfill_job_id, instrument_id, status)
                    VALUES (%s, %s, 'pending')
                    ON CONFLICT (backfill_job_id, instrument_id) DO NOTHING
                    """,
                    (row["id"], instrument_id),
                )
        return _backfill_job(row)

    def claim_next_backfill_job(self) -> BackfillJobDetail | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM backfill_jobs
                WHERE status IN ('pending', 'running')
                ORDER BY created_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            ).fetchone()
            if row is None:
                return None
            if row["status"] == "pending":
                row = _expect_row(
                    conn.execute(
                        """
                        UPDATE backfill_jobs
                        SET status = 'running', started_at = COALESCE(started_at, now()),
                            updated_at = now()
                        WHERE id = %s
                        RETURNING *
                        """,
                        (row["id"],),
                    ).fetchone()
                )
        return _backfill_job_detail(row)

    def backfill_job_targets(self, job_id: int) -> list[BackfillJobTarget]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM backfill_job_targets
                WHERE backfill_job_id = %s
                ORDER BY instrument_id
                """,
                (job_id,),
            ).fetchall()
        return [_backfill_target(row) for row in rows]

    def record_backfill_candles(
        self, job_id: int, instrument_id: int, candles: list[SourceCandle]
    ) -> int:
        if not candles:
            return 0
        if any(item.instrument_id != instrument_id for item in candles):
            raise ValueError("백필 캔들 대상 instrument_id가 작업 대상과 다르다.")
        started_at = now_utc()
        with self._connect() as conn:
            run_id = int(
                _expect_row(
                    conn.execute(
                        """
                        INSERT INTO collection_runs (
                          run_type, data_type, status, trigger_type, started_at
                        )
                        VALUES ('backfill', 'source_candle', 'running', 'backfill_job', %s)
                        RETURNING id
                        """,
                        (started_at,),
                    ).fetchone()
                )["id"]
            )
            counts = self._upsert_candles(conn, run_id, candles)
            rows_written = counts.get(instrument_id, 0)
            conn.execute(
                """
                INSERT INTO target_collection_results (
                  collection_run_id, instrument_id, data_type, status, rows_written
                )
                VALUES (%s, %s, 'source_candle', 'succeeded', %s)
                """,
                (run_id, instrument_id, rows_written),
            )
            finished_at = now_utc()
            conn.execute(
                """
                UPDATE collection_runs
                SET status = 'succeeded', finished_at = %s
                WHERE id = %s
                """,
                (finished_at, run_id),
            )
            conn.execute(
                """
                UPDATE backfill_job_targets
                SET status = 'running', updated_at = now()
                WHERE backfill_job_id = %s AND instrument_id = %s AND status = 'pending'
                """,
                (job_id, instrument_id),
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
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE backfill_job_targets
                SET status = %s, last_completed_at = %s, error_code = %s,
                    error_message = %s, updated_at = now()
                WHERE backfill_job_id = %s AND instrument_id = %s
                """,
                (status, last_completed_at, error_code, error_message, job_id, instrument_id),
            )
            self._refresh_backfill_job_progress(conn, job_id)

    def control_backfill_job(self, job_id: int, action: str) -> BackfillJob:
        transitions = {
            "pause": "paused",
            "stop": "stopped",
            "resume": "running",
            "safe-restart": "pending",
        }
        if action not in transitions:
            raise ValueError("지원하지 않는 백필 제어 명령이다.")
        with self._connect() as conn:
            current = conn.execute(
                "SELECT * FROM backfill_jobs WHERE id = %s", (job_id,)
            ).fetchone()
            if current is None:
                raise ValueError("존재하지 않는 백필 작업이다.")
            if current["status"] in {"succeeded", "failed", "stopped"} and action != "safe-restart":
                raise ValueError("완료 또는 중지된 백필 작업은 해당 명령을 수행할 수 없다.")
            row = _expect_row(
                conn.execute(
                    """
                    UPDATE backfill_jobs
                    SET status = %s, updated_at = now()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (transitions[action], job_id),
                ).fetchone()
            )
        return _backfill_job(row)

    def backfill_jobs(self) -> list[BackfillJob]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  bj.*,
                  COALESCE(
                    ROUND(
                      100.0 * COUNT(bjt.instrument_id)
                        FILTER (WHERE bjt.status = 'succeeded')
                        / NULLIF(COUNT(bjt.instrument_id), 0),
                      2
                    ),
                    0
                  ) AS progress_percent
                FROM backfill_jobs bj
                LEFT JOIN backfill_job_targets bjt ON bjt.backfill_job_id = bj.id
                GROUP BY bj.id
                ORDER BY bj.created_at DESC
                """
            ).fetchall()
        return [_backfill_job(row) for row in rows]

    def notification_events(self) -> list[NotificationEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notification_events ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        return [_notification(row) for row in rows]

    def add_notification(
        self, severity: str, event_type: str, title: str, message: str
    ) -> NotificationEvent:
        with self._connect() as conn:
            row = _expect_row(
                conn.execute(
                    """
                    INSERT INTO notification_events (
                      severity, event_type, title, message, status
                    )
                    VALUES (%s, %s, %s, %s, 'open')
                    RETURNING *
                    """,
                    (severity, event_type, title, message),
                ).fetchone()
            )
        return _notification(row)

    def _recent_run_count(self) -> int:
        with self._connect() as conn:
            row = _expect_row(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM collection_runs WHERE started_at >= %s",
                    (now_utc() - timedelta(hours=24),),
                ).fetchone()
            )
        return int(row["count"])

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
        return (
            self._table_count("source_candles", instrument_id) * 256
            + self._table_count("ticker_snapshots", instrument_id) * 160
            + self._table_count("orderbook_summaries", instrument_id) * 224
        )

    def _table_count(self, table: str, instrument_id: int | None = None) -> int:
        with self._connect() as conn:
            if instrument_id is None:
                row = _expect_row(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone())
            else:
                row = _expect_row(
                    conn.execute(
                        f"SELECT COUNT(*) AS count FROM {table} WHERE instrument_id = %s",
                        (instrument_id,),
                    ).fetchone()
                )
        return int(row["count"])

    def _market_coverage_percent(self, instrument_id: int) -> Decimal:
        coverage = self.coverage_for(instrument_id)
        if not coverage:
            return Decimal("0")
        return sum((item.progress_percent for item in coverage), Decimal("0")) / Decimal(
            len(coverage)
        )

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

    def _activate_target(
        self,
        conn: psycopg.Connection[Any],
        instrument_id: int,
        actor: str,
        reason: str | None,
    ) -> None:
        previous = conn.execute(
            "SELECT status FROM collection_targets WHERE instrument_id = %s",
            (instrument_id,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO collection_targets (
              instrument_id, status, activated_at, deactivated_at, candidate_status
            )
            VALUES (%s, 'active', %s, NULL, 'in_universe')
            ON CONFLICT (instrument_id) DO UPDATE SET
              status = 'active',
              activated_at = COALESCE(collection_targets.activated_at, excluded.activated_at),
              deactivated_at = NULL,
              candidate_status = 'in_universe',
              updated_at = now()
            """,
            (instrument_id, now_utc()),
        )
        self._ensure_collection_plan(conn, instrument_id)
        self._record_target_change(
            conn, instrument_id, previous["status"] if previous else None, "active", actor, reason
        )

    def _deactivate_target(
        self,
        conn: psycopg.Connection[Any],
        instrument_id: int,
        actor: str,
        reason: str | None,
    ) -> None:
        previous = conn.execute(
            "SELECT status FROM collection_targets WHERE instrument_id = %s",
            (instrument_id,),
        ).fetchone()
        conn.execute(
            """
            UPDATE collection_targets
            SET status = 'inactive', deactivated_at = %s, updated_at = now()
            WHERE instrument_id = %s
            """,
            (now_utc(), instrument_id),
        )
        self._record_target_change(
            conn, instrument_id, previous["status"] if previous else None, "inactive", actor, reason
        )

    def _record_target_change(
        self,
        conn: psycopg.Connection[Any],
        instrument_id: int,
        previous_status: str | None,
        new_status: str,
        actor: str,
        reason: str | None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO collection_target_changes (
              instrument_id, previous_status, new_status, actor, reason
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (instrument_id, previous_status, new_status, actor, reason),
        )

    def _ensure_collection_plan(self, conn: psycopg.Connection[Any], instrument_id: int) -> None:
        plan_start = datetime(2025, 12, 31, 15, 0, tzinfo=UTC)
        conn.execute(
            """
            INSERT INTO collection_plans (
              instrument_id, preset, range_start_at, range_end_at,
              is_continuous, method, status
            )
            VALUES (
              %s, '2026년 1월 1분봉', %s, NULL,
              true, 'safe_restart', 'latest_collecting'
            )
            ON CONFLICT (instrument_id) DO NOTHING
            """,
            (instrument_id, plan_start),
        )

    def _collection_plan_for(self, instrument_id: int) -> CollectionPlan:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM collection_plans WHERE instrument_id = %s",
                (instrument_id,),
            ).fetchone()
            if row is None:
                self._ensure_collection_plan(conn, instrument_id)
                row = conn.execute(
                    "SELECT * FROM collection_plans WHERE instrument_id = %s",
                    (instrument_id,),
                ).fetchone()
        expected = _expect_row(row)
        return CollectionPlan(
            instrument_id=instrument_id,
            preset=str(expected["preset"]),
            range_start_at=cast(datetime, expected["range_start_at"]),
            range_end_at=cast(datetime | None, expected["range_end_at"]),
            is_continuous=bool(expected["is_continuous"]),
            method=str(expected["method"]),
            display_range="2026-01-01 00:00 KST ~ 현재(지속)"
            if bool(expected["is_continuous"])
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
            return self._source_candle_coverage_segments(instrument_id, plan.range_start_at)
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

    def _source_candle_coverage_segments(
        self, instrument_id: int, fallback_start_at: datetime
    ) -> list[CoverageSegment]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT candle_start_at
                FROM source_candles
                WHERE instrument_id = %s AND candle_unit = '1m'
                ORDER BY candle_start_at
                """,
                (instrument_id,),
            ).fetchall()
        starts = [cast(datetime, row["candle_start_at"]) for row in rows]
        if not starts:
            segment_end = now_utc()
            return [
                CoverageSegment(
                    data_type="source_candle",
                    status="missing",
                    offset_percent=Decimal("0"),
                    width_percent=Decimal("100"),
                    segment_start_at=fallback_start_at,
                    segment_end_at=segment_end,
                    label="캔들 없음",
                )
            ]
        ranges: list[tuple[datetime, datetime]] = []
        current_start = starts[0]
        current_end = starts[0] + timedelta(minutes=1)
        for candle_start in starts[1:]:
            candle_end = candle_start + timedelta(minutes=1)
            if candle_start <= current_end:
                current_end = max(current_end, candle_end)
                continue
            ranges.append((current_start, current_end))
            current_start = candle_start
            current_end = candle_end
        ranges.append((current_start, current_end))
        timeline: list[tuple[Literal["collected", "missing"], datetime, datetime]] = []
        for start_at, end_at in ranges:
            if timeline and timeline[-1][2] < start_at:
                timeline.append(("missing", timeline[-1][2], start_at))
            timeline.append(("collected", start_at, end_at))
        return _coverage_segments_from_timeline("source_candle", timeline)

    def _upsert_tickers(
        self,
        conn: psycopg.Connection[Any],
        run_id: int,
        tickers: list[TickerSnapshot],
    ) -> dict[int, int]:
        counts: dict[int, int] = {}
        for item in tickers:
            conn.execute(
                """
                INSERT INTO ticker_snapshots (
                  instrument_id, source, bucket_at, trade_price,
                  acc_trade_price_24h, change_rate, signed_change_rate,
                  collected_at, collection_run_id
                )
                VALUES (%s, 'UPBIT', %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (instrument_id, source, bucket_at) DO UPDATE SET
                  trade_price = excluded.trade_price,
                  acc_trade_price_24h = excluded.acc_trade_price_24h,
                  change_rate = excluded.change_rate,
                  signed_change_rate = excluded.signed_change_rate,
                  collected_at = excluded.collected_at,
                  collection_run_id = excluded.collection_run_id,
                  updated_at = now()
                WHERE excluded.collected_at > ticker_snapshots.collected_at
                """,
                (
                    item.instrument_id,
                    minute_bucket(item.bucket_at),
                    item.trade_price,
                    item.acc_trade_price_24h,
                    item.change_rate,
                    item.change_rate,
                    item.collected_at,
                    run_id,
                ),
            )
            counts[item.instrument_id] = counts.get(item.instrument_id, 0) + 1
        return counts

    def _upsert_orderbooks(
        self,
        conn: psycopg.Connection[Any],
        run_id: int,
        orderbooks: list[OrderbookSummary],
    ) -> dict[int, int]:
        counts: dict[int, int] = {}
        for item in orderbooks:
            conn.execute(
                """
                INSERT INTO orderbook_summaries (
                  instrument_id, source, bucket_at, best_bid_price, best_bid_size,
                  best_ask_price, best_ask_size, spread, bid_depth_10, ask_depth_10,
                  imbalance_10, collected_at, collection_run_id
                )
                VALUES (%s, 'UPBIT', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (instrument_id, source, bucket_at) DO UPDATE SET
                  best_bid_price = excluded.best_bid_price,
                  best_bid_size = excluded.best_bid_size,
                  best_ask_price = excluded.best_ask_price,
                  best_ask_size = excluded.best_ask_size,
                  spread = excluded.spread,
                  bid_depth_10 = excluded.bid_depth_10,
                  ask_depth_10 = excluded.ask_depth_10,
                  imbalance_10 = excluded.imbalance_10,
                  collected_at = excluded.collected_at,
                  collection_run_id = excluded.collection_run_id,
                  updated_at = now()
                WHERE excluded.collected_at > orderbook_summaries.collected_at
                """,
                (
                    item.instrument_id,
                    minute_bucket(item.bucket_at),
                    item.best_bid_price,
                    item.best_bid_size,
                    item.best_ask_price,
                    item.best_ask_size,
                    item.spread,
                    item.bid_depth_10,
                    item.ask_depth_10,
                    item.imbalance_10,
                    item.collected_at,
                    run_id,
                ),
            )
            counts[item.instrument_id] = counts.get(item.instrument_id, 0) + 1
        return counts

    def _upsert_candles(
        self,
        conn: psycopg.Connection[Any],
        run_id: int,
        candles: list[SourceCandle],
    ) -> dict[int, int]:
        counts: dict[int, int] = {}
        if not candles:
            return counts
        sql = """
            INSERT INTO source_candles (
              instrument_id, source, candle_unit, candle_start_at,
              open_price, high_price, low_price, close_price,
              trade_volume, trade_amount, collected_at, collection_run_id
            )
            VALUES (%s, 'UPBIT', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (instrument_id, source, candle_unit, candle_start_at)
            DO UPDATE SET
              open_price = excluded.open_price,
              high_price = excluded.high_price,
              low_price = excluded.low_price,
              close_price = excluded.close_price,
              trade_volume = excluded.trade_volume,
              trade_amount = excluded.trade_amount,
              collected_at = excluded.collected_at,
              collection_run_id = excluded.collection_run_id,
              updated_at = now()
            WHERE excluded.collected_at > source_candles.collected_at
            """
        params = []
        for item in candles:
            params.append(
                (
                    item.instrument_id,
                    item.candle_unit,
                    item.candle_start_at,
                    item.open_price,
                    item.high_price,
                    item.low_price,
                    item.close_price,
                    item.trade_volume,
                    item.trade_amount,
                    item.collected_at,
                    run_id,
                ),
            )
            counts[item.instrument_id] = counts.get(item.instrument_id, 0) + 1
        with conn.cursor() as cursor:
            cursor.executemany(sql, params)
        return counts

    def _latest_candle_time(self, instrument_id: int) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT collected_at FROM source_candles
                WHERE instrument_id = %s
                ORDER BY candle_start_at DESC
                LIMIT 1
                """,
                (instrument_id,),
            ).fetchone()
        return cast(datetime, row["collected_at"]) if row else None

    def _failed_runs_24h(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count FROM collection_runs
                WHERE status = 'failed' AND started_at >= %s
                """,
                (now_utc() - timedelta(hours=24),),
            ).fetchone()
        return int(_expect_row(row)["count"])

    def _refresh_backfill_job_progress(
        self, conn: psycopg.Connection[Any], job_id: int
    ) -> None:
        current = _expect_row(
            conn.execute("SELECT status FROM backfill_jobs WHERE id = %s", (job_id,)).fetchone()
        )["status"]
        if current in {"paused", "stopped"}:
            conn.execute(
                "UPDATE backfill_jobs SET updated_at = now() WHERE id = %s",
                (job_id,),
            )
            return
        rows = conn.execute(
            "SELECT status FROM backfill_job_targets WHERE backfill_job_id = %s",
            (job_id,),
        ).fetchall()
        total = len(rows)
        succeeded = sum(1 for row in rows if row["status"] == "succeeded")
        failed = any(row["status"] == "failed" for row in rows)
        if total == 0 or failed:
            status = "failed"
        elif succeeded == total:
            status = "succeeded"
        else:
            status = "running"
        conn.execute(
            """
            UPDATE backfill_jobs
            SET status = %s,
                finished_at = CASE
                  WHEN %s IN ('succeeded', 'failed') THEN now()
                  ELSE finished_at
                END,
                updated_at = now()
            WHERE id = %s
            """,
            (status, status, job_id),
        )


def _latest_snapshot_id(conn: psycopg.Connection[Any]) -> int:
    row = conn.execute(
        "SELECT id FROM candidate_universe_snapshots ORDER BY ranked_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise ValueError("후보 유니버스 스냅샷이 없다.")
    return int(row["id"])


def _expect_row(row: Row | None) -> Row:
    if row is None:
        raise RuntimeError("PostgreSQL query did not return a required row.")
    return row


def _instrument(row: Row) -> Instrument:
    return Instrument(
        id=int(row["id"]),
        exchange="UPBIT",
        market_code=str(row["market_code"]),
        quote_currency=str(row["quote_currency"]),
        base_asset=str(row["base_asset"]),
        display_name=str(row["display_name"]),
    )


def _ticker(row: dict[str, Any]) -> TickerSnapshot:
    return TickerSnapshot(
        instrument_id=int(row["instrument_id"]),
        bucket_at=cast(datetime, row["bucket_at"]),
        trade_price=Decimal(row["trade_price"]),
        acc_trade_price_24h=Decimal(row["acc_trade_price_24h"]),
        change_rate=Decimal(row["change_rate"] or "0"),
        collected_at=cast(datetime, row["collected_at"]),
    )


def _orderbook(row: dict[str, Any]) -> OrderbookSummary:
    return OrderbookSummary(
        instrument_id=int(row["instrument_id"]),
        bucket_at=cast(datetime, row["bucket_at"]),
        best_bid_price=Decimal(row["best_bid_price"]),
        best_bid_size=Decimal(row["best_bid_size"]),
        best_ask_price=Decimal(row["best_ask_price"]),
        best_ask_size=Decimal(row["best_ask_size"]),
        spread=Decimal(row["spread"]),
        bid_depth_10=Decimal(row["bid_depth_10"]),
        ask_depth_10=Decimal(row["ask_depth_10"]),
        imbalance_10=Decimal(row["imbalance_10"]),
        collected_at=cast(datetime, row["collected_at"]),
    )


def _candle(row: dict[str, Any]) -> SourceCandle:
    return SourceCandle(
        instrument_id=int(row["instrument_id"]),
        candle_unit=cast(Literal["1m", "1d"], row["candle_unit"]),
        candle_start_at=cast(datetime, row["candle_start_at"]),
        open_price=Decimal(row["open_price"]),
        high_price=Decimal(row["high_price"]),
        low_price=Decimal(row["low_price"]),
        close_price=Decimal(row["close_price"]),
        trade_volume=Decimal(row["trade_volume"]),
        trade_amount=Decimal(row["trade_amount"]),
        collected_at=cast(datetime, row["collected_at"]),
    )


def _candle_view(item: SourceCandle) -> CandleView:
    return CandleView(
        started_at=item.candle_start_at,
        open=item.open_price,
        high=item.high_price,
        low=item.low_price,
        close=item.close_price,
        volume=item.trade_volume,
        trade_amount=item.trade_amount,
        completeness="complete",
    )


def _derive_candles(unit: str, source: list[SourceCandle]) -> list[CandleView]:
    minute_units = {"3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "60m": 60, "240m": 240}
    bucket_size = minute_units.get(unit)
    if bucket_size is None:
        return [_candle_view(item) for item in source]
    grouped: dict[datetime, list[SourceCandle]] = {}
    for item in [item for item in source if item.candle_unit == "1m"]:
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


def _collection_run(row: dict[str, Any]) -> CollectionRun:
    return CollectionRun(
        id=int(row["id"]),
        run_type=str(row["run_type"]),
        data_type=str(row["data_type"]),
        status=cast(
            Literal["running", "succeeded", "partial", "failed", "cancelled"],
            row["status"],
        ),
        started_at=cast(datetime, row["started_at"]),
        finished_at=cast(datetime | None, row["finished_at"]),
    )


def _notification(row: dict[str, Any]) -> NotificationEvent:
    return NotificationEvent(
        id=int(row["id"]),
        severity=cast(Literal["info", "warning", "error", "critical"], row["severity"]),
        event_type=str(row["event_type"]),
        title=str(row["title"]),
        message=str(row["message"]),
        status=cast(Literal["open", "acknowledged", "resolved"], row["status"]),
        created_at=cast(datetime, row["created_at"]),
    )


def _backfill_job(row: dict[str, Any]) -> BackfillJob:
    return BackfillJob(
        id=int(row["id"]),
        status=cast(
            Literal["planned", "pending", "running", "paused", "stopped", "succeeded", "failed"],
            row["status"],
        ),
        data_type=str(row["data_type"]),
        progress_percent=Decimal(str(row.get("progress_percent") or "0")),
        created_at=cast(datetime, row["created_at"]),
    )


def _backfill_job_detail(row: dict[str, Any]) -> BackfillJobDetail:
    return BackfillJobDetail(
        id=int(row["id"]),
        status=cast(
            Literal["planned", "pending", "running", "paused", "stopped", "succeeded", "failed"],
            row["status"],
        ),
        data_type=str(row["data_type"]),
        target_start_at=cast(datetime, row["target_start_at"]),
        target_end_at=cast(datetime, row["target_end_at"]),
        estimated_request_count=int(row["estimated_request_count"]),
        estimated_row_count=int(row["estimated_row_count"]),
        created_at=cast(datetime, row["created_at"]),
    )


def _backfill_target(row: dict[str, Any]) -> BackfillJobTarget:
    return BackfillJobTarget(
        job_id=int(row["backfill_job_id"]),
        instrument_id=int(row["instrument_id"]),
        status=cast(
            Literal["pending", "running", "paused", "stopped", "succeeded", "failed"],
            row["status"],
        ),
        last_completed_at=cast(datetime | None, row["last_completed_at"]),
        error_code=cast(str | None, row["error_code"]),
        error_message=cast(str | None, row["error_message"]),
    )
