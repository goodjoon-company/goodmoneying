# 2026-06-21 backfill worker 지연 오판과 상태 상세 검증

## 상태

Done

## 검증 대상

- 백필 수집 워커(Backfill Collection Worker) 장시간 작업 중 heartbeat 갱신
- `DashboardSummary.workerStatus.diagnostics` API 계약과 응답
- worker 상태 라벨 클릭 상세 레이어 팝업

## 실행 명령

```bash
uv run pytest tests/contracts/test_api_contract.py tests/api/test_operations_api.py tests/worker/test_collector_behavior.py -q
```

결과: `37 passed, 1 warning`

```bash
uv run ruff check .
```

결과: `All checks passed!`

```bash
uv run mypy apps/api apps/worker packages/shared tests
```

결과: `Success: no issues found in 30 source files`

```bash
uv run pytest -q
```

결과: `90 passed, 1 warning`
최종 재실행 결과: `91 passed, 1 warning`

```bash
npm test
```

결과: `7 passed`, `29 passed`

```bash
npm run build
```

결과: 성공

```bash
npm test -- App.test.tsx
```

결과: `8 passed`

```bash
npm run e2e
```

결과: Chromium `1 passed`

```bash
git diff --check
```

결과: 출력 없음

## 런타임 확인

수정 전 확인:

- `./dev.sh status`: `backfill-collection-worker running pid=33692`
- `/v1/dashboard/summary`: `backfill.status=stale`, `statusLabel=지연`
- DB: `backfill_collection.last_heartbeat_at=2026-06-21 09:13:32+00`, DB 현재 시각 `2026-06-21 09:16:21+00`
- DB: 백필 job 1개 `running`, target 5개 `succeeded`, 45개 `pending`

판단: worker 프로세스는 살아 있고 백필 작업은 진행 중이지만, 긴 백필 실행 중 heartbeat가 갱신되지 않아 지연으로 오판됐다.

추가 회귀 확인:

- 백필 대상 처리 시작 시 target 상태를 `running`으로 기록한다.
- `동작중 코인`은 worker가 실제 fetch 중인 대상을 DB 상태로 관측할 수 있다.

수정 후 확인은 로컬 API와 백필 worker 재시작 뒤 `/v1/dashboard/summary` 응답의 `workerStatus.backfill`로 수행한다.

수정 후 확인:

- `./dev.sh app restart api`: 새 API 프로세스 `pid=76456`
- `./dev.sh app restart backfill-collection-worker`: 새 worker 프로세스 `pid=87527`
- 즉시 확인: `workerStatus.backfill.status=running`, `statusLabel=동작 중`, `lastHeartbeatAt=2026-06-21T09:26:10Z`, `diagnostics` 포함
- 35초 뒤 확인: `workerStatus.backfill.status=running`, `statusLabel=동작 중`, `lastHeartbeatAt=2026-06-21T09:26:57Z`
- DB 확인: `backfill_collection.last_heartbeat_at=2026-06-21 09:26:57+00`, DB 현재 시각 `2026-06-21 09:26:59+00`
- 최종 worker 재시작 후 확인: `workerStatus.backfill.status=running`, `statusLabel=동작 중`, `lastHeartbeatAt=2026-06-21T09:29:54Z`, `runningTargetCount=1`, `totalTargetCount=250`, 진단 `동작중 코인 1/250개`

## 화면 시간대 확인

- `formatShortDateTime`, `formatFreshness`, `formatShortDay`, 실시간 수집 히트맵 시간 마커는 `Asia/Seoul` 기준으로 표시한다.
- worker 오류 상세의 `occurredAt`은 `formatShortDateTime`을 통해 KST(Korea Standard Time)로 표시한다.
- worker 동작 상세의 `diagnostics.value` 중 ISO 시각 문자열은 화면에서 `formatShortDateTime`을 통해 KST로 표시한다.
- `App.test.tsx`에 worker 동작 상세 팝업이 원본 ISO 문자열을 직접 표시하지 않고 KST 09:00 형식으로 표시하는 회귀 테스트를 추가했다.
- 백필 계획 생성 입력은 `수집 범위 시작 · KST`, `수집 범위 종료 · KST`로 표시하며, `datetime-local` 입력값은 `+09:00` 오프셋을 붙여 API payload로 보낸다.
