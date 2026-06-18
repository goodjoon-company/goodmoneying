import {
  Activity,
  Bell,
  CheckCircle2,
  Database,
  LineChart,
  ListChecks,
  RefreshCcw,
  Search,
  X
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient
} from "@tanstack/react-query";
import {
  createBackfillPlan,
  demoSnapshot,
  JANUARY_2026_BACKFILL_END,
  JANUARY_2026_BACKFILL_START,
  loadInstrumentSnapshot,
  loadOperationsSnapshot,
  updateCollectionTargets,
  type Candle,
  type CollectionDashboardTarget,
  type CoverageSegment,
  type MarketListRow,
  type OperationsSnapshot
} from "./api";

type SectionId = "dashboard" | "targets" | "markets";

const productNav = [
  { label: "데이터 수집관리", icon: Activity, enabled: true },
  { label: "종목 발굴", icon: Search, enabled: false },
  { label: "매매 전략", icon: LineChart, enabled: false },
  { label: "봇 관리", icon: RefreshCcw, enabled: false },
  { label: "시스템 관리", icon: Database, enabled: false }
];

const sections: { id: SectionId; label: string; icon: typeof Activity }[] = [
  { id: "dashboard", label: "운영 상태 대시보드", icon: Activity },
  { id: "targets", label: "수집 대상 설정", icon: ListChecks },
  { id: "markets", label: "시장 리스트", icon: Search }
];

export function App() {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <OperationsApp />
    </QueryClientProvider>
  );
}

function OperationsApp() {
  const [snapshot, setSnapshot] = useState<OperationsSnapshot | null>(null);
  const [activeSection, setActiveSection] = useState<SectionId>("dashboard");
  const [selectedInstrumentId, setSelectedInstrumentId] = useState<number | null>(null);
  const [isDetailOpen, setDetailOpen] = useState(false);
  const query = useQuery({
    queryKey: ["operations"],
    queryFn: () => (import.meta.env.MODE === "test" ? Promise.resolve(demoSnapshot()) : loadOperationsSnapshot()),
    refetchInterval: activeSection === "dashboard" ? 15_000 : false
  });

  useEffect(() => {
    if (query.data) {
      setSnapshot(query.data);
      setSelectedInstrumentId((current) => current ?? query.data.detail.instrument.id);
    }
  }, [query.data]);

  const openInstrumentDetail = async (instrumentId: number) => {
    setSelectedInstrumentId(instrumentId);
    setDetailOpen(true);
    if (!snapshot || snapshot.detail.instrument.id === instrumentId) {
      return;
    }
    if (import.meta.env.MODE === "test") {
      setSnapshot(selectDemoInstrument(snapshot, instrumentId));
      return;
    }
    const next = await loadInstrumentSnapshot(instrumentId);
    setSnapshot((previous) =>
      previous ? { ...previous, detail: next.detail, candles: next.candles } : previous
    );
  };

  if (query.error) {
    return <main className="app-shell error-state">운영 API를 불러오지 못했습니다.</main>;
  }

  if (!snapshot) {
    return <main className="app-shell loading-state">운영 상태를 불러오는 중</main>;
  }

  return (
    <main className="app-shell app-layout" data-theme="dark">
      <aside className="sidebar" aria-label="제품 메뉴">
        <div className="brand-block">
          <strong>goodmoneying</strong>
          <span>개인 투자 데이터 플랫폼</span>
        </div>
        <nav className="product-nav">
          {productNav.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.label}
                className={item.enabled ? "active" : ""}
                type="button"
                disabled={!item.enabled}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">데이터 수집관리</p>
            <h1>{sections.find((item) => item.id === activeSection)?.label}</h1>
          </div>
          <div className={`status-pill ${snapshot.dashboard.status}`}>
            <CheckCircle2 size={18} />
            <span>{statusText(snapshot.dashboard.status)}</span>
          </div>
        </header>

        <nav className="section-tabs" aria-label="데이터 수집관리 화면">
          {sections.map((section) => {
            const Icon = section.icon;
            return (
              <button
                key={section.id}
                className={activeSection === section.id ? "active" : ""}
                type="button"
                onClick={() => setActiveSection(section.id)}
              >
                <Icon size={18} />
                <span>{section.label}</span>
              </button>
            );
          })}
        </nav>

        {activeSection === "dashboard" ? <Dashboard snapshot={snapshot} /> : null}
        {activeSection === "targets" ? <Targets snapshot={snapshot} /> : null}
        {activeSection === "markets" ? (
          <Markets
            snapshot={snapshot}
            selectedInstrumentId={selectedInstrumentId}
            onSelectInstrument={openInstrumentDetail}
          />
        ) : null}
      </section>

      {isDetailOpen ? (
        <DetailModal snapshot={snapshot} onClose={() => setDetailOpen(false)} />
      ) : null}
    </main>
  );
}

