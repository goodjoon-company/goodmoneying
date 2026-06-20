import {
  Activity,
  Bell,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  LineChart,
  ListChecks,
  RefreshCcw,
  Search,
  Settings2,
  X
} from "lucide-react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  type UTCTimestamp
} from "lightweight-charts";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient
} from "@tanstack/react-query";
import {
  approveBackfillJob,
  createBackfillPlan,
  loadCollectionCoverageSegments,
  loadCandidateUniverse,
  loadMarketList,
  updateCollectionTargets,
  type Candle,
  type CandidateUniverseEntry,
  type CollectionActivityBucket,
  type CollectionDashboardTarget,
  type CoverageSegment,
  type Instrument,
  type InstrumentDetail,
  type MarketListRow,
  type MissingRangeSummary,
  type OperationsTrendPoint,
  type OperationsSnapshot,
  type StorageBreakdownItem,
  type Status
} from "./api";
import { createFixtureOperationsDataClient } from "./operationsFixture";
import {
  addDraftBackfillPlan,
  canApproveBackfillPlans,
  canCreateBackfillPlan,
  canSaveTargets,
  filterAndSortCandidateEntries,
  initialSelectedInstrumentIds,
  removeDraftBackfillPlan,
  sumDraftBackfillPlans,
  toggleSelectedInstrument,
  type BackfillDraftPlan,
  type SortMode
} from "./targetBackfillWorkflow";
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
  formatShortDateTime,
  formatShortDay,
  heatmapCells
} from "./operationsDisplay";
import { useOperationsConsole, type SectionId } from "./useOperationsConsole";

const EMPTY_CANDIDATE_ENTRIES: CandidateUniverseEntry[] = [];
const EMPTY_MARKET_ROWS: MarketListRow[] = [];
const DEFAULT_BACKFILL_START_INPUT = "2026-01-01T00:00";
const DEFAULT_BACKFILL_END_INPUT = "2026-02-01T00:00";

const menuGroups: {
  title: string;
  items: { id?: SectionId; label: string; badge: string; enabled: boolean }[];
}[] = [
  {
    title: "데이터 수집관리",
    items: [
      { id: "dashboard", label: "운영 상태", badge: "MVP", enabled: true },
      { id: "targets", label: "수집 대상/설정", badge: "MVP", enabled: true },
      { id: "markets", label: "시장 리스트", badge: "MVP", enabled: true },
      { id: "scalability", label: "확장성 점검", badge: "M3.5", enabled: true }
    ]
  },
  {
    title: "종목 발굴",
    items: [
      { label: "국내 주식 리스트", badge: "후속", enabled: false },
      { label: "미국 주식 리스트", badge: "후속", enabled: false },
      { label: "통합 시장 스캐닝", badge: "후속", enabled: false },
      { label: "신호/이벤트 타임라인", badge: "후속", enabled: false }
    ]
  },
  {
    title: "매매 전략 · 봇 관리",
    items: [
      { label: "전략 작업대", badge: "후속", enabled: false },
      { label: "봇 설계 / 시뮬레이션", badge: "후속", enabled: false },
      { label: "모의매매 준비", badge: "후속", enabled: false }
    ]
  }
];

const sectionMeta: Record<SectionId, { crumb: string; milestone: string; title: string; desc: string }> = {
  dashboard: {
    crumb: "goodmoneying / 운영 상태 / M1",
    milestone: "M1 · 운영 관제형",
    title: "업비트 수집 운영 상태",
    desc: "수집 대상 최대 50개 코인의 최신성, 지연, 결측, 실패, 저장 행을 한 화면에서 확인하는 고밀도 운영 콘솔"
  },
  targets: {
    crumb: "goodmoneying / 수집 대상/설정 / M2",
    milestone: "M2 · 운영 관제형",
    title: "수집 대상과 백필 설정",
    desc: "상위 100개 후보 중 활성 수집 대상 최대 50개를 조정하고 백필 계획을 승인합니다."
  },
  markets: {
    crumb: "goodmoneying / 시장 리스트 / M2",
    milestone: "M2 · 운영 관제형",
    title: "시장 리스트",
    desc: "수집 대상 코인의 현재가, 거래대금, 등락률, 최신성, 커버리지와 저장 행을 비교합니다."
  },
  scalability: {
    crumb: "goodmoneying / 확장성 점검 / M3.5",
    milestone: "M3.5 · 의사결정 게이트",
    title: "확장성 점검",
    desc: "국내 주식 확장 전 다중 워커, 메시지 큐, 보존 정책, 알림 발송 결정을 확인합니다."
  }
};

