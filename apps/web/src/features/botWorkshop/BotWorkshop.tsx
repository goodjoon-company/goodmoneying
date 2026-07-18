const promotionStages: {
  id: string;
  label: string;
  summary: string;
  description: string;
  locked?: boolean;
}[] = [
  {
    id: "draft",
    label: "draft",
    summary: "설계 중",
    description: "전략과 포트폴리오 배분을 검토합니다."
  },
  {
    id: "backtest_ready",
    label: "backtest_ready",
    summary: "백테스트 준비",
    description: "재현 가능한 backtest 결과와 입력 근거를 고정합니다."
  },
  {
    id: "paper",
    label: "paper",
    summary: "paper rehearsal",
    description: "risk evaluation을 통과한 주문 의도만 paper execution job으로 연결합니다."
  },
  {
    id: "shadow",
    label: "shadow",
    summary: "shadow rehearsal",
    description: "실시간 신호를 관찰하되 외부 실행을 하지 않습니다."
  },
  {
    id: "live_ready",
    label: "live_ready",
    summary: "안전 잠금",
    description: "승인 checklist가 충족되어도 일반 UI에서 활성화할 수 없습니다.",
    locked: true
  },
  {
    id: "live",
    label: "live",
    summary: "안전 잠금",
    description: "이 화면에서는 상태 확인만 제공하며 활성화 기능이 없습니다.",
    locked: true
  }
] as const;

const pipeline = [
  "order intent",
  "risk evaluation",
  "paper execution job",
  "reconciliation",
  "position projection"
];

export function BotWorkshop() {
  return (
    <section className="bot-workshop" aria-labelledby="bot-workshop-title">
      <header className="bot-workshop-title-row">
        <div>
          <p className="eyebrow">P5-6 · Bot Workshop</p>
          <h2 id="bot-workshop-title">Bot Workshop</h2>
          <p>Portfolio allocation에서 paper 운영으로 이어지는 상태와 증적을 읽기 전용으로 확인합니다.</p>
        </div>
        <span className="bot-workshop-read-only">읽기 전용</span>
      </header>

      <section className="bot-workshop-panel bot-workshop-connection" aria-label="포트폴리오 배분과 paper 운영 연결">
        <p className="bot-workshop-kicker">운영 연결</p>
        <strong>Portfolio allocation → paper 운영 준비</strong>
        <span>승인된 배분 기준은 paper 실행의 입력으로만 준비됩니다. 이 화면은 주문을 만들거나 활성화하지 않습니다.</span>
      </section>

      <section className="bot-workshop-panel" aria-label="봇 승격 단계">
        <div className="panel-heading"><h3>봇 승격 단계</h3><span>상태 확인</span></div>
        <ol className="bot-workshop-stages">
          {promotionStages.map((stage) => (
            <li key={stage.id} className={stage.locked ? "is-locked" : ""}>
              <code>{stage.label}</code>
              <strong>{`${stage.label} · ${stage.summary}`}</strong>
              {stage.locked ? <small>일반 UI action 없음</small> : null}
              <span>{stage.description}</span>
            </li>
          ))}
        </ol>
        <p className="bot-workshop-lock-note" role="status" aria-label="live 안전 잠금">
          <strong>live-ready · live 잠금</strong> · 일반 UI action으로 live_ready 또는 live를 활성화할 수 없습니다.
        </p>
      </section>

      <section className="bot-workshop-panel" aria-label="주문 파이프라인">
        <div className="panel-heading"><h3>주문 파이프라인</h3><span>paper/shadow 범위</span></div>
        <ol className="bot-workshop-pipeline">
          {pipeline.map((step, index) => (
            <li key={step}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{step}</strong>
            </li>
          ))}
        </ol>
      </section>

      <div className="bot-workshop-grid">
        <section className="bot-workshop-panel" aria-label="킬스위치와 승인 checklist">
          <div className="panel-heading"><h3>kill switch</h3><span className="bot-workshop-status">armed</span></div>
          <p><strong>global kill switch</strong>가 arm되면 신규 주문 의도와 이미 승인된 paper job의 claim·completion을 차단합니다.</p>
          <ul className="bot-workshop-checklist" aria-label="승인 checklist">
            <li>Portfolio allocation 검토 완료</li>
            <li>위험 한도와 차단 조건 확인</li>
            <li>reconciliation 증적 확인</li>
          </ul>
        </section>

        <section className="bot-workshop-panel" aria-label="운영 안전 경계">
          <div className="panel-heading"><h3>안전 경계</h3><span>외부 실행 없음</span></div>
          <p>Bot Workshop은 P5 paper/shadow 운영 rehearsal을 보여주는 화면이며, 외부 주문 제출 기능을 포함하지 않습니다.</p>
          <dl className="bot-workshop-boundary">
            <div><dt>허용</dt><dd>상태 확인, checklist 확인, 대사 증적 확인</dd></div>
            <div><dt>차단</dt><dd>실제 주문 생성, live 전이, 외부 계좌 실행</dd></div>
          </dl>
        </section>
      </div>

      <section className="bot-workshop-panel" aria-label="대사 증적">
        <div className="panel-heading"><h3>reconciliation evidence</h3><span>운영자 확인</span></div>
        <div className="bot-workshop-evidence">
          <article><span>reconciliation_mismatch</span><strong>대조 필요</strong><p>의도, paper 실행 작업, 포지션 투영 간 불일치 여부를 검토합니다.</p></article>
          <article><span>outcome_unknown</span><strong>결과 미확정</strong><p>확정되지 않은 결과는 분리해 확인하며 자동 진행하지 않습니다.</p></article>
        </div>
      </section>
    </section>
  );
}
