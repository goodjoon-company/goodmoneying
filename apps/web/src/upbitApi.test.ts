import { describe, expect, it, vi } from "vitest";
import { fetchUpbitCandles } from "./upbitApi";

describe("업비트 공개 캔들 API", () => {
  it("선택한 분봉을 요청하고 최신순 응답을 시간 오름차순으로 정렬한다", async () => {
    const fetch = vi.fn().mockResolvedValue(
      Response.json([
        candle("2026-07-14T00:05:00", "105"),
        candle("2026-07-14T00:00:00", "100")
      ])
    );
    vi.stubGlobal("fetch", fetch);

    const result = await fetchUpbitCandles({ market: "krw-btc", interval: "5m", count: 2 });

    expect(fetch).toHaveBeenCalledWith(
      "https://api.upbit.com/v1/candles/minutes/5?market=KRW-BTC&count=2",
      expect.objectContaining({ signal: undefined })
    );
    expect(result.map((item) => item.startedAt)).toEqual([
      "2026-07-14T00:00:00Z",
      "2026-07-14T00:05:00Z"
    ]);
    expect(result[0]).toMatchObject({ open: 100, high: 110, low: 90, close: 100, volume: 3 });
  });

  it("업비트가 실패 응답을 돌려주면 HTTP 상태를 포함해 실패한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("too many", { status: 429 })));

    await expect(fetchUpbitCandles({ market: "KRW-BTC", interval: "1d", count: 20 })).rejects.toThrow(
      "업비트 캔들 조회에 실패했습니다 (HTTP 429)"
    );
  });

  it("일·주·월과 함께 업비트가 지원하는 3·10·30분 봉을 요청할 수 있다", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json([]));
    vi.stubGlobal("fetch", fetch);

    await fetchUpbitCandles({ market: "KRW-BTC", interval: "30m", count: 1 });

    expect(fetch).toHaveBeenCalledWith(
      "https://api.upbit.com/v1/candles/minutes/30?market=KRW-BTC&count=1",
      expect.anything()
    );
  });

  it("거래쌍 형식과 조회 개수 범위를 요청 전에 검증한다", async () => {
    await expect(fetchUpbitCandles({ market: "BTC", interval: "1d", count: 20 })).rejects.toThrow(
      "거래쌍은 KRW-BTC 형식으로 입력해 주세요."
    );
    await expect(fetchUpbitCandles({ market: "KRW-BTC", interval: "1d", count: 201 })).rejects.toThrow(
      "조회 개수는 1~200 사이여야 합니다."
    );
  });
});

function candle(time: string, close: string) {
  return {
    market: "KRW-BTC",
    candle_date_time_utc: time,
    opening_price: 100,
    high_price: 110,
    low_price: 90,
    trade_price: Number(close),
    candle_acc_trade_volume: 3,
    candle_acc_trade_price: 300
  };
}