export function App() {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <OperationsApp />
    </QueryClientProvider>
  );
}

function OperationsApp() {
  const dataClient = useMemo(
    () => (import.meta.env.MODE === "test" ? createFixtureOperationsDataClient() : undefined),
    []
  );
  const {
    snapshot,
    activeSection,
    setActiveSection,
    selectedInstrumentId,
    isDetailOpen,
    setDetailOpen,
    openInstrumentDetail,
    query
  } = useOperationsConsole({ dataClient });

  if (query.error) {
    return <main className="app-shell error-state">운영 API를 불러오지 못했습니다.</main>;
  }

  if (!snapshot) {
    return <main className="app-shell loading-state">운영 상태를 불러오는 중</main>;
  }

  const meta = sectionMeta[activeSection];

  return (
    <main className="app-shell app-layout" data-theme="dark">
      <aside className="sidebar" aria-label="제품 메뉴">
        <div className="brand-block">
          <div className="brand-mark">g</div>
          <div>
            <strong>goodmoneying</strong>
            <span>운영 관제형 콘솔</span>
          </div>
        </div>
        <nav className="product-nav">
          {menuGroups.map((group) => (
            <section key={group.title}>
              <h2>{group.title}</h2>
              {group.items.map((item) => (
                <button
                  key={`${group.title}-${item.label}`}
                  className={item.id === activeSection ? "active" : ""}
                  type="button"
                  aria-label={item.label.replace("/", " ")}
                  disabled={!item.enabled}
                  onClick={() => item.id && setActiveSection(item.id)}
                >
                  <span>{item.label}</span>
                  <em>{item.badge}</em>
                </button>
              ))}
            </section>
          ))}
        </nav>
      </aside>

      <section className="workspace">
        <header className="workspace-header">
          <div className="breadcrumb">{meta.crumb}</div>
          <div className="header-actions">
            <button type="button" aria-label="새로고침" onClick={() => query.refetch()}>
              <RefreshCcw size={16} />
              새로고침
            </button>
          </div>
        </header>

        <section className="hero-row">
          <div>
            <p className="eyebrow">{meta.milestone}</p>
            <h1>{meta.title}</h1>
            <p className="page-desc">{meta.desc}</p>
          </div>
          <div className="runtime-pills" aria-label="화면 갱신 기준">
            <span>표시 KST</span>
            <span>저장 UTC</span>
            <span>폴링 15초</span>
            <span>마지막 갱신 {formatFreshness(snapshot.dashboard.refreshedAt)}</span>
          </div>
        </section>

        {activeSection === "dashboard" ? (
          <Dashboard snapshot={snapshot} onSelectInstrument={openInstrumentDetail} />
        ) : null}
        {activeSection === "targets" ? <Targets snapshot={snapshot} /> : null}
        {activeSection === "markets" ? (
          <Markets
            snapshot={snapshot}
            selectedInstrumentId={selectedInstrumentId}
            onSelectInstrument={openInstrumentDetail}
          />
        ) : null}
        {activeSection === "scalability" ? <ScalabilityReadiness /> : null}
      </section>

      {isDetailOpen ? <DetailModal snapshot={snapshot} onClose={() => setDetailOpen(false)} /> : null}
    </main>
  );
}

