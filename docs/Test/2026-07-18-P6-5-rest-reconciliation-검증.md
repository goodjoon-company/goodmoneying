# 2026-07-18 P6-5 REST snapshot 대사 검증

## 범위

- Upbit REST 주문 snapshot을 내부 대사 입력으로 정규화한다.
- terminal snapshot만 기존 `reconcile_exchange_order()`로 원장에 적용한다.
- 진행 중 snapshot은 원장을 변경하지 않고 observe-only로 처리한다.
- 실제 REST 호출, 주문 제출, 취소, private WebSocket 연결은 추가하지 않는다.

## RED

- `uv run pytest tests/shared/test_upbit_rest_reconciliation.py tests/e2e/test_live_postgres_p6_rest_reconciliation.py -q`
  - 결과: 수집 오류 2건
  - 원인: `goodmoneying_shared.upbit_rest_reconciliation` 모듈이 없었다.
- `uv run pytest tests/contracts/test_p6_rest_reconciliation_contract.py -q`
  - 결과: `1 failed, 1 passed`
  - 원인: `docs/contracts/upbit/rest-order-reconciliation.md` 계약 문서가 없었다.

## GREEN

- `uv run pytest tests/shared/test_upbit_rest_reconciliation.py tests/e2e/test_live_postgres_p6_rest_reconciliation.py -q`
  - 결과: `4 passed, 1 skipped`
  - skip 사유: live PostgreSQL 환경 변수 미설정
- `uv run pytest tests/contracts/test_p6_rest_reconciliation_contract.py -q`
  - 결과: `2 passed`
- `uv run pytest tests/shared/test_upbit_rest_reconciliation.py tests/contracts/test_p6_rest_reconciliation_contract.py tests/contracts/test_p6_myorder_contract.py tests/shared/test_upbit_myorder.py -q`
  - 결과: `12 passed`
- `uv run ruff check packages/shared/goodmoneying_shared/upbit_rest_reconciliation.py tests/shared/test_upbit_rest_reconciliation.py tests/contracts/test_p6_rest_reconciliation_contract.py tests/e2e/test_live_postgres_p6_rest_reconciliation.py`
  - 1차 결과: ruff 오류 2건
  - 수정 후 결과: `All checks passed!`
- `uv run mypy packages/shared/goodmoneying_shared/upbit_rest_reconciliation.py tests/shared/test_upbit_rest_reconciliation.py tests/contracts/test_p6_rest_reconciliation_contract.py tests/e2e/test_live_postgres_p6_rest_reconciliation.py`
  - 1차 결과: redundant cast 오류 1건
  - 수정 후 결과: `Success: no issues found in 4 source files`

## 리뷰 보강

- 코드 리뷰 결과: Critical 없음, Important 3건
  - `smp_type`, `state`, `paidFee`, `tradesCount` 증거 누락
  - 허용 주문조회 endpoint allow-list 미검증
  - malformed `trades` 입력이 계약 예외로 닫히지 않음
- 보강 후 `uv run pytest tests/shared/test_upbit_rest_reconciliation.py -q`
  - 결과: `7 passed`
- 보강 후 `uv run pytest tests/shared/test_upbit_rest_reconciliation.py tests/e2e/test_live_postgres_p6_rest_reconciliation.py tests/contracts/test_p6_rest_reconciliation_contract.py -q`
  - 결과: `9 passed, 1 skipped`
- 보강 후 관련 ruff
  - 결과: `All checks passed!`
- 보강 후 관련 mypy
  - 결과: `Success: no issues found in 4 source files`

## 전체 회귀 검증

- `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh`
  - 최종 결과: `138 passed`, versions=24, data_rows=1, timezone=UTC, API=200, snapshot=동일, 집계상태=동일
- `uv run ruff check .`
  - 결과: `All checks passed!`
- `uv run mypy apps/api apps/worker packages/shared tests`
  - 결과: `Success: no issues found in 166 source files`
- `uv run pytest -q`
  - 최종 결과: `794 passed, 140 skipped, 1 warning`
  - 경고: 기존 Starlette `httpx` deprecation warning
- `npm test && npm run build`
  - 결과: web test `181 passed`, build 통과
  - 경고: 기존 Vite chunk size warning
