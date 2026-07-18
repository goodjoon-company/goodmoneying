import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCcw, Star, X } from "lucide-react";
import { loadMarketList, subscribeMarketList, type MarketListRow } from "../api";
import { formatCurrencyAmount, formatFreshness } from "../operationsDisplay";
import { formatKstDateTime } from "../displayFormat";
import { useOperationsConsole, type SectionId } from "../useOperationsConsole";
import { DataLab } from "../features/dataLab/DataLab";
import { InstrumentName } from "./common";
import { Dashboard } from "./Dashboard";
import { CoinAnalysis } from "./CoinAnalysis";
import { CoverageQuality } from "./CoverageQuality";
import { DetailModal } from "./Detail";
import { Markets } from "./Markets";
import { Targets } from "./Targets";
import { SystemManagement } from "./SystemManagement";
import { UpbitApiTest } from "./UpbitApiTest";
import type { WorkbenchModuleId } from "./upbit-api-test/types";

const menuGroups: {
  title: string;
  items: { id: SectionId; label: string; badge: string }[];
}[] = [
  {
    title: "데이터 수집관리",
    items: [
      { id: "dashboard", label: "운영 상태", badge: "MVP" },
      { id: "coverage", label: "Coverage & Quality", badge: "P1" },
      { id: "targets", label: "Backfill 관리", badge: "호환" },
      { id: "system", label: "시스템 관리", badge: "NEW" }
    ]
  }
];

const primaryMenuItems: { id: SectionId; label: string; badge: string }[] = [
  { id: "markets", label: "관심종목", badge: "MVP" },
  { id: "analysis", label: "코인 분석", badge: "NEW" },
  { id: "data-lab", label: "Data Lab", badge: "P2-6" }
];

const upbitWorkbenchMenuItems: { id: WorkbenchModuleId; label: string; badge: string }[] = [
  { id: "quotation", label: "Quotation API 테스트", badge: "13" },
  { id: "exchange", label: "Exchange API 테스트", badge: "38" },
  { id: "websocket", label: "WebSocket API 테스트", badge: "14" }
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
  coverage: {
    crumb: "goodmoneying / Coverage & Quality / P1",
    milestone: "P1 · 데이터 기반",
    title: "시장 수집 정책과 커버리지",
    desc: "모든 KRW 시장의 2024 UTC 기본 정책, 자동 백필·실시간 desired state, 5단계 품질 증거를 확인합니다."
  },
  markets: {
    crumb: "goodmoneying / 관심종목 / M2",
    milestone: "M2 · 운영 관제형",
    title: "관심종목",
    desc: "수집 후보군에서 코인을 관심목록에 추가하고 현재가, 거래대금, 기준일시와 캔들 커버리지를 비교합니다."
  },
  analysis: {
    crumb: "goodmoneying / 코인 분석 / P2",
    milestone: "P2 · 코인 전용",
    title: "코인 분석",
    desc: "관심 코인의 차트, 거래량, 기술 지표와 현재가·호가·체결 흐름을 실시간으로 분석합니다."
  },
  "data-lab": {
    crumb: "goodmoneying / Data Lab / P2-6",
    milestone: "P2-6 · 연구 데이터",
    title: "Data Lab",
    desc: "불변 데이터셋 build와 version, coverage, exact member를 고정된 REST 계약으로 탐색합니다."
  },
  system: {
    crumb: "goodmoneying / 시스템 관리 / P2.1",
    milestone: "P2.1 · 운영 자동화",
    title: "시스템 관리",
    desc: "실시간·Backfill 수집과 자동 캔들 집계 작업의 코인별 대상, 데이터 유형, 진행률을 실시간으로 확인합니다."
  },
  "upbit-api-test": {
    crumb: "goodmoneying / 개발 도구 / 업비트 API 테스트",
    milestone: "P2 · 개발·검증 도구",
    title: "업비트 API 테스트",
    desc: "공식 카탈로그의 시세 조회(Quotation), 거래 및 자산 관리(Exchange), WebSocket 기능을 격리된 게이트웨이에서 시험합니다."
  }
};

