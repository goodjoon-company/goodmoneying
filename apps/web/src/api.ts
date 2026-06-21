export type Status = "normal" | "warning" | "incident";

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
  rangeTimeZone: "KST" | "UTC";
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
  expectedRowsAll: number;
  actualRowsAll: number;
  expectedRowsByType: {
    source_candle: number;
    ticker_snapshot: number;
    orderbook_summary: number;
  };
  actualRowsByType: {
    source_candle: number;
    ticker_snapshot: number;
    orderbook_summary: number;
  };
  actualRatioPercent: string;
  status: "none" | "low" | "collecting" | "high";
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

export type RealtimeWorkerStatus = {
  status: "running" | "stale" | "failed";
  statusLabel: string;
  statusDetail: string;
  lastHeartbeatAt: string | null;
  lastCollectedAt: string | null;
  errorCount24h: number;
  failureRate24h: string;
  recentErrors: CollectionWorkerError[];
};

export type BackfillWorkerStatus = {
  status: "running" | "stale" | "failed";
  statusLabel: string;
  statusDetail: string;
  lastHeartbeatAt: string | null;
  lastCollectedAt: string | null;
  totalErrorCount: number;
  failureRateAll: string;
  runningTargetCount: number;
  totalTargetCount: number;
  recentErrors: CollectionWorkerError[];
};

export type CollectionWorkerStatus = {
  realtime: RealtimeWorkerStatus;
  backfill: BackfillWorkerStatus;
};

export type StorageBreakdownItem = {
  dataType: "source_candle" | "ticker_snapshot" | "orderbook_summary" | "quality_result";
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
  candidateStatus: "in_universe" | "out_of_universe";
  qualityStatus: Status;
  qualityDetail: string;
  collectionRangeDisplay: string;
};

export type MarketListRow = {
  instrument: Instrument;
  tradePrice: string;
  accTradePrice24h: string;
  accTradePrice24hDisplay: string;
  changeRate: string;
  tickerCollectedAt: string;
  orderbookCollectedAt: string;
  qualityStatus: Status;
  coveragePercent: string;
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
  status: "planned" | "pending" | "running" | "paused" | "stopped" | "succeeded" | "failed";
  dataType: string;
  progressPercent: string;
  createdAt: string;
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
const OPERATOR_TOKEN = import.meta.env.VITE_OPERATOR_TOKEN ?? "";
export const JANUARY_2026_BACKFILL_START = "2026-01-01T00:00:00.000Z";
export const JANUARY_2026_BACKFILL_END = "2026-02-01T00:00:00.000Z";

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
    backfillJobs: jobs.items,
    notifications: dashboard.alerts,
    source: "api"
  };
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
      errorCount24h: numberOrZero(workerStatus?.realtime?.errorCount24h),
      failureRate24h: workerStatus?.realtime?.failureRate24h ?? "0",
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
      recentErrors: workerStatus?.backfill?.recentErrors ?? []
    }
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
  const seed = existingBuckets[existingBuckets.length - 1];
  const fallbackExpectedRowsByType = {
    source_candle: 60,
    ticker_snapshot: 60,
    orderbook_summary: 60
  };
  const expectedRowsByType = seed?.expectedRowsByType ?? fallbackExpectedRowsByType;
  const expectedRowsAll = seed?.expectedRowsAll ?? 180;
  const bucketsToPrepend = wanted - buckets.length;
  const firstStart = new Date(currentHourStart.getTime() - 3600 * 1000 * (bucketsToPrepend));
  const padding = Array.from({ length: bucketsToPrepend }, (_, index) => ({
    bucketStartAt: new Date(firstStart.getTime() + index * 3600 * 1000).toISOString(),
    expectedRowsAll,
    actualRowsAll: 0,
    expectedRowsByType,
    actualRowsByType: {
      source_candle: 0,
      ticker_snapshot: 0,
      orderbook_summary: 0
    },
    actualRatioPercent: "0",
    status: "none" as const
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

export async function loadMarketList(): Promise<MarketListRow[]> {
  const market = await getJson<{ rows: MarketListRow[] }>("/v1/market-list");
  return market.rows.map(normalizeMarketListRow);
}

function normalizeMarketListRow(row: MarketListRow): MarketListRow {
  return {
    ...row,
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
  if (OPERATOR_TOKEN) {
    headers["X-Operator-Token"] = OPERATOR_TOKEN;
  }
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

export async function updateCollectionTargets(instrumentIds: number[]): Promise<void> {
  await sendJson("/v1/collection-targets", "PUT", {
    instrumentIds,
    reason: "운영 화면에서 수집 대상 변경"
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

export async function approveBackfillJob(planId: string): Promise<BackfillJob> {
  return sendJson<BackfillJob>("/v1/backfill/jobs", "POST", { planId });
}

export async function controlBackfillJob(jobId: number, action: string): Promise<BackfillJob> {
  return sendJson<BackfillJob>(`/v1/backfill/jobs/${jobId}/${action}`, "POST");
}
