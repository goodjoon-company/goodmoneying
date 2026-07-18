# 2026-07-18 P5-5 reconciliation 검증

## 범위

- paper/shadow 내부 `exchange_orders` 대사 실행 증적을 `reconciliation_runs`에 append-only로 기록한다.
- 동일 `exchange_order_id`, `run_key`, request hash는 멱등으로 흡수한다.
- `outcome_unknown` 주문에 관측 fill이 있으면 `order_fills(fill_source='reconciliation')`를 append하고 같은 transaction에서 `position_projections`를 갱신한다.
- 기존 fill sequence와 관측 fill이 불일치하면 projection을 바꾸지 않고 `reconciliation_mismatch` 위험 이벤트를 기록한다.
- 실제 Upbit 주문, private WebSocket, 주문 테스트 API는 사용하지 않는다.

## RED

- `uv run pytest tests/contracts/test_p5_reconciliation_contract.py -q`
  - 결과: `3 failed, 1 passed`
  - 원인: `reconcile_exchange_order()` Store 메서드, `ReconciliationIdempotencyConflictError`, P5-5 문서 경계가 아직 없음

## GREEN

- `uv run pytest tests/contracts/test_p5_reconciliation_contract.py -q`
  - 결과: `4 passed`
- `uv run ruff check packages/shared/goodmoneying_shared/portfolio_bot_store.py tests/contracts/test_p5_reconciliation_contract.py tests/e2e/test_live_postgres_reconciliation.py`
  - 결과: `All checks passed!`
- `uv run mypy packages/shared/goodmoneying_shared/portfolio_bot_store.py tests/e2e/test_live_postgres_reconciliation.py`
  - 결과: `Success: no issues found in 2 source files`

## DB migration E2E

- `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh`
  - 결과: `134 passed in 38.22s`
  - 요약: `versions=22 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`
  - schema snapshot: `docs/contracts/db/schema.sql` 갱신

## 코드 리뷰 반영

- 리뷰 지적: 다중 fill 대사에서 뒤쪽 mismatch가 발견되면 앞쪽 새 fill과 position 변경이 먼저 커밋될 수 있었다.
  - 조치: 모든 관측 fill을 sequence 순으로 정렬하고 기존 fill·late fill·중복 sequence를 먼저 검증한 뒤, mismatch가 없을 때만 append와 projection 갱신을 수행한다.
- 리뷰 지적: 동일 run의 동시 요청이 기존 run 조회를 동시에 통과하면 unique 제약 위반으로 실패할 수 있었다.
  - 조치: `reconciliation-run:{exchange_order_id}:{run_key}` advisory lock을 먼저 획득한 뒤 기존 run을 재조회한다.
- 리뷰 지적: 관측 fill 입력 배열 순서에 따라 projection 결과가 달라질 수 있었다.
  - 조치: 관측 fill을 `fillSequence` 기준으로 정렬하고, 이미 더 높은 sequence가 반영된 상태에서 낮은 sequence를 새로 받으면 late fill mismatch로 기록한다.
- 추가 E2E: 기존 higher sequence가 있는 상태에서 앞쪽 새 fill과 뒤쪽 mismatch가 함께 들어와도 신규 fill과 projection이 부분 반영되지 않는지 검증했다.
- `uv run ruff check packages/shared/goodmoneying_shared/portfolio_bot_store.py tests/contracts/test_p5_reconciliation_contract.py tests/e2e/test_live_postgres_reconciliation.py`
  - 결과: `All checks passed!`
- `uv run mypy packages/shared/goodmoneying_shared/portfolio_bot_store.py tests/e2e/test_live_postgres_reconciliation.py`
  - 결과: `Success: no issues found in 2 source files`
- `uv run pytest tests/contracts/test_p5_reconciliation_contract.py -q`
  - 결과: `4 passed`
- `tests/e2e/run_dbmate_migration_e2e.sh`
  - 결과: `135 passed in 40.63s`
  - 요약: `versions=22 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`

## 전체 회귀와 빌드

- `uv run ruff check .`
  - 결과: `All checks passed!`
- `uv run mypy apps/api apps/worker packages/shared tests`
  - 결과: `Success: no issues found in 151 source files`
- `uv run pytest -q`
  - 결과: `767 passed, 136 skipped, 1 warning in 60.73s`
- `npm test`
  - 결과: `28 passed`, `178 passed`
- `npm run build`
  - 결과: 성공
  - 비고: 기존 Vite chunk size warning 유지
- `docker compose config`와 prod-home compose YAML load
  - 결과: 통과, `compose yaml ok`
- `git diff --check`
  - 결과: 통과
- 최종 코드 리뷰
  - 결과: 이전 Important 3건 모두 해결, 새 Critical/Important 없음, 승인
  - Minor: 동시 멱등 경로는 소스 계약 검사로 고정되어 있으며, 향후 실제 두 연결 기반 concurrent E2E를 추가하면 더 견고함

## 후속 검증

원격 CI 결과는 커밋·푸시 후 추가 기록한다.
