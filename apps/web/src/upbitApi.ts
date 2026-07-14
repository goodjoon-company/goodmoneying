export type UpbitCandleInterval = "1m" | "3m" | "5m" | "10m" | "15m" | "30m" | "1h" | "4h" | "1d" | "1w" | "1M";

export type UpbitCandle = {
  startedAt: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  tradeAmount: number;
};

type UpbitCandleResponse = {
  candle_date_time_utc: string;
  opening_price: number;
  high_price: number;
  low_price: number;
  trade_price: number;
  candle_acc_trade_volume: number;
  candle_acc_trade_price: number;
};

const candlePaths: Record<UpbitCandleInterval, string> = {
  "1m": "minutes/1",
  "3m": "minutes/3",
  "5m": "minutes/5",
  "10m": "minutes/10",
  "15m": "minutes/15",
  "30m": "minutes/30",
  "1h": "minutes/60",
  "4h": "minutes/240",
  "1d": "days",
  "1w": "weeks",
  "1M": "months"
};

export async function fetchUpbitCandles({
  market,
  interval,
  count,
  signal
}: {
  market: string;
  interval: UpbitCandleInterval;
  count: number;
  signal?: AbortSignal;
}): Promise<UpbitCandle[]> {
  const normalizedMarket = market.trim().toUpperCase();
  if (!/^[A-Z0-9]+-[A-Z0-9]+$/.test(normalizedMarket)) {
    throw new Error("거래쌍은 KRW-BTC 형식으로 입력해 주세요.");
  }
  if (!Number.isInteger(count) || count < 1 || count > 200) {
    throw new Error("조회 개수는 1~200 사이여야 합니다.");
  }

  const query = new URLSearchParams({ market: normalizedMarket, count: String(count) });
  const response = await fetch(
    `https://api.upbit.com/v1/candles/${candlePaths[interval]}?${query.toString()}`,
    { signal }
  );
  if (!response.ok) {
    throw new Error(`업비트 캔들 조회에 실패했습니다 (HTTP ${response.status})`);
  }
  const payload = (await response.json()) as UpbitCandleResponse[];
  if (!Array.isArray(payload)) {
    throw new Error("업비트 캔들 응답 형식이 올바르지 않습니다.");
  }
  return payload
    .map((candle) => ({
      startedAt: toUtcIso(candle.candle_date_time_utc),
      open: candle.opening_price,
      high: candle.high_price,
      low: candle.low_price,
      close: candle.trade_price,
      volume: candle.candle_acc_trade_volume,
      tradeAmount: candle.candle_acc_trade_price
    }))
    .sort((left, right) => left.startedAt.localeCompare(right.startedAt));
}

function toUtcIso(value: string): string {
  return new Date(value.endsWith("Z") ? value : `${value}Z`).toISOString().replace(".000Z", "Z");
}
