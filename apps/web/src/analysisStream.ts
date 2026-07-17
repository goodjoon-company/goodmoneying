import type { Candle, Instrument, OrderbookSummary, TickerSnapshot } from "./api";

export type AnalysisUnit = "1m" | "3m" | "5m" | "10m" | "15m" | "30m" | "1h" | "4h" | "1d" | "1w" | "1M";
export type AnalysisRangeDays = 1 | 7 | 30 | 90 | 365 | 1095;

export type IndicatorPoint = {
  startedAt: string;
  sma20: string | null;
  sma60: string | null;
  ema20: string | null;
  bollingerUpper: string | null;
  bollingerMiddle: string | null;
  bollingerLower: string | null;
  rsi14: string | null;
};

export type AnalysisMarket = {
  ticker: Pick<TickerSnapshot, "tradePrice" | "accTradePrice24h" | "changeRate" | "collectedAt">;
  orderbook: Pick<
    OrderbookSummary,
    | "bestBidPrice"
    | "bestBidSize"
    | "bestAskPrice"
    | "bestAskSize"
    | "spread"
    | "bidDepth10"
    | "askDepth10"
    | "imbalance10"
    | "collectedAt"
  >;
  tradeSummary: { tradeCount: number; buyVolume: string; sellVolume: string; lastTradeAt: string | null };
};

export type AnalysisState = {
  instrument: Instrument | null;
  candles: Candle[];
  indicators: IndicatorPoint[];
  market: AnalysisMarket | null;
  error: string | null;
};

export const initialAnalysisState: AnalysisState = {
  instrument: null,
  candles: [],
  indicators: [],
  market: null,
  error: null
};

export type AnalysisMessage =
  | { type: "analysis.session"; subscriptionId: string }
  | { type: "analysis.instrument"; instrument: Instrument }
  | { type: "analysis.chart"; unit: AnalysisUnit; chunkIndex: number; chunkCount: number; candles: Candle[] }
  | { type: "analysis.indicators"; chunkIndex: number; chunkCount: number; points: IndicatorPoint[] }
  | { type: "analysis.indicator.upsert"; point: IndicatorPoint }
  | { type: "analysis.market"; ticker: AnalysisMarket["ticker"]; orderbook: AnalysisMarket["orderbook"]; tradeSummary: AnalysisMarket["tradeSummary"] }
  | { type: "analysis.candle.upsert"; candle: Candle }
  | { type: "analysis.error"; code: string; message: string };

export function applyAnalysisMessage(state: AnalysisState, message: AnalysisMessage): AnalysisState {
  switch (message.type) {
    case "analysis.instrument":
      return { ...state, instrument: message.instrument, error: null };
    case "analysis.chart":
      return {
        ...state,
        candles: message.chunkIndex === 0 ? message.candles : [...state.candles, ...message.candles],
        indicators: message.chunkIndex === 0 ? [] : state.indicators,
        error: null
      };
    case "analysis.indicators":
      return {
        ...state,
        indicators: message.chunkIndex === 0 ? message.points : [...state.indicators, ...message.points],
        error: null
      };
    case "analysis.market":
      return {
        ...state,
        market: { ticker: message.ticker, orderbook: message.orderbook, tradeSummary: message.tradeSummary },
        error: null
      };
    case "analysis.indicator.upsert":
      return { ...state, indicators: upsertIndicator(state.indicators, message.point) };
    case "analysis.candle.upsert":
      return { ...state, candles: upsertCandle(state.candles, message.candle) };
    case "analysis.error":
      return { ...state, error: message.message };
    default:
      return state;
  }
}

function upsertCandle(candles: Candle[], candle: Candle): Candle[] {
  const index = candles.findIndex((item) => item.startedAt === candle.startedAt);
  if (index === -1) return [...candles, candle];
  return candles.map((item, itemIndex) => (itemIndex === index ? candle : item));
}

function upsertIndicator(indicators: IndicatorPoint[], point: IndicatorPoint): IndicatorPoint[] {
  const index = indicators.findIndex((item) => item.startedAt === point.startedAt);
  if (index === -1) return [...indicators, point];
  return indicators.map((item, itemIndex) => (itemIndex === index ? point : item));
}
