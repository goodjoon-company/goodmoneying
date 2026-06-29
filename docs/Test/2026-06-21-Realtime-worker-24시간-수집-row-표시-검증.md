# 2026-06-21 Realtime worker 24시간 수집 row 표시 검증

Date: 2026-06-21
Related Task: `docs/Task/M3.md`
Environment: macOS local, Python 3.14 가상환경(Virtual Environment), Node.js workspace, Playwright Chromium

## 검증 대상

- `/v1/dashboard/summary`의 `workerStatus.realtime.collectedRowCount24h`
- OpenAPI 계약(Contract)의 `RealtimeWorkerStatus` 필드
- 운영 상태 worker 현황 패널의 `24시간 수집` 표시
- E2E(End-to-End) 시나리오의 worker 현황 패널 검증

## 실행 명령

| 명령 | 결과 | 메모 |
|---|---|---|
| `.venv/bin/python -m pytest tests/api/test_operations_api.py::test_dashboard_summary_exposes_collection_worker_status tests/contracts/test_api_contract.py::test_openapi_contract_exposes_m2_collection_dashboard_view_model -q` | 통과 | API 응답과 OpenAPI required field 검증 |
| `npm --workspace apps/web run test -- src/App.test.tsx -t "운영 상태는 코인별 실시간 수집"` | 통과 | worker 현황 패널의 24시간 수집 표시 검증 |
| `.venv/bin/python -m pytest tests/api/test_operations_api.py tests/contracts/test_api_contract.py tests/shared/test_repository_behavior.py -q` | 통과 | 32 passed, 1 warning |
| `npm --workspace apps/web run test -- src/App.test.tsx src/api.test.ts src/useOperationsConsole.test.tsx` | 통과 | 18 passed |
| `.venv/bin/python -m ruff check .` | 통과 | All checks passed |
| `.venv/bin/python -m mypy apps/api apps/worker packages/shared tests` | 통과 | Success |
| `.venv/bin/python -m pytest -q` | 통과 | 97 passed, 1 warning |
| `npm test -- --run` | 통과 | 31 passed, npm `--run` 경고 있음 |
| `npm run build` | 통과 | TypeScript 빌드와 Vite 빌드 통과 |
| `npm run e2e` | 통과 | Playwright 1 passed |

## 미검증 항목

- 장시간 브라우저를 열어 두고 15초 폴링마다 수치가 증가하는지 수동 관찰하지 않았다.

## 수동 검증

- `./dev.sh app restart api && ./dev.sh app restart web`로 로컬 API와 웹을 새 코드로 재시작했다.
- `http://127.0.0.1:8000/v1/dashboard/summary` 실제 응답에서 `workerStatus.realtime.collectedRowCount24h=427700`을 확인했다.
- 같은 응답의 진단(diagnostics)에 `24시간 수집 row = 427,700 rows`가 포함됨을 확인했다.

## 결론

Realtime worker의 최근 24시간 실시간 수집 행(row) 수가 API 계약과 운영 상태 화면에 연결됐다.
