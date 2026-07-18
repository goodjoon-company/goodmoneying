import { useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CopyPlus, GitCompareArrows, RefreshCcw } from "lucide-react";
import {
  createDatasetBuild,
  loadDataFoundation,
  loadDatasetBuilds,
  loadDatasetCoverage,
  loadDatasetSeries,
  loadDatasetVersions,
  type DataFoundationMarket,
  type DatasetBuild,
  type DatasetCoverage,
  type DatasetSeriesResponse,
  type DatasetVersion
} from "../../api";
import { formatKstDateTime } from "../../displayFormat";
import {
  buildDatasetCommandFromKst,
  findComparableSeries,
  validateSeriesLimit
} from "./model";

export function DataLab() {
  const queryClient = useQueryClient();
  const [selectedVersionId, setSelectedVersionId] = useState<number | null>(null);
  const [selectedSeriesId, setSelectedSeriesId] = useState<number | null>(null);
  const [actorId, setActorId] = useState("operator:data-lab");
  const [reason, setReason] = useState("Data Lab 신규 build");
  const [fromKst, setFromKst] = useState("2026-07-17T09:00");
  const [toKst, setToKst] = useState("2026-07-17T11:00");
  const [selectedInstrumentId, setSelectedInstrumentId] = useState<number | null>(null);
  const foundationQuery = useQuery({
    queryKey: ["data-foundation"],
    queryFn: loadDataFoundation
  });
  const buildsQuery = useInfiniteQuery({
    queryKey: ["dataset-builds"],
    queryFn: ({ pageParam }) => loadDatasetBuilds({ pageSize: 50, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) =>
        page.items.some((item) => ["pending", "running", "retry_wait"].includes(item.status))
      )
        ? 5_000
        : false
  });
  const versionsQuery = useInfiniteQuery({
    queryKey: ["dataset-versions"],
    queryFn: ({ pageParam }) => loadDatasetVersions({ pageSize: 50, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor
  });
  const builds = buildsQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const versions = versionsQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const krwMarkets = useMemo(
    () =>
      foundationQuery.data?.markets.filter(
        (market) => market.quoteCurrency === "KRW" && market.tradingStatus === "active"
      ) ?? [],
    [foundationQuery.data]
  );
  const selectedMarket =
    krwMarkets.find((market) => market.instrumentId === selectedInstrumentId) ?? krwMarkets[0];
  const selectedVersion =
    versions.find((version) => version.datasetVersionId === selectedVersionId) ?? versions[0];
  const compareVersion = versions.find(
    (version) => version.datasetVersionId !== selectedVersion?.datasetVersionId
  );
  const selectedSeries =
    selectedVersion?.series.find((series) => series.seriesId === selectedSeriesId) ??
    selectedVersion?.series[0] ??
    null;
  const coverageQuery = useQuery({
    queryKey: ["dataset-coverage", selectedVersion?.datasetVersionId],
    queryFn: () => loadDatasetCoverage(selectedVersion!.datasetVersionId),
    enabled: Boolean(selectedVersion)
  });
  const seriesQuery = useInfiniteQuery({
    queryKey: [
      "dataset-series",
      selectedVersion?.datasetVersionId,
      selectedSeries?.seriesId,
      selectedVersion?.from,
      selectedVersion?.to
    ],
    queryFn: ({ pageParam }) =>
      loadDatasetSeries({
        datasetVersionId: selectedVersion!.datasetVersionId,
        seriesId: selectedSeries!.seriesId,
        from: selectedVersion!.from,
        to: selectedVersion!.to,
        pageSize: 500,
        cursor: pageParam
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
    enabled: Boolean(selectedVersion && selectedSeries)
  });
  const seriesResponse = seriesQuery.data?.pages[0]
    ? {
        ...seriesQuery.data.pages[0],
        items: seriesQuery.data.pages.flatMap((page) => page.items),
        nextCursor: seriesQuery.hasNextPage
          ? seriesQuery.data.pages.at(-1)?.nextCursor ?? null
          : null
      }
    : null;
  const comparableSeries = useMemo(
    () => (selectedVersion && compareVersion ? findComparableSeries(selectedVersion, compareVersion) : []),
    [selectedVersion, compareVersion]
  );
  const cloneMutation = useMutation({
    mutationFn: (version: DatasetVersion) =>
      createDatasetBuild(
        buildDatasetCommandFromKst({
          nowUtc: new Date().toISOString(),
          actorId: "operator:data-lab",
          reason: `Data Lab version ${version.datasetVersionId} 복제`,
          asOfKst: utcToKstInput(version.asOf),
          fromKst: utcToKstInput(version.from),
          toKst: utcToKstInput(version.to),
          series: version.series,
          fillPolicy: version.fillPolicy,
          missingPolicy: version.missingPolicy
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["dataset-builds"] });
    }
  });
  const createMutation = useMutation({
    mutationFn: () => {
      if (!selectedMarket) throw new Error("선택 가능한 KRW 시장이 없습니다.");
      return createDatasetBuild(
        buildDatasetCommandFromKst({
          nowUtc: new Date().toISOString(),
          actorId,
          reason,
          asOfKst: toKst,
          fromKst,
          toKst,
          series: [marketToDefaultSeries(selectedMarket)],
          fillPolicy: "none",
          missingPolicy: "fail"
        })
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["dataset-builds"] });
    }
  });

  return (
    <section className="data-lab" aria-labelledby="data-lab-title">
      <header className="data-lab-title-row">
        <div>
          <p className="eyebrow">P2-6 · 연구 데이터 작업대</p>
          <h2 id="data-lab-title">Data Lab</h2>
          <p>
            빌드 수명주기와 불변 version, coverage, exact member를 REST polling으로 확인합니다.
          </p>
        </div>
        <button
          type="button"
          aria-label="Data Lab 새로고침"
          onClick={() => {
            void queryClient.invalidateQueries({ queryKey: ["dataset-builds"] });
            void queryClient.invalidateQueries({ queryKey: ["dataset-versions"] });
          }}
        >
          <RefreshCcw size={16} />새로고침
        </button>
      </header>

      <div className="data-lab-grid">
        <BuildComposer
          actorId={actorId}
          fromKst={fromKst}
          markets={krwMarkets}
          pending={createMutation.isPending}
          reason={reason}
          selectedInstrumentId={selectedMarket?.instrumentId ?? null}
          toKst={toKst}
          onActorIdChange={setActorId}
          onFromKstChange={setFromKst}
          onInstrumentChange={setSelectedInstrumentId}
          onReasonChange={setReason}
          onSubmit={() => createMutation.mutate()}
          onToKstChange={setToKst}
        />
        <BuildList
          builds={builds}
          hasMore={buildsQuery.hasNextPage}
          loadingMore={buildsQuery.isFetchingNextPage}
          onLoadMore={() => void buildsQuery.fetchNextPage()}
        />
      </div>

      <div className="data-lab-grid">
        <VersionList
          versions={versions}
          selectedVersionId={selectedVersion?.datasetVersionId ?? null}
          hasMore={versionsQuery.hasNextPage}
          loadingMore={versionsQuery.isFetchingNextPage}
          onLoadMore={() => void versionsQuery.fetchNextPage()}
          onSelect={(datasetVersionId) => {
            setSelectedVersionId(datasetVersionId);
            setSelectedSeriesId(null);
          }}
          onClone={(version) => cloneMutation.mutate(version)}
        />
      </div>

      <div className="data-lab-grid data-lab-grid-bottom">
        <CoveragePanel coverage={coverageQuery.data ?? null} />
        <SeriesPanel
          hasMore={seriesQuery.hasNextPage}
          loadingMore={seriesQuery.isFetchingNextPage}
          selectedSeriesId={selectedSeries?.seriesId ?? null}
          series={seriesResponse}
          seriesOptions={selectedVersion?.series ?? []}
          onLoadMore={() => void seriesQuery.fetchNextPage()}
          onSeriesSelect={setSelectedSeriesId}
        />
        <ComparePanel comparableSeries={comparableSeries} />
      </div>
    </section>
  );
}

function BuildList({
  builds,
  hasMore,
  loadingMore,
  onLoadMore
}: {
  builds: DatasetBuild[];
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}) {
  return (
    <section className="panel data-lab-panel" aria-label="데이터셋 build 목록">
      <div className="panel-heading">
        <h3>Build Lifecycle</h3>
        <span>{builds.length.toLocaleString("ko-KR")}개</span>
      </div>
      {builds.length === 0 ? <p className="empty-inline">진행 중인 build가 없습니다.</p> : null}
      {builds.map((build) => (
        <article className="data-lab-build-row" key={build.buildId}>
          <div>
            <strong>Build #{build.buildId}</strong>
            <span>{formatKstDateTime(build.frozenAt)}</span>
          </div>
          <StatusBadge value={build.status} />
          <small>시도 {build.attemptCount}/{build.maxAttempts}</small>
          {build.nextRetryAt ? <small>다음 재시도 {formatKstDateTime(build.nextRetryAt)}</small> : null}
          {build.deadLetterReason ? <small>{build.deadLetterReason}</small> : null}
        </article>
      ))}
      {hasMore ? (
        <button className="secondary-action data-lab-load-more" type="button" disabled={loadingMore} onClick={onLoadMore}>
          Build 더 보기
        </button>
      ) : null}
    </section>
  );
}

function BuildComposer({
  actorId,
  fromKst,
  markets,
  pending,
  reason,
  selectedInstrumentId,
  toKst,
  onActorIdChange,
  onFromKstChange,
  onInstrumentChange,
  onReasonChange,
  onSubmit,
  onToKstChange
}: {
  actorId: string;
  fromKst: string;
  markets: DataFoundationMarket[];
  pending: boolean;
  reason: string;
  selectedInstrumentId: number | null;
  toKst: string;
  onActorIdChange: (value: string) => void;
  onFromKstChange: (value: string) => void;
  onInstrumentChange: (value: number) => void;
  onReasonChange: (value: string) => void;
  onSubmit: () => void;
  onToKstChange: (value: string) => void;
}) {
  return (
    <section className="panel data-lab-panel" aria-label="데이터셋 build 생성">
      <div className="panel-heading">
        <h3>Build Composer</h3>
        <span>KST 입력</span>
      </div>
      <div className="data-lab-form">
        <label>
          시장
          <select
            aria-label="시장"
            value={selectedInstrumentId ?? ""}
            onChange={(event) => onInstrumentChange(Number(event.target.value))}
          >
            {markets.map((market) => (
              <option key={market.instrumentId} value={market.instrumentId}>
                {market.marketCode} · {market.koreanName}
              </option>
            ))}
          </select>
        </label>
        <label>
          작업자 ID
          <input value={actorId} onChange={(event) => onActorIdChange(event.target.value)} />
        </label>
        <label>
          사유
          <input value={reason} onChange={(event) => onReasonChange(event.target.value)} />
        </label>
        <label>
          시작 KST
          <input type="datetime-local" value={fromKst} onChange={(event) => onFromKstChange(event.target.value)} />
        </label>
        <label>
          종료 KST
          <input type="datetime-local" value={toKst} onChange={(event) => onToKstChange(event.target.value)} />
        </label>
      </div>
      <button
        className="primary-action"
        disabled={pending || !actorId.trim() || !reason.trim() || markets.length === 0}
        type="button"
        onClick={onSubmit}
      >
        신규 build 생성
      </button>
    </section>
  );
}

function VersionList({
  versions,
  selectedVersionId,
  hasMore,
  loadingMore,
  onLoadMore,
  onSelect,
  onClone
}: {
  versions: DatasetVersion[];
  selectedVersionId: number | null;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onSelect: (datasetVersionId: number) => void;
  onClone: (version: DatasetVersion) => void;
}) {
  return (
    <section className="panel data-lab-panel" aria-label="불변 dataset version 목록">
      <div className="panel-heading">
        <h3>Immutable Versions</h3>
        <span>{versions.length.toLocaleString("ko-KR")}개</span>
      </div>
      {versions.map((version) => {
        const validation = validateSeriesLimit(version.series);
        return (
          <article
            className={`data-lab-version-row ${version.datasetVersionId === selectedVersionId ? "active" : ""}`}
            key={version.datasetVersionId}
          >
            <button type="button" onClick={() => onSelect(version.datasetVersionId)}>
              <strong>Version #{version.datasetVersionId}</strong>
              <span>{shortHash(version.contentHash)}</span>
            </button>
            <dl>
              <div><dt>asOf</dt><dd>{formatKstDateTime(version.asOf)}</dd></div>
              <div><dt>범위</dt><dd>{formatKstDateTime(version.from)} ~ {formatKstDateTime(version.to)}</dd></div>
              <div><dt>정책</dt><dd>{version.fillPolicy} · {version.missingPolicy}</dd></div>
            </dl>
            {validation ? <p role="alert">{validation}</p> : null}
            <button
              className="secondary-action"
              type="button"
              aria-label={`Version #${version.datasetVersionId} 복제`}
              onClick={() => onClone(version)}
            >
              <CopyPlus size={16} />복제
            </button>
          </article>
        );
      })}
      {hasMore ? (
        <button className="secondary-action data-lab-load-more" type="button" disabled={loadingMore} onClick={onLoadMore}>
          Version 더 보기
        </button>
      ) : null}
    </section>
  );
}

function CoveragePanel({ coverage }: { coverage: DatasetCoverage | null }) {
  const counts = coverage?.counts;
  return (
    <section className="panel data-lab-panel" aria-label="dataset coverage heatmap">
      <div className="panel-heading">
        <h3>Coverage Heatmap</h3>
        <span>{coverage ? `${coverage.eligibleBucketCount}/${coverage.requestedBucketCount}` : "-"}</span>
      </div>
      {counts ? (
        <div className="data-lab-coverage-counts">
          {Object.entries(counts).map(([status, count]) => (
            <span className={`status-${status}`} key={status}>{status} {count}</span>
          ))}
        </div>
      ) : null}
      <div className="data-lab-coverage-bars">
        {(coverage?.items ?? []).map((item) => (
          <span
            className={`data-lab-coverage-bar status-${item.status}`}
            key={`${item.seriesId}-${item.rangeStartAt}`}
            title={`${item.status} ${item.bucketCount}`}
          />
        ))}
      </div>
    </section>
  );
}

function SeriesPanel({
  series,
  seriesOptions,
  selectedSeriesId,
  hasMore,
  loadingMore,
  onSeriesSelect,
  onLoadMore
}: {
  series: DatasetSeriesResponse | null;
  seriesOptions: DatasetVersion["series"];
  selectedSeriesId: number | null;
  hasMore: boolean;
  loadingMore: boolean;
  onSeriesSelect: (seriesId: number) => void;
  onLoadMore: () => void;
}) {
  const items = series?.items ?? [];
  return (
    <section className="panel data-lab-panel" aria-label="series exact member">
      <div className="panel-heading">
        <h3>Series Exact Members</h3>
        <span>{items.length}개</span>
      </div>
      {seriesOptions.length > 0 ? (
        <label className="data-lab-series-select">
          Series
          <select
            value={selectedSeriesId ?? ""}
            onChange={(event) => onSeriesSelect(Number(event.target.value))}
          >
            {seriesOptions.map((option) => (
              <option key={option.seriesId} value={option.seriesId}>
                #{option.seriesId} · {option.instrumentId} · {option.dataKind} · {option.unit}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <SeriesChart items={items} />
      <div className="data-lab-member-table-wrap">
        <table className="data-lab-member-table" aria-label="series exact member table">
          <thead>
            <tr>
              <th scope="col">발생 시각</th>
              <th scope="col">품질</th>
              <th scope="col">값</th>
              <th scope="col">내용 hash</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={`${item.occurredAt}-${item.contentHash}`}>
                <td><time>{formatKstDateTime(item.occurredAt)}</time></td>
                <td><strong>{item.quality}</strong></td>
                <td>{formatValues(item.values)}</td>
                <td><small>{shortHash(item.contentHash)}</small></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hasMore ? (
        <button className="secondary-action data-lab-load-more" type="button" disabled={loadingMore} onClick={onLoadMore}>
          Series 더 보기
        </button>
      ) : null}
    </section>
  );
}

function SeriesChart({ items }: { items: DatasetSeriesResponse["items"] }) {
  const points = items
    .map((item, index) => ({
      index,
      value: Number(item.values.close ?? item.values.open ?? 0)
    }))
    .filter((point) => Number.isFinite(point.value));
  if (points.length === 0) {
    return <p className="empty-inline">표시할 series 값이 없습니다.</p>;
  }
  const min = Math.min(...points.map((point) => point.value));
  const max = Math.max(...points.map((point) => point.value));
  const range = max - min || 1;
  const width = 260;
  const height = 92;
  const line = points
    .map((point) => {
      const x = points.length === 1 ? width / 2 : (point.index / (points.length - 1)) * width;
      const y = height - ((point.value - min) / range) * (height - 16) - 8;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <figure className="data-lab-series-chart">
      <svg
        role="img"
        aria-label="series exact member chart"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
      >
        <polyline points={line} />
        {points.map((point) => {
          const x = points.length === 1 ? width / 2 : (point.index / (points.length - 1)) * width;
          const y = height - ((point.value - min) / range) * (height - 16) - 8;
          return <circle cx={x} cy={y} r="3.5" key={`${point.index}-${point.value}`} />;
        })}
      </svg>
      <figcaption>close 기준 {points.length.toLocaleString("ko-KR")}개 exact member</figcaption>
    </figure>
  );
}

function ComparePanel({ comparableSeries }: { comparableSeries: { label: string }[] }) {
  return (
    <section className="panel data-lab-panel" aria-label="dataset A/B 비교">
      <div className="panel-heading">
        <h3><GitCompareArrows size={16} />A/B Compare</h3>
        <span>{comparableSeries.length}쌍</span>
      </div>
      {comparableSeries.map((item) => (
        <p className="data-lab-compare-row" key={item.label}>A/B {item.label}</p>
      ))}
    </section>
  );
}

function StatusBadge({ value }: { value: DatasetBuild["status"] }) {
  return <span className={`data-lab-status status-${value}`}>{value}</span>;
}

function shortHash(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
}

function formatValues(values: DatasetSeriesResponse["items"][number]["values"]) {
  return Object.entries(values).map(([key, value]) => `${key} ${value ?? "-"}`).join(" · ");
}

function utcToKstInput(value: string): string {
  const date = new Date(value);
  const kst = new Date(date.getTime() + 9 * 60 * 60 * 1000);
  return kst.toISOString().slice(0, 16);
}

function marketToDefaultSeries(market: DataFoundationMarket): DatasetVersion["series"][number] {
  return {
    seriesId: 0,
    instrumentId: market.instrumentId,
    dataKind: "candle",
    unit: "1m",
    definitionSetHash: null,
    calculationVersion: "source-candle-v1"
  };
}
