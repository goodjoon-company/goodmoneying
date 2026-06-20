import { Fragment, useState } from "react";
import { Bell } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import {
  loadCollectionCoverageSegments,
  type CollectionDashboardTarget,
  type RealtimeCollectionHeatmapRow,
  type MissingRangeSummary,
  type OperationsSnapshot,
  type OperationsTrendPoint,
  type StorageBreakdownItem
} from "../api";
import {
  emptyStorageBreakdown,
  emptyTrendPoints,
  formatBytes,
  formatCompactCount,
  formatFreshness,
  formatPercent,
  formatShortDateTime,
  formatShortDay,
  normalizeRealtimeCollectionHeatmapRows
} from "../operationsDisplay";
import {
  CoverageBar,
  CoverageMeter,
  InstrumentName,
  MiniMetric,
  StatusDot,
  StatusIcon,
  statusFromTarget
} from "./common";

export function Dashboard({
  snapshot,
  onSelectInstrument
}: {
  snapshot: OperationsSnapshot;
  onSelectInstrument: (instrumentId: number) => void;
}) {
  const totals = snapshot.dashboard.totals;
  return (
    <section className="dashboard-page">
      <div className="ops-kpi-grid">
        <section className="panel ops-summary-card">
          <span className="panel-kicker">수집 현황</span>
          <div className="ops-summary-lines">
            <MetricLine
              label="활성 대상"
              value={`${totals.activeTargets}`}
              suffix={`/${totals.activeTargetLimit}`}
            />
            <MetricLine
              label="주의 / 장애"
              value={`${totals.warningTargets}`}
              suffix={` / ${totals.incidentTargets}`}
              tone={totals.incidentTargets ? "danger" : totals.warningTargets ? "warning" : "default"}
            />
            <MetricLine
              label="실패율"
              value={formatPercent(totals.failureRate24h).replace("%", "")}
              suffix="%"
              tone={Number(totals.failureRate24h) > 0 ? "danger" : "success"}
            />
          </div>
        </section>

        <section className="panel ops-activity-card">
          <div className="ops-card-title">
            <div>
              <span className="panel-kicker">실시간 수집 현황</span>
              <strong>실시간 정보 수집 현황</strong>
              <em>최근 24시간 기준 · 최대 50개 코인</em>
            </div>
            <div className="heatmap-legend" aria-label="실시간 수집 상태 범례">
              <span><i className="none" />예상 미달</span>
              <span><i className="none" />없음</span>
              <span><i className="low" />적음</span>
              <span><i className="high" />많음</span>
            </div>
          </div>
          <RealtimeCollectionHeatmap rows={snapshot.dashboard.realtimeCollectionHeatmap} />
          <p className="panel-note">칸 하나는 1시간 기준 수집 기대치 대비 수집량</p>
        </section>

        <section className="panel ops-storage-card">
          <div className="ops-card-title">
            <div>
              <span className="panel-kicker">오늘 저장 Row Count</span>
              <strong>{formatCompactCount(totals.storageRowsToday)}</strong>
            </div>
            <em>{totals.storageRowsToday.toLocaleString("ko-KR")} rows</em>
          </div>
          <StorageRowsTable items={snapshot.dashboard.storageBreakdown} />
        </section>
      </div>

      <div className="ops-content-grid">
        <section className="panel ops-chart-panel">
          <div className="panel-heading">
            <h2>구간형 수집 진행 상태</h2>
            <span>KST 전일 23:59:59 기준</span>
          </div>
          <OperationsTrendSurface points={snapshot.dashboard.operationsTrend} />
          <div className="chart-legend">
            <span><i className="coverage" />수집 커버리지</span>
            <span><i className="storage" />저장 Row Count</span>
            <span><i className="warn" />주의 / 장애 구간</span>
          </div>
          <div className="ops-mini-card-grid">
            <MiniMetric label="결측 구간" value={totals.missingRangesOpen.toLocaleString("ko-KR")} detail="캔들 결측 기준" />
            <MiniMetric label="백필 대기" value={`${snapshot.backfillJobs.length}건`} detail={`대상 결과 ${totals.recentRequestCount.toLocaleString("ko-KR")}`} />
            <MiniMetric
              label="최근 1분 수집 건수"
              value={`${totals.realtimeRowsLastMinute.toLocaleString("ko-KR")} / ${totals.backfillRowsLastMinute.toLocaleString("ko-KR")}`}
              detail="실시간 / 백필 row"
            />
          </div>
        </section>

        <section className="panel ops-health-panel">
          <div className="panel-heading">
            <h2>운영 헬스</h2>
            <Bell size={18} />
          </div>
          <div className="health-list">
            {snapshot.dashboard.healthChecks.map((check) => (
              <article className="health-item" key={check.title}>
                <StatusIcon status={check.status} />
                <span>{check.title}</span>
                <strong className={check.status}>{check.statusLabel}</strong>
                <em>{check.detail}</em>
              </article>
            ))}
          </div>
        </section>
      </div>

      <section className="panel full">
        <div className="panel-heading">
          <h2>코인별 수집 상태</h2>
          <span>{snapshot.dashboard.targets.length}개</span>
        </div>
        <div className="dashboard-table ops-coin-table">
          <div className="dashboard-table-head ops-coin-table-head">
            <span>코인</span>
            <span>상태</span>
            <span>등락률</span>
            <span>24H 거래대금</span>
            <span>최신성</span>
            <span>수집 커버리지</span>
            <span>저장 행</span>
          </div>
          {snapshot.dashboard.targets.slice(0, 8).map((target) => (
            <CollectionTargetRow
              key={target.instrument.id}
              target={target}
              onSelectInstrument={onSelectInstrument}
            />
          ))}
        </div>
      </section>
    </section>
  );
}

