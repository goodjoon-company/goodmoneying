# 2026-07-18 P5-5 reconciliation 인계

## 변경 요약

- `20260718000800_p5_reconciliation_runs.sql` migration을 추가해 paper/shadow 내부 대사 실행 증적을 append-only로 저장한다.
- `PostgresPortfolioBotStore.reconcile_exchange_order()`를 추가해 `exchange_orders`와 `order_intents`를 잠근 뒤 reconciliation fill과 position projection을 같은 transaction에서 처리한다.
- 동일 `run_key`와 동일 request hash는 멱등으로 흡수하고, 같은 key의 다른 payload는 `ReconciliationIdempotencyConflictError`로 거부한다.
- 기존 fill sequence와 관측 fill이 불일치하면 projection을 변경하지 않고 `reconciliation_mismatch` 위험 이벤트를 남긴다.
- `outcome_unknown` 주문은 관측 fill이 있을 때만 reconciliation fill과 position projection으로 복구하며, 관측 불명 상태는 `outcome_unknown` 위험 이벤트로 남긴다.
- paper completion과 reconciliation이 같은 position scope advisory lock을 사용하도록 `_upsert_position_projection()`에 scope lock을 추가했다.
- 코드 리뷰 결과에 따라 reconciliation run-key advisory lock, fill sequence 선검증, late fill mismatch 정책을 추가해 동시 멱등·원자성·순서 의존성을 보강했다.
- Product, Architecture, Task, DB README, schema snapshot, DB migration E2E 목록을 현행화했다.

## 안전 경계

- P5-5는 private 계좌 조회, private WebSocket, 주문 테스트 API, 실제 Upbit 주문 제출·취소·조회 경로를 추가하지 않는다.
- P5-5의 `reconciliation_runs`는 내부 paper/shadow 원장 대사 증적이며, P6 private 계좌 대사의 외부 관측 권위와 분리한다.
- reconciliation mismatch는 projection을 낙관적으로 고치지 않고 위험 이벤트로 남긴다.

## 검증

- 상세 증적: `docs/Test/2026-07-18-P5-5-reconciliation-검증.md`
- DB migration E2E: `135 passed`, `versions=22`, `API=200`, `snapshot=동일`
- 최종 코드 리뷰: Critical/Important 없음, 승인

## 후속 범위

- P5-6 Bot Workshop UI와 E2E
- P6 private 주문·체결·잔고 대사와 live-ready 검증
