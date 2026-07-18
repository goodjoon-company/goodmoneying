export type Status = "normal" | "warning" | "incident";

export type CoverageIntervalStatus =
  | "available"
  | "no_trade"
  | "missing"
  | "unavailable"
  | "unverified";

export type CoverageCounts = Record<CoverageIntervalStatus, number>;

export type CollectionPolicyDataType =
  | "source_candle"
  | "trade_event"
  | "orderbook_snapshot"
  | "ticker_snapshot";

export type MarketCollectionPolicy = {
  startAt: string;
  dataTypes: CollectionPolicyDataType[];
  candleUnit: "1m";
  retentionDays: number | null;
  priority: number;
  continuous: boolean;
};

export type ChangeCommandEnvelope = {
  requestId: string;
  idempotencyKey: string;
  actorId: string;
  requestedAt: string;
};

export type DataFoundationMarket = {
  instrumentId: number;
  marketCode: string;
  koreanName: string;
  englishName: string;
  quoteCurrency: string;
  tradingStatus: "active" | "inactive" | "delisted" | "unknown";
  marketWarning: string;
  targetStatus: "active" | "paused" | "excluded" | "not_targeted";
  activeDataTypeCount: number;
  totalDataTypeCount: number;
  coverageCounts: CoverageCounts;
  collectionPolicy: MarketCollectionPolicy | null;
};

export type DataFoundation = {
  timeZone: "UTC";
  policyStartAt: string;
  summary: {
    marketCount: number;
    krwMarketCount: number;
    activeTargetCount: number;
    pendingBackfillJobCount: number;
    desiredSubscriptionCount: number;
    coverageCounts: CoverageCounts;
  };
  markets: DataFoundationMarket[];
};

export type Instrument = {
  id: number;
  exchange: "UPBIT";
  marketCode: string;
  quoteCurrency: string;
  baseAsset: string;
  displayName: string;
};

export type NotificationEvent = {
  id: number;
  severity: "info" | "warning" | "error" | "critical";
  eventType: string;
  title: string;
  message: string;
  status: "open" | "acknowledged" | "resolved";
  createdAt: string;
};

export type CoverageStatus = {
  instrumentId: number;
  dataType: "source_candle" | "ticker_snapshot" | "orderbook_summary";
  status: Status | "backfilling";
  progressPercent: string;
  lastSuccessfulAt: string;
};

export type CollectionPlan = {
  instrumentId: number;
  preset: string;
  rangeStartAt: string;
  rangeEndAt: string | null;
  isContinuous: boolean;
  method: string;
  displayRange: string;
  rangeTimeZone: "KST";
  progressBasis: string;
};

export type CollectionDataStatus = {
  dataType: "source_candle" | "ticker_snapshot" | "orderbook_summary";
  label: string;
  status: Status | "backfilling";
  statusLabel: string;
  lastSuccessfulAt: string;
  progressPercent: string;
  missingSegmentCount: number;
  storedRowCount: number;
};

export type CoverageSegment = {
  dataType: "source_candle" | "ticker_snapshot" | "orderbook_summary";
  status: "collected" | "missing" | "collecting" | "future";
  offsetPercent: string;
  widthPercent: string;
  segmentStartAt: string;
  segmentEndAt: string;
  label: string;
};

export type CollectionDashboardTarget = {
  instrument: Instrument;
  overallStatus: "latest_collecting" | "collecting" | "warning" | "incident";
  overallStatusLabel: string;
  plan: CollectionPlan;
  dataStatuses: CollectionDataStatus[];
  coverageSegments: CoverageSegment[];
  changeRate: string;
  accTradePrice24hDisplay: string;
  tickerFreshnessLabel: string;
  coveragePercent: string;
  storageRowCount: number;
  storageBytesDisplay: string;
};

