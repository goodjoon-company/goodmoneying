# 2026-07-18 P6-6 안전 주문 outbox 검증

## 범위

- 주문하기·주문조회 권한 준비도 증적과 출금 권한 미사용 검증을 DB 계약으로 고정한다.
- 실제 제출 전 `upbit_order_outbox`를 추가하되 submit attempt를 0으로 고정한다.
- shared adapter는 live capability와 권한 준비도를 fail-closed로 평가한다.

## RED

- `uv run pytest tests/shared/test_upbit_safe_order_adapter.py tests/contracts/test_p6_safe_order_adapter_contract.py tests/e2e/test_live_postgres_p6_safe_order_outbox.py -q`
  - 결과: 수집 오류
  - 원인: `goodmoneying_shared.upbit_safe_order_adapter` 모듈이 없었다.

## GREEN

- `uv run pytest tests/shared/test_upbit_safe_order_adapter.py tests/contracts/test_p6_safe_order_adapter_contract.py tests/e2e/test_live_postgres_p6_safe_order_outbox.py -q`
  - 결과: `6 passed, 3 skipped`
  - skip 사유: 일반 환경에서 live PostgreSQL 변수 미설정
- 관련 ruff
  - 1차 결과: E2E import 정렬 오류 1건
  - 수정 후 결과: `All checks passed!`
- 관련 mypy
  - 결과: `Success: no issues found in 4 source files`

## 코드 리뷰 반영

- Critical: `upbit_order_outbox`가 `live_order_identifiers`와 `upbit_api_key_permission_attestations`의 존재만 확인하고 같은 계좌·주문 의도인지 보장하지 못했다.
  - 조치: `validate_p6_upbit_order_outbox_consistency()` 트리거(trigger)를 추가해 `exchange_account_id`, `order_intent_id`, 권한 증적(permission attestation)의 계좌 일치를 강제했다.
- Important: `actor_id` 차단 정규식이 대소문자를 구분해 `CI:` 같은 변형을 통과시킬 수 있었다.
  - 조치: `!~* '^(ci|ai|service):'`로 대소문자 무시 정규식(case-insensitive regex)을 적용하고 E2E 부정 테스트를 추가했다.
- Important: ready outbox가 권한 증적의 만료·권한 상태를 insert 시점에 다시 검증하지 않았다.
  - 조치: ready 상태에서는 주문하기(order), 주문조회(order-read), 출금(withdraw) 미포함, `expires_at > clock_timestamp()`를 트리거(trigger)에서 재검증한다.
- Important: `blocked` outbox가 권한 증적(permission attestation)을 참조할 때 다른 계좌의 증적을 저장할 수 있었다.
  - 조치: `permission_attestation_id IS NOT NULL`이면 `status`와 무관하게 outbox 계좌와 권한 증적 계좌 일치를 트리거(trigger)에서 강제하고 E2E 부정 테스트를 추가했다.
- Important: ready outbox가 연결된 주문 의도(order intent)의 상태를 확인하지 않았다.
  - 조치: ready outbox는 `order_intents.status='approved'`만 허용하도록 DB 트리거(trigger)와 E2E 부정 테스트를 추가했다.

## 전체 회귀 검증

- `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh`
  - 1차 결과: `2 failed, 139 passed`
  - 원인: 신규 P6-6 E2E fixture가 실제 `exchange_accounts` 컬럼 대신 잘못된 `account_alias` 컬럼을 사용했다.
  - 2차 결과: `1 failed, 141 passed`
  - 원인: 재리뷰 반영 E2E helper의 SQL 파라미터 순서가 잘못돼 `blocked_reason`이 JSON 컬럼에 전달됐다.
  - 수정 후 최종 결과: `142 passed`, versions=25, data_rows=1, timezone=UTC, API=200, snapshot=동일, 집계상태=동일
- `uv run ruff check .`
  - 결과: `All checks passed!`
- `uv run mypy apps/api apps/worker packages/shared tests`
  - 결과: `Success: no issues found in 170 source files`
- `uv run pytest -q`
  - 결과: `800 passed, 143 skipped, 1 warning`
  - 경고: 기존 Starlette `httpx` deprecation warning
- `npm test && npm run build`
  - 결과: web test `181 passed`, build 통과
  - 경고: 기존 Vite chunk size warning
