from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    code: str
    message: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    checkedAt: datetime


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
    rangeTimeZone: Literal["KST", "UTC"]
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
    recentRequestCount: int
    rateLimitRemainingPercent: str


class HealthCheckResponse(BaseModel):
    title: str
    status: Literal["normal", "warning", "incident"]
    statusLabel: str
    detail: str


class DashboardSummaryResponse(BaseModel):
    status: Literal["normal", "warning", "incident"]
    refreshedAt: datetime
    totals: DashboardTotalsResponse
    coverage: list[CoverageStatusResponse]
    targets: list[CollectionDashboardTargetResponse]
    alerts: list[NotificationEventResponse]
    healthChecks: list[HealthCheckResponse]


class CandidateUniverseEntryResponse(BaseModel):
    instrument: InstrumentResponse
    rank: int
    accTradePrice24h: str
    accTradePrice24hDisplay: str
    selected: bool
    candidateStatus: Literal["in_universe", "out_of_universe"]
    qualityStatus: Literal["normal", "warning", "incident"]
    qualityDetail: str
    collectionRangeDisplay: str


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
    tradePrice: str
    accTradePrice24h: str
    accTradePrice24hDisplay: str
    changeRate: str
    tickerCollectedAt: datetime
    orderbookCollectedAt: datetime
    qualityStatus: Literal["normal", "warning", "incident"]
    coveragePercent: str
    storageBytes: int
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


class InstrumentDetailResponse(BaseModel):
    instrument: InstrumentResponse
    latestTicker: TickerSnapshotResponse
    latestOrderbook: OrderbookSummaryResponse
    coverage: list[CoverageStatusResponse]
    duplicateRows24h: int
    tickerFreshnessLabel: str
    orderbookFreshnessLabel: str


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


class BackfillPlanResponse(BaseModel):
    planId: str
    dataType: str
    estimatedRequestCount: int
    estimatedRowCount: int
    estimatedStorageBytes: int
    targets: list[int]


class ApproveBackfillJobRequest(BaseModel):
    planId: str


class BackfillJobResponse(BaseModel):
    id: int
    status: Literal["planned", "pending", "running", "paused", "stopped", "succeeded", "failed"]
    dataType: str
    progressPercent: str
    createdAt: datetime


class BackfillJobsResponse(BaseModel):
    items: list[BackfillJobResponse]


class NotificationEventsResponse(BaseModel):
    items: list[NotificationEventResponse]


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
