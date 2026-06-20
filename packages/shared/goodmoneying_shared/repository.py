from __future__ import annotations

from datetime import datetime
from typing import Protocol

from goodmoneying_shared.models import (
    BackfillJob,
    BackfillJobDetail,
    BackfillJobTarget,
    BackfillPlan,
    CandidateUniverseEntry,
    CandleView,
    CollectionDashboardTarget,
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

    def dashboard_summary(self) -> DashboardSummary: ...

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

    def ticker_snapshots(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> list[TickerSnapshot]: ...

    def orderbook_summaries(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> list[OrderbookSummary]: ...

    def collection_runs(self, limit: int) -> list[CollectionRun]: ...

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

    def backfill_jobs(self) -> list[BackfillJob]: ...

    def notification_events(self) -> list[NotificationEvent]: ...
