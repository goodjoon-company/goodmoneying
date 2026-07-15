import { describe, expect, it } from "vitest";

import { mergeCandleRows, nextCandleParameters } from "./pagination";

const candle = (startedAt: string) => ({
  startedAt, open: 1, high: 2, low: 0, close: 1, volume: 3, tradeAmount: 3, raw: {}
});

describe("캔들 연속 조회", () => {
  it("과거 페이지는 가장 오래된 시각을 to로 사용하고 중복 캔들을 제거한다", () => {
    const current = [candle("2026-07-16T00:01:00Z"), candle("2026-07-16T00:02:00Z")];
    expect(nextCandleParameters("past", { market: "KRW-BTC", count: 200 }, current, "minute", 1))
      .toEqual({ market: "KRW-BTC", count: 200, to: "2026-07-16T00:01:00Z" });
    expect(mergeCandleRows(current, [
      candle("2026-07-16T00:00:00Z"), candle("2026-07-16T00:01:00Z")
    ])).toHaveLength(3);
  });

  it("현재 시각에 닿은 최신 페이지는 future 요청을 만들지 않아 반복 루프를 막는다", () => {
    const current = [candle("2026-07-16T00:02:00Z")];
    expect(nextCandleParameters(
      "future", { market: "KRW-BTC", count: 200, to: "2026-07-16T00:03:00Z" },
      current, "minute", 1, new Date("2026-07-16T00:02:30Z")
    )).toBeNull();
  });

  it("일 캔들 future 조회는 count만큼 다음 종료 시각을 전진시킨다", () => {
    expect(nextCandleParameters(
      "future", { market: "KRW-BTC", count: 2, to: "2026-01-01T00:00:00Z" },
      [candle("2026-01-01T00:00:00Z")], "day", 1, new Date("2026-01-10T00:00:00Z")
    )).toEqual({ market: "KRW-BTC", count: 2, to: "2026-01-03T00:00:00.000Z" });
  });
});