export type DashboardSummary = {
  status: Status;
  refreshedAt: string;
  totals: {
    activeTargets: number;
    activeTargetLimit: number;
    normalTargets: number;
    warningTargets: number;
    incidentTargets: number;
    failedRuns24h: number;
    failureRate24h: string;
    delayedTargets: number;
    missingRangesOpen: number;
    storageBytesToday: number;
    storageBytesTodayDisplay: string;
    storageRowsToday: number;
    realtimeRowsLastMinute: number;
    backfillRowsLastMinute: number;
    recentRequestCount: number;
  };
  coverage: CoverageStatus[];
  targets: CollectionDashboardTarget[];
  alerts: NotificationEvent[];
  healthChecks: {
    title: string;
    status: Status;
    statusLabel: string;
    detail: string;
  }[];
  metricPrinciples: MetricPrinciple[];
  collectionActivity: CollectionActivityBucket[];
  realtimeCollectionHeatmap: RealtimeCollectionHeatmapRow[];
  workerStatus: CollectionWorkerStatus;
  storageBreakdown: StorageBreakdownItem[];
  operationsTrend: OperationsTrendPoint[];
  missingRangeTop: MissingRangeSummary[];
  auditLogSummary: AuditLogSummary;
};

export type AuditLogSummary = {
  targetChangeCount24h: number;
  backfillChangeCount24h: number;
  latestChangeAt: string | null;
  latestChangeLabel: string;
};

export type MetricPrinciple = {
  metricKey: "rateLimitRemainingPercent" | "duplicateRows24h";
  label: string;
  displayStatus: "displayed" | "excluded";
  evidenceStatus: "available" | "missing_persistence" | "missing_measurement";
  reason: string;
};

export type CollectionActivityBucket = {
  bucketStartAt: string;
  runCount: number;
  resultCount: number;
  status: "none" | "low" | "collecting" | "high";
};

export type RealtimeCollectionHeatmapCell = {
  bucketStartAt: string;
  tradeCount: number;
  averageTradesPerMinute: string;
  tradeStrength: string;
  tradeVolume: string;
  tradeAmount: string;
  status: "red" | "orange" | "yellow" | "blue" | "green";
};

export type RealtimeCollectionHeatmapRow = {
  instrument: Instrument;
  instrumentDisplayName: string;
  hourlyBuckets: RealtimeCollectionHeatmapCell[];
};

export type CollectionWorkerError = {
  occurredAt: string;
  code: string;
  message: string;
};

export type CollectionWorkerDiagnostic = {
  label: string;
  value: string;
  detail: string;
};

export type RealtimeWorkerStatus = {
  status: "running" | "gated" | "stale" | "failed";
  statusLabel: string;
  statusDetail: string;
  lastHeartbeatAt: string | null;
  lastCollectedAt: string | null;
  collectedRowCount24h: number;
  errorCount24h: number;
  failureRate24h: string;
  diagnostics: CollectionWorkerDiagnostic[];
  recentErrors: CollectionWorkerError[];
};

export type BackfillWorkerStatus = {
  status: "running" | "gated" | "stale" | "failed";
  statusLabel: string;
  statusDetail: string;
  lastHeartbeatAt: string | null;
  lastCollectedAt: string | null;
  totalErrorCount: number;
  failureRateAll: string;
  runningTargetCount: number;
  totalTargetCount: number;
  queuedJobCount: number;
  queuedTargetCount: number;
  diagnostics: CollectionWorkerDiagnostic[];
  recentErrors: CollectionWorkerError[];
};

export type CollectionWorkerStatus = {
  realtime: RealtimeWorkerStatus;
  backfill: BackfillWorkerStatus;
};

export type StorageBreakdownItem = {
  dataType: "source_candle" | "ticker_snapshot" | "orderbook_summary";
  label: string;
  rowCount: number;
  bytes: number;
  bytesDisplay: string;
  sharePercent: string;
};

export type OperationsTrendPoint = {
  bucketDate: string;
  coveragePercent: string;
  storageBytes: number;
  warningTargets: number;
  incidentTargets: number;
};

export type MissingRangeSummary = {
  instrument: Instrument;
  missingSegmentCount: number;
  coveragePercent: string;
  lastSuccessfulAt: string;
};

export type CandidateUniverseEntry = {
  instrument: Instrument;
  rank: number;
  accTradePrice24h: string;
  accTradePrice24hDisplay: string;
  selected: boolean;
  favoriteOrder: number | null;
  candidateStatus: "in_universe" | "out_of_universe";
  qualityStatus: Status;
  qualityDetail: string;
  collectionRangeDisplay: string;
  collectedStartAt: string | null;
  collectedEndAt: string | null;
  isRealtimeTarget: boolean;
};

