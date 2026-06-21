# M3-T10 backfill worker 지연 오판과 상태 상세 수정

## 목표

백필 수집 워커(Backfill Collection Worker)가 실제 작업 중인데도 heartbeat가 갱신되지 않아 `지연`으로 표시되는 문제를 수정하고, worker 상태 라벨을 클릭하면 지연/오류/동작 사유를 확인할 수 있는 상세 정보를 표시한다.

## 요구사항 링크

- `docs/02_Architecture/upbit-collection-pipeline.md`
- `docs/contracts/api/openapi.yaml`
- 사용자 요청: backfill worker 상태가 `지연`으로 나오는 사유 확인 및 상태 클릭 상세 제공

## 우선순위

P1

## 상태

Done

## 선행 Task

- `docs/Task/M3-T09-2026-06-21-001-worker-현황판-계약과-화면-구현.md`

## 범위

- 백필 처리 중 heartbeat 갱신
- 백필 대상 처리 시작 시 target 상태 `running` 기록
- `DashboardSummary.workerStatus`에 진단 정보(`diagnostics`) 추가
- worker 상태 라벨 클릭 시 동작 상세 레이어 팝업 표시
- API, worker, 화면 회귀 테스트 추가

## 비범위

- 백필 작업 큐 정책 변경
- 백필 대상 우선순위와 병렬 처리 변경
- 운영 서버 배포 설정 변경

## 현재 맥락

로컬 확인 결과 `backfill-collection-worker` 프로세스는 실행 중이고 백필 작업도 `running` 상태였지만, `collection_worker_heartbeats.backfill_collection.last_heartbeat_at`이 장시간 갱신되지 않아 API가 `stale`/`지연`으로 판정했다.

## 설계 메모

- 지연 오판의 원인은 긴 `run_backfill_once()` 실행 중 heartbeat가 작업 시작 전후에만 기록되는 구조였다.
- `run_backfill_once()`에 진행 callback을 추가하고, 백필 worker가 이 callback으로 `backfill_collection` heartbeat를 기록한다.
- 상태 상세는 오류 목록과 분리한다. 오류 버튼은 오류 상세를 열고, 상태 라벨 버튼은 worker 동작 진단 정보를 연다.

## 계약 링크

- `docs/contracts/api/openapi.yaml`
- `docs/contracts/db/schema.sql`

## 계약 변경

- `CollectionWorkerDiagnostic` 스키마 추가
- `RealtimeWorkerStatus.diagnostics` 추가
- `BackfillWorkerStatus.diagnostics` 추가

## 실패 케이스

- 백필 worker가 긴 job 처리 중 heartbeat를 갱신하지 않아 살아 있어도 `지연`으로 표시된다.
- 상태 라벨을 클릭해도 지연 사유, 마지막 heartbeat, 진행 중 대상 수를 확인할 수 없다.
- 실제 fetch 중인 target이 `pending`에 머물러 `동작중 코인`이 0으로 보일 수 있다.

## 실행 계획

- [x] 현재 런타임에서 `지연` 재현과 DB heartbeat 확인
- [x] API 계약과 응답 테스트 추가
- [x] worker 진행 callback 회귀 테스트 추가
- [x] 화면 상태 상세 팝업 테스트 추가
- [x] heartbeat 갱신과 진단 정보 구현
- [x] 문서와 검증 증적 갱신

## 완료 기준

- 백필 처리 중 진행 callback이 여러 번 호출된다.
- `DashboardSummary.workerStatus`가 `diagnostics`를 포함한다.
- worker 상태 라벨 클릭 시 동작 상세 레이어 팝업이 열린다.
- 관련 자동화 테스트가 통과한다.

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

## 실행 로그

- RED: `CollectionWorkerDiagnostic` 미정의, `diagnostics` 누락, `run_backfill_once(on_progress=...)` 미지원 실패 확인
- GREEN: targeted backend/frontend 테스트 통과
- 전체 검증: pytest, web test/build, Playwright E2E, lint/type/diff check 통과
- 최종 pytest 결과: `91 passed, 1 warning`
- 런타임 확인: API와 backfill worker 재시작 후 35초 뒤에도 `workerStatus.backfill.status=running` 유지

## 복잡도 제한

- multi-worker 상태 추론이나 프로세스 테이블 연동은 넣지 않는다.
- 상태 상세는 dashboard summary 응답에 포함된 진단 항목만 표시한다.

## 추적성

- API 계약: `docs/contracts/api/openapi.yaml`
- 구현: `apps/worker/goodmoneying_worker/collector.py`, `apps/worker/goodmoneying_worker/backfill_collection_worker.py`, `packages/shared/goodmoneying_shared/*_repository.py`, `apps/web/src/components/Dashboard.tsx`
- 검증: `docs/Test/2026-06-21-backfill-worker-지연-오판과-상태-상세-검증.md`
