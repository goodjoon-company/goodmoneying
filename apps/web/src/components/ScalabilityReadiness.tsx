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

export function ScalabilityReadiness() {
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
