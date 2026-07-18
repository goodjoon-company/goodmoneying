# 2026-07-18 P5-4 risk evaluation 인계

## 변경 요약

- `RiskEvaluationWorker`를 추가해 `created` paper/shadow 주문 의도를 평가한다.
- `PostgresPortfolioBotStore.evaluate_next_order_intent_risk()`를 추가해 주문 의도, 봇, 포트폴리오, 위험 한도, kill switch 최신 상태를 하나의 PostgreSQL transaction에서 판정한다.
- 승인 결과는 `approved`, `policy_approved`, paper 실행 queue 연결로 기록한다.
- 거부 결과는 `risk_rejected`, `limit_rejected|kill_switch_rejected` 위험 이벤트로 기록한다.
- `PaperExecutionWorker` claim과 completion 경계에서 활성 kill switch를 다시 검사해 arm 이후 신규 모의 체결을 차단한다.
- DB migration은 추가하지 않고 P5-1·P5-3 계약을 소비했다.
- P5 live PostgreSQL 테스트를 DB migration E2E 묶음에 포함했고, 기존 P5-1 backtest fixture를 현행 P4 계약에 맞게 보정했다.
- 리뷰 결과에 따라 kill switch race를 `kill_switches` table lock으로 막고, blocked completion의 `retry_wait` 전이가 rollback되지 않게 수정했다.
- `risk_evaluation_collection_worker`, local `dev.sh`, local compose, prod-home compose·healthcheck에 risk worker 실행 경로를 추가했다.
- 재리뷰 결과에 따라 kill switch completion 차단은 paper job 시도 예산을 소비하지 않게 환급하고, 위험 평가는 실제 paper fill과 같은 명목 금액 계산을 사용하도록 보정했다.

## 안전 경계

- P5-4는 실제 Upbit 주문 제출·취소·조회, private WebSocket, 주문 테스트 API, live-ready/live 전이를 추가하지 않았다.
- `max_order_notional` 외 활성 위험 한도는 P5-4에서 계산 증거가 없으면 승인하지 않는다.
- account scope kill switch와 live capability는 P6 이후 범위로 유지한다.

## 사용한 절차

- `goodjoon-workflow:good-tdd`: RED 테스트 후 최소 구현을 진행했다.
- `goodjoon-workflow:good-spec`: 위험 평가 transaction과 kill switch 차단 경계를 문서와 계약 테스트에 맞췄다.
- `goodjoon-workflow:good-sync`: Product, Architecture, Task, DB README, Test, History를 갱신했다.
- `subagent-driven-development`: 읽기 전용 subagent로 P5-4 갭과 kill switch/paper job 차단 누락 위험을 확인했다.

## 검증

- 상세 증적: `docs/Test/2026-07-18-P5-4-risk-evaluation-검증.md`
- DB migration E2E: `130 passed`, `versions=21`, `API=200`, `snapshot=동일`
- 재리뷰 후 DB migration E2E: `132 passed`, `versions=21`, `API=200`, `snapshot=동일`

## 후속 범위

- P5-5 reconciliation과 position projection 보강
- P5-6 Bot Workshop UI와 E2E
