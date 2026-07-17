import { describe, expect, test } from "vitest";
import { applyAnalysisMessage, initialAnalysisState } from "./analysisStream";

describe("코인 분석 WebSocket 메시지", () => {
  test("차트와 시장 상태를 서로 다른 메시지로 병합한다", () => {
    const chartState = applyAnalysisMessage(initialAnalysisState, {
      type: "analysis.chart",
      unit: "1d",
      chunkIndex: 0,
      chunkCount: 1,
      candles: [
        {
          startedAt: "2026-07-14T00:00:00+09:00",
          open: "100",
          high: "110",
          low: "90",
          close: "105",
          volume: "12",
          tradeAmount: "1200",
          calculationVersion: "candle-rollup-v2",
          sourceAsOf: "2026-07-14T00:00:00Z",
          knowledgeAt: "2026-07-14T00:00:01Z",
          inputContentHash: "0".repeat(64),
          quality: "available",
          completeness: "complete"
        }
      ]
    });
    const marketState = applyAnalysisMessage(chartState, {
      type: "analysis.market",
      ticker: { tradePrice: "106", accTradePrice24h: "1000", changeRate: "0.01", collectedAt: "2026-07-14T00:00:00+09:00" },
      orderbook: { bestBidPrice: "105", bestBidSize: "1", bestAskPrice: "107", bestAskSize: "1", spread: "2", bidDepth10: "10", askDepth10: "10", imbalance10: "0", collectedAt: "2026-07-14T00:00:00+09:00" },
      tradeSummary: { tradeCount: 4, buyVolume: "2", sellVolume: "1", lastTradeAt: null }
    });

    expect(marketState.candles).toHaveLength(1);
    expect(marketState.market?.ticker.tradePrice).toBe("106");
    expect(chartState.market).toBeNull();
  });

  test("분할된 지표 메시지를 순서대로 병합한다", () => {
    const first = applyAnalysisMessage(initialAnalysisState, {
      type: "analysis.indicators",
      chunkIndex: 0,
      chunkCount: 2,
      points: [indicatorPoint("2026-07-14T00:00:00+09:00")]
    });
    const merged = applyAnalysisMessage(first, {
      type: "analysis.indicators",
      chunkIndex: 1,
      chunkCount: 2,
      points: [indicatorPoint("2026-07-15T00:00:00+09:00")]
    });

    expect(merged.indicators).toHaveLength(2);
  });

  test("새 봉의 지표는 기존 지표 전체를 보존하며 갱신한다", () => {
    const state = applyAnalysisMessage(initialAnalysisState, {
      type: "analysis.indicators",
      chunkIndex: 0,
      chunkCount: 1,
      points: [indicatorPoint("2026-07-14T00:00:00+09:00"), indicatorPoint("2026-07-15T00:00:00+09:00")]
    });
    const updated = applyAnalysisMessage(state, {
      type: "analysis.indicator.upsert",
      point: { ...indicatorPoint("2026-07-15T00:00:00+09:00"), ema20: "110" }
    });

    expect(updated.indicators).toHaveLength(2);
    expect(updated.indicators[1]?.ema20).toBe("110");
  });
});

function indicatorPoint(startedAt: string) {
  return { startedAt, sma20: null, sma60: null, ema20: "100", bollingerUpper: null, bollingerMiddle: null, bollingerLower: null, rsi14: null };
}
