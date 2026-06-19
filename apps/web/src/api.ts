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
    recentRequestCount: number;
    rateLimitRemainingPercent: string;
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
  duplicateRows24h: number;
  tickerFreshnessLabel: string;
  orderbookFreshnessLabel: string;
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
  const [dashboard, jobs] = await Promise.all([
    getJson<DashboardSummary>("/v1/dashboard/summary"),
    getJson<{ items: BackfillJob[] }>("/v1/backfill/jobs")
  ]);
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

export async function loadCandidateUniverse(): Promise<CandidateUniverseEntry[]> {
  const universe = await getJson<{ entries: CandidateUniverseEntry[] }>("/v1/candidate-universe");
  return universe.entries;
}

export async function loadMarketList(): Promise<MarketListRow[]> {
  const market = await getJson<{ rows: MarketListRow[] }>("/v1/market-list");
  return market.rows;
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

export async function createBackfillPlan(instrumentIds: number[]): Promise<BackfillPlan> {
  return sendJson<BackfillPlan>("/v1/backfill/plans", "POST", {
    dataType: "source_candle",
    targetStartAt: JANUARY_2026_BACKFILL_START,
    targetEndAt: JANUARY_2026_BACKFILL_END,
    instrumentIds
  });
}

export async function approveBackfillJob(planId: string): Promise<BackfillJob> {
  return sendJson<BackfillJob>("/v1/backfill/jobs", "POST", { planId });
}

export async function controlBackfillJob(jobId: number, action: string): Promise<BackfillJob> {
  return sendJson<BackfillJob>(`/v1/backfill/jobs/${jobId}/${action}`, "POST");
}

export function demoSnapshot(): OperationsSnapshot {
  const now = new Date("2026-06-18T00:00:00Z").toISOString();
  const instruments = Array.from({ length: 100 }, (_, index) => {
    const rank = index + 1;
    const code =
      rank === 1 ? "KRW-BTC" : rank === 2 ? "KRW-ETH" : `KRW-GM${rank.toString().padStart(3, "0")}`;
    return {
      id: rank,
      exchange: "UPBIT" as const,
      marketCode: code,
      quoteCurrency: "KRW",
      baseAsset: code.replace("KRW-", ""),
      displayName: rank === 1 ? "비트코인" : rank === 2 ? "이더리움" : `굿머니코인 ${rank}`
    };
  });
  const candidateEntries = instruments.map((instrument, index) => ({
    instrument,
    rank: index + 1,
    accTradePrice24h: `${100000000000 - index * 1000000}`,
    accTradePrice24hDisplay: `₩${(100000000000 - index * 1000000).toLocaleString("ko-KR")}`,
    selected: index < 50,
    candidateStatus: "in_universe" as const,
    qualityStatus: index % 9 === 0 ? ("warning" as const) : ("normal" as const),
    qualityDetail:
      index % 9 === 0
        ? "품질 주의: 결측 1구간, 백필 확인 필요"
        : "품질 정상: 최신성/결측/중복/저장량 기준 정상권",
    collectionRangeDisplay: "2024-01-01 00:00 KST ~ NOW"
  }));
  const marketRows = instruments.slice(0, 50).map((instrument, index) => ({
    instrument,
    tradePrice: `${1000000 - index * 1250}`,
    accTradePrice24h: `${100000000000 - index * 1000000}`,
    accTradePrice24hDisplay: `₩${(100000000000 - index * 1000000).toLocaleString("ko-KR")}`,
    changeRate: `${(index % 7) / 100}`,
    tickerCollectedAt: now,
    orderbookCollectedAt: now,
    qualityStatus: index % 13 === 0 ? ("warning" as const) : ("normal" as const),
    coveragePercent: `${100 - (index % 6) * 1.6}`,
    storageBytes: 24000000 - index * 120000,
    storageBytesDisplay: `${(24 - index * 0.12).toFixed(1)}MB`
  }));
  const targetRows = instruments.slice(0, 50).map((instrument) => {
    const dataStatuses = [
      {
        dataType: "source_candle" as const,
        label: "캔들",
        status: "normal" as const,
        statusLabel: "정상",
        lastSuccessfulAt: now,
        progressPercent: "100",
        missingSegmentCount: 1,
        storedRowCount: 44640
      },
      {
        dataType: "ticker_snapshot" as const,
        label: "현재가",
        status: "normal" as const,
        statusLabel: "정상",
        lastSuccessfulAt: now,
        progressPercent: "100",
        missingSegmentCount: 0,
        storedRowCount: 0
      },
      {
        dataType: "orderbook_summary" as const,
        label: "호가 요약",
        status: "normal" as const,
        statusLabel: "정상",
        lastSuccessfulAt: now,
        progressPercent: "100",
        missingSegmentCount: 0,
        storedRowCount: 0
      }
    ];
    const rangeStartAt = "2025-12-31T15:00:00.000Z";
    return {
      instrument,
      overallStatus: "latest_collecting" as const,
      overallStatusLabel: "최신수집중",
      plan: {
        instrumentId: instrument.id,
        preset: "2026년 1월 1분봉",
        rangeStartAt,
        rangeEndAt: null,
        isContinuous: true,
        method: "safe_restart",
        displayRange: "2026-01-01 00:00 KST ~ NOW",
        rangeTimeZone: "KST" as const,
        progressBasis: "현재(지속)은 KST 전일 23:59:59까지 기준"
      },
      dataStatuses,
      coverageSegments: [
        {
          dataType: "source_candle" as const,
          status: "collected" as const,
          offsetPercent: "0",
          widthPercent: "64",
          segmentStartAt: rangeStartAt,
          segmentEndAt: now,
          label: "수집 완료"
        },
        {
          dataType: "source_candle" as const,
          status: "missing" as const,
          offsetPercent: "64",
          widthPercent: "8",
          segmentStartAt: rangeStartAt,
          segmentEndAt: now,
          label: "결측"
        },
        {
          dataType: "source_candle" as const,
          status: "collected" as const,
          offsetPercent: "72",
          widthPercent: "28",
          segmentStartAt: rangeStartAt,
          segmentEndAt: now,
          label: "수집 완료"
        },
        ...dataStatuses
          .filter((status) => status.dataType !== "source_candle")
          .map((status) => ({
            dataType: status.dataType,
            status: "collected" as const,
            offsetPercent: "0",
            widthPercent: "100",
            segmentStartAt: rangeStartAt,
            segmentEndAt: now,
            label: "수집 완료"
          }))
      ]
    };
  });
  const latestTicker = {
    bucketAt: now,
    tradePrice: "1000000",
    accTradePrice24h: "100000000000",
    changeRate: "0.012",
    collectedAt: now
  };
  const latestOrderbook = {
    bucketAt: now,
    bestBidPrice: "999990",
    bestBidSize: "1.5",
    bestAskPrice: "1000010",
    bestAskSize: "1.2",
    spread: "20",
    bidDepth10: "1200",
    askDepth10: "1100",
    imbalance10: "0.0434",
    collectedAt: now
  };
  return {
    dashboard: {
      status: "normal",
      refreshedAt: now,
      totals: {
        activeTargets: 50,
        activeTargetLimit: 50,
        normalTargets: 47,
        warningTargets: 2,
        incidentTargets: 1,
        failedRuns24h: 0,
        failureRate24h: "0.0018",
        delayedTargets: 0,
        missingRangesOpen: 0,
        storageBytesToday: 81388912640,
        storageBytesTodayDisplay: "75.8GB",
        recentRequestCount: 14200,
        rateLimitRemainingPercent: "64"
      },
      coverage: [
        {
          instrumentId: 1,
          dataType: "ticker_snapshot",
          status: "normal",
          progressPercent: "100",
          lastSuccessfulAt: now
        },
        {
          instrumentId: 1,
          dataType: "orderbook_summary",
          status: "normal",
          progressPercent: "100",
          lastSuccessfulAt: now
        },
        {
          instrumentId: 1,
          dataType: "source_candle",
          status: "normal",
          progressPercent: "100",
          lastSuccessfulAt: now
        }
      ],
      targets: targetRows,
      alerts: [
        {
          id: 1,
          severity: "info",
          eventType: "collector_bootstrap",
          title: "M1 fixture 수집 완료",
          message: "후보 유니버스와 기본 활성 수집 대상 50개가 준비되었습니다.",
          status: "open",
          createdAt: now
        }
      ],
      healthChecks: [
        {
          title: "현재가·거래대금",
          status: "normal",
          statusLabel: "정상",
          detail: "최근 1-3분 정상"
        },
        {
          title: "캔들 상태",
          status: "normal",
          statusLabel: "정상",
          detail: "직전 완성 1분봉 저장"
        },
        {
          title: "호가 상태",
          status: "normal",
          statusLabel: "정상",
          detail: "매수 잔량 우세"
        },
        {
          title: "완전성 검사",
          status: "warning",
          statusLabel: "주의",
          detail: "결측 1구간"
        }
      ]
    },
    candidateEntries,
    marketRows,
    detail: {
      instrument: instruments[0],
      latestTicker,
      latestOrderbook,
      coverage: [
        {
          instrumentId: 1,
          dataType: "ticker_snapshot",
          status: "normal",
          progressPercent: "100",
          lastSuccessfulAt: now
        }
      ],
      duplicateRows24h: 0,
      tickerFreshnessLabel: "49초 전",
      orderbookFreshnessLabel: "57초 전"
    },
    candles: demoCandles("1000000"),
    backfillJobs: [],
    notifications: [
      {
        id: 1,
        severity: "info",
        eventType: "collector_bootstrap",
        title: "M1 fixture 수집 완료",
        message: "로컬 fixture 데이터를 표시하고 있습니다.",
        status: "open",
        createdAt: now
      }
    ],
    source: "fixture"
  };
}

function demoCandles(anchorPrice: string): Candle[] {
  const base = Number(anchorPrice);
  const start = Date.parse("2026-01-01T00:00:00.000Z");
  return Array.from({ length: 96 }, (_, index) => {
    const open = base + Math.sin(index / 7) * 2400 + index * 38;
    const close = open + Math.cos(index / 5) * 1800;
    return {
      startedAt: new Date(start + index * 60_000).toISOString(),
      open: `${Math.round(open)}`,
      high: `${Math.round(Math.max(open, close) + 1200)}`,
      low: `${Math.round(Math.min(open, close) - 1200)}`,
      close: `${Math.round(close)}`,
      volume: `${120 + index * 1.7}`,
      tradeAmount: `${Math.round(close * (120 + index * 1.7))}`,
      completeness: "complete"
    };
  });
}
