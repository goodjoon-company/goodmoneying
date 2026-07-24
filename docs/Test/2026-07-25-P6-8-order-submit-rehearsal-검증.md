# 2026-07-25 P6-8 주문 제출 리허설 검증

## 범위

- Upbit `POST /v1/orders` 실제 제출 전 dry-run/rehearsal 증적 계약을 추가한다.
- `ready` outbox, reserved live identifier, permission attestation 일치를 DB에서 강제한다.
- 실제 HTTP 주문 제출, 취소, private WebSocket 연결은 추가하지 않는다.
- 리허설 결과는 응답 UUID·identifier를 저장할 수 없고 live binding으로 해석할 수 없다.

## 공식 문서 재확인

- Upbit 주문 생성은 `POST /v1/orders`이며 주문하기 권한이 필요하다.
- `identifier`는 계정 전체 주문 기준으로 유일하고 최대 64자다.
- `post_only`와 `smp_type`은 동시에 사용할 수 없다.
- order-test는 실제 주문을 생성하지 않으며 테스트 응답 UUID·identifier를 조회·취소에 사용할 수 없다.

## RED

- `uv run pytest tests/shared/test_upbit_order_submit_rehearsal.py -q`
  - 결과: 수집 오류
  - 원인: `goodmoneying_shared.upbit_order_submit_rehearsal` 모듈이 없었다.
- `uv run pytest tests/contracts/test_p6_order_submit_rehearsal_contract.py -q`
  - 결과: `2 failed`
  - 원인: `20260718001300_p6_order_submit_rehearsal.sql`과 `docs/contracts/upbit/order-submit-rehearsal.md`가 없었다.

## GREEN

- `uv run pytest tests/shared/test_upbit_order_submit_rehearsal.py -q`
  - 결과: `2 passed`
- `uv run pytest tests/contracts/test_p6_order_submit_rehearsal_contract.py -q`
  - 결과: `2 passed`
- `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh`
  - 1차 결과: `4 failed, 148 passed`
  - 원인: P6-8 E2E 테스트가 JSONB 값을 plain dict로 재전달해 psycopg adaptation에 실패했다.
  - 조치: P6-8 E2E의 `request_payload` 전달을 `Jsonb(...)`로 수정했다.
- `uv run pytest tests/shared/test_upbit_order_submit_rehearsal.py tests/contracts/test_p6_order_submit_rehearsal_contract.py -q`
  - 결과: `4 passed`
- 알 수 없는 주문 필드가 hash 리허설에서 조용히 버려지는 위험을 발견해 실패 테스트를 추가했다.
  - RED: `uv run pytest tests/shared/test_upbit_order_submit_rehearsal.py -q` → `1 failed, 2 passed`
  - 조치: adapter가 공식 주문 생성 허용 필드 외의 key를 거부하도록 변경했다.
- 코드 리뷰에서 시장가 주문(`ord_type='price'|'market'`)에 `time_in_force`가 통과되는 문제가 발견되어 실패 테스트를 추가했다.
  - RED: `uv run pytest tests/shared/test_upbit_order_submit_rehearsal.py -q` → `1 failed, 3 passed`
  - 조치: 시장가 주문은 `time_in_force`와 함께 리허설할 수 없도록 adapter 검증을 보강했다.
- `uv run pytest tests/shared/test_upbit_order_submit_rehearsal.py tests/contracts/test_p6_order_submit_rehearsal_contract.py -q`
  - 최종 결과: `6 passed`
- 코드 리뷰에서 P6-8 live PostgreSQL E2E가 adapter 산출 payload/hash/query hash를 쓰지 않는 문제가 발견되어 fixture를 분리했다.
  - 1차 결과: `4 failed, 148 passed`
  - 원인: 새 P6-8 fixture의 live identifier idempotency key가 `order_intents.idempotency_key`와 달랐다.
  - 조치: fixture idempotency key를 기존 order intent helper와 일치시켰다.
- `uv run ruff check packages/shared/goodmoneying_shared/upbit_order_submit_rehearsal.py tests/shared/test_upbit_order_submit_rehearsal.py tests/contracts/test_p6_order_submit_rehearsal_contract.py tests/e2e/test_live_postgres_p6_order_submit_rehearsal.py`
  - 1차 결과: 실패
  - 원인: `typing.Mapping` import와 100자 초과 line
  - 수정 후 결과: `All checks passed!`
- `uv run mypy packages/shared/goodmoneying_shared/upbit_order_submit_rehearsal.py tests/shared/test_upbit_order_submit_rehearsal.py tests/contracts/test_p6_order_submit_rehearsal_contract.py tests/e2e/test_live_postgres_p6_order_submit_rehearsal.py`
  - 1차 결과: 실패
  - 원인: E2E helper의 `permission_attestation_id`가 `object`로 추론됐다.
  - 수정 후 결과: `Success: no issues found in 4 source files`
- `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh`
  - 수정 후 최종 결과: `152 passed`, versions=27, data_rows=1, timezone=UTC, API=200, snapshot=동일, 집계상태=동일

## goodjoon-workflow 게이트

- `GOODJOON_WORKFLOW_ALLOW_NO_TASK=1 python /Users/goodjoon/.codex/plugins/cache/goodjoon-codex-tools/goodjoon-workflow/0.3.0/scripts/goodjoon_workflow_gate.py external-skills --root /Users/goodjoon/.codex/plugins/cache/goodjoon-codex-tools/goodjoon-workflow/0.3.0 --state .goodjoon-workflow/external-skills-state.json`
  - 결과: 실패
  - 원인: 로컬 환경에 `python` 명령이 없었다.
- `GOODJOON_WORKFLOW_ALLOW_NO_TASK=1 python3 /Users/goodjoon/.codex/plugins/cache/goodjoon-codex-tools/goodjoon-workflow/0.3.0/workflow/scripts/goodjoon_workflow_gate.py external-skills --root /Users/goodjoon/.codex/plugins/cache/goodjoon-codex-tools/goodjoon-workflow/0.3.0 --state .goodjoon-workflow/external-skills-state.json`
  - 결과: 실패
  - 원인: 번들 캐시에 `harness/config/goodjoon-workflow-harness.json`가 없었다.
  - 대체: 저장소 AGENTS.md, goodjoon-workflow good-tdd/good-spec/good-review/good-sync/good-handoff 지침, DB 계약 테스트, E2E, 코드 리뷰로 게이트를 대체한다.

## 코드 리뷰

- 1차 리뷰
  - Critical: 없음
  - Important:
    1. 시장가 주문(`ord_type='price'|'market'`)에 `time_in_force`가 허용됨
    2. P6-8 live PostgreSQL E2E가 adapter 산출 payload/hash/query hash를 실제 outbox/rehearsal insert에 사용하지 않음
  - 조치: 두 항목 모두 수정하고 RED/GREEN 및 DB E2E를 재실행했다.
- 재리뷰
  - Critical: 없음
  - Important: 없음
  - Minor: 없음
  - 판정: Ready to merge = Yes

## 전체 회귀 검증

- `uv run ruff check .`
  - 결과: `All checks passed!`
- `uv run mypy apps/api apps/worker packages/shared tests`
  - 결과: `Success: no issues found in 178 source files`
- `uv run pytest -q`
  - 결과: `810 passed, 153 skipped, 1 warning`
  - 경고: 기존 Starlette `httpx` deprecation warning
- `npm test && npm run build`
  - 결과: web test `181 passed`, build 통과
  - 경고: 기존 Vite chunk size warning
- `git diff --check`
  - 결과: 통과
