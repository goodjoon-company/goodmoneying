import { describe, expect, it } from "vitest";
import {
  appendBoundedFrame,
  defaultGatewayWebSocketUrl,
  framePayloads,
  marketOptions,
  streamForTab,
  workbenchStreamEndpointIds
} from "./protocol";
import type { GatewayFrameEvent } from "./types";

describe("업비트 웹소켓 작업대 프로토콜", () => {
  it("공통 마켓 모델을 대문자 코드 선택지로 변환한다", () => {
    expect(marketOptions([
      { market: "krw-btc", koreanName: "비트코인" },
      { market: "KRW-ETH", koreanName: "이더리움" }
    ])).toEqual([
      { value: "KRW-BTC", label: "KRW-BTC · 비트코인" },
      { value: "KRW-ETH", label: "KRW-ETH · 이더리움" }
    ]);
  });

  it("공개·비공개 탭을 14개 카탈로그 endpoint와 가시성으로 매핑한다", () => {
    expect(workbenchStreamEndpointIds()).toEqual([
      "websocket.ticker",
      "websocket.trade",
      "websocket.orderbook",
      "websocket.candle-1s",
      "websocket.candle-1m",
      "websocket.candle-3m",
      "websocket.candle-5m",
      "websocket.candle-10m",
      "websocket.candle-15m",
      "websocket.candle-30m",
      "websocket.candle-60m",
      "websocket.candle-240m",
      "websocket.my-asset",
      "websocket.my-order"
    ]);
    expect(streamForTab("ticker", "1s")).toEqual({ endpointId: "websocket.ticker", visibility: "public" });
    expect(streamForTab("asset", "1s")).toEqual({ endpointId: "websocket.my-asset", visibility: "private" });
  });

  it("브라우저 토큰 없이 같은 출처의 게이트웨이 프록시 URL을 만든다", () => {
    expect(defaultGatewayWebSocketUrl({ protocol: "http:", host: "localhost:5173" })).toBe(
      "ws://localhost:5173/upbit-gateway/v1/websocket"
    );
    expect(defaultGatewayWebSocketUrl({ protocol: "https:", host: "money.example" })).toBe(
      "wss://money.example/upbit-gateway/v1/websocket"
    );
  });

  it("JSON_LIST frame을 시각화 항목으로 풀고 raw frame은 최근 200개만 보존한다", () => {
    const frame = (sequence: number): GatewayFrameEvent => ({
      event: "frame",
      trace_id: `trace-${sequence}`,
      connection_id: "connection",
      sequence,
      received_at: "2026-07-16T00:00:00Z",
      payload: [{ type: "ticker", code: "KRW-BTC", trade_price: sequence }],
      raw: "[]",
      binary: true,
      provenance: { visibility: "public", format: "JSON_LIST", endpoint_ids: ["websocket.ticker"] }
    });

    const frames = Array.from({ length: 205 }, (_, index) => frame(index + 1)).reduce(appendBoundedFrame, [] as GatewayFrameEvent[]);

    expect(frames).toHaveLength(200);
    expect(frames[0].sequence).toBe(6);
    expect(framePayloads(frames.at(-1)!)).toEqual([{ type: "ticker", code: "KRW-BTC", trade_price: 205 }]);
  });
});
