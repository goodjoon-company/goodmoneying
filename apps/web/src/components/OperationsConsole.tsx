import { RefreshCcw } from "lucide-react";
import { formatFreshness } from "../operationsDisplay";
import { useOperationsConsole, type SectionId } from "../useOperationsConsole";
import { Dashboard } from "./Dashboard";
import { DetailModal } from "./Detail";
import { Markets } from "./Markets";
import { ScalabilityReadiness } from "./ScalabilityReadiness";
import { Targets } from "./Targets";

const menuGroups: {
  title: string;
  items: { id?: SectionId; label: string; badge: string; enabled: boolean }[];
}[] = [
  {
    title: "데이터 수집관리",
    items: [
      { id: "dashboard", label: "운영 상태", badge: "MVP", enabled: true },
      { id: "targets", label: "Backfill 관리", badge: "MVP", enabled: true },
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
    crumb: "goodmoneying / Backfill 관리 / M2",
    milestone: "M2 · 운영 관제형",
    title: "Backfill 관리",
    desc: "상위 100개 후보 중 활성 수집 대상 최대 50개를 조정하고 백필 작업을 시작합니다."
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

export function OperationsConsole() {
  const {
    snapshot,
    activeSection,
    setActiveSection,
    selectedInstrumentId,
    isDetailOpen,
    setDetailOpen,
    openInstrumentDetail,
    query
  } = useOperationsConsole();

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
            <span>저장 KST</span>
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
