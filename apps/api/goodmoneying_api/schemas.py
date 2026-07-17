from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ErrorResponse(BaseModel):
    code: str
    message: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    checkedAt: datetime


class CoverageCountsResponse(BaseModel):
    available: int
    no_trade: int
    missing: int
    unavailable: int
    unverified: int


class DataFoundationSummaryResponse(BaseModel):
    marketCount: int
    krwMarketCount: int
    activeTargetCount: int
    pendingBackfillJobCount: int
    desiredSubscriptionCount: int
    coverageCounts: CoverageCountsResponse


class MarketCollectionPolicyResponse(BaseModel):
    startAt: datetime
    dataTypes: list[
        Literal[
            "source_candle",
            "trade_event",
            "orderbook_snapshot",
            "ticker_snapshot",
        ]
    ]
    candleUnit: Literal["1m"]
    retentionDays: int | None
    priority: int
    continuous: bool


class DataFoundationMarketResponse(BaseModel):
    marketCode: str
    koreanName: str
    englishName: str
    quoteCurrency: str
    tradingStatus: Literal["active", "inactive", "delisted", "unknown"]
    marketWarning: str
    targetStatus: Literal["active", "paused", "excluded", "not_targeted"]
    activeDataTypeCount: int
    totalDataTypeCount: int
    coverageCounts: CoverageCountsResponse
    collectionPolicy: MarketCollectionPolicyResponse | None


class DataFoundationResponse(BaseModel):
    timeZone: Literal["UTC"]
    policyStartAt: datetime
    summary: DataFoundationSummaryResponse
    markets: list[DataFoundationMarketResponse]


class UpdateMarketCollectionPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    startAt: datetime
    dataTypes: tuple[
        Literal[
            "source_candle",
            "trade_event",
            "orderbook_snapshot",
            "ticker_snapshot",
        ],
        ...,
    ] = Field(min_length=1)
    candleUnit: Literal["1m"]
    retentionDays: int | None = Field(default=None, ge=1, le=36_500)
    priority: int = Field(ge=1, le=1000)
    continuous: bool

    @field_validator("startAt")
    @classmethod
    def require_utc_start_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("startAt은 UTC timezone-aware datetime이어야 한다.")
        return value

    @field_validator("dataTypes")
    @classmethod
    def require_unique_data_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("dataTypes는 중복될 수 없다.")
        return value


class UpdateMarketTargetStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requestId: str = Field(min_length=1, max_length=200)
    idempotencyKey: str = Field(min_length=1, max_length=200)
    actorId: str = Field(min_length=1, max_length=200)
    requestedAt: datetime
    state: Literal["active", "paused", "excluded"]
    reason: str = Field(min_length=1, max_length=500)
    policy: UpdateMarketCollectionPolicyRequest | None = None

    @field_validator("requestedAt")
    @classmethod
    def require_utc_requested_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("requestedAt은 UTC timezone-aware datetime이어야 한다.")
        return value

    @field_validator("requestId", "idempotencyKey", "actorId", "reason")
    @classmethod
    def reject_blank_command_strings(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("명령 문자열은 공백일 수 없다.")
        return stripped


class UpdateMarketTargetStateResponse(BaseModel):
    marketCode: str
    state: Literal["active", "paused", "excluded"]
    changedAt: datetime


class InstrumentResponse(BaseModel):
    id: int
    exchange: Literal["UPBIT"]
    marketCode: str
    quoteCurrency: str
    baseAsset: str
    displayName: str


class NotificationEventResponse(BaseModel):
    id: int
    severity: Literal["info", "warning", "error", "critical"]
    eventType: str
    title: str
    message: str
    status: Literal["open", "acknowledged", "resolved"]
    createdAt: datetime


class CoverageStatusResponse(BaseModel):
    instrumentId: int
    dataType: Literal["source_candle", "ticker_snapshot", "orderbook_summary"]
    status: Literal["normal", "warning", "incident", "backfilling"]
    progressPercent: str
    lastSuccessfulAt: datetime


class CollectionPlanResponse(BaseModel):
    instrumentId: int
    preset: str
    rangeStartAt: datetime
    rangeEndAt: datetime | None
    isContinuous: bool
    method: str
    displayRange: str
    rangeTimeZone: Literal["KST"]
    progressBasis: str


class CollectionDataStatusResponse(BaseModel):
    dataType: Literal["source_candle", "ticker_snapshot", "orderbook_summary"]
    label: str
    status: Literal["normal", "warning", "incident", "backfilling"]
    statusLabel: str
    lastSuccessfulAt: datetime
    progressPercent: str
    missingSegmentCount: int
    storedRowCount: int


class CoverageSegmentResponse(BaseModel):
    dataType: Literal["source_candle", "ticker_snapshot", "orderbook_summary"]
    status: Literal["collected", "missing", "collecting", "future"]
    offsetPercent: str
    widthPercent: str
    segmentStartAt: datetime
    segmentEndAt: datetime
    label: str


class CollectionDashboardTargetResponse(BaseModel):
    instrument: InstrumentResponse
    overallStatus: Literal["latest_collecting", "collecting", "warning", "incident"]
    overallStatusLabel: str
    plan: CollectionPlanResponse
    dataStatuses: list[CollectionDataStatusResponse]
    coverageSegments: list[CoverageSegmentResponse]
    changeRate: str
    accTradePrice24hDisplay: str
    tickerFreshnessLabel: str
    coveragePercent: str
    storageRowCount: int
    storageBytesDisplay: str


class CollectionCoverageSegmentsResponse(BaseModel):
    instrumentId: int
    items: list[CoverageSegmentResponse]


class DashboardTotalsResponse(BaseModel):
    activeTargets: int
    activeTargetLimit: int
    normalTargets: int
    warningTargets: int
    incidentTargets: int
    failedRuns24h: int
    failureRate24h: str
    delayedTargets: int
    missingRangesOpen: int
    storageBytesToday: int
    storageBytesTodayDisplay: str
    storageRowsToday: int
    realtimeRowsLastMinute: int
    backfillRowsLastMinute: int
    recentRequestCount: int


class MetricPrincipleResponse(BaseModel):
    metricKey: Literal["rateLimitRemainingPercent", "duplicateRows24h"]
    label: str
    displayStatus: Literal["displayed", "excluded"]
    evidenceStatus: Literal["available", "missing_persistence", "missing_measurement"]
    reason: str


class HealthCheckResponse(BaseModel):
    title: str
    status: Literal["normal", "warning", "incident"]
    statusLabel: str
    detail: str


class CollectionActivityBucketResponse(BaseModel):
    bucketStartAt: datetime
    runCount: int
    resultCount: int
    status: Literal["none", "low", "collecting", "high"]


class RealtimeCollectionHeatmapCellResponse(BaseModel):
    bucketStartAt: datetime
    tradeCount: int
    averageTradesPerMinute: str
    tradeStrength: str
    tradeVolume: str
    tradeAmount: str
    status: Literal["red", "orange", "yellow", "blue", "green"]


class RealtimeCollectionHeatmapRowResponse(BaseModel):
    instrument: InstrumentResponse
    instrumentDisplayName: str
    hourlyBuckets: list[RealtimeCollectionHeatmapCellResponse]


class StorageBreakdownItemResponse(BaseModel):
    dataType: Literal["source_candle", "ticker_snapshot", "orderbook_summary"]
    label: str
    rowCount: int
    bytes: int
    bytesDisplay: str
    sharePercent: str


class OperationsTrendPointResponse(BaseModel):
    bucketDate: datetime
    coveragePercent: str
    storageBytes: int
    warningTargets: int
    incidentTargets: int


class MissingRangeSummaryResponse(BaseModel):
    instrument: InstrumentResponse
    missingSegmentCount: int
    coveragePercent: str
    lastSuccessfulAt: datetime


class AuditLogSummaryResponse(BaseModel):
    targetChangeCount24h: int
    backfillChangeCount24h: int
    latestChangeAt: datetime | None
    latestChangeLabel: str


class CollectionWorkerErrorResponse(BaseModel):
    occurredAt: datetime
    code: str
    message: str


class CollectionWorkerDiagnosticResponse(BaseModel):
    label: str
    value: str
    detail: str


class RealtimeWorkerStatusResponse(BaseModel):
    status: Literal["running", "gated", "stale", "failed"]
    statusLabel: str
    statusDetail: str
    lastHeartbeatAt: datetime | None
    lastCollectedAt: datetime | None
    collectedRowCount24h: int
    errorCount24h: int
    failureRate24h: str
    diagnostics: list[CollectionWorkerDiagnosticResponse]
    recentErrors: list[CollectionWorkerErrorResponse]


class BackfillWorkerStatusResponse(BaseModel):
    status: Literal["running", "gated", "stale", "failed"]
    statusLabel: str
    statusDetail: str
    lastHeartbeatAt: datetime | None
    lastCollectedAt: datetime | None
    totalErrorCount: int
    failureRateAll: str
    runningTargetCount: int
    totalTargetCount: int
    queuedJobCount: int
    queuedTargetCount: int
    diagnostics: list[CollectionWorkerDiagnosticResponse]
    recentErrors: list[CollectionWorkerErrorResponse]


class CollectionWorkerStatusResponse(BaseModel):
    realtime: RealtimeWorkerStatusResponse
    backfill: BackfillWorkerStatusResponse


class DashboardSummaryResponse(BaseModel):
    status: Literal["normal", "warning", "incident"]
    refreshedAt: datetime
    totals: DashboardTotalsResponse
    coverage: list[CoverageStatusResponse]
    targets: list[CollectionDashboardTargetResponse]
    alerts: list[NotificationEventResponse]
    healthChecks: list[HealthCheckResponse]
    metricPrinciples: list[MetricPrincipleResponse]
    collectionActivity: list[CollectionActivityBucketResponse]
    realtimeCollectionHeatmap: list[RealtimeCollectionHeatmapRowResponse]
    storageBreakdown: list[StorageBreakdownItemResponse]
    operationsTrend: list[OperationsTrendPointResponse]
    missingRangeTop: list[MissingRangeSummaryResponse]
    auditLogSummary: AuditLogSummaryResponse
    workerStatus: CollectionWorkerStatusResponse


class DashboardOverviewResponse(BaseModel):
    status: Literal["normal", "warning", "incident"]
    refreshedAt: datetime
    recommendedRefreshSeconds: int
    totals: DashboardTotalsResponse
    alerts: list[NotificationEventResponse]
    healthChecks: list[HealthCheckResponse]
    metricPrinciples: list[MetricPrincipleResponse]


class DashboardTargetsResponse(BaseModel):
    items: list[CollectionDashboardTargetResponse]
    total: int
    limit: int
    offset: int
    recommendedRefreshSeconds: int
    refreshedAt: datetime


class DashboardCoverageResponse(BaseModel):
    items: list[CoverageStatusResponse]
    total: int
    limit: int
    offset: int
    recommendedRefreshSeconds: int
    refreshedAt: datetime


class DashboardCollectionActivityResponse(BaseModel):
    items: list[CollectionActivityBucketResponse]
    recommendedRefreshSeconds: int
    refreshedAt: datetime


class DashboardRealtimeHeatmapResponse(BaseModel):
    items: list[RealtimeCollectionHeatmapRowResponse]
    total: int
    limit: int
    offset: int
    recommendedRefreshSeconds: int
    refreshedAt: datetime


class DashboardStorageBreakdownResponse(BaseModel):
    items: list[StorageBreakdownItemResponse]
    recommendedRefreshSeconds: int
    refreshedAt: datetime


class DashboardOperationsTrendResponse(BaseModel):
    items: list[OperationsTrendPointResponse]
    recommendedRefreshSeconds: int
    refreshedAt: datetime


class DashboardMissingRangesResponse(BaseModel):
    items: list[MissingRangeSummaryResponse]
    total: int
    limit: int
    offset: int
    recommendedRefreshSeconds: int
    refreshedAt: datetime


class DashboardAuditLogSummaryResponse(BaseModel):
    targetChangeCount24h: int
    backfillChangeCount24h: int
    latestChangeAt: datetime | None
    latestChangeLabel: str
    recommendedRefreshSeconds: int
    refreshedAt: datetime


class CandidateUniverseEntryResponse(BaseModel):
    instrument: InstrumentResponse
    rank: int
    accTradePrice24h: str
    accTradePrice24hDisplay: str
    selected: bool
    favoriteOrder: int | None
    candidateStatus: Literal["in_universe", "out_of_universe"]
    qualityStatus: Literal["normal", "warning", "incident"]
    qualityDetail: str
    collectionRangeDisplay: str
    collectedStartAt: datetime | None
    collectedEndAt: datetime | None
    isRealtimeTarget: bool


class CandidateUniverseResponse(BaseModel):
    rankedAt: datetime
    entries: list[CandidateUniverseEntryResponse]


class UpdateCollectionTargetsRequest(BaseModel):
    instrumentIds: list[int] = Field(max_length=50)
    reason: str | None = None


class CollectionTargetsResponse(BaseModel):
    targets: list[InstrumentResponse]


class MarketListRowResponse(BaseModel):
    instrument: InstrumentResponse
    assetType: Literal["coin"]
    isFavorite: bool
    favoriteOrder: int | None
    tradePrice: str | None
    priceCurrency: str
    accTradePrice24h: str
    accTradePrice24hDisplay: str
    tradeAmountCurrency: str
    changeRate: str | None
    changeRateBasis: str
    tickerCollectedAt: datetime | None
    orderbookCollectedAt: datetime | None
    qualityStatus: Literal["normal", "warning", "incident"]
    coveragePercent: str
    candleCoverageStartAt: datetime | None
    candleCoverageEndAt: datetime | None
    candleCoverageCurrentAt: datetime
    oneMinuteCandleCount: int
    storageBytes: int
    storageRowCount: int
    storageBytesDisplay: str


class MarketListResponse(BaseModel):
    rows: list[MarketListRowResponse]


class TickerSnapshotResponse(BaseModel):
    bucketAt: datetime
    tradePrice: str
    accTradePrice24h: str
    changeRate: str
    collectedAt: datetime


class OrderbookSummaryResponse(BaseModel):
    bucketAt: datetime
    bestBidPrice: str
    bestBidSize: str
    bestAskPrice: str
    bestAskSize: str
    spread: str
    bidDepth10: str
    askDepth10: str
    imbalance10: str
    collectedAt: datetime


class QualityHistoryEventResponse(BaseModel):
    occurredAt: datetime
    status: Literal["normal", "warning", "incident"]
    title: str
    detail: str


class InstrumentDetailResponse(BaseModel):
    instrument: InstrumentResponse
    latestTicker: TickerSnapshotResponse
    latestOrderbook: OrderbookSummaryResponse
    coverage: list[CoverageStatusResponse]
    priceChangeAmount24h: str
    priceChangeRate24h: str
    tradeVolume24h: str
    tradeVolumeChangeRate24h: str
    tickerFreshnessLabel: str
    orderbookFreshnessLabel: str
    qualityHistory: list[QualityHistoryEventResponse]


class CandleResponse(BaseModel):
    startedAt: datetime
    open: str
    high: str
    low: str
    close: str
    volume: str
    tradeAmount: str
    completeness: Literal["complete", "partial", "empty"]


class CandleSeriesResponse(BaseModel):
    unit: str
    candles: list[CandleResponse]


class TickerSnapshotsResponse(BaseModel):
    items: list[TickerSnapshotResponse]


class OrderbookSummariesResponse(BaseModel):
    items: list[OrderbookSummaryResponse]


class CollectionRunResponse(BaseModel):
    id: int
    runType: str
    dataType: str
    status: str
    startedAt: datetime
    finishedAt: datetime | None = None


class CollectionRunsResponse(BaseModel):
    items: list[CollectionRunResponse]


class CreateBackfillPlanRequest(BaseModel):
    dataType: Literal["source_candle"]
    targetStartAt: datetime
    targetEndAt: datetime
    instrumentIds: list[int]


class CreateBackfillJobRequest(BaseModel):
    dataType: Literal["source_candle"]
    targetStartAt: datetime
    targetEndAt: datetime
    instrumentIds: list[int]


class BackfillPlanResponse(BaseModel):
    planId: str
    dataType: str
    estimatedRequestCount: int
    estimatedRowCount: int
    estimatedStorageBytes: int
    targets: list[int]


class BackfillJobResponse(BaseModel):
    id: int
    status: Literal[
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
    dataType: str
    progressPercent: str
    estimatedRequestCount: int
    totalTargetCount: int
    completedTargetCount: int
    runningTargetIndex: int | None
    currentTarget: InstrumentResponse | None
    currentTargetBackfillRowCount: int
    processedMissingRangeCount: int
    estimatedMissingRangeCount: int
    targetStartAt: datetime
    targetEndAt: datetime
    targets: list[InstrumentResponse]
    createdAt: datetime
    attemptCount: int
    maxAttempts: int
    nextRetryAt: datetime | None
    lastErrorCode: str | None
    deadLetterReason: str | None


class BackfillJobsResponse(BaseModel):
    items: list[BackfillJobResponse]


class NotificationEventsResponse(BaseModel):
    items: list[NotificationEventResponse]


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