function Dashboard({ snapshot }: { snapshot: OperationsSnapshot }) {
  const totals = snapshot.dashboard.totals;
  const [expandedId, setExpandedId] = useState<number | null>(null);
  return (
    <section className="page-grid">
      <div className="metric-band">
        <Metric label="활성 수집 대상" value={totals.activeTargets.toString()} />
        <Metric label="24시간 실패 실행" value={totals.failedRuns24h.toString()} tone="danger" />
        <Metric label="지연 대상" value={totals.delayedTargets.toString()} tone="warning" />
        <Metric label="열린 결측 구간" value={totals.missingRangesOpen.toString()} />
      </div>
      <section className="panel full">
        <div className="panel-heading">
          <h2>코인별 수집 상태</h2>
          <span>{snapshot.dashboard.targets.length}개</span>
        </div>
        <div className="dashboard-list">
          {snapshot.dashboard.targets.map((target) => (
            <CollectionTargetRow
              key={target.instrument.id}
              target={target}
              expanded={expandedId === target.instrument.id}
              onToggle={() =>
                setExpandedId((current) =>
                  current === target.instrument.id ? null : target.instrument.id
                )
              }
            />
          ))}
        </div>
      </section>
      <section className="panel full">
        <div className="panel-heading">
          <h2>알림 이벤트</h2>
          <Bell size={18} />
        </div>
        {snapshot.notifications.map((item) => (
          <article className="event-item" key={item.id}>
            <strong>{item.title}</strong>
            <span>{item.message}</span>
          </article>
        ))}
      </section>
    </section>
  );
}