function Dashboard({
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
              <span className="panel-kicker">정상 수집</span>
              <strong>{totals.normalTargets}</strong>
              <em>활성 {totals.activeTargets} × 최근 7일</em>
            </div>
            <div className="heatmap-legend" aria-label="수집 활동 범례">
              <span><i className="none" />없음</span>
              <span><i className="low" />적음</span>
              <span><i className="high" />많음</span>
            </div>
          </div>
          <ActivityHeatmap buckets={snapshot.dashboard.collectionActivity} compact />
          <p className="panel-note">최근 1분 수집 행 수 · 칸 하나가 1시간</p>
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

function ActivityHeatmap({
  buckets,
  compact = false
}: {
  buckets: CollectionActivityBucket[];
  compact?: boolean;
}) {
  const cells = heatmapCells(buckets);
  return (
    <section className={compact ? "activity-panel compact" : "panel activity-panel"}>
      {!compact ? (
        <div className="panel-heading">
          <h2>시간대별 수집 활동</h2>
          <span>최근 7일 x 24시간</span>
        </div>
      ) : null}
      <div className="activity-hour-ticks" aria-hidden="true">
        {Array.from({ length: 24 }, (_, hour) => (
          <span key={hour}>{hour % 6 === 0 ? hour.toString().padStart(2, "0") : ""}</span>
        ))}
      </div>
      <div className="activity-heatmap" aria-label="시간대별 수집 활동 히트맵">
        {cells.map((bucket, index) => (
          <span
            className={`activity-cell ${bucket.status}`}
            key={`${bucket.bucketStartAt}-${index}`}
            title={`${formatFreshness(bucket.bucketStartAt)} · 실행 ${bucket.runCount} · 결과 ${bucket.resultCount}`}
          />
        ))}
      </div>
    </section>
  );
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

function Targets({ snapshot }: { snapshot: OperationsSnapshot }) {
  const queryClient = useQueryClient();
  const [isBackfillDialogOpen, setBackfillDialogOpen] = useState(false);
  const [pendingPlans, setPendingPlans] = useState<BackfillDraftPlan[]>([]);
  const [approvedJobs, setApprovedJobs] = useState<number[]>([]);
  const [searchText, setSearchText] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("trade");
  const universeQuery = useQuery({
    queryKey: ["candidate-universe"],
    queryFn: loadCandidateUniverse,
    enabled: snapshot.source === "api"
  });
  const entries =
    snapshot.source === "api"
      ? universeQuery.data ?? EMPTY_CANDIDATE_ENTRIES
      : snapshot.candidateEntries;
  const visibleEntries = useMemo(
    () => filterAndSortCandidateEntries(entries, searchText, sortMode),
    [entries, searchText, sortMode]
  );
  const [selectedIds, setSelectedIds] = useState<Set<number>>(
    () => initialSelectedInstrumentIds(entries)
  );
  useEffect(() => {
    setSelectedIds(initialSelectedInstrumentIds(entries));
  }, [entries]);
  const mutation = useMutation({
    mutationFn: (ids: number[]) => updateCollectionTargets(ids),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["operations"] });
      void queryClient.invalidateQueries({ queryKey: ["candidate-universe"] });
    }
  });
  const createPlanMutation = useMutation({
    mutationFn: (options: { targetStartAt: string; targetEndAt: string }) =>
      createBackfillPlan(Array.from(selectedIds), options),
    onSuccess: (plan, variables) => {
      setPendingPlans((current) => addDraftBackfillPlan(current, plan, variables));
      setBackfillDialogOpen(false);
    }
  });
  const approvePlansMutation = useMutation({
    mutationFn: async (plans: BackfillDraftPlan[]) => {
      const jobs = [];
      for (const plan of plans) {
        jobs.push(await approveBackfillJob(plan.planId));
      }
      return jobs;
    },
    onSuccess: (jobs) => {
      setApprovedJobs(jobs.map((job) => job.id));
      setPendingPlans([]);
      void queryClient.invalidateQueries({ queryKey: ["operations"] });
    }
  });
  const selected = selectedIds.size;
  const canSave = canSaveTargets(selected, mutation.isPending);
  const canCreatePlan =
    canCreateBackfillPlan(selected, createPlanMutation.isPending);
  const canApprovePlans =
    canApproveBackfillPlans(pendingPlans.length, approvePlansMutation.isPending);
  const toggle = (instrumentId: number) => {
    setSelectedIds((previous) => toggleSelectedInstrument(previous, instrumentId));
  };
  return (
    <section className="split-page">
      <section className="panel">
        <div className="panel-heading">
          <h2>후보 유니버스 상위 100개</h2>
          <span>선택 {selected}/50</span>
        </div>
        <div className="target-toolbar">
          <label>
            <Search size={16} />
            <input
              placeholder="코인명 또는 심볼 검색"
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
            />
          </label>
          <select
            aria-label="후보 정렬"
            value={sortMode}
            onChange={(event) => setSortMode(event.target.value as SortMode)}
          >
            <option value="trade">거래대금순</option>
            <option value="quality">품질순</option>
          </select>
          <button
            type="button"
            disabled={!canCreatePlan}
            onClick={() => setBackfillDialogOpen(true)}
          >
            <ListChecks size={16} />
            백필 계획 생성
          </button>
          <button type="button" disabled={!canSave} onClick={() => mutation.mutate(Array.from(selectedIds))}>
            <CheckCircle2 size={16} />
            저장
          </button>
        </div>
        {mutation.isError ? <p className="error-text">수집 대상 저장에 실패했습니다.</p> : null}
        {createPlanMutation.isError ? <p className="error-text">백필 계획 생성에 실패했습니다.</p> : null}
        <div className="target-table">
          <div className="target-table-head">
            <span>활성</span>
            <span>후보</span>
            <span>거래대금</span>
            <span>품질</span>
            <span>수집 범위</span>
          </div>
          {entries.length === 0 ? <p className="helper-text">후보 유니버스를 불러오는 중입니다.</p> : null}
          {entries.length > 0 && visibleEntries.length === 0 ? (
            <p className="helper-text">검색 조건에 맞는 후보가 없습니다.</p>
          ) : null}
          {visibleEntries.slice(0, 100).map((entry) => (
            <label className="target-row" key={entry.instrument.id}>
              <span>
                <input
                  type="checkbox"
                  checked={selectedIds.has(entry.instrument.id)}
                  onChange={() => toggle(entry.instrument.id)}
                />
                수집
              </span>
              <InstrumentName instrument={entry.instrument} />
              <strong>{entry.accTradePrice24hDisplay}</strong>
              <em className={`quality ${entry.qualityStatus}`} title={entry.qualityDetail}>
                {statusText(entry.qualityStatus)}
              </em>
              <span>{entry.collectionRangeDisplay}</span>
            </label>
          ))}
        </div>
      </section>
      <section className="panel side-panel">
        <div className="panel-heading">
          <h2>백필 승인 패널</h2>
          <Settings2 size={18} />
        </div>
        <MiniMetric
          label="예상 요청 수"
          value={sumDraftBackfillPlans(pendingPlans, "estimatedRequestCount").toLocaleString("ko-KR")}
          detail="1분 캔들 기준"
        />
        <MiniMetric
          label="예상 저장량"
          value={formatBytes(sumDraftBackfillPlans(pendingPlans, "estimatedStorageBytes"))}
          detail="중복 기간 요청 제외"
        />
        <MiniMetric
          label="감사 로그"
          value={`대상 변경 ${snapshot.dashboard.auditLogSummary.targetChangeCount24h}건`}
          detail={`${snapshot.dashboard.auditLogSummary.latestChangeLabel} · 최근 24시간`}
        />
        <div className="backfill-plan-list" aria-label="백필 계획 목록">
          {pendingPlans.length === 0 ? (
            <p className="helper-text">선택 코인으로 백필 계획을 생성하면 승인 대기 목록에 표시됩니다.</p>
          ) : null}
          {pendingPlans.map((plan) => (
            <article className="backfill-plan-card" key={plan.planId}>
              <div>
                <strong>계획 {plan.planId}</strong>
                <button
                  className="icon-button small"
                  type="button"
                  aria-label={`계획 ${plan.planId} 삭제`}
                  onClick={() =>
                    setPendingPlans((current) => removeDraftBackfillPlan(current, plan.planId))
                  }
                >
                  <X size={14} />
                </button>
              </div>
              <span>대상 {plan.targets.length}개</span>
              <em>
                {formatDateTimeRange(plan.targetStartAt, plan.targetEndAt)}
              </em>
              <span>
                요청 {plan.estimatedRequestCount.toLocaleString("ko-KR")} · 행{" "}
                {plan.estimatedRowCount.toLocaleString("ko-KR")}
              </span>
            </article>
          ))}
        </div>
        {approvedJobs.length > 0 ? (
          <p className="success-text">승인된 작업 {approvedJobs.join(", ")}</p>
        ) : null}
        {approvePlansMutation.isError ? <p className="error-text">백필 계획 승인에 실패했습니다.</p> : null}
        <button
          className="approve-backfill-button"
          type="button"
          disabled={!canApprovePlans}
          onClick={() => approvePlansMutation.mutate(pendingPlans)}
        >
          백필 계획 승인
        </button>
      </section>
      {isBackfillDialogOpen ? (
        <BackfillPlanDialog
          selectedCount={selected}
          isPending={createPlanMutation.isPending}
          onClose={() => setBackfillDialogOpen(false)}
          onConfirm={(range) => createPlanMutation.mutate(range)}
        />
      ) : null}
    </section>
  );
}

