import type {
  BackfillJob,
  Candle,
  CandidateUniverseEntry,
  CollectionDashboardTarget,
  DashboardSummary,
  Instrument,
  InstrumentDetail,
  MarketListRow,
  OperationsSnapshot
} from "./api";

const NOW = "2026-06-19T00:00:00.000Z";

export function createTestInstruments(size = 100): Instrument[] {
  return Array.from({ length: size }, (_, index) => {
    const rank = index + 1;
    const marketCode =
      rank === 1 ? "KRW-BTC" : rank === 2 ? "KRW-ETH" : `KRW-GM${rank.toString().padStart(3, "0")}`;
    return {
      id: rank,
      exchange: "UPBIT",
      marketCode,
      quoteCurrency: "KRW",
      baseAsset: marketCode.replace("KRW-", ""),
      displayName: rank === 1 ? "비트코인" : rank === 2 ? "이더리움" : `굿머니코인 ${rank}`
    };
  });
}

export function createTestCandidateUniverse(): CandidateUniverseEntry[] {
  return createTestInstruments(100).map((instrument, index) => ({
    instrument,
    rank: index + 1,
    accTradePrice24h: `${100000000000 - index * 1000000}`,
    accTradePrice24hDisplay: `₩${(100000000000 - index * 1000000).toLocaleString("ko-KR")}`,
    selected: index < 50,
    candidateStatus: "in_universe",
    qualityStatus: index % 9 === 0 ? "warning" : "normal",
    qualityDetail: index % 9 === 0 ? "품질 주의" : "정상",
    collectionRangeDisplay: "2026-01-01 00:00 KST ~ NOW",
    collectedStartAt: "2026-01-01T00:00:00+09:00",
    collectedEndAt: "2026-06-19T09:00:00+09:00",
    isRealtimeTarget: index < 50
  }));
}

export function createTestMarketRows(): MarketListRow[] {
  return createTestInstruments(50).map((instrument, index) => ({
    instrument,
    tradePrice: `${1000000 + index * 1000}`,
    accTradePrice24h: `${100000000000 - index * 1000000}`,
    accTradePrice24hDisplay: `₩${(100000000000 - index * 1000000).toLocaleString("ko-KR")}`,
    changeRate: index % 2 === 0 ? "0.012" : "-0.004",
    tickerCollectedAt: NOW,
    orderbookCollectedAt: NOW,
    qualityStatus: index % 9 === 0 ? "warning" : "normal",
    coveragePercent: "99.1",
    storageBytes: 1024,
    storageRowCount: 1000 + index,
    storageBytesDisplay: "1.0KB"
  }));
}

