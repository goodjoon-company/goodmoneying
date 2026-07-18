from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


DatasetDataKind = Literal["candle", "indicator", "market_statistic", "microstructure"]
DatasetFillPolicy = Literal["none", "no_trade_carry_forward_v1"]
DatasetMissingPolicy = Literal["fail", "null", "drop"]
DatasetQuality = Literal["available", "no_trade", "missing", "unavailable", "unverified"]
StrategyValidationCode = Literal[
    "cycle_detected",
    "port_type_mismatch",
    "timeframe_incompatible",
    "look_ahead_detected",
    "parameter_out_of_range",
    "missing_data_policy_required",
    "insufficient_warmup",
    "missing_output",
]


class StrategyGraphPort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    dataType: str = Field(min_length=1, max_length=100)
    timeframe: (
        Literal["1m", "3m", "5m", "10m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"]
        | None
    ) = None


class StrategyGraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=100)
    config: dict[str, object] = Field(default_factory=dict)
    input_ports: list[StrategyGraphPort]
    output_ports: list[StrategyGraphPort]


class StrategyGraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_node: str = Field(min_length=1, max_length=200)
    from_port: str = Field(min_length=1, max_length=100)
    to_node: str = Field(min_length=1, max_length=200)
    to_port: str = Field(min_length=1, max_length=100)


class StrategyGraphOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str = Field(min_length=1, max_length=200)
    port: str = Field(min_length=1, max_length=100)


class StrategyGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["strategy-graph-v1"]
    nodes: list[StrategyGraphNode] = Field(min_length=1, max_length=500)
    edges: list[StrategyGraphEdge] = Field(max_length=2000)
    outputs: list[StrategyGraphOutput] = Field(min_length=1, max_length=20)


class ValidateStrategyGraphRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph: StrategyGraph


class StrategyValidationErrorResponse(BaseModel):
    code: StrategyValidationCode
    nodeId: str | None = None
    edgeIndex: int | None = Field(default=None, ge=0)
    message: str


class StrategyValidationResponse(BaseModel):
    valid: bool
    errors: list[StrategyValidationErrorResponse]
    graphHash: str = Field(pattern="^[0-9a-f]{64}$")


class StrategyCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requestId: str = Field(min_length=1, max_length=200)
    idempotencyKey: str = Field(min_length=1, max_length=200)
    actorId: str = Field(min_length=1, max_length=200)
    requestedAt: datetime
    reason: str = Field(min_length=1, max_length=500)

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
            raise ValueError("전략 명령 문자열은 공백일 수 없다.")
        return stripped


class CreateStrategyRequest(StrategyCommandRequest):
    ownerId: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)


class CreatePortfolioRequest(StrategyCommandRequest):
    ownerId: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    baseCurrency: Literal["KRW", "BTC", "USDT"] = "KRW"


class PublishStrategyVersionRequest(StrategyCommandRequest):
    graph: StrategyGraph


class StrategyDefinitionResponse(BaseModel):
    strategyId: int
    ownerId: str
    name: str
    createdAt: datetime


class PortfolioResponse(BaseModel):
    portfolioId: int
    ownerId: str
    name: str
    baseCurrency: Literal["KRW", "BTC", "USDT"]
    status: Literal["active", "archived"]
    createdAt: datetime


class PortfoliosResponse(BaseModel):
    items: list[PortfolioResponse]
    nextCursor: str | None


class StrategyVersionResponse(BaseModel):
    strategyVersionId: int
    strategyId: int
    version: int = Field(ge=1)
    schemaVersion: Literal["strategy-graph-v1"]
    status: Literal["draft", "validated", "published", "retired"]
    graphHash: str = Field(pattern="^[0-9a-f]{64}$")
    validation: StrategyValidationResponse
    graph: StrategyGraph
    createdAt: datetime
    publishedAt: datetime | None


class StrategyVersionsResponse(BaseModel):
    items: list[StrategyVersionResponse]
    nextCursor: str | None


class DatasetSeriesSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrumentId: int = Field(gt=0)
    dataKind: DatasetDataKind
    unit: Literal["1m", "3m", "5m", "10m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"]
    definitionSetHash: str | None = Field(pattern="^[0-9a-f]{64}$")
    calculationVersion: str | None = Field(min_length=1, max_length=100)


class DatasetSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asOf: datetime
    from_: datetime = Field(alias="from")
    to: datetime
    series: tuple[DatasetSeriesSelectionRequest, ...] = Field(min_length=1, max_length=200)

    @field_validator("asOf", "from_", "to")
    @classmethod
    def require_utc_selection_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("데이터셋 선택 시각은 UTC timezone-aware datetime이어야 한다.")
        return value

    @model_validator(mode="after")
    def validate_range_and_uniqueness(self) -> DatasetSelectionRequest:
        if self.from_ >= self.to:
            raise ValueError("데이터셋 범위는 from < to인 반개방 구간이어야 한다.")
        if self.to > self.asOf:
            raise ValueError("데이터셋 범위 끝은 asOf 이후일 수 없다.")
        natural_keys = [
            (
                item.instrumentId,
                item.dataKind,
                item.unit,
                item.definitionSetHash,
                item.calculationVersion,
            )
            for item in self.series
        ]
        if len(natural_keys) != len(set(natural_keys)):
            raise ValueError("데이터셋 series 선택은 중복될 수 없다.")
        return self


class DatasetPoliciesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    availabilityPolicy: Literal["point_in_time_v1"]
    fillPolicy: DatasetFillPolicy
    missingPolicy: DatasetMissingPolicy


class CreateDatasetBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requestId: str = Field(min_length=1, max_length=200)
    idempotencyKey: str = Field(min_length=1, max_length=200)
    actorId: str = Field(min_length=1, max_length=200)
    requestedAt: datetime
    reason: str = Field(min_length=1, max_length=500)
    selection: DatasetSelectionRequest
    policies: DatasetPoliciesRequest

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

    @model_validator(mode="after")
    def validate_fill_policy(self) -> CreateDatasetBuildRequest:
        if self.selection.asOf > self.requestedAt:
            raise ValueError("selection.asOf는 requestedAt 이후일 수 없다.")
        if self.policies.fillPolicy == "no_trade_carry_forward_v1" and any(
            item.dataKind != "candle" for item in self.selection.series
        ):
            raise ValueError("no_trade_carry_forward_v1 채움은 candle series에만 허용한다.")
        return self


class DatasetBuildResponse(BaseModel):
    buildId: int
    requestId: str
    idempotencyKey: str
    actorId: str
    requestedAt: datetime
    frozenAt: datetime
    status: Literal[
        "pending",
        "running",
        "retry_wait",
        "succeeded",
        "failed",
        "dead_letter",
        "cancelled",
    ]
    attemptCount: int = Field(ge=0)
    maxAttempts: int = Field(gt=0)
    nextRetryAt: datetime | None
    deadLetterReason: str | None
    datasetVersionId: int | None
    errorCode: str | None
    errorMessage: str | None


class DatasetBuildsResponse(BaseModel):
    items: list[DatasetBuildResponse]
    nextCursor: str | None


class DatasetVersionSeriesResponse(BaseModel):
    seriesId: int
    instrumentId: int
    dataKind: DatasetDataKind
    unit: str
    definitionSetHash: str | None
    calculationVersion: str | None


class DatasetVersionResponse(BaseModel):
    datasetVersionId: int
    schemaVersion: Literal["dataset-v1"]
    asOf: datetime
    from_: datetime = Field(alias="from")
    to: datetime
    contentHash: str = Field(pattern="^[0-9a-f]{64}$")
    availabilityPolicy: Literal["point_in_time_v1"]
    fillPolicy: DatasetFillPolicy
    missingPolicy: DatasetMissingPolicy
    createdAt: datetime
    series: list[DatasetVersionSeriesResponse]


class DatasetVersionsResponse(BaseModel):
    items: list[DatasetVersionResponse]
    nextCursor: str | None


class DatasetCoverageItemResponse(BaseModel):
    seriesId: int
    rangeStartAt: datetime
    rangeEndAt: datetime
    knowledgeAt: datetime
    status: DatasetQuality
    bucketCount: int = Field(ge=0)


class DatasetCoverageResponse(BaseModel):
    datasetVersionId: int
    snapshotHash: str = Field(pattern="^[0-9a-f]{64}$")
    requestedBucketCount: int = Field(ge=0)
    eligibleBucketCount: int = Field(ge=0)
    usableRatio: str
    counts: CoverageCountsResponse
    items: list[DatasetCoverageItemResponse]


