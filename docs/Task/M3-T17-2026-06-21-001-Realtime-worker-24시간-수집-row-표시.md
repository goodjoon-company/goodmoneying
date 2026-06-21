# M3-T17 Realtime worker 24시간 수집 row 표시

Status: Done
Created: 2026-06-21
Updated: 2026-06-21
Owner: Codex

## 목표

운영 상태의 worker 현황 패널에서 Realtime worker가 최근 24시간 동안 저장한 전체 수집 행(row) 수를 실시간 갱신 데이터로 표시한다.

## 요구사항 링크

- 사용자 요청: 운영상태의 worker 현황 패널에서 Realtime worker에 24시간 동안 받아온 데이터의 전체 count 수를 실시간으로 표시한다.
- API 계약(Contract): `docs/contracts/api/openapi.yaml#/components/schemas/RealtimeWorkerStatus`

## 우선순위

P2

## 상태

Done

## 선행 Task

- `docs/Task/M3-T09-2026-06-21-001-worker-현황판-계약과-화면-구현.md`

## 범위

- `RealtimeWorkerStatus` API 응답에 최근 24시간 수집 행(row) 합계 추가
- SQLite/PostgreSQL 저장소에서 `target_collection_results.rows_written`를 `run_type='incremental'` 기준으로 집계
- 운영 상태 worker 현황 패널에 `24시간 수집` 수치 표시
- API 계약(Contract), 웹 타입, 테스트, E2E(End-to-End) 검증 갱신

## 비범위

- Upbit 원본 응답 건수와 DB 저장 행(row) 수를 분리 측정
- 데이터 타입별 24시간 수집 수 breakdown 표시
- 별도 실시간 push 채널 추가

## 현재 맥락

Realtime worker는 ticker snapshot, orderbook summary, source candle을 주기적으로 수집하고 `target_collection_results.rows_written`에 대상별 저장 행(row) 수를 기록한다. 기존 worker 현황 패널은 마지막 저장 성공, 실패율, 오류 수만 보여 주어 최근 24시간 동안 실제로 얼마나 수집됐는지 한눈에 확인하기 어려웠다.

## 설계 메모

- 새 API 필드는 `collectedRowCount24h`로 둔다.
- 값은 최근 24시간 `target_collection_results.rows_written` 합계이며, 연결된 `collection_runs.run_type`이 `incremental`인 행(row)만 포함한다.
- 화면 표기는 `24시간 수집 {n} rows`로 하되, 상세 진단(diagnostics)에도 `24시간 수집 row` 항목을 포함한다.
- 기존 dashboard summary 계약은 유지하고 additive field만 추가한다.

## 계약 링크

- API: `docs/contracts/api/openapi.yaml#/components/schemas/RealtimeWorkerStatus`

## 계약 변경

- `RealtimeWorkerStatus.required`에 `collectedRowCount24h` 추가
- `RealtimeWorkerStatus.properties.collectedRowCount24h` 추가

## 실패 케이스

- 최근 24시간 수집이 있는데 worker 패널에 수집 수가 0으로 보인다.
- 백필(backfill) 수집 행(row)이 Realtime worker 수치에 섞인다.
- 기존 클라이언트 정규화(normalization) 경로에서 필드 누락 시 화면이 깨진다.

## 실행 계획

- [x] API 실패 테스트 추가
- [x] OpenAPI 계약 테스트 추가
- [x] 웹 화면 실패 테스트 추가
- [x] 모델과 SQLite/PostgreSQL 집계 구현
- [x] API 응답 스키마와 웹 타입 갱신
- [x] 운영 상태 worker 현황 패널 표시 추가
- [x] E2E 검증 보강
- [x] 자동화 테스트, 빌드, E2E 검증

## 완료 기준

- `/v1/dashboard/summary`의 `workerStatus.realtime.collectedRowCount24h`가 최근 24시간 실시간 수집 행(row) 합계를 반환한다.
- 운영 상태 worker 현황 패널에서 `24시간 수집` 수치가 보인다.
- E2E가 새 표시를 검증한다.
- 자동화 테스트와 빌드가 통과한다.

## 검증

- `docs/Test/2026-06-21-Realtime-worker-24시간-수집-row-표시-검증.md`

## 실행 로그

- 2026-06-21: TDD(Test-Driven Development)로 API/계약/웹 실패 테스트를 먼저 추가했다.
- 2026-06-21: `collectedRowCount24h` 필드와 저장소 집계, worker 패널 표시를 구현했다.

## 복잡도 제한

- 이번 변경은 Realtime worker 전체 24시간 합계만 다룬다.
- 데이터 타입별 breakdown이 필요하면 별도 Task로 분리한다.

## 추적성

- Related PR: TBD
- Related Test: `docs/Test/2026-06-21-Realtime-worker-24시간-수집-row-표시-검증.md`
- Related History: `docs/History/2026-06-21-Realtime-worker-24시간-수집-row-표시.md`
