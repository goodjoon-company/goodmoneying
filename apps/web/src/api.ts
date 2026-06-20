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
  source: "api" | "fixture";
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

export { demoSnapshot } from "./operationsFixture";
