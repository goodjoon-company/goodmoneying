# 2026-07-18 P6-7 live 주문 결합 검증

## 범위

- Upbit live 주문 UUID·identifier를 내부 `exchange_orders`와 결합하는 DB 계약을 추가한다.
- live `exchange_orders`가 binding 없이 커밋되지 않도록 지연 제약 트리거(deferrable constraint trigger)를 추가한다.
- Upbit 주문 UUID는 표준 UUID 형식으로 제한한다.
- 실제 주문 제출, 취소, private WebSocket 연결은 추가하지 않는다.
- order-test 응답 식별자는 live 주문 결합에 사용할 수 없게 한다.

## RED

- `uv run pytest tests/shared/test_upbit_live_order_binding.py tests/contracts/test_p6_live_order_binding_contract.py tests/e2e/test_live_postgres_p6_live_order_binding.py -q`
  - 결과: 수집 오류
  - 원인: `goodmoneying_shared.upbit_live_order_binding` 모듈이 없었다.

## GREEN

- `uv run pytest tests/shared/test_upbit_live_order_binding.py tests/contracts/test_p6_live_order_binding_contract.py tests/e2e/test_live_postgres_p6_live_order_binding.py -q`
  - 1차 결과: `1 failed, 3 passed, 3 skipped`
  - 원인: 테스트 fixture identifier가 `gm1_` + 52자 형식을 만족하지 않았다.
  - 수정 후 결과: `4 passed, 6 skipped`
  - skip 사유: 일반 환경에서 live PostgreSQL 변수 미설정
- 관련 ruff
  - 결과: `All checks passed!`
- 관련 mypy
  - 결과: `Success: no issues found in 4 source files`
- `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh`
  - 1차 결과: `2 failed, 143 passed`
  - 원인: 테스트 시각이 DB `clock_timestamp()`보다 미래라 `observed_at <= bound_at`, `created_at >= requested_at` 제약을 위반했다.
  - 2차 결과: `3 failed, 142 passed`
  - 원인: live `exchange_orders`가 같은 transaction 끝에서 binding을 요구하는 지연 제약 트리거(deferrable constraint trigger)에 맞춰 E2E fixture를 같은 transaction으로 구성하지 않았다.
  - 코드 리뷰 결과: Critical 1건(`live_order_identifiers.status` 미검증), Important 1건(binding 이후 `exchange_orders` 변경 불변식 미검증)
  - 조치: binding 시 `live_order_identifiers.status='reserved'`를 강제하고, binding 이후 live `exchange_orders.order_intent_id`와 `simulated_order_key` 변경을 지연 제약 트리거(deferrable constraint trigger)에서 재검증했다.
  - 수정 후 최종 결과: `148 passed`, versions=26, data_rows=1, timezone=UTC, API=200, snapshot=동일, 집계상태=동일

## 전체 회귀 검증

- `uv run ruff check .`
  - 결과: `All checks passed!`
- `uv run mypy apps/api apps/worker packages/shared tests`
  - 결과: `Success: no issues found in 174 source files`
- `uv run pytest -q`
  - 결과: `804 passed, 149 skipped, 1 warning`
  - 경고: 기존 Starlette `httpx` deprecation warning
- `npm test && npm run build`
  - 결과: web test `181 passed`, build 통과
  - 경고: 기존 Vite chunk size warning
