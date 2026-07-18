# 2026-07-18 P4-6 백테스트 결과 pagination 검증

## 범위

- 체결 결과 pagination API
- 자산곡선 결과 pagination API
- run 문맥·sequence 상한·HMAC cursor 검증
- Web API client 함수

## 검증 결과

| 명령 | 결과 | 근거 |
|---|---|---|
| `uv run pytest tests/contracts/test_api_contract.py tests/contracts/test_p4_backtest_api_contract.py tests/api/test_backtest_runs_api.py tests/shared/test_backtest_result_pagination.py -q` | PASS | `25 passed, 1 warning in 1.80s` |
| `npm --workspace apps/web run test -- api.test.ts` | PASS | `1 passed`, `13 passed` |
| `uv run ruff check packages/shared/goodmoneying_shared/backtest_store.py apps/api/goodmoneying_api/main.py apps/api/goodmoneying_api/schemas.py tests/shared/test_backtest_result_pagination.py tests/api/test_backtest_runs_api.py tests/contracts/test_p4_backtest_api_contract.py` | PASS | `All checks passed!` |
| `uv run ruff check .` | PASS | `All checks passed!` |
| `uv run mypy apps/api apps/worker packages/shared tests` | PASS | `Success: no issues found in 134 source files` |
| `uv run pytest -q` | PASS | `722 passed, 124 skipped, 1 warning in 61.54s` |
| `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh` | PASS | `123 passed in 40.09s`, `versions=17`, `timezone=UTC`, `API=200`, `snapshot=동일`, `집계상태=동일` |
| `npm test` | PASS | `28 passed`, `176 passed` |
| `npm run build` | PASS | Vite build 성공, 기존 chunk size 경고만 출력 |
| `npx playwright test tests/e2e/p4-backtest-lab.spec.ts` | PASS | `1 passed (4.2s)` |

## RED/GREEN 기록

- RED: `list_run_trades`, `list_run_equity_points`, 새 OpenAPI path와 response schema가 없어 import/계약 테스트가 실패했다.
- GREEN: Store pagination, FastAPI endpoint, OpenAPI schema, Web client 함수를 추가했다.
- 보정: P4-5 이후 pending/running run은 아직 `result_hash`가 없을 수 있으므로 `resultHash`를 nullable로 고정하고 API 회귀 테스트를 추가했다.
