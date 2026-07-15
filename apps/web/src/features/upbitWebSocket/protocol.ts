import type { CandleUnit, GatewayFrameEvent, MarketLike, Visibility, WorkbenchTab } from "./types";

export const workbenchCandleUnits: CandleUnit[] = [
  "1s", "1m", "3m", "5m", "10m", "15m", "30m", "60m", "240m"
];

export function workbenchStreamEndpointIds(): string[] {
  return [
    streamForTab("ticker", "1s").endpointId,
    streamForTab("trade", "1s").endpointId,
    streamForTab("orderbook", "1s").endpointId,
    ...workbenchCandleUnits.map((unit) => streamForTab("candle", unit).endpointId),
    streamForTab("asset", "1s").endpointId,
    streamForTab("order", "1s").endpointId
  ];
}

export function defaultGatewayWebSocketUrl(location: Pick<Location, "protocol" | "host">) {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/upbit-gateway/v1/websocket`;
}

export function marketOptions(markets: MarketLike[]) {
  return markets.map((market) => {
    const value = market.market.trim().toUpperCase();
    const name = market.koreanName ?? market.englishName;
    return { value, label: name ? `${value} · ${name}` : value };
  });
}

export function streamForTab(tab: WorkbenchTab, candleUnit: CandleUnit): { endpointId: string; visibility: Visibility } {
  if (tab === "asset") return { endpointId: "websocket.my-asset", visibility: "private" };
  if (tab === "order") return { endpointId: "websocket.my-order", visibility: "private" };
  if (tab === "candle") return { endpointId: `websocket.candle-${candleUnit}`, visibility: "public" };
  return { endpointId: `websocket.${tab}`, visibility: "public" };
}

export function appendBoundedFrame(frames: GatewayFrameEvent[], frame: GatewayFrameEvent) {
  return [...frames, frame].slice(-200);
}

export function framePayloads(frame: GatewayFrameEvent): Record<string, unknown>[] {
  const values = Array.isArray(frame.payload) ? frame.payload : [frame.payload];
  return values.filter((value): value is Record<string, unknown> => value !== null && typeof value === "object");
}

export function isGatewayFrame(value: unknown): value is GatewayFrameEvent {
  return value !== null && typeof value === "object" && (value as { event?: unknown }).event === "frame";
}
