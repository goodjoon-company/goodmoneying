import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRealtimeAnalysis } from "./useRealtimeAnalysis";

class FakeWebSocket {
  static readonly OPEN = 1;
  static instances: FakeWebSocket[] = [];

  readonly sentMessages: string[] = [];
  private currentReadyState = 0;
  private openOnReadyStateRead = false;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  get readyState() {
    if (this.openOnReadyStateRead) {
      this.openOnReadyStateRead = false;
      this.open();
    }
    return this.currentReadyState;
  }

  open() {
    this.currentReadyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  openDuringNextReadyStateRead() {
    this.openOnReadyStateRead = true;
  }

  send(message: string) {
    this.sentMessages.push(message);
  }

  receive(message: object) {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }

  close() {
    this.currentReadyState = 3;
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
    act(() => {
      socket.receive({
        type: "analysis.session",
        subscriptionId: "subscription-1"
      });
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
        type: "analysis.session",
        subscriptionId: "subscription-2"
      });
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

  it("같은 연결에서 BTC에서 ETH로 구독을 바꾼 직후 도착한 BTC 잔여 프레임을 버린다", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const { result, rerender } = renderHook(
      ({ instrumentId }: { instrumentId: number }) =>
        useRealtimeAnalysis(instrumentId, "1d", 365),
      { initialProps: { instrumentId: 1 } }
    );
    const socket = FakeWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.receive({ type: "analysis.session", subscriptionId: "btc-subscription" });
      socket.receive({
        type: "analysis.instrument",
        instrument: {
          id: 1,
          marketCode: "KRW-BTC",
          baseAsset: "BTC",
          quoteCurrency: "KRW",
          displayName: "비트코인"
        }
      });
    });

    rerender({ instrumentId: 2 });
    act(() => {
      socket.receive({
        type: "analysis.market",
        ticker: { tradePrice: "100", accTradePrice24h: "1000", changeRate: "0.01", collectedAt: "2026-07-16T00:00:00+09:00" },
        orderbook: {
          bestBidPrice: "99", bestBidSize: "1", bestAskPrice: "101", bestAskSize: "1",
          spread: "2", bidDepth10: "10", askDepth10: "10", imbalance10: "0",
          collectedAt: "2026-07-16T00:00:00+09:00"
        },
        tradeSummary: { tradeCount: 99, buyVolume: "9", sellVolume: "1", lastTradeAt: null }
      });
    });

    expect(result.current.instrument?.marketCode).toBe("KRW-BTC");
    expect(result.current.market).toBeNull();

    act(() => {
      socket.receive({ type: "analysis.session", subscriptionId: "eth-subscription" });
      socket.receive({
        type: "analysis.instrument",
        instrument: {
          id: 2,
          marketCode: "KRW-ETH",
          baseAsset: "ETH",
          quoteCurrency: "KRW",
          displayName: "이더리움"
        }
      });
    });

    expect(result.current.instrument?.marketCode).toBe("KRW-ETH");
    expect(result.current.market).toBeNull();
  });

  it("유효 구독 뒤 실패한 구독은 성공 화면과 오류를 유지하고 다음 승인에서만 교체한다", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const { result, rerender } = renderHook(
      ({ instrumentId }: { instrumentId: number }) =>
        useRealtimeAnalysis(instrumentId, "1d", 365),
      { initialProps: { instrumentId: 1 } }
    );
    const socket = FakeWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.receive({ type: "analysis.session", subscriptionId: "btc-subscription" });
      socket.receive({
        type: "analysis.instrument",
        instrument: {
          id: 1,
          marketCode: "KRW-BTC",
          baseAsset: "BTC",
          quoteCurrency: "KRW",
          displayName: "비트코인"
        }
      });
      socket.receive({
        type: "analysis.chart",
        unit: "1d",
        chunkIndex: 0,
        chunkCount: 1,
        candles: [{
          startedAt: "2026-07-15T00:00:00+09:00",
          open: "100", high: "110", low: "90", close: "105",
          volume: "10", tradeAmount: "1050", completeness: "complete"
        }]
      });
    });

    rerender({ instrumentId: 999 });

    expect(result.current.instrument?.marketCode).toBe("KRW-BTC");
    expect(result.current.candles).toHaveLength(1);
    expect(result.current.error).toBeNull();

    act(() => {
      socket.receive({
        type: "analysis.instrument",
        instrument: {
          id: 98,
          marketCode: "KRW-STALE",
          baseAsset: "STALE",
          quoteCurrency: "KRW",
          displayName: "지연 프레임"
        }
      });
      socket.receive({
        type: "analysis.error",
        code: "NOT_WATCHLISTED",
        message: "관심목록에 포함된 코인만 분석할 수 있습니다."
      });
      socket.receive({
        type: "analysis.instrument",
        instrument: {
          id: 97,
          marketCode: "KRW-STALE-AFTER-ERROR",
          baseAsset: "STALE",
          quoteCurrency: "KRW",
          displayName: "오류 뒤 지연 프레임"
        }
      });
    });

    expect(result.current.instrument?.marketCode).toBe("KRW-BTC");
    expect(result.current.candles).toHaveLength(1);
    expect(result.current.error).toBe("관심목록에 포함된 코인만 분석할 수 있습니다.");

    rerender({ instrumentId: 2 });

    expect(result.current.instrument?.marketCode).toBe("KRW-BTC");
    expect(result.current.error).toBe("관심목록에 포함된 코인만 분석할 수 있습니다.");

    act(() => {
      socket.receive({ type: "analysis.session", subscriptionId: "eth-subscription" });
    });

    expect(result.current.instrument).toBeNull();
    expect(result.current.candles).toEqual([]);
    expect(result.current.error).toBeNull();

    act(() => {
      socket.receive({
        type: "analysis.instrument",
        instrument: {
          id: 2,
          marketCode: "KRW-ETH",
          baseAsset: "ETH",
          quoteCurrency: "KRW",
          displayName: "이더리움"
        }
      });
    });

    expect(result.current.instrument?.marketCode).toBe("KRW-ETH");
  });

  it("이전 구독 승인 전에 새 구독을 보내면 세대 순서대로 session을 대응한다", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const { result, rerender } = renderHook(
      ({ instrumentId }: { instrumentId: number }) =>
        useRealtimeAnalysis(instrumentId, "1d", 365),
      { initialProps: { instrumentId: 1 } }
    );
    const socket = FakeWebSocket.instances[0];
    act(() => socket.open());

    rerender({ instrumentId: 2 });
    act(() => {
      socket.receive({ type: "analysis.session", subscriptionId: "btc-subscription" });
      socket.receive({
        type: "analysis.instrument",
        instrument: {
          id: 1,
          marketCode: "KRW-BTC",
          baseAsset: "BTC",
          quoteCurrency: "KRW",
          displayName: "비트코인"
        }
      });
    });

    expect(result.current.instrument).toBeNull();

    act(() => {
      socket.receive({ type: "analysis.session", subscriptionId: "eth-subscription" });
      socket.receive({
        type: "analysis.instrument",
        instrument: {
          id: 2,
          marketCode: "KRW-ETH",
          baseAsset: "ETH",
          quoteCurrency: "KRW",
          displayName: "이더리움"
        }
      });
    });

    expect(result.current.instrument?.marketCode).toBe("KRW-ETH");
  });

  it("open 이벤트와 선택 변경 효과가 겹쳐도 최신 구독을 한 번만 보낸다", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const { rerender } = renderHook(
      ({ instrumentId }: { instrumentId: number }) =>
        useRealtimeAnalysis(instrumentId, "1d", 365),
      { initialProps: { instrumentId: 1 } }
    );
    const socket = FakeWebSocket.instances[0];

    socket.openDuringNextReadyStateRead();
    rerender({ instrumentId: 2 });

    expect(socket.sentMessages).toHaveLength(1);
    expect(JSON.parse(socket.sentMessages[0])).toMatchObject({
      type: "analysis.subscribe",
      instrumentId: 2,
      unit: "1d",
      rangeDays: 365
    });
  });

  it("언마운트 후 이전 소켓의 지연 open 이벤트는 구독을 보내지 않는다", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const { unmount } = renderHook(() => useRealtimeAnalysis(1, "1d", 365));
    const socket = FakeWebSocket.instances[0];

    unmount();
    act(() => socket.open());

    expect(socket.sentMessages).toHaveLength(0);
  });

  it("구독 해제 후 이전 소켓의 지연 프레임은 초기화된 상태를 복원하지 않는다", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const { result, rerender } = renderHook(
      ({ instrumentId }: { instrumentId: number | null }) =>
        useRealtimeAnalysis(instrumentId, "1d", 365),
      { initialProps: { instrumentId: 1 as number | null } }
    );
    const socket = FakeWebSocket.instances[0];
    act(() => socket.open());

    rerender({ instrumentId: null });
    act(() => {
      socket.receive({
        type: "analysis.instrument",
        instrument: {
          id: 1,
          marketCode: "KRW-BTC",
          baseAsset: "BTC",
          quoteCurrency: "KRW",
          displayName: "비트코인"
        }
      });
    });

    expect(result.current.instrument).toBeNull();
    expect(result.current.connectionStatus).toBe("offline");
  });

  it("WebSocket을 지원하지 않는 환경에서는 전역 상수를 평가하지 않고 오프라인으로 대체한다", () => {
    vi.stubGlobal("WebSocket", undefined);

    const { result } = renderHook(() => useRealtimeAnalysis(1, "1d", 365));

    expect(result.current.connectionStatus).toBe("offline");
    expect(result.current.instrument).toBeNull();
  });
});
