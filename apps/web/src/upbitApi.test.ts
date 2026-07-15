import { describe, expect, it, vi } from "vitest";
import { fetchUpbitCandles, fetchUpbitMarkets, mergeUpbitCandles, type UpbitCandle } from "./upbitApi";

describe("업비트 공개 마켓 API", () => {
  it("상세 마켓 목록을 요청하고 응답 필드를 카멜 표기법으로 정규화한다", async () => {
    const raw = [
      {
        market: "KRW-BTC",
        korean_name: "비트코인",
        english_name: "Bitcoin",
        market_warning: "NONE",
        market_event: { warning: false }
      }
    ];
    const fetch = vi.fn().mockResolvedValue(Response.json(raw));
    vi.stubGlobal("fetch", fetch);

    const result = await fetchUpbitMarkets({ isDetails: true });

    expect(fetch).toHaveBeenCalledWith(
      "https://api.upbit.com/v1/market/all?is_details=true",
      expect.objectContaining({ signal: undefined })
    );
    expect(new Headers((fetch.mock.calls[0][1] as RequestInit).headers).has("Authorization")).toBe(false);
    expect(result.raw).toEqual(raw);
    expect(result.markets).toEqual([
      {
        market: "KRW-BTC",
        koreanName: "비트코인",
        englishName: "Bitcoin",
        marketWarning: "NONE",
        marketEvent: { warning: false }
      }
    ]);
  });

  it("마켓 목록 응답이 배열이 아니면 거부한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ error: "bad request" })));

    await expect(fetchUpbitMarkets({ isDetails: false })).rejects.toThrow(
      "업비트 마켓 응답 형식이 올바르지 않습니다."
    );
  });

  it("마켓 목록 배열의 잘못된 원소를 명시적인 응답 형식 오류로 거부한다", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const invalidPayloads = [
      [null],
      [{ korean_name: "비트코인", english_name: "Bitcoin" }],
      [{ market: 1, korean_name: "비트코인", english_name: "Bitcoin" }],
      [{ market: "KRW-BTC", korean_name: null, english_name: "Bitcoin" }],
      [{ market: "KRW-BTC", korean_name: "비트코인", english_name: undefined }]
    ];

    for (const payload of invalidPayloads) {
      fetch.mockResolvedValueOnce(Response.json(payload));
      await expect(fetchUpbitMarkets({ isDetails: false })).rejects.toThrow(
        "업비트 마켓 응답 형식이 올바르지 않습니다."
      );
    }
  });

  it("업비트가 실패 응답을 돌려주면 HTTP 상태를 포함해 실패한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("too many", { status: 429 })));

    await expect(fetchUpbitMarkets({ isDetails: false })).rejects.toThrow(
      "업비트 마켓 조회에 실패했습니다 (HTTP 429)"
    );
  });
});

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
    expect(new Headers((fetch.mock.calls[0][1] as RequestInit).headers).has("Authorization")).toBe(false);
    expect(result.candles.map((item) => item.startedAt)).toEqual([
      "2026-07-14T00:00:00Z",
      "2026-07-14T00:05:00Z"
    ]);
    expect(result.candles[0]).toMatchObject({ open: 100, high: 110, low: 90, close: 100, volume: 3 });
  });

  it("일봉 요청의 종료 시각과 원화 환산 단위를 쿼리에 포함하고 원본 응답을 보존한다", async () => {
    const raw = [candle("2026-07-14T00:00:00", "100")];
    const fetch = vi.fn().mockResolvedValue(Response.json(raw));
    vi.stubGlobal("fetch", fetch);

    const result = await fetchUpbitCandles({
      market: "KRW-BTC",
      interval: "1d",
      count: 2,
      to: "2026-07-15T00:00:00Z",
      convertingPriceUnit: "KRW"
    });

    expect(fetch).toHaveBeenCalledWith(
      "https://api.upbit.com/v1/candles/days?market=KRW-BTC&count=2&to=2026-07-15T00%3A00%3A00Z&converting_price_unit=KRW",
      expect.anything()
    );
    expect(result.raw).toEqual(raw);
    expect(result.candles).toHaveLength(1);
  });

  it("업비트가 실패 응답을 돌려주면 HTTP 상태를 포함해 실패한다", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("too many", { status: 429 })));

    await expect(fetchUpbitCandles({ market: "KRW-BTC", interval: "1d", count: 20 })).rejects.toThrow(
      "업비트 캔들 조회에 실패했습니다 (HTTP 429)"
    );
  });

  it("캔들 배열의 잘못된 원소를 명시적인 응답 형식 오류로 거부한다", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const validCandle = candle("2026-07-14T00:00:00", "100");
    const invalidPayloads = [
      [null],
      [{ ...validCandle, candle_date_time_utc: undefined }],
      [{ ...validCandle, candle_date_time_utc: 1 }],
      [{ ...validCandle, opening_price: undefined }],
      [{ ...validCandle, opening_price: "100" }]
    ];

    for (const payload of invalidPayloads) {
      fetch.mockResolvedValueOnce(Response.json(payload));
      await expect(fetchUpbitCandles({ market: "KRW-BTC", interval: "1d", count: 1 })).rejects.toThrow(
        "업비트 캔들 응답 형식이 올바르지 않습니다."
      );
    }
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

  it("잘못된 종료 시각을 요청 전에 거부한다", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json([]));
    vi.stubGlobal("fetch", fetch);

    await expect(
      fetchUpbitCandles({ market: "KRW-BTC", interval: "1d", count: 20, to: "not-a-date" })
    ).rejects.toThrow("조회 종료 시각(to)은 ISO 8601 형식이어야 합니다.");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("JavaScript가 해석할 수 있지만 업비트 형식이 아닌 종료 시각을 요청 전에 거부한다", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json([]));
    vi.stubGlobal("fetch", fetch);
    const nonDocumentedDates = ["2026/07/15 00:00:00", "July 15, 2026"];

    for (const nonDocumentedDate of nonDocumentedDates) {
      expect(Number.isNaN(new Date(nonDocumentedDate).getTime())).toBe(false);
      await expect(
        fetchUpbitCandles({ market: "KRW-BTC", interval: "1d", count: 20, to: nonDocumentedDate })
      ).rejects.toThrow("조회 종료 시각(to)은 ISO 8601 형식이어야 합니다.");
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it("존재하지 않는 달력 날짜와 범위를 벗어난 시간을 요청 전에 거부하면서 공백 형식은 허용한다", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json([]));
    vi.stubGlobal("fetch", fetch);

    for (const invalidTo of ["2026-02-31T00:00:00Z", "2026-01-01T24:00:00Z", "2026-01-01T00:60:00Z"]) {
      await expect(
        fetchUpbitCandles({ market: "KRW-BTC", interval: "1d", count: 1, to: invalidTo })
      ).rejects.toThrow("조회 종료 시각(to)은 ISO 8601 형식이어야 합니다.");
    }
    await expect(
      fetchUpbitCandles({ market: "KRW-BTC", interval: "1d", count: 1, to: "2026-07-15 00:00:00" })
    ).resolves.toEqual({ candles: [], raw: [] });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("비일봉 원화 환산 요청을 요청 전에 거부한다", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json([]));
    vi.stubGlobal("fetch", fetch);

    await expect(
      fetchUpbitCandles({ market: "KRW-BTC", interval: "5m", count: 20, convertingPriceUnit: "KRW" })
    ).rejects.toThrow("종가 환산 통화는 일봉에서만 사용할 수 있습니다.");
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("업비트 캔들 페이지 병합", () => {
  it("동일한 시작 시각은 하나만 남기고 시간 오름차순으로 정렬한다", () => {
    const existing = [candleModel("2026-07-14T00:05:00Z", 105)];
    const page = [
      candleModel("2026-07-14T00:00:00Z", 100),
      candleModel("2026-07-14T00:05:00Z", 999)
    ];

    expect(mergeUpbitCandles(existing, page).map((item) => item.startedAt)).toEqual([
      "2026-07-14T00:00:00Z",
      "2026-07-14T00:05:00Z"
    ]);
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

function candleModel(startedAt: string, close: number): UpbitCandle {
  return {
    startedAt,
    open: close,
    high: close,
    low: close,
    close,
    volume: 1,
    tradeAmount: close
  };
}
