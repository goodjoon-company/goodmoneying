import type { CandleRow, RequestParameters } from "./types";

export type CandleGranularity = "second" | "minute" | "day" | "week" | "month" | "year";

export function mergeCandleRows(current: CandleRow[], page: CandleRow[]): CandleRow[] {
  const byStartedAt = new Map(current.map((row) => [row.startedAt, row]));
  for (const row of page) byStartedAt.set(row.startedAt, row);
  return [...byStartedAt.values()].sort((left, right) => left.startedAt.localeCompare(right.startedAt));
}

export function nextCandleParameters(
  direction: "past" | "future",
  initial: RequestParameters,
  current: CandleRow[],
  granularity: CandleGranularity,
  unit = 1,
  now = new Date()
): RequestParameters | null {
  if (current.length === 0) return null;
  if (direction === "past") return { ...initial, to: current[0].startedAt };
  const newest = new Date(current.at(-1)?.startedAt ?? 0);
  if (newest.getTime() >= now.getTime() - intervalMilliseconds(granularity, unit)) return null;
  const upper = advance(newest, granularity, unit * Number(initial.count ?? 200));
  return { ...initial, to: new Date(Math.min(upper.getTime(), now.getTime())).toISOString() };
}

function intervalMilliseconds(granularity: CandleGranularity, unit: number): number {
  if (granularity === "second") return unit * 1_000;
  if (granularity === "minute") return unit * 60_000;
  if (granularity === "day") return unit * 86_400_000;
  if (granularity === "week") return unit * 7 * 86_400_000;
  return unit * 28 * 86_400_000;
}

function advance(value: Date, granularity: CandleGranularity, count: number): Date {
  const next = new Date(value);
  if (granularity === "month") next.setUTCMonth(next.getUTCMonth() + count);
  else if (granularity === "year") next.setUTCFullYear(next.getUTCFullYear() + count);
  else next.setTime(next.getTime() + intervalMilliseconds(granularity, count));
  return next;
}

export function parseCandleRows(body: unknown): CandleRow[] {
  if (!Array.isArray(body)) return [];
  return body.flatMap((value) => {
    if (!isRecord(value) || typeof value.candle_date_time_utc !== "string") return [];
    const number = (key: string) => typeof value[key] === "number" ? value[key] as number : 0;
    return [{
      startedAt: new Date(`${value.candle_date_time_utc}${value.candle_date_time_utc.endsWith("Z") ? "" : "Z"}`).toISOString(),
      open: number("opening_price"), high: number("high_price"), low: number("low_price"),
      close: number("trade_price"), volume: number("candle_acc_trade_volume"),
      tradeAmount: number("candle_acc_trade_price"), raw: value
    }];
  }).sort((left, right) => left.startedAt.localeCompare(right.startedAt));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
