import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRealtimeAnalysis } from "./useRealtimeAnalysis";

class FakeWebSocket {
  static readonly OPEN = 1;
  static instances: FakeWebSocket[] = [];

  readonly sentMessages: string[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  send(message: string) {
    this.sentMessages.push(message);
  }

  receive(message: object) {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }
}

describe("코인 분석 WebSocket 구독", () => {
  afterEach(() => {
    FakeWebSocket.instances = [];
    vi.unstubAllGlobals();
  });

  it("관심 코인과 시간 단위를 바꿔도 기존 연결에 새 구독을 보내고 독립 메시지를 반영한다", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const { result, rerender } = renderHook(
      ({ instrumentId, unit }: { instrumentId: number; unit: "1d" | "1m" }) =>
        useRealtimeAnalysis(instrumentId, unit, 365),
      { initialProps: { instrumentId: 1, unit: "1d" } }
    );
    const socket = FakeWebSocket.instances[0];

    act(() => socket.open());
    expect(JSON.parse(socket.sentMessages[0])).toMatchObject({
      type: "analysis.subscribe",
      instrumentId: 1,
      unit: "1d",
      rangeDays: 365
    });

    rerender({ instrumentId: 2, unit: "1m" });

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(JSON.parse(socket.sentMessages[1])).toMatchObject({
      type: "analysis.subscribe",
      instrumentId: 2,
      unit: "1m",
      rangeDays: 365
    });

    act(() => {
      socket.receive({
        type: "analysis.instrument",
        instrument: {
          id: 2,
          marketCode: "KRW-GM002",
          baseAsset: "GM002",
          quoteCurrency: "KRW",
          displayName: "굿머니코인 2"
        }
      });
      socket.receive({
        type: "analysis.chart",
        unit: "1m",
        chunkIndex: 0,
        chunkCount: 1,
        candles: []
      });
      socket.receive({
        type: "analysis.indicators",
        chunkIndex: 0,
        chunkCount: 1,
        points: []
      });
      socket.receive({
        type: "analysis.market",
        ticker: { tradePrice: "2", accTradePrice24h: "20", changeRate: "0.02", collectedAt: "2026-07-16T00:00:00+09:00" },
        orderbook: {
          bestBidPrice: "1", bestBidSize: "2", bestAskPrice: "3", bestAskSize: "4",
          spread: "2", bidDepth10: "10", askDepth10: "8", imbalance10: "0.1",
          collectedAt: "2026-07-16T00:00:00+09:00"
        },
        tradeSummary: { tradeCount: 2, buyVolume: "3", sellVolume: "1", lastTradeAt: null }
      });
    });

    expect(result.current.instrument?.marketCode).toBe("KRW-GM002");
    expect(result.current.market?.tradeSummary.tradeCount).toBe(2);
    expect(result.current.connectionStatus).toBe("live");
  });
});
