import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { UpbitApiWorkbench } from "./UpbitApiWorkbench";
import type { CatalogEndpoint, TraceEnvelope, UpbitCatalog } from "./types";

afterEach(cleanup);

const names = [
  ["pair", "페어 목록 조회"],
  ["candle", "초 캔들 조회"], ["candle", "분 캔들 조회"],
  ["candle", "일 캔들 조회"], ["candle", "주 캔들 조회"],
  ["candle", "월 캔들 조회"], ["candle", "연 캔들 조회"],
  ["trade", "페어 체결 이력 조회"],
  ["ticker", "페어 단위 현재가 조회"], ["ticker", "마켓 단위 현재가 조회"],
  ["orderbook", "호가 조회"], ["orderbook", "호가 정책 조회"],
  ["orderbook", "호가 모아보기 단위 조회"]
] as const;

const endpoints: CatalogEndpoint[] = names.map(([group, title], index) => ({
  endpoint_id: index === 1 ? "rest.list-candles-seconds" : `rest.test-${index}`,
  title,
  category: "quotation",
  functional_group: group,
  deprecated: index === 12,
  method: "GET",
  path: index === 1 ? "/v1/candles/seconds" : `/v1/test/${index}`,
    parameters: index === 0
    ? [
      { name: "is_details", location: "query", type: "boolean", required: false },
      { name: "states", location: "query", type: "array", required: false }
    ]
    : index === 1
      ? [
        { name: "market", location: "query", type: "string", required: true },
        { name: "to", location: "query", type: "string", required: false, format: "date-time" },
        { name: "count", location: "query", type: "integer", required: false, maximum: 2 }
      ]
      : [{ name: "market", location: "query", type: "string", required: true }],
  rate_limit_group: group,
  safety: "read",
  source_url: `https://docs.upbit.com/kr/reference/test-${index}`
}));

const catalog: UpbitCatalog = {
  catalog_version: "1.6.3",
  verified_at: "2026-07-16",
  official_baseline: "https://docs.upbit.com/kr/llms.txt",
  rest_endpoints: endpoints
};

const trace: TraceEnvelope = {
  trace_id: "3cb59f4b-49b4-4b7d-951a-00f015bedee9",
  endpoint_id: endpoints[0].endpoint_id,
  request: { method: "GET", path: "/v1/market/all", parameters: { is_details: true } },
  response: {
    status_code: 200,
    body: [{ market: "KRW-BTC", korean_name: "비트코인", english_name: "Bitcoin" }]
  },
  rate_limit: { group: "market", remaining_sec: 9, retry_after: null },
  duration_ms: 12.4,
  received_at: "2026-07-16T00:00:00Z"
};

