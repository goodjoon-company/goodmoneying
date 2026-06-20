import type {
  BackfillJob,
  BackfillPlan,
  Candle,
  CreateBackfillPlanOptions,
  OperationsSnapshot
} from "./api";
import type { OperationsDataClient } from "./operationsData";

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
        : "품질 정상: 최신성/결측/저장 행 기준 정상권",
    collectionRangeDisplay: "2026-01-01 00:00 KST ~ NOW"
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
    storageRowCount: 44640 + index * 3,
    storageBytesDisplay: `${(24 - index * 0.12).toFixed(1)}MB`
  }));
  const targetRows = instruments.slice(0, 50).map((instrument, index) => {
    const marketRow = marketRows[index];
    const rangeStartAt = "2025-12-31T15:00:00.000Z";
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
      changeRate: marketRow.changeRate,
      accTradePrice24hDisplay: marketRow.accTradePrice24hDisplay,
      tickerFreshnessLabel: "방금 전",
      coveragePercent: marketRow.coveragePercent,
      storageRowCount: marketRow.storageRowCount,
      storageBytesDisplay: marketRow.storageBytesDisplay,
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
        storageRowsToday: 13600000,
        realtimeRowsLastMinute: 150,
        backfillRowsLastMinute: 24,
        recentRequestCount: 14200
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
        { title: "현재가·거래대금", status: "normal", statusLabel: "정상", detail: "최근 1-3분 정상" },
        { title: "캔들 상태", status: "normal", statusLabel: "정상", detail: "직전 완성 1분봉 저장" },
        { title: "호가 상태", status: "normal", statusLabel: "정상", detail: "매수 잔량 우세" },
        { title: "완전성 검사", status: "warning", statusLabel: "주의", detail: "결측 1구간" }
      ],
      metricPrinciples: [
        {
          metricKey: "rateLimitRemainingPercent",
          label: "업비트 Rate Limit 여유율",
          displayStatus: "excluded",
          evidenceStatus: "missing_persistence",
          reason: "실제 Upbit 헤더 영속화가 없어 운영 콘솔에서 제외한다."
        },
        {
          metricKey: "duplicateRows24h",
          label: "중복 저장 시도",
          displayStatus: "excluded",
          evidenceStatus: "missing_measurement",
          reason: "업서트 충돌 또는 중복 시도 측정값이 없어 운영 콘솔에서 제외한다."
        }
      ],
      collectionActivity: Array.from({ length: 168 }, (_, index) => ({
        bucketStartAt: new Date(Date.parse(now) - (167 - index) * 60 * 60 * 1000).toISOString(),
        runCount: index === 167 ? 1 : 0,
        resultCount: index === 167 ? 50 : 0,
        status: index === 167 ? ("high" as const) : ("none" as const)
      })),
      storageBreakdown: [
        { dataType: "source_candle", label: "캔들", rowCount: 2232000, bytes: 571392000, bytesDisplay: "544.9MB", sharePercent: "68" },
        { dataType: "ticker_snapshot", label: "현재가", rowCount: 72000, bytes: 11520000, bytesDisplay: "11.0MB", sharePercent: "1.4" },
        { dataType: "orderbook_summary", label: "호가", rowCount: 72000, bytes: 16128000, bytesDisplay: "15.4MB", sharePercent: "1.9" },
        { dataType: "quality_result", label: "품질/결과", rowCount: 14200, bytes: 1817600, bytesDisplay: "1.7MB", sharePercent: "0.2" }
      ],
      operationsTrend: Array.from({ length: 7 }, (_, index) => ({
        bucketDate: new Date(Date.parse(now) - (6 - index) * 24 * 60 * 60 * 1000).toISOString(),
        coveragePercent: index === 6 ? "98.4" : "0",
        storageBytes: index === 6 ? 81388912640 : 0,
        warningTargets: index === 6 ? 2 : 0,
        incidentTargets: index === 6 ? 1 : 0
      })),
      missingRangeTop: targetRows.slice(0, 5).map((target, index) => ({
        instrument: target.instrument,
        missingSegmentCount: index === 0 ? 2 : 1,
        coveragePercent: `${98 - index}`,
        lastSuccessfulAt: now
      })),
      auditLogSummary: {
        targetChangeCount24h: 50,
        backfillChangeCount24h: 0,
        latestChangeAt: now,
        latestChangeLabel: "대상 변경"
      }
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
      priceChangeAmount24h: "11857.707509881422924901",
      priceChangeRate24h: "0.012",
      tradeVolume24h: "28420.42",
      tradeVolumeChangeRate24h: "0.034",
      tickerFreshnessLabel: "49초 전",
      orderbookFreshnessLabel: "57초 전",
      qualityHistory: [
        { occurredAt: now, status: "normal", title: "현재가 수집 정상", detail: "커버리지 100%, 결측 0구간" },
        { occurredAt: now, status: "warning", title: "캔들 수집 주의", detail: "커버리지 98%, 결측 1구간" }
      ]
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

export function createFixtureOperationsDataClient(): OperationsDataClient {
  return {
    async loadOperationsSnapshot() {
      return demoSnapshot();
    },
    async loadCandidateUniverse() {
      return demoSnapshot().candidateEntries;
    },
    async loadMarketList() {
      return demoSnapshot().marketRows;
    },
    async loadCollectionCoverageSegments(instrumentId: number) {
      const target = demoSnapshot().dashboard.targets.find(
        (item) => item.instrument.id === instrumentId
      );
      return target?.coverageSegments ?? [];
    },
    async loadInstrumentSnapshot(instrumentId: number) {
      const snapshot = demoSnapshot();
      const row = snapshot.marketRows.find((item) => item.instrument.id === instrumentId);
      if (!row || !snapshot.detail) {
        return { detail: snapshot.detail!, candles: snapshot.candles };
      }
      return {
        detail: {
          ...snapshot.detail,
          instrument: row.instrument,
          latestTicker: {
            ...snapshot.detail.latestTicker,
            tradePrice: row.tradePrice,
            accTradePrice24h: row.accTradePrice24h,
            changeRate: row.changeRate,
            collectedAt: row.tickerCollectedAt
          },
          coverage: snapshot.dashboard.coverage.filter((item) => item.instrumentId === instrumentId)
        },
        candles: demoCandles(row.tradePrice)
      };
    },
    async updateCollectionTargets() {
      return undefined;
    },
    async createBackfillPlan(
      instrumentIds: number[],
      _options: CreateBackfillPlanOptions = {}
    ): Promise<BackfillPlan> {
      return {
        planId: "fixture-plan",
        dataType: "source_candle",
        estimatedRequestCount: instrumentIds.length * 1440,
        estimatedRowCount: instrumentIds.length * 1440,
        estimatedStorageBytes: instrumentIds.length * 368640,
        targets: instrumentIds
      };
    },
    async approveBackfillJob(): Promise<BackfillJob> {
      return {
        id: 1,
        status: "pending",
        dataType: "source_candle",
        progressPercent: "0",
        createdAt: new Date("2026-06-18T00:00:00.000Z").toISOString()
      };
    },
    async controlBackfillJob(jobId: number): Promise<BackfillJob> {
      return {
        id: jobId,
        status: "running",
        dataType: "source_candle",
        progressPercent: "0",
        createdAt: new Date("2026-06-18T00:00:00.000Z").toISOString()
      };
    }
  };
}