export function createTestDashboardSummary(
  overrides: Partial<DashboardSummary> = {}
): DashboardSummary {
  const instruments = createTestInstruments(50);
  const targets = instruments.map(createDashboardTarget);
  return {
    status: "normal",
    refreshedAt: NOW,
    totals: {
      activeTargets: 50,
      activeTargetLimit: 50,
      normalTargets: 49,
      warningTargets: 1,
      incidentTargets: 0,
      failedRuns24h: 0,
      failureRate24h: "0",
      delayedTargets: 0,
      missingRangesOpen: 0,
      storageBytesToday: 1024,
      storageBytesTodayDisplay: "1.0KB",
      storageRowsToday: 4,
      realtimeRowsLastMinute: 3,
      backfillRowsLastMinute: 1,
      recentRequestCount: 3
    },
    coverage: [],
    targets,
    alerts: [],
    healthChecks: [
      {
        title: "수집기",
        status: "normal",
        statusLabel: "정상",
        detail: "최근 수집 정상"
      }
    ],
    metricPrinciples: [],
    collectionActivity: [],
    workerStatus: {
      realtime: {
        status: "running",
        statusLabel: "동작 중",
        statusDetail: "최근 heartbeat 정상",
        lastHeartbeatAt: NOW,
        lastCollectedAt: NOW,
        errorCount24h: 2,
        failureRate24h: "1.5",
        diagnostics: [
          {
            label: "마지막 heartbeat",
            value: NOW,
            detail: "최근 heartbeat 정상"
          },
          {
            label: "24시간 오류",
            value: "2건",
            detail: "24시간 실패율 1.50%"
          }
        ],
        recentErrors: [
          {
            occurredAt: NOW,
            code: "UpbitTimeout",
            message: "현재가 수집 요청 시간이 초과되었습니다."
          }
        ]
      },
      backfill: {
        status: "running",
        statusLabel: "동작 중",
        statusDetail: "백필 계획을 10초 주기로 확인 중",
        lastHeartbeatAt: NOW,
        lastCollectedAt: NOW,
        totalErrorCount: 1,
        failureRateAll: "2.4",
        runningTargetCount: 1,
        totalTargetCount: 3,
        queuedJobCount: 0,
        queuedTargetCount: 0,
        diagnostics: [
          {
            label: "마지막 heartbeat",
            value: NOW,
            detail: "최근 heartbeat 정상"
          },
          {
            label: "동작중 코인",
            value: "1/3개",
            detail: "현재 실행 중인 백필 계획의 running 대상 수"
          }
        ],
        recentErrors: [
          {
            occurredAt: NOW,
            code: "UpbitBackfillError",
            message: "백필 캔들 조회 실패"
          }
        ]
      }
    },
    realtimeCollectionHeatmap: targets.map((target) => ({
      instrument: target.instrument,
      instrumentDisplayName: target.instrument.displayName,
      hourlyBuckets: Array.from({ length: 24 }, (_, hourIndex) => ({
        bucketStartAt: new Date(Date.parse(NOW) - (23 - hourIndex) * 60 * 60 * 1000).toISOString(),
        expectedRowsAll: 180,
        actualRowsAll: hourIndex % 3 === 0 ? 180 : 60,
        expectedRowsByType: {
          source_candle: 60,
          ticker_snapshot: 60,
          orderbook_summary: 60
        },
        actualRowsByType: {
          source_candle: hourIndex % 3 === 0 ? 60 : 30,
          ticker_snapshot: hourIndex % 3 === 0 ? 60 : 20,
          orderbook_summary: hourIndex % 3 === 0 ? 60 : 10
        },
        actualRatioPercent: hourIndex % 3 === 0 ? "100" : "33.3",
        status: hourIndex % 3 === 0 ? "high" : "collecting"
      }))
    })),
    storageBreakdown: [
      { dataType: "source_candle", label: "캔들", rowCount: 100, bytes: 1024, bytesDisplay: "1.0KB", sharePercent: "60" },
      { dataType: "ticker_snapshot", label: "현재가", rowCount: 50, bytes: 512, bytesDisplay: "512B", sharePercent: "30" },
      { dataType: "orderbook_summary", label: "호가", rowCount: 20, bytes: 256, bytesDisplay: "256B", sharePercent: "10" }
    ],
    operationsTrend: [
      {
        bucketDate: NOW,
        coveragePercent: "99.1",
        storageBytes: 1024,
        warningTargets: 1,
        incidentTargets: 0
      }
    ],
    missingRangeTop: [],
    auditLogSummary: {
      targetChangeCount24h: 1,
      backfillChangeCount24h: 0,
      latestChangeAt: NOW,
      latestChangeLabel: "대상 변경"
    },
    ...overrides
  };
}

export function createTestOperationsSnapshot(): OperationsSnapshot {
  const dashboard = createTestDashboardSummary();
  return {
    dashboard,
    candidateEntries: [],
    marketRows: [],
    detail: null,
    candles: [],
    backfillJobs: [],
    notifications: dashboard.alerts,
    source: "api"
  };
}

export function createTestInstrumentDetail(instrumentId: number): InstrumentDetail {
  const instrument = createTestInstruments(100).find((item) => item.id === instrumentId) ?? createTestInstruments(1)[0];
  return {
    instrument,
    latestTicker: {
      bucketAt: NOW,
      tradePrice: "1000000",
      accTradePrice24h: "100000000000",
      changeRate: "0.012",
      collectedAt: NOW
    },
    latestOrderbook: {
      bucketAt: NOW,
      bestBidPrice: "999990",
      bestBidSize: "1.5",
      bestAskPrice: "1000010",
      bestAskSize: "1.2",
      spread: "20",
      bidDepth10: "1200",
      askDepth10: "1100",
      imbalance10: "0.0434",
      collectedAt: NOW
    },
    coverage: [
      {
        instrumentId,
        dataType: "source_candle",
        status: "normal",
        progressPercent: "99.1",
        lastSuccessfulAt: NOW
      }
    ],
    priceChangeAmount24h: "12000",
    priceChangeRate24h: "0.012",
    tradeVolume24h: "1234",
    tradeVolumeChangeRate24h: "0.02",
    tickerFreshnessLabel: NOW,
    orderbookFreshnessLabel: NOW,
    qualityHistory: [
      {
        occurredAt: NOW,
        status: "normal",
        title: "수집 품질 정상",
        detail: "최근 수집 정상"
      }
    ]
  };
}

export function createTestCandles(): Candle[] {
  return Array.from({ length: 10 }, (_, index) => ({
    startedAt: new Date(Date.parse("2026-01-01T00:00:00+09:00") + index * 60 * 1000).toISOString(),
    open: "1000000",
    high: "1001000",
    low: "999000",
    close: "1000500",
    volume: "12.3",
    tradeAmount: "12300000",
    completeness: "complete"
  }));
}