export type MarketListRow = {
  instrument: Instrument;
  assetType: "coin";
  isFavorite: boolean;
  favoriteOrder: number | null;
  tradePrice: string | null;
  priceCurrency: string;
  accTradePrice24h: string;
  accTradePrice24hDisplay: string;
  tradeAmountCurrency: string;
  changeRate: string | null;
  changeRateBasis: string;
  tickerCollectedAt: string | null;
  orderbookCollectedAt: string | null;
  qualityStatus: Status;
  coveragePercent: string;
  candleCoverageStartAt: string | null;
  candleCoverageEndAt: string | null;
  candleCoverageCurrentAt: string;
  oneMinuteCandleCount: number;
  storageBytes: number;
  storageRowCount: number;
  storageBytesDisplay: string;
};

export type TickerSnapshot = {
  bucketAt: string;
  tradePrice: string;
  accTradePrice24h: string;
  changeRate: string;
  collectedAt: string;
};

export type OrderbookSummary = {
  bucketAt: string;
  bestBidPrice: string;
  bestBidSize: string;
  bestAskPrice: string;
  bestAskSize: string;
  spread: string;
  bidDepth10: string;
  askDepth10: string;
  imbalance10: string;
  collectedAt: string;
};

export type Candle = {
  startedAt: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  tradeAmount: string;
  calculationVersion: string;
  sourceAsOf: string;
  knowledgeAt: string;
  inputContentHash: string;
  quality: "available" | "no_trade" | "missing" | "unavailable" | "unverified";
  completeness: "complete" | "partial" | "empty";
};

export type InstrumentDetail = {
  instrument: Instrument;
  latestTicker: TickerSnapshot;
  latestOrderbook: OrderbookSummary;
  coverage: CoverageStatus[];
  priceChangeAmount24h: string;
  priceChangeRate24h: string;
  tradeVolume24h: string;
  tradeVolumeChangeRate24h: string;
  tickerFreshnessLabel: string;
  orderbookFreshnessLabel: string;
  qualityHistory: QualityHistoryEvent[];
};

export type QualityHistoryEvent = {
  occurredAt: string;
  status: Status;
  title: string;
  detail: string;
};

export type BackfillJob = {
  id: number;
  status:
    | "planned"
    | "pending"
    | "leased"
    | "running"
    | "retry_wait"
    | "paused"
    | "stopped"
    | "succeeded"
    | "failed"
    | "dead_letter"
    | "cancelled";
  dataType: string;
  progressPercent: string;
  estimatedRequestCount: number;
  totalTargetCount: number;
  completedTargetCount: number;
  runningTargetIndex: number | null;
  currentTarget: Instrument | null;
  currentTargetBackfillRowCount: number;
  processedMissingRangeCount: number;
  estimatedMissingRangeCount: number;
  targetStartAt: string;
  targetEndAt: string;
  targets: Instrument[];
  createdAt: string;
  attemptCount: number;
  maxAttempts: number;
  nextRetryAt: string | null;
  lastErrorCode: string | null;
  deadLetterReason: string | null;
};

export type DatasetDataKind = "candle" | "indicator" | "market_statistic" | "microstructure";
export type DatasetQuality = "available" | "no_trade" | "missing" | "unavailable" | "unverified";
export type DatasetFillPolicy = "none" | "no_trade_carry_forward_v1";
export type DatasetMissingPolicy = "fail" | "null" | "drop";

export type DatasetSeriesSelection = {
  instrumentId: number;
  dataKind: DatasetDataKind;
  unit: "1m" | "3m" | "5m" | "10m" | "15m" | "30m" | "1h" | "4h" | "1d" | "1w" | "1M";
  definitionSetHash: string | null;
  calculationVersion: string | null;
};

export type DatasetSelection = {
  asOf: string;
  from: string;
  to: string;
  series: DatasetSeriesSelection[];
};

export type DatasetPolicies = {
  availabilityPolicy: "point_in_time_v1";
  fillPolicy: DatasetFillPolicy;
  missingPolicy: DatasetMissingPolicy;
};

