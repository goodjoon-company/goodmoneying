import {
  Activity,
  ArrowUpDown,
  Bell,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  Download,
  LineChart,
  ListChecks,
  RefreshCcw,
  Save,
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
import { useEffect, useMemo, useRef, useState } from "react";
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
  loadInstrumentSnapshot,
  loadOperationsSnapshot,
  updateCollectionTargets,
  type Candle,
  type CandidateUniverseEntry,
  type CollectionDashboardTarget,
  type CoverageSegment,
  type Instrument,
  type MarketListRow,
  type OperationsSnapshot,
  type Status
} from "./api";

type SectionId = "dashboard" | "targets" | "markets";
type TargetSortKey = "name" | "trade" | "quality" | "range";
type SortDirection = "asc" | "desc";

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
      { label: "코인 상세", badge: "MVP", enabled: false },
      { label: "확장성 점검", badge: "MVP", enabled: false }
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
    title: "매매 전략",
    items: [
      { label: "전략 작업대", badge: "후속", enabled: false },
      { label: "백테스트", badge: "후속", enabled: false },
      { label: "지표 기여도", badge: "후속", enabled: false },
      { label: "호가 재생 제약", badge: "후속", enabled: false }
    ]
  },
  {
    title: "봇 관리",
    items: [
      { label: "봇 설계", badge: "후속", enabled: false },
      { label: "전략 파이프라인", badge: "후속", enabled: false },
      { label: "시뮬레이션", badge: "후속", enabled: false },
      { label: "모의매매 준비", badge: "후속", enabled: false }
    ]
  },
  {
    title: "시스템 관리",
    items: [
      { label: "보존 정책", badge: "후속", enabled: false },
      { label: "감사 로그", badge: "후속", enabled: false },
      { label: "알림 이벤트", badge: "후속", enabled: false },
      { label: "토큰 설정", badge: "후속", enabled: false }
    ]
  }
];

