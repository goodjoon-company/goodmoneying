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

export type UpbitMarket = {
  market: string;
  koreanName: string;
  englishName: string;
  marketWarning?: string;
  marketEvent?: unknown;
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

type UpbitMarketResponse = {
  market: string;
  korean_name: string;
  english_name: string;
  market_warning?: string;
  market_event?: unknown;
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
  to,
  convertingPriceUnit,
  signal
}: {
  market: string;
  interval: UpbitCandleInterval;
  count: number;
  to?: string;
  convertingPriceUnit?: "KRW";
  signal?: AbortSignal;
}): Promise<{ candles: UpbitCandle[]; raw: unknown[] }> {
  const normalizedMarket = market.trim().toUpperCase();
  if (!/^[A-Z0-9]+-[A-Z0-9]+$/.test(normalizedMarket)) {
    throw new Error("거래쌍은 KRW-BTC 형식으로 입력해 주세요.");
  }
  if (!Number.isInteger(count) || count < 1 || count > 200) {
    throw new Error("조회 개수는 1~200 사이여야 합니다.");
  }
  if (to !== undefined && !isValidUpbitDateTime(to)) {
    throw new Error("조회 종료 시각(to)은 ISO 8601 형식이어야 합니다.");
  }
  if (convertingPriceUnit !== undefined && interval !== "1d") {
    throw new Error("종가 환산 통화는 일봉에서만 사용할 수 있습니다.");
  }

  const query = new URLSearchParams({ market: normalizedMarket, count: String(count) });
  if (to !== undefined) {
    query.set("to", to);
  }
  if (convertingPriceUnit !== undefined) {
    query.set("converting_price_unit", convertingPriceUnit);
  }
  const response = await fetch(
    `https://api.upbit.com/v1/candles/${candlePaths[interval]}?${query.toString()}`,
    { signal }
  );
  if (!response.ok) {
    throw new Error(`업비트 캔들 조회에 실패했습니다 (HTTP ${response.status})`);
  }
  const payload: unknown = await response.json();
  if (!Array.isArray(payload)) {
    throw new Error("업비트 캔들 응답 형식이 올바르지 않습니다.");
  }
  const raw: unknown[] = payload;
  if (!raw.every(isUpbitCandleResponse)) {
    throw new Error("업비트 캔들 응답 형식이 올바르지 않습니다.");
  }
  const candles = raw
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
  return { candles, raw };
}

export async function fetchUpbitMarkets({
  isDetails,
  signal
}: {
  isDetails: boolean;
  signal?: AbortSignal;
}): Promise<{ markets: UpbitMarket[]; raw: unknown[] }> {
  const query = new URLSearchParams({ is_details: String(isDetails) });
  const response = await fetch(`https://api.upbit.com/v1/market/all?${query.toString()}`, { signal });
  if (!response.ok) {
    throw new Error(`업비트 마켓 조회에 실패했습니다 (HTTP ${response.status})`);
  }
  const payload: unknown = await response.json();
  if (!Array.isArray(payload)) {
    throw new Error("업비트 마켓 응답 형식이 올바르지 않습니다.");
  }
  const raw: unknown[] = payload;
  if (!raw.every(isUpbitMarketResponse)) {
    throw new Error("업비트 마켓 응답 형식이 올바르지 않습니다.");
  }
  const markets = raw.map((market) => {
    return {
      market: market.market,
      koreanName: market.korean_name,
      englishName: market.english_name,
      ...(market.market_warning === undefined ? {} : { marketWarning: market.market_warning }),
      ...(market.market_event === undefined ? {} : { marketEvent: market.market_event })
    };
  });
  return { markets, raw };
}

export function mergeUpbitCandles(current: UpbitCandle[], page: UpbitCandle[]): UpbitCandle[] {
  const candlesByStartedAt = new Map(current.map((candle) => [candle.startedAt, candle]));
  for (const candle of page) {
    candlesByStartedAt.set(candle.startedAt, candle);
  }
  return [...candlesByStartedAt.values()].sort((left, right) => left.startedAt.localeCompare(right.startedAt));
}

function toUtcIso(value: string): string {
  return new Date(value.endsWith("Z") ? value : `${value}Z`).toISOString().replace(".000Z", "Z");
}

function isUpbitMarketResponse(value: unknown): value is UpbitMarketResponse {
  return (
    isRecord(value) &&
    typeof value.market === "string" &&
    typeof value.korean_name === "string" &&
    typeof value.english_name === "string" &&
    (value.market_warning === undefined || typeof value.market_warning === "string")
  );
}

function isUpbitCandleResponse(value: unknown): value is UpbitCandleResponse {
  return (
    isRecord(value) &&
    typeof value.candle_date_time_utc === "string" &&
    isValidUpbitDateTime(value.candle_date_time_utc) &&
    [
      value.opening_price,
      value.high_price,
      value.low_price,
      value.trade_price,
      value.candle_acc_trade_volume,
      value.candle_acc_trade_price
    ].every((field) => typeof field === "number" && Number.isFinite(field))
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isValidUpbitDateTime(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T| )(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))?$/.exec(value);
  if (!match) return false;

  const [, yearText, monthText, dayText, hourText, minuteText, secondText, offsetHourText, offsetMinuteText] = match;
  const [year, month, day, hour, minute, second] = [yearText, monthText, dayText, hourText, minuteText, secondText].map(Number);
  if (month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) return false;
  if (offsetHourText !== undefined && (Number(offsetHourText) > 23 || Number(offsetMinuteText) > 59)) return false;

  const daysByMonth = [31, isLeapYear(year) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day >= 1 && day <= daysByMonth[month - 1];
}

function isLeapYear(year: number): boolean {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}
