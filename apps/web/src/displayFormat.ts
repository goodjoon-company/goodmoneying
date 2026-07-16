const TIME_ZONE = "Asia/Seoul";
const ASSET_UNIT: Record<string, string> = {
  BTC: "₿",
  KRW: "￦",
  USDT: "$"
};

const kstDateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23"
});

function parsedDate(value: string | number | Date): Date | null {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function dateParts(value: string | number | Date): Record<string, string> | null {
  const date = parsedDate(value);
  if (!date) return null;
  return Object.fromEntries(
    kstDateTimeFormatter.formatToParts(date).map((part) => [part.type, part.value])
  );
}

export function formatKstDateTime(value: string | number | Date): string {
  const parts = dateParts(value);
  if (!parts) return String(value);
  return `${parts.year}.${parts.month}.${parts.day} ${parts.hour}:${parts.minute}:${parts.second} KST`;
}

export function formatKstDate(value: string | number | Date): string {
  const parts = dateParts(value);
  if (!parts) return String(value);
  return `${parts.year}.${parts.month}.${parts.day}`;
}

export function formatNumber(
  value: string | number,
  maximumFractionDigits = 8
): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return numeric.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits
  });
}

export function formatMoney(value: string | number, currency: string): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  const normalizedCurrency = currency.toUpperCase();
  const amount = normalizedCurrency === "KRW"
    ? formatNumber(Math.trunc(numeric), 0)
    : formatNumber(numeric, 8);
  const unit = ASSET_UNIT[normalizedCurrency] ?? normalizedCurrency;
  return unit ? `${amount} ${unit}` : amount;
}

export function formatAssetAmount(value: string | number, asset: string): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  const normalizedAsset = asset.toUpperCase();
  const amount = formatNumber(numeric, normalizedAsset === "KRW" ? 0 : 8);
  const unit = ASSET_UNIT[normalizedAsset] ?? normalizedAsset;
  return unit ? `${amount} ${unit}` : amount;
}