const sectionMeta: Record<SectionId, { crumb: string; milestone: string; title: string; desc: string }> = {
  dashboard: {
    crumb: "goodmoneying / 운영 상태 / M1",
    milestone: "M1 · 운영 관제형",
    title: "업비트 수집 운영 상태",
    desc: "수집 대상 최대 50개 코인의 최신성, 지연, 결측, 실패, 저장량을 한 화면에서 확인하는 고밀도 운영 콘솔"
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
    desc: "수집 대상 코인의 현재가, 거래대금, 등락률, 최신성, 커버리지와 저장량을 비교합니다."
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
  const [snapshot, setSnapshot] = useState<OperationsSnapshot | null>(null);
  const [activeSection, setActiveSection] = useState<SectionId>("dashboard");
  const [selectedInstrumentId, setSelectedInstrumentId] = useState<number | null>(null);
  const [isDetailOpen, setDetailOpen] = useState(false);
  const query = useQuery({
    queryKey: ["operations"],
    queryFn: () =>
      import.meta.env.MODE === "test" ? Promise.resolve(demoSnapshot()) : loadOperationsSnapshot(),
    refetchInterval: activeSection === "dashboard" ? 15_000 : false
  });

  useEffect(() => {
    if (!query.data) return;
    setSnapshot(query.data);
    setSelectedInstrumentId((current) => current ?? query.data.detail.instrument.id);
  }, [query.data]);

  const openInstrumentDetail = async (instrumentId: number) => {
    setSelectedInstrumentId(instrumentId);
    setDetailOpen(true);
    if (!snapshot || snapshot.detail.instrument.id === instrumentId) return;
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

  const meta = sectionMeta[activeSection];

  return (
    <main className="app-shell app-layout" data-theme="dark">
      <aside className="sidebar" aria-label="제품 메뉴">
        <div className="brand-block">
          <div className="brand-mark">gm</div>
          <div>
            <strong>goodmoneying</strong>
            <span>운영 관제형</span>
          </div>
        </div>
        <nav className="top-product-nav" aria-label="제품 영역">
          <button className="active" type="button">데이터 수집관리</button>
          <button type="button" disabled>종목 발굴</button>
          <button type="button" disabled>매매 전략</button>
          <button type="button" disabled>봇 관리</button>
          <button type="button" disabled>시스템 관리</button>
        </nav>
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
            <button type="button" aria-label="CSV 내보내기">
              <Download size={16} />
              CSV 내보내기
            </button>
            <button type="button" className="primary-action" aria-label="운영 변경 저장">
              <Save size={16} />
              운영 변경 저장
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
            <span>폴링 10-30초</span>
          </div>
        </section>

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

      {isDetailOpen ? <DetailModal snapshot={snapshot} onClose={() => setDetailOpen(false)} /> : null}
    </main>
  );
}

function Dashboard({ snapshot }: { snapshot: OperationsSnapshot }) {
  const totals = snapshot.dashboard.totals;
  return (
    <section className="dashboard-page">
      <div className="metric-band">
        <Metric label="활성 수집 대상" value={`${totals.activeTargets}/${totals.activeTargetLimit}`} hint="상위 100 후보 기준" />
        <Metric label="정상 수집" value={totals.normalTargets.toString()} hint="최근 3분 이내" />
        <Metric
          label="주의/장애"
          value={`${totals.warningTargets}/${totals.incidentTargets}`}
          hint="결측 복구 필요"
          tone={totals.warningTargets || totals.incidentTargets ? "warning" : "default"}
        />
        <Metric label="오늘 저장량" value={totals.storageBytesTodayDisplay} hint="원천·상태 합산" />
        <Metric
          label="실패율"
          value={formatPercent(totals.failureRate24h)}
          hint="최근 24시간"
          tone={Number(totals.failureRate24h) > 0 ? "danger" : "default"}
        />
      </div>

      <section className="panel trend-panel">
        <div className="panel-heading">
          <h2>구간형 수집 진행 상태</h2>
          <TimeInline value="KST 전일 23:59:59 기준" zone="KST" />
        </div>
        <OperationalTrendChart targets={snapshot.dashboard.targets} />
        <div className="mini-metrics">
          <MiniMetric label="결측 구간" value={totals.missingRangesOpen.toString()} detail="캔들 결측 기준" />
          <MiniMetric label="백필 대기" value={snapshot.backfillJobs.length.toString()} detail={`예상 요청 ${totals.recentRequestCount.toLocaleString("ko-KR")}`} />
          <MiniMetric label="Rate limit 여유" value={`${totals.rateLimitRemainingPercent}%`} detail="전역 제한기 정상" />
        </div>
      </section>

      <section className="panel health-panel">
        <div className="panel-heading">
          <h2>운영 헬스</h2>
          <Bell size={18} />
        </div>
        <div className="health-list">
          {snapshot.dashboard.healthChecks.map((check) => (
            <article className="health-item" key={check.title}>
              <span>{check.title}</span>
              <strong className={check.status}>{check.statusLabel}</strong>
              <em>{check.detail}</em>
            </article>
          ))}
        </div>
      </section>

      <section className="panel full">
        <div className="panel-heading">
          <h2>코인별 수집 상태</h2>
          <span>{snapshot.dashboard.targets.length}개</span>
        </div>
        <div className="dashboard-table">
          <div className="dashboard-table-head">
            <span>코인</span>
            <span>상태</span>
            <span>최근성</span>
            <span>커버리지</span>
            <span>데이터 상태</span>
          </div>
          {snapshot.dashboard.targets.slice(0, 8).map((target) => (
            <CollectionTargetRow key={target.instrument.id} target={target} />
          ))}
        </div>
      </section>
    </section>
  );
}

function CollectionTargetRow({ target }: { target: CollectionDashboardTarget }) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
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
      <button
        className="dashboard-row-button"
        type="button"
        onClick={() => setExpanded((current) => !current)}
      >
        <InstrumentName instrument={target.instrument} />
        <span className={`quality ${target.overallStatus === "latest_collecting" ? "normal" : "warning"}`}>
          {target.overallStatusLabel}
        </span>
        <TimeInline value={formatFreshness(target.plan.rangeStartAt)} zone={target.plan.rangeTimeZone} />
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
                기존 데이터를 삭제하지 않는 안전 재시작(Safe Restart) 기준으로 백필 계획을 만듭니다.
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
                  결측 {status.missingSegmentCount}개 · 마지막 성공{" "}
                  {formatFreshness(status.lastSuccessfulAt)}
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
  const [searchTerm, setSearchTerm] = useState("");
  const [sortKey, setSortKey] = useState<TargetSortKey>("trade");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
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
  const sortEntries = (entries: CandidateUniverseEntry[]) =>
    [...entries].sort((a, b) => compareTargetEntries(a, b, sortKey, sortDirection));
  const normalizedSearch = normalizeSearch(searchTerm);
  const filteredEntries = sortEntries(
    snapshot.candidateEntries
      .slice(0, 100)
      .filter((entry) => targetMatchesSearch(entry, normalizedSearch))
  );
  const selectedEntries = filteredEntries.filter((entry) => selectedIds.has(entry.instrument.id));
  const candidateEntries = filteredEntries.filter((entry) => !selectedIds.has(entry.instrument.id));
  const setSort = (nextKey: TargetSortKey) => {
    if (sortKey === nextKey) {
      setSortDirection((current) => (current === "desc" ? "asc" : "desc"));
      return;
    }
    setSortKey(nextKey);
    setSortDirection(nextKey === "trade" ? "desc" : "asc");
  };
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
    <section className="targets-page">
      <section className="panel full">
        <div className="panel-heading">
          <h2>관심종목 설정</h2>
          <span>선택 {selected}/50</span>
        </div>
        <div className="target-toolbar">
          <label>
            <Search size={16} />
            <input
              placeholder="코인명 또는 심볼 검색"
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
            />
          </label>
          <button type="button" disabled={!canSave} onClick={() => mutation.mutate(Array.from(selectedIds))}>
            <CheckCircle2 size={16} />
            저장
          </button>
        </div>
        {mutation.isError ? <p className="error-text">수집 대상 저장에 실패했습니다.</p> : null}
        {searchTerm ? (
          <p className="helper-text">검색 결과 {filteredEntries.length}개 · 관심추가 {selectedEntries.length}개</p>
        ) : null}
        <div className="target-table">
          <div className="target-section-heading">
            <h3>관심추가 항목</h3>
            <span>{selectedEntries.length}개</span>
          </div>
          <div className="target-table-head">
            <span>활성</span>
            <TargetSortButton
              active={sortKey === "name"}
              direction={sortDirection}
              label="종목"
              onClick={() => setSort("name")}
            />
            <TargetSortButton
              active={sortKey === "trade"}
              direction={sortDirection}
              label="24시간 거래대금"
              onClick={() => setSort("trade")}
            />
            <TargetSortButton
              active={sortKey === "quality"}
              direction={sortDirection}
              label="품질"
              onClick={() => setSort("quality")}
            />
            <TargetSortButton
              active={sortKey === "range"}
              direction={sortDirection}
              label="수집 범위"
              onClick={() => setSort("range")}
            />
          </div>
          {selectedEntries.length > 0 ? (
            selectedEntries.map((entry) => (
              <TargetRow
                entry={entry}
                key={`selected-${entry.instrument.id}`}
                selected={selectedIds.has(entry.instrument.id)}
                onToggle={() => toggle(entry.instrument.id)}
              />
            ))
          ) : (
            <p className="empty-table-row">검색 조건에 맞는 관심추가 항목이 없습니다.</p>
          )}
          <div className="target-section-heading secondary">
            <h3>후보 유니버스 상위 100개</h3>
            <span>{candidateEntries.length}개</span>
          </div>
          {candidateEntries.length > 0 ? (
            candidateEntries.map((entry) => (
              <TargetRow
                entry={entry}
                key={`candidate-${entry.instrument.id}`}
                selected={selectedIds.has(entry.instrument.id)}
                onToggle={() => toggle(entry.instrument.id)}
              />
            ))
          ) : (
            <p className="empty-table-row">검색 조건에 맞는 후보 종목이 없습니다.</p>
          )}
        </div>
      </section>
      <section className="panel side-panel">
        <div className="panel-heading">
          <h2>백필 승인 패널</h2>
          <Settings2 size={18} />
        </div>
        <MiniMetric label="예상 요청 수" value="18,420" detail="1분 캔들 + 일봉 보정" />
        <MiniMetric label="예상 저장량" value="12.6GB" detail="삭제 후 재수집 없음" />
        <MiniMetric label="감사 로그" value="필수" detail="대상 변경·범위 변경 기록" />
      </section>
    </section>
  );
}