export type CreateDatasetBuildCommand = {
  requestId: string;
  idempotencyKey: string;
  actorId: string;
  requestedAt: string;
  reason: string;
  selection: DatasetSelection;
  policies: DatasetPolicies;
};

export type DatasetBuild = {
  buildId: number;
  requestId: string;
  idempotencyKey: string;
  actorId: string;
  requestedAt: string;
  frozenAt: string;
  status: "pending" | "running" | "retry_wait" | "succeeded" | "failed" | "dead_letter" | "cancelled";
  attemptCount: number;
  maxAttempts: number;
  nextRetryAt: string | null;
  deadLetterReason: string | null;
  datasetVersionId: number | null;
  errorCode: string | null;
  errorMessage: string | null;
};

export type DatasetBuildsResponse = {
  items: DatasetBuild[];
  nextCursor: string | null;
};

export type DatasetVersionSeries = DatasetSeriesSelection & {
  seriesId: number;
};

export type DatasetVersion = {
  datasetVersionId: number;
  schemaVersion: "dataset-v1";
  asOf: string;
  from: string;
  to: string;
  contentHash: string;
  availabilityPolicy: "point_in_time_v1";
  fillPolicy: DatasetFillPolicy;
  missingPolicy: DatasetMissingPolicy;
  createdAt: string;
  series: DatasetVersionSeries[];
};

export type DatasetVersionsResponse = {
  items: DatasetVersion[];
  nextCursor: string | null;
};

export type DatasetCoverageItem = {
  seriesId: number;
  rangeStartAt: string;
  rangeEndAt: string;
  knowledgeAt: string;
  status: DatasetQuality;
  bucketCount: number;
};

export type DatasetCoverage = {
  datasetVersionId: number;
  snapshotHash: string;
  requestedBucketCount: number;
  eligibleBucketCount: number;
  usableRatio: string;
  counts: CoverageCounts;
  items: DatasetCoverageItem[];
};

export type DatasetSeriesPoint = {
  occurredAt: string;
  knowledgeAt: string;
  quality: DatasetQuality;
  contentHash: string;
  values: Record<string, string | number | boolean | null>;
};

export type DatasetSeriesResponse = {
  datasetVersionId: number;
  seriesId: number;
  dataKind: DatasetDataKind;
  unit: string;
  items: DatasetSeriesPoint[];
  nextCursor: string | null;
};

export type OperationsSnapshot = {
  dashboard: DashboardSummary;
  candidateEntries: CandidateUniverseEntry[];
  marketRows: MarketListRow[];
  detail: InstrumentDetail | null;
  candles: Candle[];
  backfillJobs: BackfillJob[];
  notifications: NotificationEvent[];
  source: "api";
};