function MetricLine({
  label,
  value,
  suffix,
  tone = "default"
}: {
  label: string;
  value: string;
  suffix?: string;
  tone?: "default" | "success" | "warning" | "danger";
}) {
  return (
    <div className="metric-line">
      <span>{label}</span>
      <strong className={tone}>
        {value}
        {suffix ? <em>{suffix}</em> : null}
      </strong>
    </div>
  );
}

function RealtimeCollectionHeatmap({ rows }: { rows: RealtimeCollectionHeatmapRow[] }) {
  const normalizedRows = normalizeRealtimeCollectionHeatmapRows(rows).slice(0, 50);
  const visibleRows = normalizedRows.length > 0 ? normalizedRows : [];
  const rowGroups = [
    visibleRows.slice(0, 17),
    visibleRows.slice(17, 34),
    visibleRows.slice(34, 50)
  ].filter((group) => group.length > 0);
  return (
    <section
      className="panel activity-panel"
      aria-label="실시간 정보 수집 현황 히트맵"
    >
      <div className="realtime-heatmap-grid">
        {rowGroups.map((group, groupIndex) => (
          <div
            className="realtime-heatmap-block"
            key={`realtime-heatmap-group-${groupIndex}`}
          >
            <div className="realtime-hour-markers" aria-hidden="true">
              {group[0].hourlyBuckets.map((bucket, index) => (
                <span key={`${bucket.bucketStartAt}-${index}`}>
                  {index % 3 === 0 ? formatHeatmapHour(bucket.bucketStartAt) : ""}
                </span>
              ))}
            </div>
            <div className="realtime-cell-grid">
              {group.flatMap((row) =>
                row.hourlyBuckets.map((bucket, index) => {
                  const tooltip = [
                    `${row.instrumentDisplayName} (${row.instrument.marketCode})`,
                    `${formatShortDateTime(bucket.bucketStartAt)} 수집`,
                    `전체 실제 ${bucket.actualRowsAll} / 예상 ${bucket.expectedRowsAll}`,
                    `현재가 ${bucket.actualRowsByType.ticker_snapshot}`,
                    `캔들 ${bucket.actualRowsByType.source_candle}`,
                    `호가 ${bucket.actualRowsByType.orderbook_summary}`
                  ].join(" · ");
                  return (
                    <span
                      aria-label={tooltip}
                      className={`realtime-cell ${bucket.status}`}
                      key={`${row.instrument.id}-${bucket.bucketStartAt}-${index}`}
                      title={tooltip}
                    />
                  );
                })
              )}
            </div>
          </div>
        ))}
      </div>
      {visibleRows.length === 0 ? <p className="panel-note">표시할 수집 대상이 없습니다.</p> : null}
    </section>
  );
}

function formatHeatmapHour(value: string): string {
  const hour = new Date(value).toLocaleTimeString("en-GB", {
    timeZone: "Asia/Seoul",
    hour: "2-digit",
    hour12: false
  });
  return hour;
}

function StorageRowsTable({ items }: { items: StorageBreakdownItem[] }) {
  const visibleItems = items.length > 0 ? items : emptyStorageBreakdown();
  const totalRows = Math.max(1, visibleItems.reduce((sum, item) => sum + item.rowCount, 0));
  return (
    <div className="storage-rows-table">
      <span>구분</span>
      <span>추정</span>
      <span>비중</span>
      <span>Rows</span>
      {visibleItems.map((item) => {
        const sharePercent = item.rowCount > 0
          ? (item.rowCount / totalRows) * 100
          : Number(item.sharePercent);
        return (
          <Fragment key={item.dataType}>
            <strong className={`storage-kind ${item.dataType}`}>{item.label}</strong>
            <em>{item.bytesDisplay}</em>
            <em>{Number.isFinite(sharePercent) ? `${sharePercent.toFixed(0)}%` : "0%"}</em>
            <em>{formatCompactCount(item.rowCount)}</em>
          </Fragment>
        );
      })}
    </div>
  );
}

