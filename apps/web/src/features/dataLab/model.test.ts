import { describe, expect, it } from "vitest";
import type { DatasetVersion } from "../../api";
import {
  buildDatasetCommandFromKst,
  findComparableSeries,
  validateSeriesLimit
} from "./model";

const versionA: DatasetVersion = {
  datasetVersionId: 11,
  schemaVersion: "dataset-v1",
  asOf: "2026-07-17T05:00:00Z",
  from: "2026-07-17T00:00:00Z",
  to: "2026-07-17T02:00:00Z",
  contentHash: "a".repeat(64),
  availabilityPolicy: "point_in_time_v1",
  fillPolicy: "none",
  missingPolicy: "fail",
  createdAt: "2026-07-17T06:00:02Z",
  series: [
    {
      seriesId: 101,
      instrumentId: 1,
      dataKind: "candle",
      unit: "1m",
      definitionSetHash: null,
      calculationVersion: "source-candle-v1"
    }
  ]
};

describe("Data Lab 모델", () => {
  it("KST 입력을 UTC build 명령으로 변환한다", () => {
    const command = buildDatasetCommandFromKst({
      nowUtc: "2026-07-17T06:00:00Z",
      actorId: "operator:test",
      reason: "Data Lab 생성",
      asOfKst: "2026-07-17T14:00",
      fromKst: "2026-07-17T09:00",
      toKst: "2026-07-17T11:00",
      series: versionA.series,
      fillPolicy: "none",
      missingPolicy: "fail"
    });

    expect(command.selection.asOf).toBe("2026-07-17T05:00:00.000Z");
    expect(command.selection.from).toBe("2026-07-17T00:00:00.000Z");
    expect(command.selection.to).toBe("2026-07-17T02:00:00.000Z");
    expect(command.requestId).toContain("dataset-build-20260717T060000");
    expect(command.policies.availabilityPolicy).toBe("point_in_time_v1");
  });

  it("series는 200개를 넘기지 못한다", () => {
    expect(validateSeriesLimit(Array.from({ length: 200 }, () => versionA.series[0]))).toBeNull();
    expect(validateSeriesLimit(Array.from({ length: 201 }, () => versionA.series[0]))).toBe(
      "Data Lab build는 한 번에 최대 200개 series만 고정할 수 있습니다."
    );
  });

  it("A/B 비교는 같은 자연 시계열만 매칭한다", () => {
    const versionB: DatasetVersion = {
      ...versionA,
      datasetVersionId: 12,
      contentHash: "b".repeat(64),
      series: [{ ...versionA.series[0], seriesId: 202 }]
    };

    expect(findComparableSeries(versionA, versionB)).toEqual([
      { leftSeriesId: 101, rightSeriesId: 202, label: "1 · candle · 1m" }
    ]);
  });
});
