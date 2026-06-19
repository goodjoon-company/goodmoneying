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
    recentRequestCount: 3,
    rateLimitRemainingPercent: "64"
  },
  coverage: [],
  targets: [],
  alerts: [],
  healthChecks: []
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("운영 API 클라이언트", () => {
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

  it("쓰기 요청은 기본적으로 브라우저 번들 토큰을 보내지 않고 같은 출처 프록시에 맡긴다", async () => {
    const fetch = vi.fn(async () => Response.json({ targets: [] }));
    vi.stubGlobal("fetch", fetch);

    const { updateCollectionTargets } = await import("./api");
    await updateCollectionTargets([1, 2]);

    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/collection-targets");
    expect((init.headers as Record<string, string>)["X-Operator-Token"]).toBeUndefined();
  });
});