function BackfillPlanDialog({
  selectedCount,
  isPending,
  onClose,
  onConfirm
}: {
  selectedCount: number;
  isPending: boolean;
  onClose: () => void;
  onConfirm: (range: { targetStartAt: string; targetEndAt: string }) => void;
}) {
  const [start, setStart] = useState(DEFAULT_BACKFILL_START_INPUT);
  const [end, setEnd] = useState(DEFAULT_BACKFILL_END_INPUT);
  const canSubmit = selectedCount > 0 && start.length > 0 && end.length > 0 && start < end;
  return (
    <div className="modal-backdrop">
      <section className="backfill-dialog" role="dialog" aria-label="백필 계획 생성" aria-modal="true">
        <button className="icon-button close-button" type="button" aria-label="닫기" onClick={onClose}>
          <X size={18} />
        </button>
        <div className="panel-heading">
          <h2>백필 계획 생성</h2>
          <span>선택 코인 {selectedCount}개</span>
        </div>
        <div className="backfill-form-grid">
          <label>
            <span>수집 데이터</span>
            <select defaultValue="source_candle">
              <option value="source_candle">1분 캔들(Source Candle)</option>
            </select>
          </label>
          <label>
            <span>백필 방식</span>
            <select defaultValue="safe_restart">
              <option value="safe_restart">안전 재시작(Safe Restart)</option>
            </select>
          </label>
          <label>
            <span>수집 범위 시작 · UTC</span>
            <input
              aria-label="수집 범위 시작"
              type="datetime-local"
              value={start}
              onChange={(event) => setStart(event.currentTarget.value)}
            />
          </label>
          <label>
            <span>수집 범위 종료 · UTC</span>
            <input
              aria-label="수집 범위 종료"
              type="datetime-local"
              value={end}
              onChange={(event) => setEnd(event.currentTarget.value)}
            />
          </label>
        </div>
        <p className="helper-text">
          승인 후 워커가 이미 저장된 시작 구간은 건너뛰고 첫 빈 구간부터 지속 백필합니다.
        </p>
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>취소</button>
          <button
            className="primary-action"
            type="button"
            disabled={!canSubmit || isPending}
            onClick={() =>
              onConfirm({
                targetStartAt: dateTimeLocalToUtcIso(start),
                targetEndAt: dateTimeLocalToUtcIso(end)
              })
            }
          >
            확인
          </button>
        </div>
      </section>
    </div>
  );
}

