export type Visibility = "public" | "private";
export type UpbitFormat = "DEFAULT" | "SIMPLE" | "JSON_LIST" | "SIMPLE_LIST";
export type WorkbenchTab = "ticker" | "trade" | "orderbook" | "candle" | "asset" | "order";
export type CandleUnit = "1s" | "1m" | "3m" | "5m" | "10m" | "15m" | "30m" | "60m" | "240m";

export type MarketLike = { market: string; koreanName?: string; englishName?: string };

export type GatewayFrameEvent = {
  event: "frame";
  trace_id: string;
  connection_id: string;
  sequence: number;
  received_at: string;
  payload: unknown;
  raw: string;
  binary: boolean;
  provenance: { visibility: Visibility; format: UpbitFormat; endpoint_ids: string[] };
};

export type GatewayEvent = GatewayFrameEvent | {
  event: "connection" | "subscription" | "error";
  state?: string;
  action?: string;
  code?: string;
  message?: string;
  status?: number;
  [key: string]: unknown;
};

export interface BrowserSocket {
  readyState: number;
  addEventListener(type: string, listener: (event: Event) => void): void;
  removeEventListener(type: string, listener: (event: Event) => void): void;
  send(data: string): void;
  close(): void;
}

export type SocketFactory = (url: string) => BrowserSocket;
