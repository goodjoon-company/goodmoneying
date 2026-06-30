# 2026-06-21 Realtime worker 24시간 수집 row 표시

Date: 2026-06-21
Related Task: `docs/Task/M3.md`
Related PR: TBD

## 변경 요약

- `RealtimeWorkerStatus`에 최근 24시간 실시간 수집 행(row) 합계인 `collectedRowCount24h`를 추가했다.
- SQLite/PostgreSQL 저장소에서 `target_collection_results.rows_written`를 `collection_runs.run_type='incremental'` 기준으로 합산한다.
- 운영 상태 worker 현황 패널에 `24시간 수집` 수치를 표시했다.
- Realtime worker 진단(diagnostics)에 `24시간 수집 row` 항목을 추가했다.
- E2E(End-to-End)에서 worker 현황 패널의 새 수치 표시를 검증하도록 보강했다.

## 영향 문서

- `docs/Task/M3.md`
- `docs/Test/2026-06-21-Realtime-worker-24시간-수집-row-표시-검증.md`

## 영향 계약

- `docs/contracts/api/openapi.yaml`
  - `RealtimeWorkerStatus.collectedRowCount24h` 추가

## 검증

- 표적 API/계약 테스트 통과
- 표적 웹 테스트 통과

## 리스크

- 이 수치는 Upbit 원본 응답 건수가 아니라 DB에 저장된 행(row) 수다.
- 데이터 타입별 breakdown은 아직 제공하지 않는다.