function Markets({
  snapshot,
  selectedInstrumentId,
  onSelectInstrument
}: {
  snapshot: OperationsSnapshot;
  selectedInstrumentId: number | null;
  onSelectInstrument: (instrumentId: number) => void;
}) {
  const marketQuery = useQuery({
    queryKey: ["market-list"],
    queryFn: loadMarketList,
    enabled: snapshot.source === "api"
  });
  const rows: MarketListRow[] =
    snapshot.source === "api" ? marketQuery.data ?? EMPTY_MARKET_ROWS : snapshot.marketRows;
  return (
    <section className="panel full">
      <div className="panel-heading">
        <h2>수집 데이터 요약</h2>
        <span>{rows.length}개</span>
      </div>
      <div className="data-table">
        <div className="table-header">
          <span>거래 상품</span>
          <span>현재가</span>
          <span>24시간 거래대금</span>
          <span>등락률</span>
          <span>최신성</span>
          <span>커버리지</span>
          <span>저장 행</span>
          <span>품질</span>
        </div>
        {rows.length === 0 ? <p className="helper-text">시장 리스트를 불러오는 중입니다.</p> : null}
        {rows.map((row) => (
          <button
            className={`table-row market-row-button ${
              selectedInstrumentId === row.instrument.id ? "selected" : ""
            }`}
            key={row.instrument.id}
            type="button"
            onClick={() => onSelectInstrument(row.instrument.id)}
          >
            <InstrumentName instrument={row.instrument} />
            <span>{formatNumber(row.tradePrice)}</span>
            <span>{row.accTradePrice24hDisplay}</span>
            <span className={Number(row.changeRate) >= 0 ? "change up" : "change down"}>
              {formatPercent(row.changeRate)}
            </span>
            <TimeInline value={formatFreshness(row.tickerCollectedAt)} zone="KST" />
            <CoverageMeter value={row.coveragePercent} />
            <span>{row.storageRowCount.toLocaleString("ko-KR")}</span>
            <span className={`quality ${row.qualityStatus}`}>{statusText(row.qualityStatus)}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

const readinessItems = [
  {
    title: "수평 확장",
    body: "단일 워커에서 다중 워커로 전환하기 전 작업 분배와 중복 실행 방지 정책을 결정한다.",
    status: "준비 중 · M3.5"
  },
  {
    title: "메시지 큐",
    body: "백필과 증분 수집을 분리할 큐 경계, 재시도, dead-letter 정책을 확정한다.",
    status: "미정 · M3.5"
  },
  {
    title: "보존 정책",
    body: "호가 원천 스냅샷 보존 기간, 파티셔닝, 압축, 다운샘플링 기준을 결정한다.",
    status: "준비 중 · M3.5"
  },
  {
    title: "알림 발송",
    body: "외부 채널 연결 전 알림 심각도, 중복 억제, 감사 로그 연결 기준을 확정한다.",
    status: "미정 · M3.5"
  }
];

function ScalabilityReadiness() {
  return (
    <section className="readiness-page">
      <section className="panel full">
        <div className="panel-heading">
          <h2>확장성 점검</h2>
          <span>구현 모니터링 아님</span>
        </div>
        <div className="readiness-grid">
          {readinessItems.map((item) => (
            <article className="readiness-card" key={item.title}>
              <strong>{item.title}</strong>
              <p>{item.body}</p>
              <em>{item.status}</em>
            </article>
          ))}
        </div>
        <p className="readiness-note">
          국내 주식(M4) 확장 전 게이트입니다. 다중 워커, 운영 서버 다중 인스턴스,
          메시지 큐, 분산 rate limit, PostgreSQL 보존/파티셔닝/복제/장애조치 전략이
          결정되어야 다음 마일스톤으로 진행합니다.
        </p>
      </section>
    </section>
  );
}

function DetailModal({
  snapshot,
  onClose
}: {
  snapshot: OperationsSnapshot;
  onClose: () => void;
}) {
  if (!snapshot.detail) {
    return (
      <div className="modal-backdrop">
        <section className="detail-modal" role="dialog" aria-label="코인 상세" aria-modal="true">
          <button className="icon-button close-button" type="button" aria-label="닫기" onClick={onClose}>
            <X size={18} />
          </button>
          <main className="loading-state">코인 상세를 불러오는 중</main>
        </section>
      </div>
    );
  }
  return (
    <div className="modal-backdrop">
      <section className="detail-modal" role="dialog" aria-label="코인 상세" aria-modal="true">
        <button className="icon-button close-button" type="button" aria-label="닫기" onClick={onClose}>
          <X size={18} />
        </button>
        <Detail detail={snapshot.detail} candles={snapshot.candles} />
      </section>
    </div>
  );
}

function Detail({ detail, candles: rawCandles }: { detail: InstrumentDetail; candles: Candle[] }) {
  const candles = useMemo(() => sampleCandles(rawCandles, 180), [rawCandles]);
  const instrument = detail.instrument;
  const sourceCoverage = detail.coverage.find((item) => item.dataType === "source_candle");
  return (
    <section className="detail-page">
      <h2 className="detail-title"><InstrumentTitle instrument={instrument} /></h2>
      <section className="panel chart-panel">
        <div className="panel-heading">
          <h2><InstrumentTitle instrument={instrument} /> 캔들·거래대금</h2>
          <span>2026년 1월 1분봉</span>
        </div>
        <TradingViewCandleChart
          candles={candles}
          instrument={instrument}
          currentPrice={detail.latestTicker.tradePrice}
        />
        <div className="detail-stats">
          <MiniMetric label="현재가" value={`₩${formatNumber(detail.latestTicker.tradePrice)}`} detail={detail.tickerFreshnessLabel} />
          <MiniMetric
            label="24H 변동금액"
            value={`₩${formatNumber(detail.priceChangeAmount24h)}`}
            detail={formatPercent(detail.priceChangeRate24h)}
          />
          <MiniMetric
            label="24H 거래량"
            value={formatNumber(detail.tradeVolume24h)}
            detail={formatPercent(detail.tradeVolumeChangeRate24h)}
          />
          <MiniMetric
            label="캔들 커버리지"
            value={`${formatNumber(sourceCoverage?.progressPercent ?? "0")}%`}
            detail={sourceCoverage?.status ?? "unknown"}
          />
        </div>
      </section>
      <section className="panel orderbook-panel">
        <div className="panel-heading">
          <h2>호가 요약</h2>
          <TimeInline value={detail.orderbookFreshnessLabel} zone="KST" />
        </div>
        <div className="orderbook-grid">
          <MiniMetric label="최우선 매수" value={formatNumber(detail.latestOrderbook.bestBidPrice)} detail={`수량 ${detail.latestOrderbook.bestBidSize} ${instrument.baseAsset}`} />
          <MiniMetric label="최우선 매도" value={formatNumber(detail.latestOrderbook.bestAskPrice)} detail={`수량 ${detail.latestOrderbook.bestAskSize} ${instrument.baseAsset}`} />
          <MiniMetric label="스프레드" value={`${detail.latestOrderbook.spread}`} detail="정상 범위" />
          <MiniMetric label="호가 불균형" value={formatPercent(detail.latestOrderbook.imbalance10)} detail="매수 잔량 우세" />
        </div>
      </section>
      <section className="panel quality-history-panel">
        <div className="panel-heading">
          <h2>수집 품질 이력</h2>
          <span>{detail.qualityHistory.length}개</span>
        </div>
        <div className="quality-history-list">
          {detail.qualityHistory.map((event) => (
            <article className="quality-history-item" key={`${event.title}-${event.occurredAt}`}>
              <span className={`quality ${event.status}`}>{statusText(event.status)}</span>
              <div>
                <strong>{event.title}</strong>
                <em>{formatFreshness(event.occurredAt)}</em>
              </div>
              <p>{event.detail}</p>
            </article>
          ))}
        </div>
      </section>
    </section>
  );
}

function TradingViewCandleChart({
  candles,
  instrument,
  currentPrice
}: {
  candles: Candle[];
  instrument: Instrument;
  currentPrice: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!containerRef.current || candles.length === 0 || typeof ResizeObserver === "undefined") {
      return;
    }
    const container = containerRef.current;
    const chart = createChart(container, {
      width: container.clientWidth || 900,
      height: 328,
      layout: {
        background: { type: ColorType.Solid, color: "#0c1010" },
        textColor: "#9ca7a0"
      },
      grid: {
        vertLines: { color: "rgba(148, 163, 184, 0.12)" },
        horzLines: { color: "rgba(148, 163, 184, 0.12)" }
      },
      rightPriceScale: { borderColor: "rgba(148, 163, 184, 0.2)" },
      timeScale: { borderColor: "rgba(148, 163, 184, 0.2)", timeVisible: true }
    });
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#22c7a5",
      downColor: "#ff4d5a",
      borderVisible: false,
      wickUpColor: "#22c7a5",
      wickDownColor: "#ff4d5a"
    });
    candleSeries.setData(
      candles.map((item) => ({
        time: Math.floor(new Date(item.startedAt).getTime() / 1000) as UTCTimestamp,
        open: Number(item.open),
        high: Number(item.high),
        low: Number(item.low),
        close: Number(item.close)
      }))
    );
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume"
    });
    volumeSeries.setData(
      candles.map((item) => ({
        time: Math.floor(new Date(item.startedAt).getTime() / 1000) as UTCTimestamp,
        value: Number(item.volume),
        color: Number(item.close) >= Number(item.open) ? "rgba(34, 199, 165, 0.42)" : "rgba(255, 77, 90, 0.42)"
      }))
    );
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
    chart.timeScale().fitContent();
    const observer = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
    });
    observer.observe(container);
    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [candles]);

  return (
    <div className="trading-chart-shell" aria-label="TradingView 캔들 차트">
      <div className="chart-titlebar">
        <span>{instrument.baseAsset} / {instrument.quoteCurrency} · 1분 · UpBit</span>
        <strong>{formatNumber(currentPrice)}</strong>
      </div>
      <div className="chart-canvas" ref={containerRef}>
        {candles.length === 0 ? <span>선택한 기간에 저장된 캔들이 없습니다.</span> : null}
      </div>
      <div className="price-gauge">
        <span>현재가 게이지</span>
        <strong>{formatNumber(currentPrice)}</strong>
      </div>
      <div className="volume-gauge">
        <span>거래량 게이지</span>
        <strong>{candles.length > 0 ? formatNumber(candles.at(-1)?.volume ?? "0") : "0"}</strong>
      </div>
      <div className="trading-watermark">TradingView Lightweight Charts</div>
    </div>
  );
}

