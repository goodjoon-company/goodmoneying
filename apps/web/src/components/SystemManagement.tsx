import { useSystemManagement } from "../useSystemManagement";

const dataLabels: Record<string, string> = {
  source_candle: "원천 캔들", ticker_snapshot: "시세", orderbook_summary: "호가"
};

export function SystemManagement() {
  const { snapshot, connectionStatus } = useSystemManagement();
  if (!snapshot) return <section className="system-management loading-state">시스템 상태를 연결하는 중</section>;
  const aggregation = snapshot.aggregation;
  return <section className="system-management">
    <div className="system-live-bar"><strong>WebSocket {connectionStatus === "live" ? "연결됨" : "재연결 중"}</strong><span>상태 조각을 1초 단위로 갱신합니다.</span></div>
    <div className="system-card-grid">
      <WorkerCard title="실시간 수집" status={snapshot.realtime.statusLabel} items={snapshot.realtime.items} />
      <WorkerCard title="Backfill 수집" status={snapshot.backfill.statusLabel} items={snapshot.backfill.items} empty="실행 중인 Backfill이 없습니다." />
      <article className="panel system-worker-card">
        <div className="panel-heading"><div><p className="eyebrow">자동 집계 테이블</p><h2>{aggregation ? aggregation.status : "최신"}</h2></div><strong>{aggregation ? `${aggregation.progressPercent}%` : "100%"}</strong></div>
        <p>{aggregation ? `${aggregation.completedTargetCount}/${aggregation.totalTargetCount} 대상 완료 · 실행 ${aggregation.runningTargetCount} · 실패 ${aggregation.failedTargetCount}` : "모든 활성 코인의 집계 테이블이 최신입니다."}</p>
        <div className="system-progress"><span style={{ width: `${aggregation?.progressPercent ?? "100"}%` }} /></div>
        <ul className="system-items">{aggregation?.items.slice(0, 12).map((item) => <li key={`${item.instrument.id}-${item.unit}`}><strong>{item.instrument.marketCode}</strong><span>{item.unit} · {item.status} · {item.rowsWritten.toLocaleString("ko-KR")}행</span></li>)}</ul>
      </article>
    </div>
  </section>;
}

function WorkerCard({ title, status, items, empty = "활성 수집 대상이 없습니다." }: { title: string; status: string; items: { instrument: { id: number; marketCode: string }; dataTypes: string[] }[]; empty?: string }) {
  return <article className="panel system-worker-card"><div className="panel-heading"><div><p className="eyebrow">수집 워커</p><h2>{title}</h2></div><strong>{status}</strong></div><p>{items.length ? `현재 ${items.length}개 코인 데이터 유형을 수집 중입니다.` : empty}</p><ul className="system-items">{items.slice(0, 12).map((item) => <li key={item.instrument.id}><strong>{item.instrument.marketCode}</strong><span>{item.dataTypes.map((type) => dataLabels[type] ?? type).join(" · ")}</span></li>)}</ul></article>;
}
