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
    "planned",
    "pending",
    "leased",
    "running",
    "retry_wait",
    "paused",
    "stopped",
    "succeeded",
    "failed",
    "dead_letter",
    "cancelled",
]
CollectionRunStatus = Literal["running", "succeeded", "partial", "failed", "cancelled"]
CollectionDataType = Literal["source_candle", "ticker_snapshot", "orderbook_summary"]
CollectionRowsByType = dict[CollectionDataType, int]
CollectionWorkerType = Literal["realtime_collection", "backfill_collection", "candle_aggregation"]
CollectionWorkerHeartbeatStatus = Literal["running", "gated", "failed"]
CollectionWorkerStatus = Literal["running", "gated", "stale", "failed"]
TradeDirection = Literal["ASK", "BID"]
TradeFrequencyStatus = Literal["red", "orange", "yellow", "blue", "green"]


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
    favorite_order: int | None = None


@dataclass(frozen=True, init=False)
class TickerSnapshot:
    instrument_id: int
    bucket_at: datetime
    trade_price: Decimal
    acc_trade_price_24h: Decimal
    change_rate: Decimal
    occurred_at: datetime
    received_at: datetime

    def __init__(
        self,
        instrument_id: int,
        bucket_at: datetime,
        trade_price: Decimal,
        acc_trade_price_24h: Decimal,
        change_rate: Decimal,
        *,
        occurred_at: datetime | None = None,
        received_at: datetime | None = None,
        collected_at: datetime | None = None,
    ) -> None:
        resolved_received_at = received_at or collected_at
        if resolved_received_at is None:
            raise TypeError("TickerSnapshot에는 received_at이 필요하다.")
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "bucket_at", bucket_at)
        object.__setattr__(self, "trade_price", trade_price)
        object.__setattr__(self, "acc_trade_price_24h", acc_trade_price_24h)
        object.__setattr__(self, "change_rate", change_rate)
        object.__setattr__(self, "occurred_at", occurred_at or bucket_at)
        object.__setattr__(self, "received_at", resolved_received_at)

    @property
    def collected_at(self) -> datetime:
        """기존 호출자 호환 별칭이며 의미는 로컬 수신 시각이다."""

        return self.received_at


@dataclass(frozen=True, init=False)
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
    occurred_at: datetime
    received_at: datetime

    def __init__(
        self,
        instrument_id: int,
        bucket_at: datetime,
        best_bid_price: Decimal,
        best_bid_size: Decimal,
        best_ask_price: Decimal,
        best_ask_size: Decimal,
        spread: Decimal,
        bid_depth_10: Decimal,
        ask_depth_10: Decimal,
        imbalance_10: Decimal,
        *,
        occurred_at: datetime | None = None,
        received_at: datetime | None = None,
        collected_at: datetime | None = None,
    ) -> None:
        resolved_received_at = received_at or collected_at
        if resolved_received_at is None:
            raise TypeError("OrderbookSummary에는 received_at이 필요하다.")
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "bucket_at", bucket_at)
        object.__setattr__(self, "best_bid_price", best_bid_price)
        object.__setattr__(self, "best_bid_size", best_bid_size)
        object.__setattr__(self, "best_ask_price", best_ask_price)
        object.__setattr__(self, "best_ask_size", best_ask_size)
        object.__setattr__(self, "spread", spread)
        object.__setattr__(self, "bid_depth_10", bid_depth_10)
        object.__setattr__(self, "ask_depth_10", ask_depth_10)
        object.__setattr__(self, "imbalance_10", imbalance_10)
        object.__setattr__(self, "occurred_at", occurred_at or bucket_at)
        object.__setattr__(self, "received_at", resolved_received_at)

    @property
    def collected_at(self) -> datetime:
        """기존 호출자 호환 별칭이며 의미는 로컬 수신 시각이다."""

        return self.received_at


@dataclass(frozen=True)
class SourceReceipt:
    data_type: str
    instrument_id: int
    connection_id: str
    frame_sequence: int
    occurred_at: datetime
    received_at: datetime
    payload_checksum: str
    raw_payload: dict[str, object]
    fetch_manifest_id: int | None = None


@dataclass(frozen=True)
class OrderbookSnapshotLevel:
    level_index: int
    ask_price: Decimal
    ask_size: Decimal
    bid_price: Decimal
    bid_size: Decimal


@dataclass(frozen=True)
class OrderbookSnapshot:
    instrument_id: int
    source: Exchange
    occurred_at: datetime
    received_at: datetime
    total_ask_size: Decimal
    total_bid_size: Decimal
    level_count: int
    level: Decimal | None
    stream_type: str | None
    payload_checksum: str
    levels: tuple[OrderbookSnapshotLevel, ...]
    fetch_manifest_id: int | None = None


