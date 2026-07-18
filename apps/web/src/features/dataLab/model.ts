import type {
  CreateDatasetBuildCommand,
  DatasetFillPolicy,
  DatasetMissingPolicy,
  DatasetVersion,
  DatasetVersionSeries
} from "../../api";

export type BuildDatasetCommandFromKstInput = {
  nowUtc: string;
  actorId: string;
  reason: string;
  asOfKst: string;
  fromKst: string;
  toKst: string;
  series: DatasetVersionSeries[];
  fillPolicy: DatasetFillPolicy;
  missingPolicy: DatasetMissingPolicy;
};

export type ComparableSeries = {
  leftSeriesId: number;
  rightSeriesId: number;
  label: string;
};

export function buildDatasetCommandFromKst(
  input: BuildDatasetCommandFromKstInput
): CreateDatasetBuildCommand {
  const requestedAt = new Date(input.nowUtc);
  const requestStamp = compactUtcStamp(requestedAt);
  return {
    requestId: `dataset-build-${requestStamp}`,
    idempotencyKey: `dataset-build-${requestStamp}`,
    actorId: input.actorId.trim(),
    requestedAt: requestedAt.toISOString(),
    reason: input.reason.trim(),
    selection: {
      asOf: kstLocalDateTimeToUtc(input.asOfKst),
      from: kstLocalDateTimeToUtc(input.fromKst),
      to: kstLocalDateTimeToUtc(input.toKst),
      series: input.series.map(({ seriesId: _seriesId, ...series }) => series)
    },
    policies: {
      availabilityPolicy: "point_in_time_v1",
      fillPolicy: input.fillPolicy,
      missingPolicy: input.missingPolicy
    }
  };
}

export function validateSeriesLimit(series: readonly unknown[]): string | null {
  if (series.length > 200) {
    return "Data Lab build는 한 번에 최대 200개 series만 고정할 수 있습니다.";
  }
  return null;
}

export function findComparableSeries(
  left: DatasetVersion,
  right: DatasetVersion
): ComparableSeries[] {
  const rightByNaturalKey = new Map(
    right.series.map((series) => [seriesNaturalKey(series), series])
  );
  return left.series.flatMap((leftSeries) => {
    const rightSeries = rightByNaturalKey.get(seriesNaturalKey(leftSeries));
    return rightSeries
      ? [
          {
            leftSeriesId: leftSeries.seriesId,
            rightSeriesId: rightSeries.seriesId,
            label: `${leftSeries.instrumentId} · ${leftSeries.dataKind} · ${leftSeries.unit}`
          }
        ]
      : [];
  });
}

function kstLocalDateTimeToUtc(value: string): string {
  return new Date(`${value}:00+09:00`).toISOString();
}

function compactUtcStamp(value: Date): string {
  return value.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "");
}

function seriesNaturalKey(series: DatasetVersionSeries): string {
  return [
    series.instrumentId,
    series.dataKind,
    series.unit,
    series.definitionSetHash ?? "",
    series.calculationVersion ?? ""
  ].join("|");
}