function CollectionTargetRow({
  target,
  expanded,
  onToggle
}: {
  target: CollectionDashboardTarget;
  expanded: boolean;
  onToggle: () => void;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const backfillPlan = useMutation({
    mutationFn: () => createBackfillPlan([target.instrument.id]),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["operations"] })
  });
  const candleSegments = target.coverageSegments.filter(
    (segment) => segment.dataType === "source_candle"
  );
  return (
    <article className={`accordion-row ${expanded ? "expanded" : ""}`}>
      <button className="accordion-summary" type="button" onClick={onToggle}>
        <strong>{target.instrument.marketCode}</strong>
        <span>{target.instrument.displayName}</span>
        <span className={`quality ${target.overallStatus === "latest_collecting" ? "normal" : "warning"}`}>
          {target.overallStatusLabel}
        </span>
        <span>{target.plan.displayRange}</span>
        <TimeBadge value={target.plan.rangeTimeZone} />
        <TimeBadge value="UTC" />
        <CoverageBar segments={candleSegments} />
        <span className="mini-statuses">
          {target.dataStatuses.map((status) => (
            <em key={status.dataType}>{status.label} {status.statusLabel}</em>
          ))}
        </span>
      </button>
      {expanded ? (
        <div className="accordion-detail">
          <div className="detail-grid">
            <section>
              <div className="subheading">
                <h3>수집 계획</h3>
                <button type="button" onClick={() => setEditing((current) => !current)}>
                  수정
                </button>
              </div>
              <dl className="definition-list compact">
                <div>
                  <dt>프리셋</dt>
                  <dd>{target.plan.preset}</dd>
                </div>
                <div>
                  <dt>범위</dt>
                  <dd>{target.plan.displayRange}</dd>
                </div>
                <div>
                  <dt>방식</dt>
                  <dd>{target.plan.method}</dd>
                </div>
                <div>
                  <dt>진행 기준</dt>
                  <dd>{target.plan.progressBasis}</dd>
                </div>
              </dl>
              {editing ? <PlanEditor target={target} /> : null}
            </section>
            <section>
              <div className="subheading">
                <h3>백필</h3>
                <button
                  type="button"
                  disabled={backfillPlan.isPending}
                  onClick={() => backfillPlan.mutate()}
                >
                  계획 생성
                </button>
              </div>
              <p className="helper-text">
                코인별 수집 계획 안에서 안전 재시작 중심으로 백필을 실행합니다.
              </p>
            </section>
          </div>
          <div className="data-status-grid">
            {target.dataStatuses.map((status) => (
              <article className="data-status-card" key={status.dataType}>
                <div>
                  <strong>{status.label}</strong>
                  <span className={`quality ${status.status}`}>{status.statusLabel}</span>
                </div>
                <CoverageBar
                  segments={target.coverageSegments.filter(
                    (segment) => segment.dataType === status.dataType
                  )}
                />
                <span>
                  결측 {status.missingSegmentCount}개 · 마지막 성공 {formatFreshness(status.lastSuccessfulAt)}
                </span>
              </article>
            ))}
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
  const [selectedIds, setSelectedIds] = useState<Set<number>>(
    () =>
      new Set(
        snapshot.candidateEntries
          .filter((entry) => entry.selected)
          .map((entry) => entry.instrument.id)
      )
  );
  useEffect(() => {
    setSelectedIds(
      new Set(
        snapshot.candidateEntries
          .filter((entry) => entry.selected)
          .map((entry) => entry.instrument.id)
      )
    );
  }, [snapshot.candidateEntries]);
  const mutation = useMutation({
    mutationFn: (ids: number[]) => updateCollectionTargets(ids),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["operations"] })
  });
  const selected = selectedIds.size;
  const canSave = selected <= 50 && !mutation.isPending;
  const toggle = (instrumentId: number) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(instrumentId)) {
        next.delete(instrumentId);
      } else if (next.size < 50) {
        next.add(instrumentId);
      }
      return next;
    });
  };
  return (
    <section className="panel full">
      <div className="panel-heading">
        <h2>후보 유니버스 상위 100개</h2>
        <div className="heading-actions">
          <span>선택 {selected}/최대 50</span>
          <button
            type="button"
            disabled={!canSave}
            onClick={() => mutation.mutate(Array.from(selectedIds))}
          >
            <CheckCircle2 size={16} />
            저장
          </button>
        </div>
      </div>
      {mutation.isError ? <p className="error-text">수집 대상 저장에 실패했습니다.</p> : null}
      <div className="target-grid">
        {snapshot.candidateEntries.slice(0, 100).map((entry) => (
          <label className="target-item" key={entry.instrument.id}>
            <input
              type="checkbox"
              checked={selectedIds.has(entry.instrument.id)}
              onChange={() => toggle(entry.instrument.id)}
            />
            <span>{entry.rank}</span>
            <strong>{entry.instrument.marketCode}</strong>
            <em>{formatNumber(entry.accTradePrice24h)}</em>
          </label>
        ))}
      </div>
    </section>
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
  return (
    <section className="panel full">
      <div className="panel-heading">
        <h2>수집 데이터 요약</h2>
        <span>{snapshot.marketRows.length}개</span>
      </div>
      <div className="data-table">
        <div className="table-header">
          <span>거래 상품</span>
          <span>현재가</span>
          <span>24시간 거래대금</span>
          <span>등락률</span>
          <span>캔들 최신성</span>
          <span>품질</span>
        </div>
        {snapshot.marketRows.map((row) => (
          <button
            className={`table-row market-row-button ${
              selectedInstrumentId === row.instrument.id ? "selected" : ""
            }`}
            key={row.instrument.id}
            type="button"
            onClick={() => onSelectInstrument(row.instrument.id)}
          >
            <strong>{row.instrument.marketCode}</strong>
            <span>{formatNumber(row.tradePrice)}</span>
            <span>{row.accTradePrice24hDisplay}</span>
            <span className={Number(row.changeRate) >= 0 ? "change up" : "change down"}>
              {formatPercent(row.changeRate)}
            </span>
            <span>{formatFreshness(row.tickerCollectedAt)}</span>
            <span className={`quality ${row.qualityStatus}`}>{statusText(row.qualityStatus)}</span>
          </button>
        ))}
      </div>
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
  return (
    <div className="modal-backdrop">
      <section className="detail-modal" role="dialog" aria-label="코인 상세" aria-modal="true">
        <button className="icon-button close-button" type="button" aria-label="닫기" onClick={onClose}>
          <X size={18} />
        </button>
        <Detail snapshot={snapshot} />
      </section>
    </div>
  );
}

function Detail({ snapshot }: { snapshot: OperationsSnapshot }) {
  const candles = useMemo(() => sampleCandles(snapshot.candles, 96), [snapshot.candles]);
  const maxClose = Math.max(...candles.map((item) => Number(item.close)), 1);
  return (
    <section className="page-grid two detail-surface">
      <section className="panel">
        <div className="panel-heading">
          <h2>{snapshot.detail.instrument.marketCode}</h2>
          <span>{snapshot.detail.instrument.displayName}</span>
        </div>
        <dl className="definition-list">
          <div>
            <dt>현재가</dt>
            <dd>{formatNumber(snapshot.detail.latestTicker.tradePrice)}</dd>
          </div>
          <div>
            <dt>거래대금</dt>
            <dd>{formatNumber(snapshot.detail.latestTicker.accTradePrice24h)}</dd>
          </div>
          <div>
            <dt>스프레드</dt>
            <dd>{snapshot.detail.latestOrderbook.spread}</dd>
          </div>
          <div>
            <dt>호가 불균형</dt>
            <dd>{snapshot.detail.latestOrderbook.imbalance10}</dd>
          </div>
        </dl>
      </section>
      <section className="panel">
        <div className="panel-heading">
          <h2>캔들 흐름</h2>
          <span>2026년 1월 1분봉</span>
        </div>
        <p className="chart-meta">
          UTC 기준 2026-01-01 00:00 ~ 2026-02-01 00:00 · 표시 {candles.length}개 / 저장{" "}
          {snapshot.candles.length}개
        </p>
        <CandleChart candles={candles} maxClose={maxClose} />
      </section>
    </section>
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
          style={{
            left: `${segment.offsetPercent}%`,
            width: `${segment.widthPercent}%`
          }}
        />
      ))}
    </div>
  );
}