function TargetSortButton({
  active,
  direction,
  label,
  onClick
}: {
  active: boolean;
  direction: SortDirection;
  label: string;
  onClick: () => void;
}) {
  const sortValue = active ? (direction === "asc" ? "ascending" : "descending") : "none";
  return (
    <button
      aria-label={`${label} 정렬`}
      aria-sort={sortValue}
      className={`table-sort-button ${active ? "active" : ""}`}
      type="button"
      onClick={onClick}
    >
      <span>{label}</span>
      <ArrowUpDown size={13} />
    </button>
  );
}

function TargetRow({
  entry,
  selected,
  onToggle
}: {
  entry: CandidateUniverseEntry;
  selected: boolean;
  onToggle: () => void;
}) {
  return (
    <label className="target-row">
      <span>
        <input
          aria-label="관심추가"
          type="checkbox"
          checked={selected}
          onChange={onToggle}
        />
        관심
      </span>
      <InstrumentName instrument={entry.instrument} />
      <strong>{entry.accTradePrice24hDisplay}</strong>
      <em className={`quality ${entry.qualityStatus}`}>{statusText(entry.qualityStatus)}</em>
      <span>{entry.collectionRangeDisplay}</span>
    </label>
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
  const changeRateBasis = latestMarketTickerBasis(snapshot.marketRows);
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
          <span className="stacked-header">
            등락률
            <em>{changeRateBasis} 기준</em>
          </span>
          <span>최신성</span>
          <span>커버리지</span>
          <span>저장량</span>
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
            <InstrumentName instrument={row.instrument} />
            <span>{formatNumber(row.tradePrice)}</span>
            <span>{row.accTradePrice24hDisplay}</span>
            <span className={Number(row.changeRate) >= 0 ? "change up" : "change down"}>
              {formatPercent(row.changeRate)}
            </span>
            <TimeInline value={formatFreshness(row.tickerCollectedAt)} zone="KST" />
            <CoverageMeter value={row.coveragePercent} />
            <span>{row.storageBytesDisplay}</span>
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
  const candles = useMemo(() => sampleCandles(snapshot.candles, 180), [snapshot.candles]);
  const instrument = snapshot.detail.instrument;
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
          currentPrice={snapshot.detail.latestTicker.tradePrice}
        />
        <div className="detail-stats">
          <MiniMetric label="현재가" value={`₩${formatNumber(snapshot.detail.latestTicker.tradePrice)}`} detail={snapshot.detail.tickerFreshnessLabel} />
          <MiniMetric label="거래대금" value={formatNumber(snapshot.detail.latestTicker.accTradePrice24h)} detail="소수점 생략" />
          <MiniMetric label="중복 행" value={snapshot.detail.duplicateRows24h.toString()} detail="최근 24시간" />
        </div>
      </section>
      <section className="panel orderbook-panel">
        <div className="panel-heading">
          <h2>호가 요약</h2>
          <TimeInline value={snapshot.detail.orderbookFreshnessLabel} zone="KST" />
        </div>
        <div className="orderbook-grid">
          <MiniMetric label="최우선 매수" value={formatNumber(snapshot.detail.latestOrderbook.bestBidPrice)} detail={`수량 ${snapshot.detail.latestOrderbook.bestBidSize} ${instrument.baseAsset}`} />
          <MiniMetric label="최우선 매도" value={formatNumber(snapshot.detail.latestOrderbook.bestAskPrice)} detail={`수량 ${snapshot.detail.latestOrderbook.bestAskSize} ${instrument.baseAsset}`} />
          <MiniMetric label="스프레드" value={`${snapshot.detail.latestOrderbook.spread}`} detail="정상 범위" />
          <MiniMetric label="호가 불균형" value={formatPercent(snapshot.detail.latestOrderbook.imbalance10)} detail="매수 잔량 우세" />
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

function OperationalTrendChart({ targets }: { targets: CollectionDashboardTarget[] }) {
  const points = targets.slice(0, 12).map((target, index) => ({
    x: 24 + index * 76,
    y: 150 - Number(target.dataStatuses[0]?.progressPercent ?? 0) * 0.9
  }));
  const line = points.map((point) => `${point.x},${point.y}`).join(" ");
  return (
    <div className="ops-chart" aria-label="구간형 수집 진행 상태 차트">
      <svg viewBox="0 0 900 220" role="img">
        <defs>
          <linearGradient id="coverage-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#24d6a2" stopOpacity="0.35" />
            <stop offset="100%" stopColor="#24d6a2" stopOpacity="0.03" />
          </linearGradient>
        </defs>
        {Array.from({ length: 8 }, (_, index) => (
          <line key={`v-${index}`} x1={40 + index * 110} x2={40 + index * 110} y1="18" y2="198" />
        ))}
        {Array.from({ length: 4 }, (_, index) => (
          <line key={`h-${index}`} x1="20" x2="880" y1={40 + index * 46} y2={40 + index * 46} />
        ))}
        <polyline points={`20,190 ${line} 880,86 880,198 20,198`} className="area" />
        <polyline points={line} className="line primary" />
        <polyline points={points.map((point) => `${point.x},${point.y + 34}`).join(" ")} className="line secondary" />
        <circle cx="690" cy="82" r="7" className="dot warning" />
        <circle cx="820" cy="92" r="7" className="dot danger" />
      </svg>
      <span>녹색=수집 커버리지, 파랑=저장량, 점=주의/장애 구간</span>
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

function compareTargetEntries(
  first: CandidateUniverseEntry,
  second: CandidateUniverseEntry,
  sortKey: TargetSortKey,
  direction: SortDirection
) {
  const multiplier = direction === "asc" ? 1 : -1;
  if (sortKey === "name") {
    return (
      first.instrument.baseAsset.localeCompare(second.instrument.baseAsset, "ko-KR") * multiplier
    );
  }
  if (sortKey === "quality") {
    const qualityRank: Record<Status, number> = { normal: 0, warning: 1, incident: 2 };
    return (qualityRank[first.qualityStatus] - qualityRank[second.qualityStatus]) * multiplier;
  }
  if (sortKey === "range") {
    return first.collectionRangeDisplay.localeCompare(second.collectionRangeDisplay, "ko-KR") * multiplier;
  }
  return (Number(first.accTradePrice24h) - Number(second.accTradePrice24h)) * multiplier;
}

function normalizeSearch(value: string) {
  return value.trim().toLocaleLowerCase("ko-KR");
}

function targetMatchesSearch(entry: CandidateUniverseEntry, normalizedSearch: string) {
  if (!normalizedSearch) return true;
  const haystack = [
    entry.instrument.marketCode,
    entry.instrument.baseAsset,
    entry.instrument.displayName,
    `${entry.instrument.baseAsset} / ${entry.instrument.quoteCurrency}`
  ]
    .join(" ")
    .toLocaleLowerCase("ko-KR");
  return haystack.includes(normalizedSearch);
}

function latestMarketTickerBasis(rows: MarketListRow[]) {
  const latest = rows
    .map((row) => Date.parse(row.tickerCollectedAt))
    .filter((value) => Number.isFinite(value))
    .sort((a, b) => b - a)[0];
  return Number.isFinite(latest) ? formatKstShort(new Date(latest).toISOString()) : "기준 없음";
}

function formatKstShort(value: string) {
  const parts = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).formatToParts(new Date(value));
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${get("month")}.${get("day")} ${get("hour")}:${get("minute")} KST`;
}

function statusText(status: string) {
  if (status === "normal") return "정상";
  if (status === "warning") return "주의";
  if (status === "incident") return "장애";
  return status;
}

function sampleCandles(candles: Candle[], maxCount: number) {
  if (candles.length <= maxCount) return candles;
  const step = candles.length / maxCount;
  return Array.from({ length: maxCount }, (_, index) => candles[Math.floor(index * step)]).filter(
    Boolean
  );
}

function selectDemoInstrument(snapshot: OperationsSnapshot, instrumentId: number) {
  const row = snapshot.marketRows.find((item) => item.instrument.id === instrumentId);
  if (!row) return snapshot;
  return {
    ...snapshot,
    detail: {
      ...snapshot.detail,
      instrument: row.instrument,
      latestTicker: {
        ...snapshot.detail.latestTicker,
        tradePrice: row.tradePrice,
        accTradePrice24h: row.accTradePrice24h,
        changeRate: row.changeRate,
        collectedAt: row.tickerCollectedAt
      },
      coverage: snapshot.dashboard.coverage.filter((item) => item.instrumentId === instrumentId)
    },
    candles: demoCandles(row.tradePrice)
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

function demoCandles(anchorPrice: string): Candle[] {
  const base = Number(anchorPrice);
  const start = Date.parse("2026-01-01T00:00:00.000Z");
  return Array.from({ length: 96 }, (_, index) => {
    const open = base + Math.sin(index / 7) * 2400 + index * 38;
    const close = open + Math.cos(index / 5) * 1800;
    return {
      startedAt: new Date(start + index * 60_000).toISOString(),
      open: `${Math.round(open)}`,
      high: `${Math.round(Math.max(open, close) + 1200)}`,
      low: `${Math.round(Math.min(open, close) - 1200)}`,
      close: `${Math.round(close)}`,
      volume: `${120 + index * 1.7}`,
      tradeAmount: `${Math.round(close * (120 + index * 1.7))}`,
      completeness: "complete"
    };
  });
}
