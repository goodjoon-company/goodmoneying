import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UpbitWebSocketWorkbench } from "./UpbitWebSocketWorkbench";
import type { BrowserSocket, SocketFactory } from "./types";

afterEach(cleanup);

class FakeSocket implements BrowserSocket {
  readyState = 0;
  sent: string[] = [];
  closed = false;
  private listeners = new Map<string, Set<(event: Event) => void>>();

  addEventListener(type: string, listener: (event: Event) => void) {
    const listeners = this.listeners.get(type) ?? new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }
  removeEventListener(type: string, listener: (event: Event) => void) {
    this.listeners.get(type)?.delete(listener);
  }
  send(data: string) { this.sent.push(data); }
  close() { this.closed = true; this.readyState = 3; }
  open() { this.readyState = 1; this.listeners.get("open")?.forEach((listener) => listener(new Event("open"))); }
  message(value: unknown) {
    const event = new MessageEvent("message", { data: JSON.stringify(value) });
    this.listeners.get("message")?.forEach((listener) => listener(event));
  }
}

describe("업비트 웹소켓 작업대", () => {
  it("공개 스트림을 연결·구독하고 현재가와 raw 추적을 표시한다", () => {
    const sockets: FakeSocket[] = [];
    const socketFactory: SocketFactory = () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    };
    render(
      <UpbitWebSocketWorkbench
        socketFactory={socketFactory}
        markets={[{ market: "KRW-BTC", koreanName: "비트코인" }]}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "연결" }));
    act(() => sockets[0].open());
    expect(JSON.parse(sockets[0].sent[0])).toMatchObject({
      action: "connect",
      visibility: "public",
      format: "DEFAULT"
    });
    act(() => sockets[0].message({ event: "connection", state: "connected", visibility: "public", format: "DEFAULT" }));
    fireEvent.click(screen.getByRole("button", { name: "구독" }));
    expect(JSON.parse(sockets[0].sent[1])).toMatchObject({
      action: "subscribe",
      endpoint_id: "websocket.ticker",
      parameters: { codes: ["KRW-BTC"] }
    });
    act(() => sockets[0].message({
      event: "frame",
      trace_id: "trace-1",
      connection_id: "connection-1",
      sequence: 1,
      received_at: "2026-07-16T00:00:00Z",
      payload: { type: "ticker", code: "KRW-BTC", trade_price: 123456 },
      raw: "{\"type\":\"ticker\"}",
      binary: true,
      provenance: { visibility: "public", format: "DEFAULT", endpoint_ids: ["websocket.ticker"] }
    }));

    expect(screen.getByLabelText("실시간 현재가")).toHaveTextContent("123,456");
    const traceTrigger = screen.getByRole("button", { name: "raw 추적" });
    expect(traceTrigger.querySelector("svg")).not.toBeNull();
    expect(traceTrigger).toHaveTextContent("");
    fireEvent.click(traceTrigger);
    expect(screen.getByRole("dialog", { name: "raw frame 추적" })).toHaveTextContent("trace-1");
  });

  it("비공개 탭은 브라우저 키 입력 없이 private 연결과 내 자산 구독만 보낸다", () => {
    const sockets: FakeSocket[] = [];
    render(<UpbitWebSocketWorkbench socketFactory={() => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    }} />);

    fireEvent.click(screen.getByRole("tab", { name: "내 자산" }));
    expect(screen.queryByLabelText(/접근 키|비밀 키/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "연결" }));
    act(() => sockets[0].open());
    expect(JSON.parse(sockets[0].sent[0])).toMatchObject({ action: "connect", visibility: "private" });
    act(() => sockets[0].message({ event: "connection", state: "connected", visibility: "private", format: "DEFAULT" }));
    fireEvent.click(screen.getByRole("button", { name: "구독" }));
    expect(JSON.parse(sockets[0].sent[1])).toMatchObject({
      action: "subscribe",
      endpoint_id: "websocket.my-asset",
      parameters: {}
    });
    expect(sockets[0].sent.join(" ")).not.toMatch(/access|secret|authorization|bearer/i);
  });

  it("pause/list/reconnect/unsubscribe 제어를 현재 소켓으로 보낸다", () => {
    const socket = new FakeSocket();
    render(<UpbitWebSocketWorkbench socketFactory={() => socket} />);
    fireEvent.click(screen.getByRole("button", { name: "연결" }));
    act(() => socket.open());
    act(() => socket.message({ event: "connection", state: "connected", visibility: "public", format: "DEFAULT" }));

    for (const name of ["일시 정지", "목록 조회", "재연결", "구독 해제"]) {
      fireEvent.click(screen.getByRole("button", { name }));
    }

    expect(socket.sent.slice(1).map((item) => JSON.parse(item).action)).toEqual([
      "pause", "list", "reconnect", "unsubscribe"
    ]);
  });

  it("공개·비공개 연결과 프레임 상태를 분리하고 선택한 연결만 해제·지운다", () => {
    const sockets: FakeSocket[] = [];
    render(<UpbitWebSocketWorkbench socketFactory={() => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    }} />);

    fireEvent.click(screen.getByRole("button", { name: /^연결$/ }));
    act(() => sockets[0].open());
    act(() => sockets[0].message({ event: "connection", state: "connected", visibility: "public", format: "DEFAULT" }));
    act(() => sockets[0].message(frameEvent("public-trace", "public", { type: "ticker", code: "KRW-BTC", trade_price: 100 })));
    fireEvent.click(screen.getByRole("tab", { name: "내 자산" }));
    fireEvent.click(screen.getByRole("button", { name: /^연결$/ }));
    act(() => sockets[1].open());
    act(() => sockets[1].message({ event: "connection", state: "connected", visibility: "private", format: "DEFAULT" }));

    expect(sockets[0].closed).toBe(false);
    expect(screen.getByLabelText("비공개 연결 상태")).toHaveTextContent("connected");
    fireEvent.click(screen.getByRole("button", { name: "연결 해제" }));
    expect(sockets[1].closed).toBe(true);
    expect(sockets[0].closed).toBe(false);
    fireEvent.click(screen.getByRole("tab", { name: "현재가" }));
    expect(screen.getByLabelText("실시간 현재가")).toHaveTextContent("100");
    fireEvent.click(screen.getByRole("button", { name: "프레임 지우기" }));
    expect(screen.queryByLabelText("실시간 현재가")).not.toBeInTheDocument();
  });

  it("헤더에서 공개·비공개 연결 상태를 동시에 독립적으로 표시한다", () => {
    const sockets: FakeSocket[] = [];
    render(<UpbitWebSocketWorkbench socketFactory={() => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    }} />);

    fireEvent.click(screen.getByRole("button", { name: /^연결$/ }));
    act(() => sockets[0].open());
    act(() => sockets[0].message({ event: "connection", state: "connected", visibility: "public", format: "DEFAULT" }));
    expect(screen.getByLabelText("공개 연결 상태")).toHaveTextContent("connected");
    expect(screen.getByLabelText("비공개 연결 상태")).toHaveTextContent("closed");

    fireEvent.click(screen.getByRole("tab", { name: "내 자산" }));
    fireEvent.click(screen.getByRole("button", { name: /^연결$/ }));
    act(() => sockets[1].open());
    act(() => sockets[1].message({ event: "connection", state: "connected", visibility: "private", format: "DEFAULT" }));
    fireEvent.click(screen.getByRole("tab", { name: "현재가" }));
    act(() => sockets[0].message({ event: "connection", state: "paused", visibility: "public", format: "DEFAULT" }));

    expect(screen.getByLabelText("공개 연결 상태")).toHaveTextContent("paused");
    expect(screen.getByLabelText("비공개 연결 상태")).toHaveTextContent("connected");
  });

  it("raw 출처와 비공개 자산·주문 목록을 구조화해 표시한다", () => {
    const socket = new FakeSocket();
    render(<UpbitWebSocketWorkbench socketFactory={() => socket} />);
    fireEvent.click(screen.getByRole("tab", { name: "내 자산" }));
    fireEvent.click(screen.getByRole("button", { name: /^연결$/ }));
    act(() => socket.open());
    act(() => socket.message({ event: "connection", state: "connected", visibility: "private", format: "DEFAULT" }));
    act(() => socket.message(frameEvent("asset-trace", "private", {
      type: "myAsset",
      assets: [{ currency: "KRW", balance: 1200, locked: 30 }]
    }, ["websocket.my-asset"])));

    expect(screen.getByLabelText("내 자산 이벤트")).toHaveTextContent("KRW");
    expect(screen.getByLabelText("내 자산 이벤트")).toHaveTextContent("1,200");
    fireEvent.click(screen.getByRole("button", { name: "raw 추적" }));
    const dialog = screen.getByRole("dialog", { name: "raw frame 추적" });
    expect(dialog).toHaveTextContent("private · DEFAULT · websocket.my-asset");
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "raw frame 추적" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "raw 추적" })).toHaveFocus();

    fireEvent.click(screen.getByRole("tab", { name: "내 주문" }));
    act(() => socket.message(frameEvent("order-trace", "private", {
      type: "myOrder", code: "KRW-BTC", state: "wait", price: 1000, volume: 2
    }, ["websocket.my-order"])));
    expect(screen.getByLabelText("내 주문 이벤트")).toHaveTextContent("KRW-BTC");
    expect(screen.getByLabelText("내 주문 이벤트")).toHaveTextContent("wait");
  });

  it("방향키로 탭을 이동하고 선택한 탭에 초점을 둔다", () => {
    render(<UpbitWebSocketWorkbench />);
    const ticker = screen.getByRole("tab", { name: "현재가" });

    ticker.focus();
    fireEvent.keyDown(ticker, { key: "ArrowRight" });

    const trade = screen.getByRole("tab", { name: "체결" });
    expect(trade).toHaveAttribute("aria-selected", "true");
    expect(trade).toHaveFocus();
  });

  it("raw 대화상자의 첫·마지막 초점 요소에서 Tab과 Shift+Tab을 순환한다", () => {
    const socket = new FakeSocket();
    render(<UpbitWebSocketWorkbench socketFactory={() => socket} />);
    fireEvent.click(screen.getByRole("button", { name: /^연결$/ }));
    act(() => socket.open());
    act(() => socket.message(frameEvent("focus-trace", "public", {
      type: "ticker", code: "KRW-BTC", trade_price: 100
    })));
    fireEvent.click(screen.getByRole("button", { name: "raw 추적" }));

    const close = screen.getByRole("button", { name: "닫기" });
    const rawFrame = screen.getByLabelText("raw frame 1");
    rawFrame.focus();
    fireEvent.keyDown(rawFrame, { key: "Tab" });
    expect(close).toHaveFocus();

    close.focus();
    fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
    expect(rawFrame).toHaveFocus();
  });

  it("상위 공통 페어 선택을 사용하고 변경을 통지하며 탭·재연결 뒤에도 유지한다", () => {
    const socket = new FakeSocket();
    const onMarketCodeChange = vi.fn();
    const view = render(<UpbitWebSocketWorkbench
      socketFactory={() => socket}
      markets={[
        { market: "KRW-BTC", koreanName: "비트코인" },
        { market: "KRW-ETH", koreanName: "이더리움" }
      ]}
      marketCode="KRW-ETH"
      onMarketCodeChange={onMarketCodeChange}
    />);

    expect(screen.getByLabelText("페어")).toHaveValue("KRW-ETH");
    fireEvent.change(screen.getByLabelText("페어"), { target: { value: "KRW-BTC" } });
    expect(onMarketCodeChange).toHaveBeenCalledWith("KRW-BTC");
    view.rerender(<UpbitWebSocketWorkbench
      socketFactory={() => socket}
      markets={[{ market: "KRW-BTC", koreanName: "비트코인" }]}
      marketCode="KRW-BTC"
      onMarketCodeChange={onMarketCodeChange}
    />);
    fireEvent.click(screen.getByRole("button", { name: /^연결$/ }));
    act(() => socket.open());
    act(() => socket.message({ event: "connection", state: "connected", visibility: "public", format: "DEFAULT" }));
    fireEvent.click(screen.getByRole("tab", { name: "체결" }));
    fireEvent.click(screen.getByRole("button", { name: "재연결" }));
    fireEvent.click(screen.getByRole("button", { name: /^구독$/ }));

    const subscribe = socket.sent.map((item) => JSON.parse(item)).find((item) => item.action === "subscribe");
    expect(subscribe).toMatchObject({ endpoint_id: "websocket.trade", parameters: { codes: ["KRW-BTC"] } });
  });
});

function frameEvent(
  traceId: string,
  visibility: "public" | "private",
  payload: Record<string, unknown>,
  endpointIds = ["websocket.ticker"]
) {
  return {
    event: "frame",
    trace_id: traceId,
    connection_id: `${visibility}-connection`,
    sequence: 1,
    received_at: "2026-07-16T00:00:00Z",
    payload,
    raw: JSON.stringify(payload),
    binary: true,
    provenance: { visibility, format: "DEFAULT", endpoint_ids: endpointIds }
  };
}