function TimeBadge({ value }: { value: "KST" | "UTC" }) {
  return <span className={`time-badge ${value.toLowerCase()}`}>{value}</span>;
}

function CandleChart({ candles, maxClose }: { candles: Candle[]; maxClose: number }) {
  if (candles.length === 0) {
    return (
      <div className="candle-chart empty" aria-label="캔들 차트">
        <span>선택한 기간에 저장된 캔들이 없습니다.</span>
      </div>
    );
  }
  const high = Math.max(...candles.map((item) => Number(item.high)), maxClose);
  const low = Math.min(...candles.map((item) => Number(item.low)));
  const range = Math.max(high - low, 1);
  const width = Math.max(560, candles.length * 12);
  const height = 240;
  const plotTop = 18;
  const plotBottom = height - 34;
  const y = (value: number) => plotBottom - ((value - low) / range) * (plotBottom - plotTop);
  const step = width / candles.length;
  return (
    <div className="candle-chart" aria-label="캔들 차트">
      <svg viewBox={`0 0 ${width} ${height}`} role="img">
        {candles.map((item, index) => {
          const x = index * step + step / 2;
          const open = Number(item.open);
          const close = Number(item.close);
          const candleHigh = Number(item.high);
          const candleLow = Number(item.low);
          const rising = close >= open;
          const bodyTop = y(Math.max(open, close));
          const bodyHeight = Math.max(2, Math.abs(y(open) - y(close)));
          return (
            <g key={item.startedAt} className={rising ? "candle up" : "candle down"}>
              <line x1={x} x2={x} y1={y(candleHigh)} y2={y(candleLow)} />
              <rect
                x={x - Math.max(3, step * 0.28)}
                y={bodyTop}
                width={Math.max(6, step * 0.56)}
                height={bodyHeight}
                rx="2"
              />
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function Metric({
  label,
  value,
  tone = "default"
}: {
  label: string;
  value: string;
  tone?: "default" | "warning" | "danger";
}) {
  return (
    <article className={`metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function statusText(status: string) {
  if (status === "normal") return "정상";
  if (status === "warning") return "주의";
  if (status === "incident") return "장애";
  return status;
}

function sampleCandles(candles: Candle[], maxCount: number) {
  if (candles.length <= maxCount) {
    return candles;
  }
  const step = candles.length / maxCount;
  return Array.from({ length: maxCount }, (_, index) => candles[Math.floor(index * step)]).filter(
    Boolean
  );
}

function selectDemoInstrument(snapshot: OperationsSnapshot, instrumentId: number) {
  const row = snapshot.marketRows.find((item) => item.instrument.id === instrumentId);
  if (!row) {
    return snapshot;
  }
  return {
    ...snapshot,
    detail: {
      instrument: row.instrument,
      latestTicker: {
        ...snapshot.detail.latestTicker,
        tradePrice: row.tradePrice,
        accTradePrice24h: row.accTradePrice24h,
        changeRate: row.changeRate,
        collectedAt: row.tickerCollectedAt
      },
      latestOrderbook: snapshot.detail.latestOrderbook,
      coverage: snapshot.dashboard.coverage.filter((item) => item.instrumentId === instrumentId)
    },
    candles: []
  };
}

function formatNumber(value: string) {
  return Number(value).toLocaleString("ko-KR", { maximumFractionDigits: 4 });
}

function formatPercent(value: string) {
  const percent = Number(value) * 100;
  const prefix = percent > 0 ? "+" : "";
  return `${prefix}${percent.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}%`;
}

function formatFreshness(value: string) {
  return new Date(value).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}
