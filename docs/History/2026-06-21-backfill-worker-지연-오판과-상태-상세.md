# 2026-06-21 backfill worker 지연 오판과 상태 상세

## 변경 요약

- 백필 수집 워커(Backfill Collection Worker)가 긴 백필 작업 중에도 진행 지점마다 heartbeat를 기록하도록 수정했다.
- 백필 대상 처리 시작 시 target 상태를 `running`으로 기록해 `동작중 코인`이 실제 처리 대상을 관측할 수 있게 했다.
- `DashboardSummary.workerStatus`에 worker 동작 진단 정보(`diagnostics`)를 추가했다.
- worker 상태 라벨을 클릭하면 마지막 heartbeat, 마지막 수집, 오류율, 동작 중 코인 수를 확인하는 레이어 팝업을 표시한다.

## 원인

백필 worker 프로세스는 실행 중이었지만 `run_backfill_once()`가 오래 실행되는 동안 heartbeat가 시작 전후에만 기록됐다. 대시보드는 마지막 heartbeat가 30초를 넘으면 `지연`으로 판정하므로, 실제 작업 중인 worker도 지연으로 표시됐다.
또한 target 상태가 fetch 이후에야 `running`으로 바뀌어 실제 처리 중인 대상 수가 0으로 보일 수 있었다.

## 검증

- `uv run ruff check .`
- `uv run mypy apps/api apps/worker packages/shared tests`
- `uv run pytest -q`
- `npm test`
- `npm run build`
- `npm run e2e`
- `git diff --check`
- `uv run pytest tests/contracts/test_api_contract.py tests/api/test_operations_api.py tests/worker/test_collector_behavior.py -q`
- `npm test -- App.test.tsx`

로컬 API와 backfill worker를 재시작한 뒤 `/v1/dashboard/summary`에서 `workerStatus.backfill.status=running`, `statusLabel=동작 중`, `diagnostics` 포함을 확인했다. 35초 뒤에도 heartbeat가 갱신되어 `지연`으로 바뀌지 않았다.

## 후속 작업

- 전체 테스트와 E2E 실행 후 배포 브랜치에 반영한다.