@dataclass(frozen=True)
class RealtimeSourceFrame:
    receipt: SourceReceipt
    snapshot: OrderbookSnapshot | None = None
    summary: OrderbookSummary | None = None


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
    revision_id: int | None = None
    input_content_hash: str | None = None
    knowledge_at: datetime | None = None


@dataclass(frozen=True)
class SourceCandleRevisionCreated:
    """같은 트랜잭션에서 새로 생성된 원천 캔들 개정만 나타낸다."""

    id: int
    revision_number: int
    market_id: int
    candle_start_at: datetime
    knowledge_at: datetime
    input_content_hash: str


@dataclass(frozen=True)
class FetchEvidence:
    endpoint: str
    request_parameters: dict[str, str | int]
    requested_at: datetime
    responded_at: datetime
    response_status: int | None
    response_payload: object | None
    error_type: str | None = None
    error_message: str | None = None
    requested_range_start_at: datetime | None = None
    requested_range_end_at: datetime | None = None


@dataclass(frozen=True)
class FetchedCandlePage:
    rows: list[dict[str, str]]
    evidence: FetchEvidence


@dataclass(frozen=True)
class TradeEvent:
    instrument_id: int
    sequential_id: int
    trade_timestamp_at: datetime
    trade_price: Decimal
    trade_volume: Decimal
    trade_amount: Decimal
    ask_bid: TradeDirection
    collected_at: datetime


@dataclass(frozen=True)
class TradeSummary:
    trade_count: int
    buy_volume: Decimal
    sell_volume: Decimal
    last_trade_at: datetime | None


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
    calculation_version: str = "candle-rollup-v2"
    source_as_of: datetime | None = None
    knowledge_at: datetime | None = None
    input_content_hash: str = ""
    quality: Literal["available", "no_trade", "missing", "unavailable", "unverified"] = "available"
    input_revision_ids: tuple[int, ...] = ()
    rollup_id: int | None = None
    source_revision_through_id: int = 0
    quality_event_through_id: int | None = None
    coverage_snapshot_hash: str = ""


@dataclass(frozen=True)
class CandleAggregationJob:
    id: int
    status: Literal["pending", "running", "succeeded", "failed"]
    progress_percent: Decimal
    total_target_count: int
    completed_target_count: int
    running_target_count: int
    pending_target_count: int
    failed_target_count: int
    created_at: datetime


@dataclass(frozen=True)
class CandleAggregationJobTarget:
    job_id: int
    instrument_id: int
    candle_unit: str
    status: Literal["pending", "running", "succeeded", "failed"]
    rows_written: int


@dataclass(frozen=True)
class CandleRollupRecomputeJob:
    id: int
    invalidation_id: int
    status: Literal[
        "pending", "running", "retry_wait", "succeeded", "dead_letter", "cancelled"
    ]
    market_id: int
    instrument_id: int
    candle_unit: str
    calculation_version: str
    range_start_at: datetime
    range_end_at: datetime
    source_revision_through_id: int
    quality_event_through_id: int | None
    attempt_count: int
    max_attempts: int
    next_retry_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    rows_written: int
    last_error_code: str | None
    dead_letter_reason: str | None


@dataclass(frozen=True)
class CoverageStatus:
    instrument_id: int
    data_type: Literal["source_candle", "ticker_snapshot", "orderbook_summary"]
    status: QualityStatus
    progress_percent: Decimal
    last_successful_at: datetime
    missing_segment_count: int = 0


@dataclass(frozen=True)
class CollectionPlan:
    instrument_id: int
    preset: str
    range_start_at: datetime
    range_end_at: datetime | None
    is_continuous: bool
    method: str
    display_range: str
    range_time_zone: Literal["KST"]
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
    stored_row_count: int


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
    change_rate: Decimal
    acc_trade_price_24h_display: str
    ticker_collected_at: datetime
    coverage_percent: Decimal
    storage_row_count: int
    storage_bytes_display: str
    collected_start_at: datetime | None
    collected_end_at: datetime | None


@dataclass(frozen=True)
class MarketListRow:
    instrument: Instrument
    asset_type: Literal["coin"]
    is_favorite: bool
    favorite_order: int | None
    trade_price: Decimal | None
    price_currency: str
    acc_trade_price_24h: Decimal
    acc_trade_price_24h_display: str
    trade_amount_currency: str
    change_rate: Decimal | None
    change_rate_basis: str
    ticker_collected_at: datetime | None
    orderbook_collected_at: datetime | None
    quality_status: Literal["normal", "warning", "incident"]
    coverage_percent: Decimal
    candle_coverage_start_at: datetime | None
    candle_coverage_end_at: datetime | None
    candle_coverage_current_at: datetime
    one_minute_candle_count: int
    storage_bytes: int
    storage_row_count: int
    storage_bytes_display: str


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
class HealthCheck:
    title: str
    status: Literal["normal", "warning", "incident"]
    status_label: str
    detail: str