export function OperationsConsole() {
  const queryClient = useQueryClient();
  const [isFavoritesOpen, setFavoritesOpen] = useState(false);
  const [activeWorkbenchModule, setActiveWorkbenchModule] = useState<WorkbenchModuleId>("quotation");
  const [workbenchMarket, setWorkbenchMarket] = useState("KRW-BTC");
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
  const marketQuery = useQuery({
    queryKey: ["market-list"],
    queryFn: loadMarketList
  });

  useEffect(
    () =>
      subscribeMarketList((streamedRows) => {
        queryClient.setQueryData<MarketListRow[]>(["market-list"], streamedRows);
      }),
    [queryClient]
  );
  const marketRows = marketQuery.data ?? [];
  const favoriteCoinRows = useMemo(
    () => marketRows.filter((row) => row.assetType === "coin" && row.isFavorite),
    [marketRows]
  );

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
          <div className="primary-nav-items">
            {primaryMenuItems.map((item) => (
              <button
                key={`primary-${item.label}`}
                className={item.id === activeSection ? "active" : ""}
                type="button"
                aria-label={item.label}
                onClick={() => setActiveSection(item.id)}
              >
                <span>{item.label}</span>
                <em>{item.badge}</em>
              </button>
            ))}
          </div>
          <section className="upbit-workbench-nav" aria-label="업비트 API 테스트 2레벨 메뉴">
            <h2>업비트 API 테스트</h2>
            {upbitWorkbenchMenuItems.map((item) => (
              <button
                key={item.id}
                className={activeSection === "upbit-api-test" && activeWorkbenchModule === item.id ? "active" : ""}
                type="button"
                aria-label={item.label}
                onClick={() => {
                  setActiveWorkbenchModule(item.id);
                  setActiveSection("upbit-api-test");
                }}
              >
                <span>{item.label}</span><em>{item.badge}</em>
              </button>
            ))}
          </section>
          {menuGroups.map((group) => (
            <section key={group.title}>
              <h2>{group.title}</h2>
              {group.items.map((item) => (
                <button
                  key={`${group.title}-${item.label}`}
                  className={item.id === activeSection ? "active" : ""}
                  type="button"
                  aria-label={item.label.replace("/", " ")}
                  onClick={() => setActiveSection(item.id)}
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
            <button
              className="favorite-summary-button"
              type="button"
              aria-label={`관심 코인 ${favoriteCoinRows.length}개 보기`}
              onClick={() => setFavoritesOpen(true)}
            >
              <Star size={16} fill="currentColor" />
              <span>관심 코인</span>
              <strong>{favoriteCoinRows.length.toLocaleString("ko-KR")}</strong>
            </button>
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
            <span>{activeSection === "analysis" || activeSection === "system" ? "WebSocket 실시간" : activeSection === "upbit-api-test" ? "게이트웨이 격리 조회" : activeSection === "data-lab" ? "REST polling" : "SSE 실시간"}</span>
            <span>마지막 갱신 {formatFreshness(snapshot.dashboard.refreshedAt)}</span>
          </div>
        </section>

        {activeSection === "dashboard" ? (
          <Dashboard snapshot={snapshot} onSelectInstrument={openInstrumentDetail} />
        ) : null}
        {activeSection === "targets" ? (
          <Targets snapshot={snapshot} favoriteRows={favoriteCoinRows} />
        ) : null}
        {activeSection === "coverage" ? <CoverageQuality /> : null}
        {activeSection === "markets" ? (
          <Markets
            rows={marketRows}
            selectedInstrumentId={selectedInstrumentId}
            onSelectInstrument={openInstrumentDetail}
          />
        ) : null}
        {activeSection === "analysis" ? (
          <CoinAnalysis rows={favoriteCoinRows} onOpenWatchlist={() => setActiveSection("targets")} />
        ) : null}
        {activeSection === "data-lab" ? <DataLab /> : null}
        {activeSection === "system" ? <SystemManagement /> : null}
        {activeSection === "upbit-api-test" ? <UpbitApiTest moduleId={activeWorkbenchModule}
          market={workbenchMarket} onMarketChange={setWorkbenchMarket} /> : null}
      </section>

      {isDetailOpen ? <DetailModal snapshot={snapshot} onClose={() => setDetailOpen(false)} /> : null}
      {isFavoritesOpen ? (
        <FavoriteCoinsDialog rows={favoriteCoinRows} onClose={() => setFavoritesOpen(false)} />
      ) : null}
    </main>
  );
}

function FavoriteCoinsDialog({
  rows,
  onClose
}: {
  rows: MarketListRow[];
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <section
        className="favorite-coins-dialog"
        role="dialog"
        aria-label="관심 코인 목록"
        aria-modal="true"
      >
        <button className="icon-button close-button" type="button" aria-label="닫기" onClick={onClose}>
          <X size={18} />
        </button>
        <div className="panel-heading">
          <h2>관심 코인 목록</h2>
          <span>{rows.length.toLocaleString("ko-KR")}개</span>
        </div>
        {rows.length === 0 ? (
          <p className="helper-text">관심 코인이 없습니다.</p>
        ) : (
          <div className="favorite-coin-list">
            {rows.map((row) => (
              <article className="favorite-coin-item" key={row.instrument.id}>
                <div>
                  <InstrumentName instrument={row.instrument} />
                  <small>{row.tickerCollectedAt ? formatKstDateTime(row.tickerCollectedAt) : "-"}</small>
                </div>
                <span className="money-cell">
                  {row.tradePrice === null ? (
                    <strong>-</strong>
                  ) : (
                    <strong>{formatCurrencyAmount(row.tradePrice, row.priceCurrency)}</strong>
                  )}
                </span>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