describe("업비트 API 공통 작업대", () => {
  it("카탈로그의 12+1 Quotation 기능과 타입 폼, 결과, 원본 추적을 제공한다", async () => {
    const client = {
      loadCatalog: vi.fn(async () => catalog),
      execute: vi.fn(async () => trace)
    };
    const user = userEvent.setup();
    render(<UpbitApiWorkbench moduleId="quotation" client={client} />);

    expect(await screen.findByLabelText("Quotation API 작업대")).toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(5);
    expect(screen.getByText("활성 12개 · 사용 중단 1개")).toBeInTheDocument();
    const selector = screen.getByLabelText("API 기능");
    expect(within(selector).getAllByRole("option")).toHaveLength(13);
    expect(screen.getByRole("textbox", { name: "states" }).tagName).toBe("TEXTAREA");

    await user.click(screen.getByLabelText("상세 정보 포함"));
    await user.click(screen.getByRole("button", { name: "요청 실행" }));
    await waitFor(() => expect(client.execute).toHaveBeenCalledWith(
      endpoints[0].endpoint_id, { is_details: true }, expect.any(AbortSignal)
    ));
    expect(await screen.findByRole("cell", { name: "비트코인" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "원본 응답과 API 출처 보기" }));
    const dialog = screen.getByRole("dialog", { name: "API 요청 추적" });
    const traceButton = screen.getByRole("button", { name: "원본 응답과 API 출처 보기" });
    expect(dialog).toHaveTextContent("3cb59f4b");
    expect(dialog).toHaveTextContent("remaining_sec");
    const sourceLink = within(dialog).getByRole("link", { name: "Upbit 공식 문서" });
    const closeButton = within(dialog).getByRole("button", { name: "닫기" });
    expect(sourceLink).toHaveAttribute("href", endpoints[0].source_url);
    expect(closeButton).toHaveFocus();
    await user.tab({ shift: true });
    expect(sourceLink).toHaveFocus();
    await user.tab();
    expect(closeButton).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "API 요청 추적" })).not.toBeInTheDocument();
    await waitFor(() => expect(traceButton).toHaveFocus());
  });

  it("endpoint 변경은 진행 중 요청을 취소하고 늦은 응답을 새 출처로 표시하지 않는다", async () => {
    let resolveTrace: ((value: TraceEnvelope) => void) | undefined;
    let requestSignal: AbortSignal | undefined;
    const client = {
      loadCatalog: vi.fn(async () => catalog),
      execute: vi.fn((_endpointId: string, _parameters: object, signal?: AbortSignal) => {
        requestSignal = signal;
        return new Promise<TraceEnvelope>((resolve) => { resolveTrace = resolve; });
      })
    };
    const user = userEvent.setup();
    render(<UpbitApiWorkbench moduleId="quotation" client={client} />);
    await screen.findByLabelText("Quotation API 작업대");
    await user.click(screen.getByRole("button", { name: "요청 실행" }));
    await user.click(screen.getByRole("tab", { name: "캔들" }));

    expect(requestSignal?.aborted).toBe(true);
    await act(async () => resolveTrace?.(trace));
    expect(screen.queryByRole("button", { name: "원본 응답과 API 출처 보기" })).not.toBeInTheDocument();
  });

  it("후속 모듈이 연결되지 않은 메뉴는 명시적 확장 슬롯을 표시한다", () => {
    render(<UpbitApiWorkbench moduleId="exchange" />);
    expect(screen.getByText("Exchange API 모듈 연결 대기")).toBeInTheDocument();
    expect(screen.getByText(/Issue #22/)).toBeInTheDocument();
  });

  it("상위 셸의 공통 거래쌍과 실제 확장 컴포넌트 주입 경계를 공유한다", async () => {
    const onMarketChange = vi.fn();
    const Extension = ({ context }: { context: { market: string } }) => (
      <p>확장 모듈 거래쌍 {context.market}</p>
    );
    const { rerender } = render(
      <UpbitApiWorkbench moduleId="exchange" market="KRW-BTC" onMarketChange={onMarketChange}
        extensions={[{ id: "exchange", label: "Exchange API", Component: Extension }]} />
    );
    expect(screen.getByText("확장 모듈 거래쌍 KRW-BTC")).toBeInTheDocument();

    rerender(<UpbitApiWorkbench moduleId="quotation" market="KRW-ETH"
      onMarketChange={onMarketChange} client={{ loadCatalog: vi.fn(async () => catalog), execute: vi.fn(async () => trace) }} />);
    expect(await screen.findByDisplayValue("KRW-ETH")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "거래쌍" }), { target: { value: "BTC-XRP" } });
    expect(onMarketChange).toHaveBeenLastCalledWith("BTC-XRP");
  });

  it("공통 거래쌍을 유일한 입력으로 사용하고 최신 값을 요청에 전파한다", async () => {
    const client = {
      loadCatalog: vi.fn(async () => catalog),
      execute: vi.fn(async () => ({ ...trace, endpoint_id: endpoints[1].endpoint_id }))
    };
    const user = userEvent.setup();
    render(<UpbitApiWorkbench moduleId="quotation" client={client} />);
    await screen.findByLabelText("Quotation API 작업대");
    await user.click(screen.getByRole("tab", { name: "캔들" }));

    expect(screen.queryByLabelText("market")).not.toBeInTheDocument();
    const market = screen.getByRole("combobox", { name: "거래쌍" });
    await user.clear(market);
    await user.type(market, "BTC-ETH");
    await user.click(screen.getByRole("button", { name: "요청 실행" }));

    await waitFor(() => expect(client.execute).toHaveBeenCalledWith(
      endpoints[1].endpoint_id,
      expect.objectContaining({ market: "BTC-ETH" }),
      expect.any(AbortSignal)
    ));
  });

  it("빈 과거 페이지와 전부 중복인 미래 페이지를 방향별 종단으로 고정한다", async () => {
    const candleBody = (minutes: number[]) => minutes.map((minute) => ({
      candle_date_time_utc: `2026-07-15T00:0${minute}:00`,
      opening_price: 1, high_price: 2, low_price: 0, trade_price: 1,
      candle_acc_trade_volume: 3, candle_acc_trade_price: 3
    }));
    const responses = [candleBody([1, 2]), [], candleBody([1, 2])];
    const client = {
      loadCatalog: vi.fn(async () => catalog),
      execute: vi.fn(async () => ({
        ...trace,
        endpoint_id: endpoints[1].endpoint_id,
        response: { status_code: 200, body: responses.shift() ?? [] }
      }))
    };
    const user = userEvent.setup();
    render(<UpbitApiWorkbench moduleId="quotation" client={client} />);
    await screen.findByLabelText("Quotation API 작업대");
    await user.click(screen.getByRole("tab", { name: "캔들" }));
    fireEvent.change(screen.getByLabelText("to"), { target: { value: "2026-07-15T09:03" } });
    await user.click(screen.getByRole("button", { name: "요청 실행" }));
    await screen.findByText("2개 캔들 · 가장자리 이동 시 연속 조회");

    await user.click(screen.getByRole("button", { name: "과거 데이터 조회" }));
    expect(await screen.findByText("과거 데이터 끝")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "미래 데이터 조회" }));
    expect(await screen.findByText("최신 데이터 끝")).toBeInTheDocument();
    expect(client.execute).toHaveBeenCalledTimes(3);
    expect(screen.getByRole("button", { name: "과거 데이터 조회" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "미래 데이터 조회" })).toBeDisabled();
  });

  it("429 추적 응답은 친화적인 오류와 냉각 상태를 표시하고 원본 추적을 보존한다", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-16T00:00:00Z"));
    try {
      const limitedTrace: TraceEnvelope = {
        ...trace,
        response: { status_code: 429, body: { error: { name: "too_many_requests" } } },
        rate_limit: { group: "market", remaining_sec: 0, retry_after: "2" }
      };
      const client = {
        loadCatalog: vi.fn(async () => catalog),
        execute: vi.fn(async () => limitedTrace)
      };
      render(<UpbitApiWorkbench moduleId="quotation" client={client} />);
      await act(async () => { await Promise.resolve(); });
      fireEvent.click(screen.getByRole("button", { name: "요청 실행" }));
      await act(async () => { await Promise.resolve(); });

      expect(screen.getByRole("alert")).toHaveTextContent("요청 제한");
      expect(screen.getByRole("alert")).toHaveTextContent("2초");
      expect(screen.getByRole("button", { name: "요청 실행" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "원본 응답과 API 출처 보기" })).toBeInTheDocument();

      act(() => { vi.advanceTimersByTime(2_000); });
      expect(screen.getByRole("button", { name: "요청 실행" })).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });
});