export function createTestBackfillJob(overrides: Partial<BackfillJob> = {}): BackfillJob {
  return {
    id: 77,
    status: "pending",
    dataType: "source_candle",
    progressPercent: "0",
    targetStartAt: "2026-01-01T00:00:00+09:00",
    targetEndAt: "2026-02-01T00:00:00+09:00",
    targets: createTestInstruments(2),
    createdAt: NOW,
    ...overrides
  };
}

export function createTestOperationsFetch(
  options: { dashboard?: Partial<DashboardSummary>; backfillJobs?: BackfillJob[] } = {}
) {
  return async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/v1/dashboard/summary")) {
      return Response.json(createTestDashboardSummary(options.dashboard));
    }
    if (url.endsWith("/v1/backfill/jobs") && (!init || init.method !== "POST")) {
      return Response.json({ items: options.backfillJobs ?? [] });
    }
    if (url.endsWith("/v1/candidate-universe")) {
      return Response.json({ entries: createTestCandidateUniverse() });
    }
    if (url.endsWith("/v1/market-list")) {
      return Response.json({ rows: createTestMarketRows() });
    }
    if (url.match(/\/v1\/instruments\/\d+$/)) {
      const instrumentId = Number(url.split("/").at(-1));
      return Response.json(createTestInstrumentDetail(instrumentId));
    }
    if (url.includes("/candles?")) {
      return Response.json({ candles: createTestCandles() });
    }
    if (url.includes("/coverage-segments")) {
      return Response.json({ instrumentId: 1, items: [] });
    }
    if (url.endsWith("/v1/collection-targets")) {
      return Response.json({ targets: [] });
    }
    if (url.endsWith("/v1/backfill/plans")) {
      return Response.json({
        planId: "plan-1",
        dataType: "source_candle",
        estimatedRequestCount: 12,
        estimatedRowCount: 2880,
        estimatedStorageBytes: 737280,
        targets: [1, 2]
      });
    }
    if (url.endsWith("/v1/backfill/jobs") && init?.method === "POST") {
      return Response.json(createTestBackfillJob(), { status: 201 });
    }
    const pauseMatch = url.match(/\/v1\/backfill\/jobs\/(\d+)\/pause$/);
    if (pauseMatch && init?.method === "POST") {
      return Response.json(
        createTestBackfillJob({ id: Number(pauseMatch[1]), status: "paused" })
      );
    }
    const resumeMatch = url.match(/\/v1\/backfill\/jobs\/(\d+)\/resume$/);
    if (resumeMatch && init?.method === "POST") {
      return Response.json(
        createTestBackfillJob({ id: Number(resumeMatch[1]), status: "running" })
      );
    }
    const controlMatch = url.match(/\/v1\/backfill\/jobs\/(\d+)\/stop$/);
    if (controlMatch && init?.method === "POST") {
      return Response.json(
        createTestBackfillJob({ id: Number(controlMatch[1]), status: "stopped" })
      );
    }
    const deleteMatch = url.match(/\/v1\/backfill\/jobs\/(\d+)$/);
    if (deleteMatch && init?.method === "DELETE") {
      return new Response(null, { status: 204 });
    }
    return new Response(`unexpected ${url}`, { status: 500 });
  };
}

function createDashboardTarget(instrument: Instrument): CollectionDashboardTarget {
  return {
    instrument,
    overallStatus: "latest_collecting",
    overallStatusLabel: "최신수집중",
    plan: {
      instrumentId: instrument.id,
      preset: "2026년 1월 1분봉",
      rangeStartAt: "2026-01-01T00:00:00+09:00",
      rangeEndAt: null,
      isContinuous: true,
      method: "safe_restart",
      displayRange: "2026-01-01 00:00 KST ~ NOW",
      rangeTimeZone: "KST",
      progressBasis: "현재 기준"
    },
    dataStatuses: [
      {
        dataType: "source_candle",
        label: "캔들",
        status: "normal",
        statusLabel: "정상",
        lastSuccessfulAt: NOW,
        progressPercent: "99.1",
        missingSegmentCount: 0,
        storedRowCount: 1000
      },
      {
        dataType: "ticker_snapshot",
        label: "현재가",
        status: "normal",
        statusLabel: "정상",
        lastSuccessfulAt: NOW,
        progressPercent: "100",
        missingSegmentCount: 0,
        storedRowCount: 1000
      },
      {
        dataType: "orderbook_summary",
        label: "호가",
        status: "normal",
        statusLabel: "정상",
        lastSuccessfulAt: NOW,
        progressPercent: "100",
        missingSegmentCount: 0,
        storedRowCount: 1000
      }
    ],
    coverageSegments: [],
    changeRate: "0.012",
    accTradePrice24hDisplay: "₩100,000,000,000",
    tickerFreshnessLabel: NOW,
    coveragePercent: "99.1",
    storageRowCount: 1000,
    storageBytesDisplay: "1.0KB"
  };
}
