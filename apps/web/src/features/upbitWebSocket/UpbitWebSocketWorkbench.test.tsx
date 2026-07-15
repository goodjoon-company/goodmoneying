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
    fireEvent.click(screen.getByRole("button", { name: "raw 추적" }));
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
