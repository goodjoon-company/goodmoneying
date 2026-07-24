# 2026-07-25 P6-9 live 주문 대사 적용 인계

## 변경 요약

- `20260718001400_p6_live_reconciliation_application.sql` migration으로 `upbit_live_reconciliation_applications` append-only 증적을 추가했다.
- live 적용 증적은 `upbit_live_exchange_order_bindings`, `reconciliation_runs`, REST snapshot evidence의 UUID·identifier·state·source endpoint 일치를 DB에서 강제한다.
- live `reconciliation_runs(status='succeeded')`는 같은 transaction 안의 application 증적 없이는 커밋될 수 없도록 지연 제약(deferrable constraint trigger)을 추가했다.
- `upbit_live_reconciliation.py` adapter는 이미 수신한 REST snapshot과 live binding snapshot을 비교하고, terminal snapshot만 원자적 store 메서드에 전달한다.
- `PostgresPortfolioBotStore.apply_upbit_live_reconciliation_application()`은 원장 대사와 live application 기록을 같은 DB transaction에서 처리한다.

## 안전 경계

- 실제 REST 호출 없음
- `POST /v1/orders` 주문 제출 없음
- 주문 취소 없음
- private WebSocket 연결 없음
- 동일 주문 재제출 금지(`can_resubmit=false`)
- 실제 요청·취소 증적은 `actual_request_sent=false`, `actual_order_cancel_sent=false`만 허용

## 검증

- 상세 증적: [2026-07-25-P6-9-live-reconciliation-검증.md](../Test/2026-07-25-P6-9-live-reconciliation-검증.md)
- 마이그레이션 E2E: `155 passed`, `versions=28`, API `200`, schema snapshot 동일
- 정적 검증: ruff 통과, mypy 통과

## 후속 작업

- 실제 submit worker는 운영자 승인 gate, kill switch, 권한 증적, 리허설 증적을 모두 통과하는 별도 단계에서 구현한다.
