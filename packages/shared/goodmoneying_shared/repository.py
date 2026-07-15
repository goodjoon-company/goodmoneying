from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from goodmoneying_shared.models import (
    AuditLogSummary,
    BackfillJob,
    BackfillJobDetail,
    BackfillJobTarget,
    BackfillPlan,
    CandidateUniverseEntry,
    CandleAggregationJob,
    CandleAggregationJobTarget,
    CandleView,
    CollectionActivityBucket,
    CollectionDashboardTarget,
    CollectionRun,
    CollectionWorkerHeartbeatStatus,
    CollectionWorkerRuntimeStatus,
    CollectionWorkerStatusSummary,
    CollectionWorkerType,
    CoverageSegment,
    CoverageStatus,
    DashboardSummary,
    Instrument,
    MarketListRow,
    MissingRangeSummary,
    NotificationEvent,
    OperationsTrendPoint,
    OrderbookSummary,
    RealtimeCollectionHeatmapRow,
    SourceCandle,
    StorageBreakdownItem,
    TickerSnapshot,
    TradeEvent,
    TradeSummary,
)


class OperationsRepository(Protocol):
    def upsert_instrument(self, market_code: str, display_name: str) -> Instrument: ...

    def refresh_candidate_universe(
        self, entries: list[tuple[str, str, str]]
    ) -> list[CandidateUniverseEntry]: ...

    def ensure_default_active_targets(self, limit: int = 50) -> list[Instrument]: ...

    def update_active_targets(
        self, instrument_ids: list[int], reason: str | None
    ) -> list[Instrument]: ...

    def list_candidate_universe(self) -> tuple[datetime, list[CandidateUniverseEntry]]: ...

    def list_active_targets(self) -> list[Instrument]: ...

    def record_incremental_collection(
        self,
        tickers: list[TickerSnapshot],
        orderbooks: list[OrderbookSummary],
        candles: list[SourceCandle],
    ) -> CollectionRun: ...

    def record_trade_events(self, trades: list[TradeEvent]) -> int: ...

    def dashboard_summary(self) -> DashboardSummary: ...

    def dashboard_coverage(self) -> list[CoverageStatus]: ...

    def dashboard_collection_activity(self) -> list[CollectionActivityBucket]: ...

    def dashboard_realtime_heatmap(self) -> list[RealtimeCollectionHeatmapRow]: ...

    def dashboard_storage_breakdown(self) -> list[StorageBreakdownItem]: ...

    def dashboard_operations_trend(self) -> list[OperationsTrendPoint]: ...

    def dashboard_missing_ranges(self) -> list[MissingRangeSummary]: ...

    def dashboard_audit_log_summary(self) -> AuditLogSummary: ...

    def dashboard_worker_status(self) -> CollectionWorkerStatusSummary: ...

    def collection_dashboard_targets(
        self, include_segments: bool = False
    ) -> list[CollectionDashboardTarget]: ...

    def coverage_segments_for(self, instrument_id: int) -> list[CoverageSegment]: ...

    def market_list(self) -> list[MarketListRow]: ...

    def get_instrument(self, instrument_id: int) -> Instrument | None: ...

    def latest_ticker(self, instrument_id: int) -> TickerSnapshot | None: ...

    def latest_orderbook(self, instrument_id: int) -> OrderbookSummary | None: ...

    def coverage_for(self, instrument_id: int) -> list[CoverageStatus]: ...

    def candles(
        self, instrument_id: int, unit: str, start_at: datetime, end_at: datetime
    ) -> list[CandleView]: ...

    def materialize_candle_rollups(
        self,
        instrument_id: int,
        unit: str,
        on_progress: Callable[[], None] | None = None,
    ) -> int: ...

    def candle_rollups(
        self, instrument_id: int, unit: str, start_at: datetime, end_at: datetime
    ) -> list[CandleView]: ...

    def schedule_candle_aggregation(self) -> CandleAggregationJob | None: ...

    def claim_next_candle_aggregation_job(self) -> CandleAggregationJob | None: ...

    def candle_aggregation_job_targets(self, job_id: int) -> list[CandleAggregationJobTarget]: ...

    def mark_candle_aggregation_target(
        self, job_id: int, instrument_id: int, unit: str, status: str, rows_written: int
    ) -> None: ...

    def latest_candle_aggregation_job(self) -> CandleAggregationJob | None: ...

    def ticker_snapshots(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> list[TickerSnapshot]: ...

    def orderbook_summaries(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> list[OrderbookSummary]: ...

    def trade_summary(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> TradeSummary: ...

    def collection_runs(self, limit: int) -> list[CollectionRun]: ...

    def record_collection_worker_heartbeat(
        self,
        worker_type: CollectionWorkerType,
        status: CollectionWorkerHeartbeatStatus,
        error_message: str | None = None,
    ) -> None: ...

    def collection_worker_runtime_status(
        self, worker_type: CollectionWorkerType
    ) -> CollectionWorkerRuntimeStatus: ...

    def record_collection_run_failure(
        self,
        run_type: str,
        data_type: str,
        started_at: datetime,
        error_code: str,
        error_message: str,
    ) -> CollectionRun: ...

    def create_backfill_plan(
        self,
        data_type: str,
        target_start_at: datetime,
        target_end_at: datetime,
        instrument_ids: list[int],
    ) -> BackfillPlan: ...

    def approve_backfill_job(self, plan_id: str) -> BackfillJob: ...

    def claim_next_backfill_job(self) -> BackfillJobDetail | None: ...

    def backfill_job_targets(self, job_id: int) -> list[BackfillJobTarget]: ...

    def record_backfill_candles(
        self, job_id: int, instrument_id: int, candles: list[SourceCandle]
    ) -> int: ...

    def record_backfill_target_progress(
        self,
        job_id: int,
        instrument_id: int,
        processed_missing_range_count: int,
        estimated_missing_range_count: int,
        rows_written_count: int,
        last_completed_at: datetime | None,
    ) -> None: ...

    def mark_backfill_target(
        self,
        job_id: int,
        instrument_id: int,
        status: str,
        last_completed_at: datetime | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None: ...

    def control_backfill_job(self, job_id: int, action: str) -> BackfillJob: ...

    def delete_backfill_job(self, job_id: int) -> None: ...

    def backfill_jobs(self) -> list[BackfillJob]: ...

    def notification_events(self) -> list[NotificationEvent]: ...
