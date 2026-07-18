import { afterEach, describe, expect, it, vi } from "vitest";
import type { StrategyGraph } from "./api";

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
  workerStatus: {
    realtime: {
      status: "running",
      statusLabel: "동작 중",
      statusDetail: "최근 heartbeat 정상",
      lastHeartbeatAt: "2026-06-19T00:00:00.000Z",
      lastCollectedAt: "2026-06-19T00:00:00.000Z",
      collectedRowCount24h: 150,
      errorCount24h: 0,
      failureRate24h: "0",
      recentErrors: []
    },
    backfill: {
      status: "running",
      statusLabel: "동작 중",
      statusDetail: "백필 계획 확인 중",
      lastHeartbeatAt: "2026-06-19T00:00:00.000Z",
      lastCollectedAt: "2026-06-19T00:00:00.000Z",
      totalErrorCount: 0,
      failureRateAll: "0",
      runningTargetCount: 0,
      totalTargetCount: 0,
      recentErrors: []
    }
  },
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
    expect(typeof httpClient.startBackfillJob).toBe("function");
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

  it("Data Lab build 목록은 안정 cursor로 조회한다", async () => {
    const build = {
      buildId: 7,
      requestId: "dataset-request-1",
      idempotencyKey: "dataset-key-1",
      actorId: "operator:test",
      requestedAt: "2026-07-17T06:00:00Z",
      frozenAt: "2026-07-17T06:00:01Z",
      status: "retry_wait",
      attemptCount: 2,
      maxAttempts: 3,
      nextRetryAt: "2026-07-17T06:05:00Z",
      deadLetterReason: null,
      datasetVersionId: null,
      errorCode: null,
      errorMessage: null
    };
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/v1/dataset-builds?pageSize=25&cursor=build-cursor")) {
        return Response.json({ items: [build], nextCursor: null });
      }
      return new Response("unexpected", { status: 500 });
    });
    vi.stubGlobal("fetch", fetch);

    const { loadDatasetBuilds } = await import("./api");
    const response = await loadDatasetBuilds({ pageSize: 25, cursor: "build-cursor" });

    expect(response.items).toEqual([build]);
    expect(response.nextCursor).toBeNull();
  });

  it("Data Lab build 생성은 UTC 명령 payload를 보내고 운영 토큰은 프록시에 맡긴다", async () => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
      Response.json({
        buildId: 7,
        requestId: "dataset-request-1",
        idempotencyKey: "dataset-key-1",
        actorId: "operator:test",
        requestedAt: "2026-07-17T06:00:00Z",
        frozenAt: "2026-07-17T06:00:01Z",
        status: "pending",
        attemptCount: 0,
        maxAttempts: 3,
        nextRetryAt: null,
        deadLetterReason: null,
        datasetVersionId: null,
        errorCode: null,
        errorMessage: null
      })
    );
    vi.stubGlobal("fetch", fetch);

    const { createDatasetBuild } = await import("./api");
    await createDatasetBuild({
      requestId: "dataset-request-1",
      idempotencyKey: "dataset-key-1",
      actorId: "operator:test",
      requestedAt: "2026-07-17T06:00:00Z",
      reason: "Data Lab 생성",
      selection: {
        asOf: "2026-07-17T05:00:00Z",
        from: "2026-07-17T00:00:00Z",
        to: "2026-07-17T02:00:00Z",
        series: [
          {
            instrumentId: 1,
            dataKind: "candle",
            unit: "1m",
            definitionSetHash: null,
            calculationVersion: "source-candle-v1"
          }
        ]
      },
      policies: {
        availabilityPolicy: "point_in_time_v1",
        fillPolicy: "none",
        missingPolicy: "fail"
      }
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/dataset-builds",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "Content-Type": "application/json"
        })
      })
    );
    expect((fetch.mock.calls[0][1]?.headers as Record<string, string>)["X-Operator-Token"])
      .toBeUndefined();
    const body = JSON.parse(String(fetch.mock.calls[0][1]?.body));
    expect(body.selection.series).toHaveLength(1);
    expect(body.selection.asOf).toBe("2026-07-17T05:00:00Z");
  });

  it("Strategy Studio는 검증·정의 생성·불변 version 게시 API를 REST 계약대로 호출한다", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      if (url === "/api/v1/strategy-graphs/validate") {
        expect(init?.method).toBe("POST");
        expect(body.graph.schema_version).toBe("strategy-graph-v1");
        return Response.json({
          valid: true,
          errors: [],
          graphHash: "a".repeat(64)
        });
      }
      if (url === "/api/v1/strategies") {
        expect(init?.method).toBe("POST");
        expect(body).toMatchObject({
          actorId: "operator:strategy-studio",
          ownerId: "operator:strategy-studio",
          name: "KRW BTC momentum"
        });
        return Response.json(
          {
            strategyId: 31,
            ownerId: "operator:strategy-studio",
            name: "KRW BTC momentum",
            createdAt: "2026-07-18T08:00:00Z"
          },
          { status: 201 }
        );
      }
      if (url === "/api/v1/strategies/31/versions") {
        expect(init?.method).toBe("POST");
        expect(body.graph.outputs[0].port).toBe("enter_long");
        return Response.json(
          {
            strategyVersionId: 41,
            strategyId: 31,
            version: 1,
            schemaVersion: "strategy-graph-v1",
            status: "published",
            graphHash: "a".repeat(64),
            validation: { valid: true, errors: [], graphHash: "a".repeat(64) },
            graph: body.graph,
            actorId: "operator:strategy-studio",
            requestedAt: "2026-07-18T08:00:00Z",
            publishedAt: "2026-07-18T08:00:01Z",
            createdAt: "2026-07-18T08:00:01Z"
          },
          { status: 201 }
        );
      }
      return new Response(`unexpected ${url}`, { status: 500 });
    });
    vi.stubGlobal("fetch", fetch);
    const {
      createStrategy,
      publishStrategyVersion,
      validateStrategyGraph
    } = await import("./api");
    const graph: StrategyGraph = {
      schema_version: "strategy-graph-v1",
      nodes: [
        {
          id: "market",
          type: "market_input",
          input_ports: [],
          output_ports: [{ name: "close", dataType: "decimal", timeframe: "1m" }],
          config: { market: "KRW-BTC" }
        },
        {
          id: "signal",
          type: "threshold_signal",
          input_ports: [{ name: "price", dataType: "decimal", timeframe: "1m" }],
          output_ports: [{ name: "enter_long", dataType: "boolean", timeframe: "1m" }],
          config: { operator: "gt", threshold: "100" }
        }
      ],
      edges: [
        {
          from_node: "market",
          from_port: "close",
          to_node: "signal",
          to_port: "price"
        }
      ],
      outputs: [{ node: "signal", port: "enter_long" }]
    };

    await expect(validateStrategyGraph(graph)).resolves.toMatchObject({
      valid: true,
      graphHash: "a".repeat(64)
    });
    const strategy = await createStrategy({
      requestId: "strategy-request-1",
      idempotencyKey: "strategy-key-1",
      actorId: "operator:strategy-studio",
      requestedAt: "2026-07-18T08:00:00Z",
      reason: "Strategy Studio 신규 전략",
      ownerId: "operator:strategy-studio",
      name: "KRW BTC momentum"
    });
    await expect(
      publishStrategyVersion(strategy.strategyId, {
        requestId: "strategy-version-request-1",
        idempotencyKey: "strategy-version-key-1",
        actorId: "operator:strategy-studio",
        requestedAt: "2026-07-18T08:00:00Z",
        reason: "Strategy Studio 불변 version 게시",
        graph
      })
    ).resolves.toMatchObject({
      strategyVersionId: 41,
      version: 1,
      status: "published",
      graphHash: "a".repeat(64)
    });
    expect((fetch.mock.calls[0][1]?.headers as Record<string, string>)["X-Operator-Token"])
      .toBeUndefined();
  });

  it("Backtest Lab은 저장된 run 조회 API를 REST 계약대로 호출한다", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/backtest-runs/21") {
        return Response.json({
          backtestRunId: 21,
          strategyVersionId: 41,
          datasetVersionId: 12,
          status: "succeeded",
          inputHash: "e".repeat(64),
          resultHash: "f".repeat(64),
          metrics: [
            {
              metricName: "finalEquity",
              scopeKey: "run",
              metricValue: "1009.579790",
              metricPayload: {}
            }
          ],
          trades: [],
          artifacts: []
        });
      }
      return new Response(`unexpected ${url}`, { status: 500 });
    });
    vi.stubGlobal("fetch", fetch);

    const { loadBacktestRun } = await import("./api");

    await expect(loadBacktestRun(21)).resolves.toMatchObject({
      backtestRunId: 21,
      status: "succeeded",
      resultHash: "f".repeat(64),
      metrics: [{ metricName: "finalEquity", metricValue: "1009.579790" }]
    });
    expect(fetch).toHaveBeenCalledWith("/api/v1/backtest-runs/21");
  });

  it("구버전 대시보드 응답에 새 운영 콘솔 필드가 없어도 첫 화면용 기본값을 채운다", async () => {
    const dashboardWithoutWorkerStatus = { ...dashboard };
    delete (dashboardWithoutWorkerStatus as Partial<typeof dashboard>).workerStatus;
    const legacyDashboard = {
      ...dashboardWithoutWorkerStatus,
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
            rangeStartAt: "2026-01-01T00:00:00+09:00",
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
    expect(snapshot.dashboard.workerStatus.realtime.status).toBe("stale");
    expect(snapshot.dashboard.workerStatus.backfill.totalTargetCount).toBe(0);
    expect(snapshot.dashboard.storageBreakdown).toEqual([]);
    expect(snapshot.dashboard.targets[0].storageRowCount).toBe(0);
    expect(snapshot.dashboard.targets[0].changeRate).toBe("0");
  });

  it("구버전 관심종목 응답에 저장 행이 없어도 기본값을 채운다", async () => {
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
    expect(rows[0].oneMinuteCandleCount).toBe(0);
    expect(rows[0].isFavorite).toBe(false);
    expect(rows[0].assetType).toBe("coin");
    expect(rows[0].priceCurrency).toBe("KRW");
  });

  it("관심종목 SSE 이벤트를 정규화해 전달한다", async () => {
    const listeners = new Map<string, (event: MessageEvent<string>) => void>();
    const close = vi.fn();
    class FakeEventSource {
      url: string;

      constructor(url: string) {
        this.url = url;
      }

      addEventListener(type: string, handler: EventListener) {
        listeners.set(type, handler as (event: MessageEvent<string>) => void);
      }

      close() {
        close();
      }
    }
    vi.stubGlobal("EventSource", FakeEventSource);

    const { subscribeMarketList } = await import("./api");
    const handler = vi.fn();
    const unsubscribe = subscribeMarketList(handler);
    const marketListHandler = listeners.get("marketList");
    if (!marketListHandler) {
      throw new Error("관심종목 SSE 구독이 등록되지 않았습니다.");
    }
    marketListHandler(
      new MessageEvent("marketList", {
        data: JSON.stringify({
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
              assetType: "coin",
              isFavorite: true,
              tradePrice: "101.5",
              priceCurrency: "KRW",
              accTradePrice24h: "1000",
              accTradePrice24hDisplay: "₩1,000",
              tradeAmountCurrency: "KRW",
              changeRate: "0.02",
              changeRateBasis: "전일 종가 대비",
              tickerCollectedAt: "2026-06-20T00:00:00.000Z",
              orderbookCollectedAt: "2026-06-20T00:00:00.000Z",
              qualityStatus: "normal",
              coveragePercent: "99.1",
              candleCoverageStartAt: "2026-01-01T00:00:00+09:00",
              candleCoverageEndAt: "2026-06-20T00:00:00.000Z",
              candleCoverageCurrentAt: "2026-06-20T00:00:00.000Z",
              oneMinuteCandleCount: 100,
              storageBytes: 1024,
              storageRowCount: 100,
              storageBytesDisplay: "1.0KB"
            }
          ]
        })
      })
    );

    expect(handler).toHaveBeenCalledWith([
      expect.objectContaining({
        tradePrice: "101.5",
        priceCurrency: "KRW",
        oneMinuteCandleCount: 100
      })
    ]);
    unsubscribe();
    expect(close).toHaveBeenCalledOnce();
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
              segmentStartAt: "2026-01-01T00:00:00+09:00",
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
