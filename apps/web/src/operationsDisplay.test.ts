import { describe, expect, it, vi } from "vitest";
import {
  dateTimeLocalToUtcIso,
  emptyStorageBreakdown,
  emptyTrendPoints,
  formatBytes,
  formatCompactCount,
  formatDateTimeRange,
  formatFreshness,
  formatNumber,
  formatPercent,
  heatmapCells
} from "./operationsDisplay";

describe("운영 표시 모델", () => {
  it("숫자, 비율, 저장량을 화면 표시값으로 변환한다", () => {
    expect(formatNumber("1234.56789")).toBe("1,234.5679");
    expect(formatCompactCount(1_250_000)).toBe("1.3M");
    expect(formatCompactCount(12_500)).toBe("12.5K");
    expect(formatBytes(1024 ** 2)).toBe("1.0MB");
    expect(formatPercent("0.0123")).toBe("+1.23%");
    expect(formatPercent("-0.034")).toBe("-3.4%");
  });

  it("표시 시각과 datetime-local 값을 명확히 변환한다", () => {
    expect(formatFreshness("2026-01-01T00:00:00.000Z")).toContain("01. 01.");
    expect(formatDateTimeRange("2026-01-01T00:00:00.000Z", "2026-01-02T00:00:00.000Z"))
      .toContain("~");
    expect(dateTimeLocalToUtcIso("2026-01-01T00:00")).toBe("2026-01-01T00:00:00.000Z");
  });

  it("빈 저장 breakdown과 운영 추이 fallback을 제공한다", () => {
    vi.setSystemTime(new Date("2026-06-20T00:00:00.000Z"));

    expect(emptyStorageBreakdown()).toHaveLength(4);
    expect(emptyTrendPoints()).toHaveLength(7);
    expect(emptyTrendPoints().at(-1)?.coveragePercent).toBe("0");

    vi.useRealTimers();
  });

  it("수집 활동 heatmap은 최근 168개를 보존하고 부족하면 none bucket을 채운다", () => {
    vi.setSystemTime(new Date("2026-06-20T00:00:00.000Z"));

    const cells = heatmapCells([]);
    expect(cells).toHaveLength(168);
    expect(cells.every((cell) => cell.status === "none")).toBe(true);

    vi.useRealTimers();
  });
});