function CoverageBar({ segments }: { segments: CoverageSegment[] }) {
  return (
    <div className="coverage-bar" aria-label="구간형 진행 상태">
      {segments.map((segment, index) => (
        <span
          className={`coverage-segment ${segment.status}`}
          key={`${segment.dataType}-${segment.status}-${index}`}
          title={segment.label}
          style={{ left: `${segment.offsetPercent}%`, width: `${segment.widthPercent}%` }}
        />
      ))}
    </div>
  );
}

function CoverageMeter({ value }: { value: string }) {
  const numeric = Math.max(0, Math.min(100, Number(value)));
  return (
    <span className="coverage-meter">
      <span style={{ width: `${numeric}%` }} />
      <em>{numeric.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%</em>
    </span>
  );
}

function CandleCountMeter({
  count,
  progress,
  segments
}: {
  count: number;
  progress: string;
  segments: CoverageSegment[];
}) {
  return (
    <span className="candle-count-meter" aria-label="저장된 가격 분봉 수">
      <CoverageBar segments={segments} />
      <strong>{count.toLocaleString("ko-KR")}</strong>
      <em>{Number(progress).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%</em>
    </span>
  );
}

function StatusIcon({ status }: { status: Status }) {
  if (status === "normal") {
    return <CheckCircle2 className="health-icon normal" size={18} aria-hidden="true" />;
  }
  if (status === "warning") {
    return <CircleAlert className="health-icon warning" size={18} aria-hidden="true" />;
  }
  return <CircleAlert className="health-icon incident" size={18} aria-hidden="true" />;
}

function StatusDot({ status }: { status: Status }) {
  return <span className={`status-dot ${status}`} aria-hidden="true" />;
}

function InstrumentName({ instrument }: { instrument: Instrument }) {
  return (
    <span className="instrument-name">
      <strong>{instrument.baseAsset} / {instrument.quoteCurrency}</strong>
      <em>{instrument.displayName}</em>
    </span>
  );
}

function InstrumentTitle({ instrument }: { instrument: Instrument }) {
  return <>{instrument.baseAsset} / {instrument.quoteCurrency}</>;
}

function TimeInline({ value, zone }: { value: string; zone: "KST" | "UTC" }) {
  return (
    <span className="time-inline">
      {value}
      <em>{zone}</em>
    </span>
  );
}

function Metric({
  label,
  value,
  hint,
  tone = "default"
}: {
  label: string;
  value: string;
  hint: string;
  tone?: "default" | "warning" | "danger";
}) {
  return (
    <article className={`metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <em>{hint}</em>
    </article>
  );
}

function MiniMetric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="mini-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <em>{detail}</em>
    </article>
  );
}

function statusText(status: string) {
  if (status === "normal") return "정상";
  if (status === "warning") return "주의";
  if (status === "incident") return "장애";
  return status;
}

function statusFromTarget(target: CollectionDashboardTarget): Status {
  if (target.overallStatus === "incident") return "incident";
  if (target.overallStatus === "warning") return "warning";
  return "normal";
}

function formatBackfillRange(target: CollectionDashboardTarget) {
  const candleStatus = target.dataStatuses.find((status) => status.dataType === "source_candle");
  return `${formatFreshness(target.plan.rangeStartAt)} ~ ${formatFreshness(
    candleStatus?.lastSuccessfulAt ?? target.plan.rangeStartAt
  )}`;
}

function sampleCandles(candles: Candle[], maxCount: number) {
  if (candles.length <= maxCount) return candles;
  const step = candles.length / maxCount;
  return Array.from({ length: maxCount }, (_, index) => candles[Math.floor(index * step)]).filter(
    Boolean
  );
}
