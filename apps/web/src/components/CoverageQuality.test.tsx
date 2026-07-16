import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoverageQuality } from "./CoverageQuality";

const response = {
  timeZone: "UTC",
  policyStartAt: "2024-01-01T00:00:00Z",
  summary: {
    marketCount: 2,
    krwMarketCount: 1,
    activeTargetCount: 4,
    pendingBackfillJobCount: 1,
    desiredSubscriptionCount: 3,
    coverageCounts: {
      available: 10,
      no_trade: 2,
      missing: 1,
      unavailable: 3,
      unverified: 4
    }
  },
  markets: [
    {
      marketCode: "KRW-BTC",
      koreanName: "비트코인",
      englishName: "Bitcoin",
      quoteCurrency: "KRW",
      tradingStatus: "active",
      marketWarning: "NONE",
      targetStatus: "active",
      activeDataTypeCount: 4,
      totalDataTypeCount: 4,
      coverageCounts: {
        available: 10,
        no_trade: 2,
        missing: 1,
        unavailable: 3,
        unverified: 4
      },
      collectionPolicy: {
        startAt: "2024-01-01T00:00:00Z",
        dataTypes: ["source_candle", "trade_event", "orderbook_snapshot", "ticker_snapshot"],
        candleUnit: "1m",
        retentionDays: null,
        priority: 100,
        continuous: true
      }
    }
  ]
} as const;

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/v1/data-foundation") && !init?.method) {
        return Response.json(response);
      }
      if (String(input).includes("/v1/data-foundation/markets/KRW-BTC")) {
        return Response.json({
          marketCode: "KRW-BTC",
          state: "paused",
          changedAt: "2026-07-17T00:00:00Z"
        });
      }
      return new Response("unexpected", { status: 500 });
    })
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Coverage & Quality", () => {
  it("KST 기본 정책과 5가지 커버리지 상태를 텍스트로 구분한다", async () => {
    renderCoverage();

    expect(await screen.findByRole("heading", { name: "Coverage & Quality" }))
      .toBeInTheDocument();
    expect(screen.getByText("2024-01-01 09:00 KST")).toBeInTheDocument();
    expect(screen.getByText("available · 사용 가능")).toBeInTheDocument();
    expect(screen.getByText("no_trade · 무거래 확인")).toBeInTheDocument();
    expect(
      screen.getByText("동일 성공 페이지의 양쪽 인접 캔들로 내부 무체결을 확인")
    ).toBeInTheDocument();
    expect(screen.getByText("missing · 복구 필요")).toBeInTheDocument();
    expect(screen.getByText("unavailable · 획득 불가")).toBeInTheDocument();
    expect(screen.getByText("unverified · 미검증")).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /KRW-BTC.*비트코인.*활성/ }))
      .toBeInTheDocument();
  });

  it("일시정지 변경을 API에 전달하고 결과를 알린다", async () => {
    const user = userEvent.setup();
    renderCoverage();

    await user.type(await screen.findByLabelText("작업자 ID"), "operator:test-user");
    await user.click(await screen.findByRole("button", { name: "KRW-BTC 일시정지" }));

    expect(await screen.findByRole("status")).toHaveTextContent("KRW-BTC 일시정지 요청을 저장했습니다");
    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/v1/data-foundation/markets/KRW-BTC"),
      expect.objectContaining({
        method: "PATCH",
        body: expect.any(String)
      })
    );
    const patchCall = fetchMock.mock.calls.find((call) => call[1]?.method === "PATCH");
    const body = JSON.parse(String(patchCall?.[1]?.body));
    expect(body).toEqual(expect.objectContaining({
      requestId: expect.any(String),
      idempotencyKey: expect.stringContaining("market:KRW-BTC:"),
      actorId: "operator:test-user",
      requestedAt: expect.any(String),
      state: "paused",
      reason: "Coverage & Quality 화면에서 일시정지"
    }));
  });

  it("시장별 시작일·데이터 유형·보존·우선순위 정책을 저장한다", async () => {
    const user = userEvent.setup();
    renderCoverage();

    await user.type(await screen.findByLabelText("작업자 ID"), "operator:policy-editor");
    await user.click(await screen.findByRole("button", { name: "KRW-BTC 정책 편집" }));
    await user.clear(screen.getByLabelText("수집 시작 KST"));
    await user.type(screen.getByLabelText("수집 시작 KST"), "2024-02-01T09:00");
    await user.click(screen.getByLabelText("티커 스냅숏"));
    await user.type(screen.getByLabelText("보존 기간 일수"), "3650");
    await user.clear(screen.getByLabelText("우선순위"));
    await user.type(screen.getByLabelText("우선순위"), "250");
    await user.click(screen.getByRole("button", { name: "KRW-BTC 정책 저장" }));

    expect(await screen.findByRole("status")).toHaveTextContent("KRW-BTC 정책 저장 요청을 저장했습니다");
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      expect.stringContaining("/v1/data-foundation/markets/KRW-BTC"),
      expect.objectContaining({
        method: "PATCH",
        body: expect.any(String)
      })
    );
    const patchCall = vi.mocked(fetch).mock.calls.find((call) => call[1]?.method === "PATCH");
    const body = JSON.parse(String(patchCall?.[1]?.body));
    expect(body).toEqual(expect.objectContaining({
      state: "active",
      reason: "Coverage & Quality 화면에서 정책 저장",
      policy: {
        startAt: "2024-02-01T00:00:00.000Z",
        dataTypes: ["source_candle", "trade_event", "orderbook_snapshot"],
        candleUnit: "1m",
        retentionDays: 3650,
        priority: 250,
        continuous: true
      }
    }));
  });
});

function renderCoverage() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <CoverageQuality />
    </QueryClientProvider>
  );
}