@dataclass(frozen=True)
class CollectionActivityBucket:
    bucket_start_at: datetime
    run_count: int
    result_count: int
    status: Literal["none", "low", "collecting", "high"]


@dataclass(frozen=True)
class RealtimeCollectionHeatmapBucket:
    bucket_start_at: datetime
    trade_count: int
    average_trades_per_minute: Decimal
    trade_strength: Decimal
    trade_volume: Decimal
    trade_amount: Decimal
    status: TradeFrequencyStatus


@dataclass(frozen=True)
class RealtimeCollectionHeatmapRow:
    instrument: Instrument
    instrument_display_name: str
    hourly_buckets: list[RealtimeCollectionHeatmapBucket]


@dataclass(frozen=True)
class StorageBreakdownItem:
    data_type: Literal["source_candle", "ticker_snapshot", "orderbook_summary"]
    label: str
    row_count: int
    bytes: int
    bytes_display: str
    share_percent: Decimal


@dataclass(frozen=True)
class OperationsTrendPoint:
    bucket_date: datetime
    coverage_percent: Decimal
    storage_bytes: int
    warning_targets: int
    incident_targets: int


@dataclass(frozen=True)
class MissingRangeSummary:
    instrument: Instrument
    missing_segment_count: int
    coverage_percent: Decimal
    last_successful_at: datetime


@dataclass(frozen=True)
class AuditLogSummary:
    target_change_count_24h: int
    backfill_change_count_24h: int
    latest_change_at: datetime | None
    latest_change_label: str


@dataclass(frozen=True)
class CollectionWorkerError:
    occurred_at: datetime
    code: str
    message: str


@dataclass(frozen=True)
class CollectionWorkerRuntimeStatus:
    status: CollectionWorkerStatus
    status_label: str
    status_detail: str
    last_heartbeat_at: datetime | None


@dataclass(frozen=True)
class CollectionWorkerDiagnostic:
    label: str
    value: str
    detail: str


@dataclass(frozen=True)
class RealtimeWorkerStatus:
    status: CollectionWorkerStatus
    status_label: str
    status_detail: str
    last_heartbeat_at: datetime | None
    last_collected_at: datetime | None
    collected_row_count_24h: int
    error_count_24h: int
    failure_rate_24h: Decimal
    diagnostics: list[CollectionWorkerDiagnostic]
    recent_errors: list[CollectionWorkerError]


@dataclass(frozen=True)
class BackfillWorkerStatus:
    status: CollectionWorkerStatus
    status_label: str
    status_detail: str
    last_heartbeat_at: datetime | None
    last_collected_at: datetime | None
    total_error_count: int
    failure_rate_all: Decimal
    running_target_count: int
    total_target_count: int
    queued_job_count: int
    queued_target_count: int
    diagnostics: list[CollectionWorkerDiagnostic]
    recent_errors: list[CollectionWorkerError]


@dataclass(frozen=True)
class CollectionWorkerStatusSummary:
    realtime: RealtimeWorkerStatus
    backfill: BackfillWorkerStatus


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
    estimated_request_count: int
    total_target_count: int
    completed_target_count: int
    running_target_index: int | None
    current_target: Instrument | None
    current_target_backfill_row_count: int
    processed_missing_range_count: int
    estimated_missing_range_count: int
    target_start_at: datetime
    target_end_at: datetime
    targets: list[Instrument]
    created_at: datetime
    attempt_count: int = 0
    max_attempts: int = 5
    next_retry_at: datetime | None = None
    last_error_code: str | None = None
    dead_letter_reason: str | None = None


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
    active_target_limit: int
    normal_targets: int
    warning_targets: int
    incident_targets: int
    failed_runs_24h: int
    failure_rate_24h: Decimal
    delayed_targets: int
    missing_ranges_open: int
    storage_bytes_today: int
    storage_bytes_today_display: str
    storage_rows_today: int
    realtime_rows_last_minute: int
    backfill_rows_last_minute: int
    recent_request_count: int
    coverage: list[CoverageStatus]
    targets: list[CollectionDashboardTarget]
    alerts: list[NotificationEvent]
    health_checks: list[HealthCheck]
    collection_activity: list[CollectionActivityBucket]
    realtime_collection_heatmap: list[RealtimeCollectionHeatmapRow]
    storage_breakdown: list[StorageBreakdownItem]
    operations_trend: list[OperationsTrendPoint]
    missing_range_top: list[MissingRangeSummary]
    audit_log_summary: AuditLogSummary
    worker_status: CollectionWorkerStatusSummary
    refreshed_at: datetime
