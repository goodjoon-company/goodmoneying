# 2026-06-30 fixture 후보 차단과 live 후보 복구 검증

## 범위

- 런타임(runtime)에서 fixture 업비트 클라이언트가 암묵적으로 선택되지 않는지 검증한다.
- PostgreSQL 후보 유니버스(Candidate Universe)에 `KRW-GM###`/`굿머니코인` fixture 후보가 저장되지 않는지 검증한다.
- 실제 live 업비트 후보 유니버스로 로컬 DB를 복구하고 API 응답에서 fixture 후보가 사라졌는지 확인한다.
- E2E(End-to-End)는 demo fixture API 대신 로컬 PostgreSQL 기반 API로 실행한다.

## 명령 결과

| 명령 또는 방법 | 결과 | 비고 |
| --- | --- | --- |
| `uv run pytest tests/worker/test_collector_behavior.py::test_worker_rejects_implicit_fixture_client tests/api/test_operations_api.py::test_demo_data_repository_is_disabled tests/shared/test_postgres_repository_schema.py::test_postgres_repository_rejects_fixture_candidate_entries_before_connect tests/scripts/test_environment_files.py::test_local_env_sample_lists_code_configurable_runtime_keys -q` | RED 확인 | 3 failed, 1 passed. 기존 코드가 fixture 기본값, demo fixture 저장소, PostgreSQL fixture 후보 쓰기를 허용함을 확인 |
| `uv run pytest tests/shared/test_repository_behavior.py::test_candidate_universe_defaults_to_top_50_active_targets -q` | RED 확인 | `collection_runs`에 후보 갱신 이력이 없어 `IndexError` 발생 |
| `uv run pytest tests/worker/test_collector_behavior.py::test_worker_rejects_implicit_fixture_client tests/worker/test_collector_behavior.py::test_worker_uses_live_client_when_live_profile_is_enabled tests/api/test_operations_api.py::test_demo_data_repository_is_disabled tests/shared/test_postgres_repository_schema.py::test_postgres_repository_rejects_fixture_candidate_entries_before_connect tests/shared/test_repository_behavior.py::test_candidate_universe_defaults_to_top_50_active_targets tests/scripts/test_environment_files.py::test_local_env_sample_lists_code_configurable_runtime_keys -q` | 통과 | 6 passed |
| `PYTHONPATH=apps/api:apps/worker:packages/shared .venv/bin/python - <<'PY' ... worker.refresh_candidate_universe() ... PY` | 통과 | `refreshed=100`, `ranked_at=2026-06-30T09:50:18+09:00`, `fixture_count=0`, 상위 10개가 실제 KRW 마켓으로 교체됨 |
| `./dev.sh app restart api`, `./dev.sh app restart realtime-collection-worker`, `./dev.sh app restart backfill-collection-worker` | 통과 | 새 런타임 코드 반영 |
| `./dev.sh status` | 통과 | API, web, realtime worker, backfill worker 실행 확인 |
| `curl http://127.0.0.1:8000/v1/candidate-universe ...` | 통과 | `fixtureCount=0`, `rankedAt=2026-06-30T09:50:18+09:00` |
| `curl http://127.0.0.1:8000/v1/market-list ...` | 통과 | `count=100`, `fixtureCount=0` |
| DB 조회: `collection_runs where run_type='candidate_refresh'` | 통과 | `candidate_refresh/candidate_universe/succeeded`, `started_at=2026-06-30T09:50:18+09:00` |
| `uv run ruff check .` | 통과 | All checks passed |
| `uv run mypy apps/api apps/worker packages/shared tests` | 통과 | Success, 35 source files |
| `uv run pytest -q` | 통과 | 145 passed, 1 warning |
| `npm test` | 통과 | 7 files, 38 tests passed |
| `npm run build` | 통과 | TypeScript build와 Vite build 성공 |
| `npx playwright test` | 통과 | Chromium E2E 1 passed. demo fixture API 없이 PostgreSQL 기반 API로 실행 |

## 확인된 현재 상태

- `/v1/candidate-universe` 최신 후보 유니버스는 fixture 후보를 포함하지 않는다.
- `/v1/market-list`도 `KRW-GM###` 후보를 포함하지 않는다.
- 후보 유니버스 갱신은 `collection_runs`에 `candidate_refresh/candidate_universe`로 남는다.
- 런타임 환경에서 `GOODMONEYING_LIVE_UPBIT=1`이 없으면 업비트 클라이언트를 만들지 않는다.
- `GOODMONEYING_DEMO_DATA=1` 기반 demo fixture 저장소 경로는 차단됐다.

## 남은 주의점

- 테스트 전용으로 `FixtureUpbitClient`를 직접 주입하는 단위 테스트는 유지한다.
- 기존 과거 검증 문서에는 demo fixture E2E 기록이 남아 있으나, 현재 정책은 이 문서의 검증 결과가 최신이다.
