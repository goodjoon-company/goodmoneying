from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

Exchange = Literal["UPBIT"]
CandidateStatus = Literal["in_universe", "out_of_universe"]
QualityStatus = Literal["normal", "warning", "incident", "backfilling"]
CollectionOverallStatus = Literal["latest_collecting", "collecting", "warning", "incident"]
CoverageSegmentStatus = Literal["collected", "missing", "collecting", "future"]
BackfillStatus = Literal[
    "planned", "pending", "running", "paused", "stopped", "succeeded", "failed"
]
CollectionRunStatus = Literal["running", "succeeded", "partial", "failed", "cancelled"]


def decimal_string(value: Decimal | int | str | None) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)), "f")


@dataclass(frozen=True)
class Instrument:
    id: int
    exchange: Exchange
    market_code: str
    quote_currency: str
    base_asset: str
    display_name: str


@dataclass(frozen=True)
class CandidateUniverseEntry:
    instrument: Instrument
    rank: int
    acc_trade_price_24h: Decimal
    selected: bool
    candidate_status: CandidateStatus


@dataclass(frozen=True)
class TickerSnapshot:
    instrument_id: int
    bucket_at: datetime
    trade_price: Decimal
    acc_trade_price_24h: Decimal
    change_rate: Decimal
    collected_at: datetime


@dataclass(frozen=True)
class OrderbookSummary:
    instrument_id: int
    bucket_at: datetime
    best_bid_price: Decimal
    best_bid_size: Decimal
    best_ask_price: Decimal
    best_ask_size: Decimal
    spread: Decimal
    bid_depth_10: Decimal
    ask_depth_10: Decimal
    imbalance_10: Decimal
    collected_at: datetime


@dataclass(frozen=True)
class SourceCandle:
    instrument_id: int
    candle_unit: Literal["1m", "1d"]
    candle_start_at: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    trade_volume: Decimal
    trade_amount: Decimal
    collected_at: datetime


@dataclass(frozen=True)
class CandleView:
    started_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_amount: Decimal
    completeness: Literal["complete", "partial", "empty"]


@dataclass(frozen=True)
class CoverageStatus:
    instrument_id: int
    data_type: Literal["source_candle", "ticker_snapshot", "orderbook_summary"]
    status: QualityStatus
    progress_percent: Decimal
    last_successful_at: datetime


@dataclass(frozen=True)
class CollectionPlan:
    instrument_id: int
    preset: str
    range_start_at: datetime
    range_end_at: datetime | None
    is_continuous: bool
    method: str
    display_range: str
    range_time_zone: Literal["KST", "UTC"]
    progress_basis: str


@dataclass(frozen=True)
class CollectionDataStatus:
    data_type: Literal["source_candle", "ticker_snapshot", "orderbook_summary"]
    label: str
    status: QualityStatus
    status_label: str
    last_successful_at: datetime
    progress_percent: Decimal
    missing_segment_count: int


@dataclass(frozen=True)
class CoverageSegment:
    data_type: Literal["source_candle", "ticker_snapshot", "orderbook_summary"]
    status: CoverageSegmentStatus
    offset_percent: Decimal
    width_percent: Decimal
    segment_start_at: datetime
    segment_end_at: datetime
    label: str


@dataclass(frozen=True)
class CollectionDashboardTarget:
    instrument: Instrument
    overall_status: CollectionOverallStatus
    overall_status_label: str
    plan: CollectionPlan
    data_statuses: list[CollectionDataStatus]
    coverage_segments: list[CoverageSegment]


@dataclass(frozen=True)
class MarketListRow:
    instrument: Instrument
    trade_price: Decimal
    acc_trade_price_24h: Decimal
    acc_trade_price_24h_display: str
    change_rate: Decimal
    ticker_collected_at: datetime
    orderbook_collected_at: datetime
    quality_status: Literal["normal", "warning", "incident"]


@dataclass(frozen=True)
class CollectionRun:
    id: int
    run_type: str
    data_type: str
    status: CollectionRunStatus
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class NotificationEvent:
    id: int
    severity: Literal["info", "warning", "error", "critical"]
    event_type: str
    title: str
    message: str
    status: Literal["open", "acknowledged", "resolved"]
    created_at: datetime


@dataclass(frozen=True)
class BackfillPlan:
    plan_id: str
    data_type: Literal["source_candle"]
    target_start_at: datetime
    target_end_at: datetime
    estimated_request_count: int
    estimated_row_count: int
    estimated_storage_bytes: int
    targets: list[int]


@dataclass(frozen=True)
class BackfillJob:
    id: int
    status: BackfillStatus
    data_type: str
    progress_percent: Decimal
    created_at: datetime


@dataclass(frozen=True)
class BackfillJobDetail:
    id: int
    status: BackfillStatus
    data_type: str
    target_start_at: datetime
    target_end_at: datetime
    estimated_request_count: int
    estimated_row_count: int
    created_at: datetime


@dataclass(frozen=True)
class BackfillJobTarget:
    job_id: int
    instrument_id: int
    status: Literal["pending", "running", "paused", "stopped", "succeeded", "failed"]
    last_completed_at: datetime | None
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class DashboardSummary:
    status: Literal["normal", "warning", "incident"]
    active_targets: int
    failed_runs_24h: int
    delayed_targets: int
    missing_ranges_open: int
    coverage: list[CoverageStatus]
    targets: list[CollectionDashboardTarget]
    alerts: list[NotificationEvent]
    refreshed_at: datetime
