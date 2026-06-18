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
    failedRuns24h: number;
    delayedTargets: number;
    missingRangesOpen: number;
  };
  coverage: CoverageStatus[];
  targets: CollectionDashboardTarget[];
  alerts: NotificationEvent[];
};

export type CandidateUniverseEntry = {
  instrument: Instrument;
  rank: number;
  accTradePrice24h: string;
  selected: boolean;
  candidateStatus: "in_universe" | "out_of_universe";
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
  detail: InstrumentDetail;
  candles: Candle[];
  backfillJobs: BackfillJob[];
  notifications: NotificationEvent[];
  source: "api" | "fixture";
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const OPERATOR_TOKEN = import.meta.env.VITE_OPERATOR_TOKEN ?? "local-dev-token";
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
  const dashboard = await getJson<DashboardSummary>("/v1/dashboard/summary");
  const universe = await getJson<{ entries: CandidateUniverseEntry[] }>("/v1/candidate-universe");
  const market = await getJson<{ rows: MarketListRow[] }>("/v1/market-list");
  const firstInstrumentId =
    market.rows.find((row) => row.instrument.marketCode === "KRW-BTC")?.instrument.id ??
    market.rows[0]?.instrument.id ??
    universe.entries.find((entry) => entry.instrument.marketCode === "KRW-BTC")?.instrument.id ??
    universe.entries[0]?.instrument.id;
  if (firstInstrumentId === undefined) {
    throw new Error("M1 API has no instrument");
  }
  const instrument = await loadInstrumentSnapshot(firstInstrumentId);
  const jobs = await getJson<{ items: BackfillJob[] }>("/v1/backfill/jobs");
  const notifications = await getJson<{ items: NotificationEvent[] }>("/v1/notifications");
  return {
    dashboard,
    candidateEntries: universe.entries,
    marketRows: market.rows,
    detail: instrument.detail,
    candles: instrument.candles,
    backfillJobs: jobs.items,
    notifications: notifications.items,
    source: "api"
  };
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
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Operator-Token": OPERATOR_TOKEN
    },
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
    selected: index < 50,
    candidateStatus: "in_universe" as const
  }));
  const marketRows = instruments.slice(0, 50).map((instrument, index) => ({
    instrument,
    tradePrice: `${1000000 - index * 1250}`,
    accTradePrice24h: `${100000000000 - index * 1000000}`,
    accTradePrice24hDisplay: `${100000000000 - index * 1000000}`,
    changeRate: `${(index % 7) / 100}`,
    tickerCollectedAt: now,
    orderbookCollectedAt: now,
    qualityStatus: "normal" as const
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
        missingSegmentCount: 1
      },
      {
        dataType: "ticker_snapshot" as const,
        label: "현재가",
        status: "normal" as const,
        statusLabel: "정상",
        lastSuccessfulAt: now,
        progressPercent: "100",
        missingSegmentCount: 0
      },
      {
        dataType: "orderbook_summary" as const,
        label: "호가 요약",
        status: "normal" as const,
        statusLabel: "정상",
        lastSuccessfulAt: now,
        progressPercent: "100",
        missingSegmentCount: 0
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
        displayRange: "2026-01-01 00:00 KST ~ 현재(지속)",
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
        failedRuns24h: 0,
        delayedTargets: 0,
        missingRangesOpen: 0
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
      ]
    },
    candles: [],
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