export type CollectionCoverageSegmentsResponse = {
  instrumentId: number;
  items: CoverageSegment[];
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";
export const JANUARY_2026_BACKFILL_START = "2026-01-01T00:00:00+09:00";
export const JANUARY_2026_BACKFILL_END = "2026-02-01T00:00:00+09:00";

export function analysisWebSocketUrl(): string {
  const apiUrl = new URL(API_BASE_URL, window.location.origin);
  apiUrl.protocol = apiUrl.protocol === "https:" ? "wss:" : "ws:";
  apiUrl.pathname = `${apiUrl.pathname.replace(/\/$/, "")}/v1/realtime/analysis/stream`;
  return apiUrl.toString();
}

export function systemManagementWebSocketUrl(): string {
  const apiUrl = new URL(API_BASE_URL, window.location.origin);
  apiUrl.protocol = apiUrl.protocol === "https:" ? "wss:" : "ws:";
  apiUrl.pathname = `${apiUrl.pathname.replace(/\/$/, "")}/v1/realtime/system-management`;
  return apiUrl.toString();
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new Error(`${path} failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function loadOperationsSnapshot(): Promise<OperationsSnapshot> {
  const [dashboardResponse, jobs] = await Promise.all([
    getJson<DashboardSummary>("/v1/dashboard/summary"),
    getJson<{ items: BackfillJob[] }>("/v1/backfill/jobs")
  ]);
  const dashboard = normalizeDashboardSummary(dashboardResponse);
  return {
    dashboard,
    candidateEntries: [],
    marketRows: [],
    detail: null,
    candles: [],
    backfillJobs: jobs.items.map(normalizeBackfillJob),
    notifications: dashboard.alerts,
    source: "api"
  };
}

export function subscribeDashboardSummary(
  handler: (dashboard: DashboardSummary) => void
): () => void {
  if (typeof EventSource === "undefined") {
    return () => undefined;
  }
  const source = new EventSource(`${API_BASE_URL}/v1/dashboard/summary/stream`);
  source.addEventListener("dashboard", (event) => {
    const message = event as MessageEvent<string>;
    handler(normalizeDashboardSummary(JSON.parse(message.data) as DashboardSummary));
  });
  return () => source.close();
}

export function subscribeMarketList(handler: (rows: MarketListRow[]) => void): () => void {
  if (typeof EventSource === "undefined") {
    return () => undefined;
  }
  const source = new EventSource(`${API_BASE_URL}/v1/market-list/stream`);
  source.addEventListener("marketList", (event) => {
    const message = event as MessageEvent<string>;
    const payload = JSON.parse(message.data) as { rows: MarketListRow[] };
    handler(payload.rows.map(normalizeMarketListRow));
  });
  return () => source.close();
}

function normalizeDashboardSummary(response: DashboardSummary): DashboardSummary {
  const dashboard = response as DashboardSummary & Partial<DashboardSummary>;
  const totals = dashboard.totals as DashboardSummary["totals"] &
    Partial<DashboardSummary["totals"]>;

  return {
    ...dashboard,
    totals: {
      ...totals,
      storageRowsToday: numberOrZero(totals.storageRowsToday),
      realtimeRowsLastMinute: numberOrZero(totals.realtimeRowsLastMinute),
      backfillRowsLastMinute: numberOrZero(totals.backfillRowsLastMinute)
    },
    coverage: dashboard.coverage ?? [],
    targets: (dashboard.targets ?? []).map(normalizeDashboardTarget),
    alerts: dashboard.alerts ?? [],
    healthChecks: dashboard.healthChecks ?? [],
    metricPrinciples: dashboard.metricPrinciples ?? [],
    collectionActivity: dashboard.collectionActivity ?? [],
    realtimeCollectionHeatmap: normalizeRealtimeCollectionHeatmapRows(
      dashboard.realtimeCollectionHeatmap ?? []
    ),
    workerStatus: normalizeCollectionWorkerStatus(dashboard.workerStatus),
    storageBreakdown: dashboard.storageBreakdown ?? [],
    operationsTrend: dashboard.operationsTrend ?? [],
    missingRangeTop: dashboard.missingRangeTop ?? [],
    auditLogSummary: dashboard.auditLogSummary ?? {
      targetChangeCount24h: 0,
      backfillChangeCount24h: 0,
      latestChangeAt: null,
      latestChangeLabel: "기록 없음"
    }
  };
}

function normalizeCollectionWorkerStatus(
  workerStatus: CollectionWorkerStatus | undefined
): CollectionWorkerStatus {
  return {
    realtime: {
      status: workerStatus?.realtime?.status ?? "stale",
      statusLabel: workerStatus?.realtime?.statusLabel ?? "중지 추정",
      statusDetail: workerStatus?.realtime?.statusDetail ?? "worker 상태 데이터가 없습니다.",
      lastHeartbeatAt: workerStatus?.realtime?.lastHeartbeatAt ?? null,
      lastCollectedAt: workerStatus?.realtime?.lastCollectedAt ?? null,
      collectedRowCount24h: numberOrZero(workerStatus?.realtime?.collectedRowCount24h),
      errorCount24h: numberOrZero(workerStatus?.realtime?.errorCount24h),
      failureRate24h: workerStatus?.realtime?.failureRate24h ?? "0",
      diagnostics: workerStatus?.realtime?.diagnostics ?? [],
      recentErrors: workerStatus?.realtime?.recentErrors ?? []
    },
    backfill: {
      status: workerStatus?.backfill?.status ?? "stale",
      statusLabel: workerStatus?.backfill?.statusLabel ?? "중지 추정",
      statusDetail: workerStatus?.backfill?.statusDetail ?? "worker 상태 데이터가 없습니다.",
      lastHeartbeatAt: workerStatus?.backfill?.lastHeartbeatAt ?? null,
      lastCollectedAt: workerStatus?.backfill?.lastCollectedAt ?? null,
      totalErrorCount: numberOrZero(workerStatus?.backfill?.totalErrorCount),
      failureRateAll: workerStatus?.backfill?.failureRateAll ?? "0",
      runningTargetCount: numberOrZero(workerStatus?.backfill?.runningTargetCount),
      totalTargetCount: numberOrZero(workerStatus?.backfill?.totalTargetCount),
      queuedJobCount: numberOrZero(workerStatus?.backfill?.queuedJobCount),
      queuedTargetCount: numberOrZero(workerStatus?.backfill?.queuedTargetCount),
      diagnostics: workerStatus?.backfill?.diagnostics ?? [],
      recentErrors: workerStatus?.backfill?.recentErrors ?? []
    }
  };
}

function normalizeBackfillJob(job: BackfillJob): BackfillJob {
  return {
    ...job,
    progressPercent: job.progressPercent ?? "0",
    estimatedRequestCount: numberOrZero(job.estimatedRequestCount),
    totalTargetCount: numberOrZero(job.totalTargetCount || job.targets?.length),
    completedTargetCount: numberOrZero(job.completedTargetCount),
    runningTargetIndex: job.runningTargetIndex ?? null,
    currentTarget: job.currentTarget ?? null,
    currentTargetBackfillRowCount: numberOrZero(job.currentTargetBackfillRowCount),
    processedMissingRangeCount: numberOrZero(job.processedMissingRangeCount),
    estimatedMissingRangeCount: numberOrZero(job.estimatedMissingRangeCount),
    targetStartAt: job.targetStartAt ?? JANUARY_2026_BACKFILL_START,
    targetEndAt: job.targetEndAt ?? JANUARY_2026_BACKFILL_END,
    targets: job.targets ?? [],
    attemptCount: numberOrZero(job.attemptCount),
    maxAttempts: numberOrZero(job.maxAttempts),
    nextRetryAt: job.nextRetryAt ?? null,
    lastErrorCode: job.lastErrorCode ?? null,
    deadLetterReason: job.deadLetterReason ?? null
  };
}

function normalizeDashboardTarget(target: CollectionDashboardTarget): CollectionDashboardTarget {
  return {
    ...target,
    coverageSegments: target.coverageSegments ?? [],
    changeRate: target.changeRate ?? "0",
    accTradePrice24hDisplay: target.accTradePrice24hDisplay ?? "₩0",
    tickerFreshnessLabel: target.tickerFreshnessLabel ?? formatCollectionTargetFreshness(target),
    coveragePercent: target.coveragePercent ?? "0",
    storageRowCount: numberOrZero(target.storageRowCount),
    storageBytesDisplay: target.storageBytesDisplay ?? "0B"
  };
}

function normalizeRealtimeCollectionHeatmapRows(
  rows: RealtimeCollectionHeatmapRow[]
): RealtimeCollectionHeatmapRow[] {
  const currentHour = new Date();
  const currentHourStart = new Date(
    currentHour.getFullYear(),
    currentHour.getMonth(),
    currentHour.getDate(),
    currentHour.getHours()
  );
  return rows.slice(0, 50).map((row) => ({
    ...row,
    hourlyBuckets: normalizeRealtimeHeatmapBuckets(row.hourlyBuckets, currentHourStart)
  }));
}

function normalizeRealtimeHeatmapBuckets(
  buckets: RealtimeCollectionHeatmapCell[],
  currentHourStart: Date
): RealtimeCollectionHeatmapCell[] {
  const wanted = 24;
  if (buckets.length >= wanted) {
    return buckets.slice(-wanted);
  }

  const existingBuckets = [...buckets].sort(
    (left, right) =>
      new Date(left.bucketStartAt).getTime() - new Date(right.bucketStartAt).getTime()
  );
  const bucketsToPrepend = wanted - buckets.length;
  const firstStart = new Date(currentHourStart.getTime() - 3600 * 1000 * (bucketsToPrepend));
  const padding = Array.from({ length: bucketsToPrepend }, (_, index) => ({
    bucketStartAt: new Date(firstStart.getTime() + index * 3600 * 1000).toISOString(),
    tradeCount: 0,
    averageTradesPerMinute: "0",
    tradeStrength: "0",
    tradeVolume: "0",
    tradeAmount: "0",
    status: "red" as const
  }));
  return [...padding, ...existingBuckets].slice(-wanted);
}

function formatCollectionTargetFreshness(target: CollectionDashboardTarget): string {
  const latestAt = target.dataStatuses
    .map((status) => status.lastSuccessfulAt)
    .filter(Boolean)
    .sort()
    .at(-1);
  return latestAt ?? target.plan.rangeStartAt;
}

function numberOrZero(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export async function loadCandidateUniverse(): Promise<CandidateUniverseEntry[]> {
  const universe = await getJson<{ entries: CandidateUniverseEntry[] }>("/v1/candidate-universe");
  return universe.entries;
}

export async function loadDataFoundation(): Promise<DataFoundation> {
  return getJson<DataFoundation>("/v1/data-foundation");
}

export async function updateMarketTargetState(
  marketCode: string,
  state: "active" | "paused" | "excluded",
  reason: string,
  actorId: string,
  policy?: MarketCollectionPolicy
): Promise<{ marketCode: string; state: string; changedAt: string }> {
  const requestId = globalThis.crypto.randomUUID();
  return sendJson(`/v1/data-foundation/markets/${encodeURIComponent(marketCode)}`, "PATCH", {
    requestId,
    idempotencyKey: `market:${marketCode}:${requestId}`,
    actorId,
    requestedAt: new Date().toISOString(),
    state,
    reason,
    ...(policy ? { policy } : {})
  });
}

export async function loadMarketList(): Promise<MarketListRow[]> {
  const market = await getJson<{ rows: MarketListRow[] }>("/v1/market-list");
  return market.rows.map(normalizeMarketListRow);
}

function normalizeMarketListRow(row: MarketListRow): MarketListRow {
  return {
    ...row,
    assetType: row.assetType ?? "coin",
    isFavorite: row.isFavorite ?? false,
    favoriteOrder: row.favoriteOrder ?? null,
    priceCurrency: row.priceCurrency ?? row.instrument.quoteCurrency,
    tradeAmountCurrency: row.tradeAmountCurrency ?? row.instrument.quoteCurrency,
    changeRateBasis: row.changeRateBasis ?? "전일 종가 대비",
    tickerCollectedAt: row.tickerCollectedAt ?? null,
    orderbookCollectedAt: row.orderbookCollectedAt ?? null,
    candleCoverageStartAt: row.candleCoverageStartAt ?? null,
    candleCoverageEndAt: row.candleCoverageEndAt ?? null,
    candleCoverageCurrentAt: row.candleCoverageCurrentAt ?? new Date().toISOString(),
    oneMinuteCandleCount: numberOrZero(row.oneMinuteCandleCount),
    storageRowCount: numberOrZero(row.storageRowCount)
  };
}

export async function loadCollectionCoverageSegments(
  instrumentId: number
): Promise<CoverageSegment[]> {
  const response = await getJson<CollectionCoverageSegmentsResponse>(
    `/v1/collection-targets/${instrumentId}/coverage-segments`
  );
  return response.items;
}

export async function loadDatasetBuilds(options: {
  pageSize?: number;
  cursor?: string | null;
} = {}): Promise<DatasetBuildsResponse> {
  const params = new URLSearchParams({ pageSize: String(options.pageSize ?? 50) });
  if (options.cursor) params.set("cursor", options.cursor);
  return getJson<DatasetBuildsResponse>(`/v1/dataset-builds?${params.toString()}`);
}

export async function createDatasetBuild(
  command: CreateDatasetBuildCommand
): Promise<DatasetBuild> {
  return sendJson<DatasetBuild>("/v1/dataset-builds", "POST", command);
}

export async function loadDatasetVersions(options: {
  pageSize?: number;
  cursor?: string | null;
} = {}): Promise<DatasetVersionsResponse> {
  const params = new URLSearchParams({ pageSize: String(options.pageSize ?? 50) });
  if (options.cursor) params.set("cursor", options.cursor);
  return getJson<DatasetVersionsResponse>(`/v1/dataset-versions?${params.toString()}`);
}

export async function loadDatasetVersion(datasetVersionId: number): Promise<DatasetVersion> {
  return getJson<DatasetVersion>(`/v1/dataset-versions/${datasetVersionId}`);
}

export async function loadDatasetCoverage(datasetVersionId: number): Promise<DatasetCoverage> {
  return getJson<DatasetCoverage>(`/v1/dataset-versions/${datasetVersionId}/coverage`);
}

export async function loadDatasetSeries(options: {
  datasetVersionId: number;
  seriesId: number;
  from: string;
  to: string;
  pageSize?: number;
  cursor?: string | null;
}): Promise<DatasetSeriesResponse> {
  const params = new URLSearchParams({
    seriesId: String(options.seriesId),
    from: options.from,
    to: options.to,
    pageSize: String(options.pageSize ?? 500)
  });
  if (options.cursor) params.set("cursor", options.cursor);
  return getJson<DatasetSeriesResponse>(
    `/v1/dataset-versions/${options.datasetVersionId}/series?${params.toString()}`
  );
}

export async function loadInstrumentSnapshot(
  instrumentId: number
): Promise<{ detail: InstrumentDetail; candles: Candle[] }> {
  const detail = await getJson<InstrumentDetail>(`/v1/instruments/${instrumentId}`);
  const candles = await getJson<{ candles: Candle[] }>(
    `/v1/instruments/${instrumentId}/candles?unit=1m&from=${encodeURIComponent(
      JANUARY_2026_BACKFILL_START
    )}&to=${encodeURIComponent(JANUARY_2026_BACKFILL_END)}`
  );
  return { detail, candles: candles.candles };
}

async function sendJson<T>(path: string, method: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json"
  };
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (!response.ok) {
    throw new Error(`${path} failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

async function sendEmpty(path: string, method: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: {}
  });
  if (!response.ok) {
    throw new Error(`${path} failed with ${response.status}`);
  }
}

export async function updateCollectionTargets(instrumentIds: number[]): Promise<void> {
  await sendJson("/v1/collection-targets", "PUT", {
    instrumentIds,
    reason: "운영 화면에서 수집 대상 변경"
  });
}

export async function updateFavoriteTargets(instrumentIds: number[]): Promise<void> {
  await sendJson("/v1/collection-targets", "PUT", {
    instrumentIds,
    reason: "관심종목 화면에서 관심목록 변경"
  });
}

export type BackfillPlan = {
  planId: string;
  dataType: string;
  estimatedRequestCount: number;
  estimatedRowCount: number;
  estimatedStorageBytes: number;
  targets: number[];
};

export type CreateBackfillPlanOptions = {
  targetStartAt?: string;
  targetEndAt?: string;
  dataType?: "source_candle";
};

export async function createBackfillPlan(
  instrumentIds: number[],
  options: CreateBackfillPlanOptions = {}
): Promise<BackfillPlan> {
  return sendJson<BackfillPlan>("/v1/backfill/plans", "POST", {
    dataType: options.dataType ?? "source_candle",
    targetStartAt: options.targetStartAt ?? JANUARY_2026_BACKFILL_START,
    targetEndAt: options.targetEndAt ?? JANUARY_2026_BACKFILL_END,
    instrumentIds
  });
}

export async function startBackfillJob(
  instrumentIds: number[],
  options: CreateBackfillPlanOptions = {}
): Promise<BackfillJob> {
  return normalizeBackfillJob(
    await sendJson<BackfillJob>("/v1/backfill/jobs", "POST", {
      dataType: options.dataType ?? "source_candle",
      targetStartAt: options.targetStartAt ?? JANUARY_2026_BACKFILL_START,
      targetEndAt: options.targetEndAt ?? JANUARY_2026_BACKFILL_END,
      instrumentIds
    })
  );
}

export async function controlBackfillJob(jobId: number, action: string): Promise<BackfillJob> {
  return normalizeBackfillJob(
    await sendJson<BackfillJob>(`/v1/backfill/jobs/${jobId}/${action}`, "POST")
  );
}

export async function deleteBackfillJob(jobId: number): Promise<void> {
  await sendEmpty(`/v1/backfill/jobs/${jobId}`, "DELETE");
}