function OperationsTrendSurface({ points }: { points: OperationsTrendPoint[] }) {
  const series = points.length > 0 ? points : emptyTrendPoints();
  const maxStorage = Math.max(1, ...series.map((point) => point.storageBytes));
  const maxCoverage = Math.max(100, ...series.map((point) => Number(point.coveragePercent)));
  const polyline = series
    .map((point, index) => {
      const x = (index / Math.max(1, series.length - 1)) * 100;
      const y = 82 - Math.min(78, (Number(point.coveragePercent) / maxCoverage) * 72);
      return `${x},${y}`;
    })
    .join(" ");
  const area = `0,88 ${polyline} 100,88`;
  return (
    <div className="ops-trend-surface" aria-label="구간형 수집 진행 상태 차트">
      <svg viewBox="0 0 100 92" preserveAspectRatio="none" role="img" aria-label="수집 커버리지 추이">
        <defs>
          <linearGradient id="opsCoverageFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="rgba(55,210,143,0.34)" />
            <stop offset="100%" stopColor="rgba(55,210,143,0)" />
          </linearGradient>
        </defs>
        <polygon points={area} fill="url(#opsCoverageFill)" />
        <polyline points={polyline} fill="none" stroke="#37d28f" strokeWidth="1.2" vectorEffect="non-scaling-stroke" />
        {series.map((point, index) => {
          const x = (index / Math.max(1, series.length - 1)) * 100;
          const storageHeight = Math.max(4, (point.storageBytes / maxStorage) * 34);
          return (
            <rect
              key={point.bucketDate}
              x={x - 1.1}
              y={88 - storageHeight}
              width="2.2"
              height={storageHeight}
              rx="0.8"
              fill="rgba(91,141,239,0.48)"
            />
          );
        })}
        {series.map((point, index) => {
          if (point.warningTargets === 0 && point.incidentTargets === 0) return null;
          const x = (index / Math.max(1, series.length - 1)) * 100;
          return (
            <circle
              key={`${point.bucketDate}-warning`}
              cx={x}
              cy="15"
              r="1.4"
              fill={point.incidentTargets > 0 ? "#e57a5e" : "#d8b35e"}
            />
          );
        })}
      </svg>
    </div>
  );
}

