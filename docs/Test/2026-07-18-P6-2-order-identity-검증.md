# 2026-07-18 P6-2 order-test 증적과 live 주문 identifier 검증

## 범위

- order-test 응답 UUID·identifier를 실제 주문 조회·취소 식별자로 사용하지 못하도록 DB 증적 경계를 분리한다.
- live 주문 identifier는 계좌 안정 식별자와 주문 멱등 키에서 결정론적으로 생성하며 Upbit 공식 64자 한도보다 짧아야 한다.
- 새 DB migration과 schema snapshot을 dbmate E2E로 검증한다.

## RED

- `uv run pytest tests/contracts/test_p6_order_identity_contract.py tests/shared/test_live_order_identity.py -q`
  - 결과: 수집 오류
  - 원인: `goodmoneying_shared.live_order_identity` 모듈과 `20260718000900_p6_order_identity_separation.sql` migration이 없었다.

## GREEN

- `uv run pytest tests/contracts/test_p6_order_identity_contract.py tests/shared/test_live_order_identity.py -q`
  - 결과: `4 passed`

## E2E와 회귀

- `uv run pytest tests/contracts/test_p6_order_identity_contract.py tests/shared/test_live_order_identity.py tests/scripts/test_migration_e2e_script.py tests/e2e/test_live_postgres_p6_order_identity.py -q`
  - 중간 결과: `5 passed, 1 skipped`
- `uv run ruff check packages/shared/goodmoneying_shared/live_order_identity.py tests/contracts/test_p6_order_identity_contract.py tests/shared/test_live_order_identity.py tests/e2e/test_live_postgres_p6_order_identity.py tests/scripts/test_migration_e2e_script.py`
  - 결과: `All checks passed!`
- `uv run mypy packages/shared tests/shared tests/contracts tests/scripts`
  - 결과: `Success: no issues found in 82 source files`
- 1차 `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh`
  - 결과: `1 failed, 135 passed`
  - 원인: 테스트의 `requested_at`이 현재보다 미래인 22:00 UTC라 `created_at >= requested_at` 제약을 위반했다.
- 수정 후 `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh`
  - 결과: `dbmate 마이그레이션 E2E 통과: versions=23 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`
  - live PostgreSQL: `136 passed in 42.59s`

## 코드 리뷰 반영

- 지적사항: `upbit_order_test_runs.response_identifier`와 `live_order_identifiers.identifier`의 양방향 재사용 차단, `live_order_identifiers.idempotency_key`와 `order_intents.idempotency_key` 일치 검증, shared utility 입력 앞뒤 공백 처리.
- 반영: `p6_upbit_live_order_identifier()` DB 함수와 `validate_p6_live_order_identifier()`, `validate_p6_order_test_identifier_not_live()` trigger를 추가했다.
- `uv run pytest tests/shared/test_live_order_identity.py tests/contracts/test_p6_order_identity_contract.py tests/e2e/test_live_postgres_p6_order_identity.py -q`
  - 결과: `4 passed, 1 skipped`
- `uv run ruff check tests/shared/test_live_order_identity.py tests/contracts/test_p6_order_identity_contract.py tests/e2e/test_live_postgres_p6_order_identity.py packages/shared/goodmoneying_shared/live_order_identity.py`
  - 결과: `All checks passed!`
- 1차 리뷰 반영 후 `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh`
  - 결과: `1 failed, 135 passed`
  - 원인: 잘못된 live identifier 삽입은 CHECK보다 BEFORE trigger가 먼저 차단한다.
- 테스트 기대 예외 보정 후 `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh`
  - 결과: `dbmate 마이그레이션 E2E 통과: versions=23 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`
  - live PostgreSQL: `136 passed in 36.92s`

## 코드 리뷰 2차 반영

- 지적사항: 순차 trigger 조회만으로는 `READ COMMITTED` 동시 transaction에서 live/test 교차 재사용 경쟁을 닫지 못한다.
- 반영: `upbit_order_identifier_reservations` registry를 추가하고 `(exchange_account_id, identifier)` unique key로 live identifier와 order-test 응답 UUID·identifier를 같은 계좌 namespace에 원자 예약한다.
- 추가 E2E: 두 PostgreSQL 연결을 barrier로 동시에 실행해 live insert와 order-test insert가 같은 identifier를 예약하려 할 때 정확히 하나만 성공하고 registry row가 하나만 남는지 검증한다.
- `uv run pytest tests/shared/test_live_order_identity.py tests/contracts/test_p6_order_identity_contract.py tests/e2e/test_live_postgres_p6_order_identity.py -q`
  - 결과: `4 passed, 2 skipped`
- `uv run ruff check tests/e2e/test_live_postgres_p6_order_identity.py tests/contracts/test_p6_order_identity_contract.py`
  - 결과: `All checks passed!`
- `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh`
  - 결과: `dbmate 마이그레이션 E2E 통과: versions=23 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`
  - live PostgreSQL: `137 passed in 36.70s`

## 코드 리뷰 Minor 반영

- 지적사항: `upbit_order_test_runs` UPDATE 정책이 불명확하면 registry와 증적 원본의 정합성이 애매해질 수 있다.
- 반영: `reject_p6_order_test_run_mutation()` trigger로 `upbit_order_test_runs` UPDATE·DELETE를 거부해 order-test 증적을 append-only로 고정했다.
- `uv run pytest tests/shared/test_live_order_identity.py tests/contracts/test_p6_order_identity_contract.py tests/e2e/test_live_postgres_p6_order_identity.py -q`
  - 결과: `4 passed, 2 skipped`
- `uv run ruff check tests/e2e/test_live_postgres_p6_order_identity.py tests/contracts/test_p6_order_identity_contract.py`
  - 결과: `All checks passed!`
- `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh`
  - 결과: `dbmate 마이그레이션 E2E 통과: versions=23 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`
  - live PostgreSQL: `137 passed in 37.07s`

## 안전 경계

- 실제 Upbit 주문·취소·private WebSocket 호출 없음
- order-test 증적은 조회·취소 허용 불가로만 저장
- live identifier는 예약 테이블에만 추가하고 실제 주문 제출 outbox는 아직 만들지 않음
