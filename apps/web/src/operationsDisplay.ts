import type {
  CollectionActivityBucket,
  RealtimeCollectionHeatmapCell,
  RealtimeCollectionHeatmapRow,
  OperationsTrendPoint,
  StorageBreakdownItem
} from "./api";

export function formatNumber(value: string): string {
  return Number(value).toLocaleString("ko-KR", { maximumFractionDigits: 4 });
}

export function formatCompactCount(value: number): string {
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toLocaleString("ko-KR", {
      maximumFractionDigits: 1
    })}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toLocaleString("ko-KR", {
      maximumFractionDigits: 1
    })}K`;
  }
  return value.toLocaleString("ko-KR");
}

export function heatmapCells(buckets: CollectionActivityBucket[]): CollectionActivityBucket[] {
  const wanted = 7 * 24;
  if (buckets.length >= wanted) return buckets.slice(-wanted);
  const now = Date.now();
  const padding = Array.from({ length: wanted - buckets.length }, (_, index) => ({
    bucketStartAt: new Date(now - (wanted - index) * 60 * 60 * 1000).toISOString(),
    runCount: 0,
    resultCount: 0,
    status: "none" as const
  }));
  return [...padding, ...buckets];
}

export function normalizeRealtimeCollectionHeatmapRows(
  rows: RealtimeCollectionHeatmapRow[]
): RealtimeCollectionHeatmapRow[] {
  const hourStart = new Date();
  const currentHourStart = new Date(
    hourStart.getFullYear(),
    hourStart.getMonth(),
    hourStart.getDate(),
    hourStart.getHours()
  );
  return rows.slice(0, 50).map((row) => ({
    ...row,
    hourlyBuckets: normalizeRealtimeHeatmapBuckets(row.hourlyBuckets, currentHourStart)
  }));
}

export function normalizeRealtimeHeatmapBuckets(
  buckets: RealtimeCollectionHeatmapCell[],
  anchorHourStart: Date
): RealtimeCollectionHeatmapCell[] {
  const wanted = 24;
  if (buckets.length >= wanted) {
    return buckets.slice(-wanted);
  }

  const existing = [...buckets].sort(
    (left, right) =>
      new Date(left.bucketStartAt).getTime() - new Date(right.bucketStartAt).getTime()
  );
  const seed = existing[existing.length - 1];
  const fallbackExpectedRowsByType = {
    source_candle: 60,
    ticker_snapshot: 60,
    orderbook_summary: 60
  };
  const expectedRowsByType = seed?.expectedRowsByType ?? fallbackExpectedRowsByType;
  const expectedRowsAll = seed?.expectedRowsAll ?? 180;
  const missingCount = wanted - buckets.length;
  const firstBucketStart = new Date(anchorHourStart.getTime() - missingCount * 60 * 60 * 1000);
  const padding = Array.from({ length: missingCount }, (_, index) => ({
    bucketStartAt: new Date(firstBucketStart.getTime() + index * 60 * 60 * 1000).toISOString(),
    expectedRowsAll,
    actualRowsAll: 0,
    expectedRowsByType,
    actualRowsByType: {
      source_candle: 0,
      ticker_snapshot: 0,
      orderbook_summary: 0
    },
    actualRatioPercent: "0",
    status: "none" as const
  }));
  return [...padding, ...existing].slice(-wanted);
}

export function emptyStorageBreakdown(): StorageBreakdownItem[] {
  return [
    "source_candle",
    "ticker_snapshot",
    "orderbook_summary",
    "quality_result"
  ].map((dataType) => ({
    dataType: dataType as StorageBreakdownItem["dataType"],
    label:
      dataType === "source_candle"
        ? "캔들"
        : dataType === "ticker_snapshot"
          ? "현재가"
          : dataType === "orderbook_summary"
            ? "호가"
            : "품질",
    rowCount: 0,
    bytes: 0,
    bytesDisplay: "0B",
    sharePercent: "0"
  }));
}

export function emptyTrendPoints(): OperationsTrendPoint[] {
  const today = Date.now();
  return Array.from({ length: 7 }, (_, index) => ({
    bucketDate: new Date(today - (6 - index) * 24 * 60 * 60 * 1000).toISOString(),
    coveragePercent: "0",
    storageBytes: 0,
    warningTargets: 0,
    incidentTargets: 0
  }));
}

export function formatBytes(value: number): string {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)}GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)}MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)}KB`;
  return `${value}B`;
}

export function dateTimeLocalToKstIso(value: string): string {
  return `${value}:00+09:00`;
}

export function formatDateTimeRange(startAt: string, endAt: string): string {
  return `${formatFreshness(startAt)} ~ ${formatFreshness(endAt)}`;
}

export function formatShortDay(value: string): string {
  return new Date(value).toLocaleDateString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit"
  });
}

export function formatShortDateTime(value: string): string {
  return new Date(value).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export function formatPercent(value: string): string {
  const percent = Number(value) * 100;
  const prefix = percent > 0 ? "+" : "";
  return `${prefix}${percent.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}%`;
}

export function formatFreshness(value: string): string {
  return new Date(value).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}