class DatasetSeriesPointResponse(BaseModel):
    occurredAt: datetime
    knowledgeAt: datetime
    quality: DatasetQuality
    contentHash: str = Field(pattern="^[0-9a-f]{64}$")
    values: dict[str, str | int | bool | None]


class DatasetSeriesResponse(BaseModel):
    datasetVersionId: int
    seriesId: int
    dataKind: DatasetDataKind
    unit: str
    items: list[DatasetSeriesPointResponse]
    nextCursor: str | None


class BacktestMetricResponse(BaseModel):
    metricName: str
    scopeKey: str
    metricValue: Decimal
    metricPayload: dict[str, object]


class BacktestTradeResponse(BaseModel):
    tradeSequence: int = Field(ge=1)
    side: Literal["buy", "sell"]
    requestedQuantity: Decimal
    filledQuantity: Decimal
    remainingQuantity: Decimal
    fillPrice: Decimal
    feePaid: Decimal
    status: Literal["filled", "partially_filled", "rejected"]
    occurredAt: datetime
    knowledgeAt: datetime


class BacktestEquityPointResponse(BaseModel):
    pointSequence: int = Field(ge=1)
    occurredAt: datetime
    knowledgeAt: datetime
    cash: Decimal
    basePosition: Decimal
    equity: Decimal


class BacktestArtifactResponse(BaseModel):
    artifactType: str
    contentHash: str = Field(pattern="^[0-9a-f]{64}$")
    mediaType: str
    storageUri: str | None
    metadata: dict[str, object]


BacktestParameterValue = str | int | bool | None


class BacktestExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feeRate: Decimal = Field(ge=0)
    slippageBps: Decimal = Field(ge=0)
    latencySeconds: int = Field(ge=0)
    maxParticipationRate: Decimal = Field(gt=0, le=1)


class CreateBacktestRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requestId: str = Field(min_length=1, max_length=200)
    idempotencyKey: str = Field(min_length=1, max_length=200)
    actorId: str = Field(min_length=1, max_length=200)
    requestedAt: datetime
    reason: str = Field(min_length=1, max_length=500)
    strategyVersionId: int = Field(gt=0)
    datasetVersionId: int = Field(gt=0)
    engineVersion: str = Field(min_length=1, max_length=100)
    parameters: dict[str, BacktestParameterValue]
    seed: int
    initialCash: Decimal
    execution: BacktestExecutionRequest
    maxAttempts: int = Field(ge=1, le=10)

    @field_validator("requestedAt")
    @classmethod
    def require_utc_requested_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("requestedAt은 UTC timezone-aware datetime이어야 한다.")
        return value

    @field_validator("requestId", "idempotencyKey", "actorId", "reason", "engineVersion")
    @classmethod
    def reject_blank_command_strings(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("백테스트 실행 명령 문자열은 공백일 수 없다.")
        return stripped

    @field_validator("parameters")
    @classmethod
    def reject_blank_parameter_keys(
        cls, value: dict[str, BacktestParameterValue]
    ) -> dict[str, BacktestParameterValue]:
        if any(not key.strip() for key in value):
            raise ValueError("parameters key는 공백일 수 없다.")
        return value

    @field_validator("initialCash")
    @classmethod
    def require_positive_initial_cash(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("initialCash는 0보다 커야 한다.")
        return value


class BacktestRunResponse(BaseModel):
    backtestRunId: int
    strategyVersionId: int
    datasetVersionId: int
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    inputHash: str = Field(pattern="^[0-9a-f]{64}$")
    resultHash: str | None = Field(pattern="^[0-9a-f]{64}$")
    metrics: list[BacktestMetricResponse]
    trades: list[BacktestTradeResponse]
    artifacts: list[BacktestArtifactResponse]


class BacktestRunSummaryResponse(BaseModel):
    backtestRunId: int = Field(gt=0)
    strategyVersionId: int = Field(gt=0)
    datasetVersionId: int = Field(gt=0)
    engineVersion: str
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    inputHash: str = Field(pattern="^[0-9a-f]{64}$")
    resultHash: str | None = Field(pattern="^[0-9a-f]{64}$")
    requestedAt: datetime
    startedAt: datetime | None
    finishedAt: datetime | None


class BacktestRunsResponse(BaseModel):
    items: list[BacktestRunSummaryResponse]
    nextCursor: str | None


class BacktestTradesResponse(BaseModel):
    backtestRunId: int = Field(gt=0)
    items: list[BacktestTradeResponse]
    nextCursor: str | None


class BacktestEquityPointsResponse(BaseModel):
    backtestRunId: int = Field(gt=0)
    items: list[BacktestEquityPointResponse]
    nextCursor: str | None


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
    instrumentId: int = Field(gt=0)
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
    calculationVersion: str
    sourceAsOf: datetime
    knowledgeAt: datetime
    inputContentHash: str
    quality: Literal["available", "no_trade", "missing", "unavailable", "unverified"]
    completeness: Literal["complete", "partial", "empty"]


class CandleSeriesResponse(BaseModel):
    unit: str
    candles: list[CandleResponse]
    nextCursor: str | None = None


class IndicatorPointResponse(BaseModel):
    startedAt: datetime
    values: dict[str, str | None]
    statuses: dict[str, Literal["warming_up", "ready", "missing"]]
    definitionVersions: dict[str, str]
    materializationId: int | None
    sourceRevisionThroughId: int
    qualityEventThroughId: int | None
    knowledgeAt: datetime
    sourceAsOf: datetime


class IndicatorSeriesResponse(BaseModel):
    unit: str
    asOf: datetime
    definitionSetHash: str
    items: list[IndicatorPointResponse]
    nextCursor: str | None = None


class MarketStatisticResponse(BaseModel):
    startedAt: datetime
    calculationVersion: Literal["market-statistics-v1"]
    closeReturn1: str | None
    realizedVolatility20: str | None
    tradeVolume: str | None
    tradeAmount: str | None
    volatilitySampleCount: int
    inputCompletenessRatio: str
    returnStatus: Literal["warming_up", "ready", "missing"]
    volatilityStatus: Literal["warming_up", "ready", "missing"]
    tradeStatus: Literal["warming_up", "ready", "missing"]
    materializationId: int | None
    sourceRevisionThroughId: int
    qualityEventThroughId: int | None
    sourceAsOf: datetime
    knowledgeAt: datetime
    contentHash: str


class MarketStatisticsResponse(BaseModel):
    unit: str
    asOf: datetime
    items: list[MarketStatisticResponse]
    nextCursor: str | None = None


MicrostructureCalculationStatus = Literal["ready", "missing", "partial", "invalid", "undefined"]


class MicrostructureStatisticResponse(BaseModel):
    startedAt: datetime
    calculationVersion: Literal["microstructure-v1"]
    closingOrderbookSnapshotId: int | None
    closingOrderbookSourceReceiptId: int | None
    spread: str | None
    spreadBps: str | None
    bidDepth10: str | None
    askDepth10: str | None
    orderbookImbalance10: str | None
    tradeCount: int | None
    tradeIntensityPerMinute: str | None
    volumeIntensityPerMinute: str | None
    buyCount: int | None
    sellCount: int | None
    buyVolume: str | None
    sellVolume: str | None
    buySellImbalance: str | None
    executionStrength: str | None
    orderbookStatus: MicrostructureCalculationStatus
    tradeStatus: MicrostructureCalculationStatus
    executionStrengthStatus: MicrostructureCalculationStatus
    materializationId: int
    sourceCandleRevisionId: int | None
    orderbookSnapshotThroughId: int
    tradeEventThroughId: int
    sourceReceiptThroughId: int
    connectionQualityThroughId: int
    qualityEventThroughId: int | None
    orderbookQuality: Literal["available", "no_trade", "missing", "unavailable", "unverified"]
    tradeQuality: Literal["available", "no_trade", "missing", "unavailable", "unverified"]
    sourceAsOf: datetime
    knowledgeAt: datetime
    inputLineageHash: str
    contentHash: str


class MicrostructureStatisticsResponse(BaseModel):
    unit: Literal["1m"]
    asOf: datetime
    calculationVersion: Literal["microstructure-v1"]
    items: list[MicrostructureStatisticResponse]
    nextCursor: str | None = None


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