function StorageBreakdownPanel({ items }: { items: StorageBreakdownItem[] }) {
  const totalRows = Math.max(1, items.reduce((sum, item) => sum + item.rowCount, 0));
  return (
    <section className="panel storage-breakdown-panel">
      <div className="panel-heading">
        <h2>오늘 저장 Row Count</h2>
        <span>행 수 기준</span>
      </div>
      <div className="storage-breakdown-list">
        {items.map((item) => (
          <article className="storage-breakdown-item" key={item.dataType}>
            <div>
              <strong>{item.label}</strong>
              <em>{item.bytesDisplay} 추정</em>
            </div>
            <span>{item.rowCount.toLocaleString("ko-KR")}행</span>
            <div className="storage-share" aria-label={`${item.label} 저장량 비중`}>
              <span style={{ width: `${Math.max(0, Math.min(100, (item.rowCount / totalRows) * 100))}%` }} />
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function OperationsTrendPanel({ points }: { points: OperationsTrendPoint[] }) {
  const maxStorageBytes = Math.max(1, ...points.map((point) => point.storageBytes));
  return (
    <section className="panel trend-panel">
      <div className="panel-heading">
        <h2>운영 추이</h2>
        <span>최근 7일</span>
      </div>
      <div className="trend-bars" aria-label="운영 추이 차트">
        {points.map((point) => (
          <article className="trend-point" key={point.bucketDate}>
            <span
              style={{
                height: `${Math.max(8, (point.storageBytes / maxStorageBytes) * 100)}%`
              }}
            />
            <strong>{formatShortDay(point.bucketDate)}</strong>
            <em>{formatBytes(point.storageBytes)}</em>
          </article>
        ))}
      </div>
    </section>
  );
}

function MissingRangePanel({ items }: { items: MissingRangeSummary[] }) {
  return (
    <section className="panel missing-range-panel">
      <div className="panel-heading">
        <h2>결측 상위 코인</h2>
        <span>{items.length}개</span>
      </div>
      <div className="missing-range-list">
        {items.map((item) => (
          <article className="missing-range-item" key={item.instrument.id}>
            <InstrumentName instrument={item.instrument} />
            <strong>결측 {item.missingSegmentCount}구간</strong>
            <em>커버리지 {Number(item.coveragePercent).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%</em>
          </article>
        ))}
      </div>
    </section>
  );
}

function CollectionTargetRow({
  target,
  onSelectInstrument
}: {
  target: CollectionDashboardTarget;
  onSelectInstrument: (instrumentId: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const lazySegmentsQuery = useQuery({
    queryKey: ["collection-coverage-segments", target.instrument.id],
    queryFn: () => loadCollectionCoverageSegments(target.instrument.id),
    enabled:
      expanded &&
      target.coverageSegments.length === 0 &&
      import.meta.env.MODE !== "test"
  });
  const coverageSegments =
    lazySegmentsQuery.data && lazySegmentsQuery.data.length > 0
      ? lazySegmentsQuery.data
      : target.coverageSegments;
  const candleSegments = coverageSegments.filter(
    (segment) => segment.dataType === "source_candle"
  );
  const candleStatus = target.dataStatuses.find((status) => status.dataType === "source_candle");
  const orderbookStatus = target.dataStatuses.find(
    (status) => status.dataType === "orderbook_summary"
  );
  const rowStatus = statusFromTarget(target);
  return (
    <article className={`accordion-row ${expanded ? "expanded" : ""}`}>
      <button
        className="dashboard-row-button ops-coin-row-button"
        type="button"
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="coin-cell">
          <StatusDot status={rowStatus} />
          <InstrumentName instrument={target.instrument} />
        </span>
        <span className={`quality ${rowStatus}`}>{target.overallStatusLabel}</span>
        <span className={Number(target.changeRate) >= 0 ? "change up" : "change down"}>
          {formatPercent(target.changeRate)}
        </span>
        <span className="mono-value">{target.accTradePrice24hDisplay}</span>
        <span className="mono-value freshness">
          {formatFreshness(orderbookStatus?.lastSuccessfulAt ?? target.plan.rangeStartAt)}
        </span>
        <span className="coverage-cell">
          <CoverageMeter value={target.coveragePercent} />
          <em>캔들 {candleStatus?.statusLabel ?? "미확인"}</em>
        </span>
        <span className="mono-value align-right">
          {target.storageRowCount.toLocaleString("ko-KR")}
        </span>
      </button>
      {expanded ? (
        <div className="accordion-detail">
          <div className="ops-row-detail-card">
            <div className="ops-row-detail-heading">
              <h3>코인별 수집 계획 <span>— {target.instrument.baseAsset}</span></h3>
              <button type="button" onClick={() => onSelectInstrument(target.instrument.id)}>
                상세 레이어 열기 →
              </button>
            </div>
            <div className="ops-plan-grid">
              <div>
                <span>프리셋</span>
                <strong>{target.plan.preset}</strong>
              </div>
              <div>
                <span>수집 시작 KST</span>
                <strong>{formatShortDateTime(target.plan.rangeStartAt)}</strong>
              </div>
              <div>
                <span>종료</span>
                <strong>{target.plan.isContinuous ? "현재 (지속)" : formatShortDateTime(target.plan.rangeEndAt ?? target.plan.rangeStartAt)}</strong>
              </div>
              <div>
                <span>수집 방식</span>
                <strong>{target.plan.method}</strong>
              </div>
              <div>
                <span>상태</span>
                <em className={`quality ${rowStatus}`}>{target.overallStatusLabel}</em>
              </div>
            </div>
            <div className="ops-segment-label">
              구간형 진행 상태 <span>· 녹색=데이터 있음 / 적색·황색=결측</span>
              <button type="button" onClick={() => setEditing((current) => !current)}>
                수정
              </button>
            </div>
            <CoverageBar segments={candleSegments} />
            {lazySegmentsQuery.isFetching ? (
              <span className="helper-text">구간 데이터를 불러오는 중입니다.</span>
            ) : null}
            {editing ? <PlanEditor target={target} /> : null}
          </div>
        </div>
      ) : null}
    </article>
  );
}

function PlanEditor({ target }: { target: CollectionDashboardTarget }) {
  return (
    <form className="plan-editor">
      <label>
        <span>프리셋</span>
        <select defaultValue={target.plan.preset}>
          <option>2026년 1월 1분봉</option>
          <option>현재가/호가 최신 수집</option>
        </select>
      </label>
      <label>
        <span>시작</span>
        <input defaultValue="2026-01-01 00:00" />
      </label>
      <label>
        <span>종료</span>
        <select defaultValue="continuous">
          <option value="continuous">현재(지속)</option>
          <option value="fixed">종료 일시 지정</option>
        </select>
      </label>
      <button type="button">저장</button>
    </form>
  );
}
