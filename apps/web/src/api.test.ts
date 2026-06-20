import { afterEach, describe, expect, it, vi } from "vitest";

const dashboard = {
  status: "normal",
  refreshedAt: "2026-06-19T00:00:00.000Z",
  totals: {
    activeTargets: 1,
    activeTargetLimit: 50,
    normalTargets: 1,
    warningTargets: 0,
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
  targets: [],
  alerts: [],
  healthChecks: [],
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
  collectionActivity: [],
  storageBreakdown: [],
  operationsTrend: [],
  missingRangeTop: [],
  auditLogSummary: {
    targetChangeCount24h: 1,
    backfillChangeCount24h: 0,
    latestChangeAt: "2026-06-19T00:00:00.000Z",
    latestChangeLabel: "대상 변경"
  }
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("운영 API 클라이언트", () => {
  it("HTTP 운영 데이터 Adapter는 화면이 필요한 Interface를 제공한다", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/v1/dashboard/summary")) {
        return Response.json(dashboard);
      }
      if (url.endsWith("/v1/backfill/jobs")) {
        return Response.json({ items: [] });
      }
      return new Response("unexpected", { status: 500 });
    });
    vi.stubGlobal("fetch", fetch);

    const { createHttpOperationsDataClient } = await import("./operationsData");
    const httpClient = createHttpOperationsDataClient({ apiBaseUrl: "/api", operatorToken: "" });

    await expect(httpClient.loadOperationsSnapshot()).resolves.toMatchObject({
      source: "api",
      dashboard: { status: "normal" }
    });
    expect(typeof httpClient.loadCandidateUniverse).toBe("function");
    expect(typeof httpClient.createBackfillPlan).toBe("function");
  });

  it("첫 운영 스냅샷은 대시보드와 백필 작업만 가져와 화면 표시를 빠르게 시작한다", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/v1/dashboard/summary")) {
        return Response.json(dashboard);
      }
      if (url.endsWith("/v1/backfill/jobs")) {
        return Response.json({ items: [] });
      }
      return new Response("unexpected", { status: 500 });
    });
    vi.stubGlobal("fetch", fetch);

    const { loadOperationsSnapshot } = await import("./api");
    const snapshot = await loadOperationsSnapshot();

    expect(snapshot.dashboard.status).toBe("normal");
    expect(fetch).toHaveBeenCalledTimes(2);
    const requested = fetch.mock.calls.map(([input]) => String(input));
    expect(requested).toEqual(["/api/v1/dashboard/summary", "/api/v1/backfill/jobs"]);
    expect(requested.some((url) => url.includes("/candles"))).toBe(false);
    expect(requested.some((url) => url.includes("/candidate-universe"))).toBe(false);
    expect(requested.some((url) => url.includes("/market-list"))).toBe(false);
  });

  it("구버전 대시보드 응답에 새 운영 콘솔 필드가 없어도 첫 화면용 기본값을 채운다", async () => {
    const legacyDashboard = {
      ...dashboard,
      totals: {
        activeTargets: 1,
        activeTargetLimit: 50,
        normalTargets: 1,
        warningTargets: 0,
        incidentTargets: 0,
        failedRuns24h: 0,
        failureRate24h: "0",
        delayedTargets: 0,
        missingRangesOpen: 0,
        storageBytesToday: 1024,
        storageBytesTodayDisplay: "1.0KB",
        recentRequestCount: 3
      },
      targets: [
        {
          instrument: {
            id: 1,
            exchange: "UPBIT",
            marketCode: "KRW-BTC",
            quoteCurrency: "KRW",
            baseAsset: "BTC",
            displayName: "비트코인"
          },
          overallStatus: "latest_collecting",
          overallStatusLabel: "최신수집중",
          plan: {
            instrumentId: 1,
            preset: "2026년 1월 1분봉",
            rangeStartAt: "2026-01-01T00:00:00.000Z",
            rangeEndAt: null,
            isContinuous: true,
            method: "safe_restart",
            displayRange: "2026-01-01 00:00 KST ~ NOW",
            rangeTimeZone: "KST",
            progressBasis: "현재 기준"
          },
          dataStatuses: []
        }
      ]
    };
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/v1/dashboard/summary")) {
        return Response.json(legacyDashboard);
      }
      if (url.endsWith("/v1/backfill/jobs")) {
        return Response.json({ items: [] });
      }
      return new Response("unexpected", { status: 500 });
    });
    vi.stubGlobal("fetch", fetch);

    const { loadOperationsSnapshot } = await import("./api");
    const snapshot = await loadOperationsSnapshot();

    expect(snapshot.dashboard.totals.storageRowsToday).toBe(0);
    expect(snapshot.dashboard.totals.realtimeRowsLastMinute).toBe(0);
    expect(snapshot.dashboard.totals.backfillRowsLastMinute).toBe(0);
    expect(snapshot.dashboard.collectionActivity).toEqual([]);
    expect(snapshot.dashboard.storageBreakdown).toEqual([]);
    expect(snapshot.dashboard.targets[0].storageRowCount).toBe(0);
    expect(snapshot.dashboard.targets[0].changeRate).toBe("0");
  });

  it("구버전 시장 리스트 응답에 저장 행이 없어도 기본값을 채운다", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/v1/market-list")) {
        return Response.json({
          rows: [
            {
              instrument: {
                id: 1,
                exchange: "UPBIT",
                marketCode: "KRW-BTC",
                quoteCurrency: "KRW",
                baseAsset: "BTC",
                displayName: "비트코인"
              },
              tradePrice: "100",
              accTradePrice24h: "1000",
              accTradePrice24hDisplay: "₩1,000",
              changeRate: "0.01",
              tickerCollectedAt: "2026-06-20T00:00:00.000Z",
              orderbookCollectedAt: "2026-06-20T00:00:00.000Z",
              qualityStatus: "normal",
              coveragePercent: "99.1",
              storageBytes: 1024,
              storageBytesDisplay: "1.0KB"
            }
          ]
        });
      }
      return new Response("unexpected", { status: 500 });
    });
    vi.stubGlobal("fetch", fetch);

    const { loadMarketList } = await import("./api");
    const rows = await loadMarketList();

    expect(rows).toHaveLength(1);
    expect(rows[0].storageRowCount).toBe(0);
  });

  it("쓰기 요청은 기본적으로 브라우저 번들 토큰을 보내지 않고 같은 출처 프록시에 맡긴다", async () => {
    const fetch = vi.fn(async () => Response.json({ targets: [] }));
    vi.stubGlobal("fetch", fetch);

    const { updateCollectionTargets } = await import("./api");
    await updateCollectionTargets([1, 2]);

    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/collection-targets");
    expect((init.headers as Record<string, string>)["X-Operator-Token"]).toBeUndefined();
  });

  it("수집 구간 segment는 코인 row 확장 시 별도 endpoint에서 가져온다", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/v1/collection-targets/1/coverage-segments")) {
        return Response.json({
          instrumentId: 1,
          items: [
            {
              dataType: "source_candle",
              status: "collected",
              offsetPercent: "0",
              widthPercent: "100",
              segmentStartAt: "2026-01-01T00:00:00.000Z",
              segmentEndAt: "2026-01-02T00:00:00.000Z",
              label: "수집 완료"
            }
          ]
        });
      }
      return new Response("unexpected", { status: 500 });
    });
    vi.stubGlobal("fetch", fetch);

    const { loadCollectionCoverageSegments } = await import("./api");
    const segments = await loadCollectionCoverageSegments(1);

    expect(segments).toHaveLength(1);
    expect(fetch).toHaveBeenCalledWith("/api/v1/collection-targets/1/coverage-segments");
  });
});
