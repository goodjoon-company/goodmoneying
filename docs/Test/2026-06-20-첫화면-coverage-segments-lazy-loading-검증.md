# 2026-06-20 첫 화면 coverageSegments lazy loading 검증

## 대상

- 프런트엔드 첫 접속 시 운영 상태 화면 로딩 지연
- `/v1/dashboard/summary`가 상세 `coverageSegments`를 모두 포함해 약 40MB JSON을 반환하던 문제
- 수집 대상/후보/시장 리스트의 반복 coverage 계산 비용

## 원인

- 첫 화면 대시보드 요약 API가 활성 50개 코인의 모든 1분 캔들 수집/결측 segment를 직렬화(serialization)했다.
- 실측 기준 `/v1/dashboard/summary` 응답은 약 39.9MB, `coverageSegments`는 약 20.9만 개였다.
- 후보 유니버스와 시장 리스트도 상세 dashboard target 계산 경로를 공유해 coverage 계산 비용을 반복했다.

## 변경

- `/v1/dashboard/summary`의 `targets[].coverageSegments`는 첫 화면에서 빈 배열로 반환한다.
- row 확장 시 `/v1/collection-targets/{instrumentId}/coverage-segments`에서 해당 코인의 segment만 lazy loading 한다.
- `source_candle` coverage status는 전체 segment 생성 없이 저장 행 수, gap 수, 최신 bucket을 집계 SQL로 계산한다.
- PostgreSQL 저장소(repository)는 최신 ticker/orderbook과 저장량을 배치(batch) 조회해 후보/시장 리스트의 반복 쿼리를 줄였다.

## 실측 결과

| 항목 | 변경 전 | 변경 후 |
|---|---:|---:|
| `/v1/dashboard/summary` 응답 시간 | 12.2~20.5초 | 1.6초 |
| `/v1/dashboard/summary` 응답 크기 | 약 39.9MB | 약 77.8KB |
| 대시보드 segment 수 | 209,804개 | 0개 |
| `/v1/collection-targets/1/coverage-segments` | 없음 | 0.139초, 3.1KB, 16개 |
| 브라우저 첫 화면 표시 | 약 12.7초 | 약 1.9초 |
| `/v1/candidate-universe` | 약 6.9초 | 약 1.2초 |
| `/v1/market-list` | 약 7.2초 | 약 0.83초 |

## 검증 결과

| 명령 | 결과 | 메모 |
|---|---:|---|
| `uv run pytest -q` | Pass | 75 passed, 1 warning |
| `npm test` | Pass | Vitest 2 files, 8 tests passed |
| `npm run build` | Pass | TypeScript 빌드와 Vite production build 통과 |
| `uv run ruff check . && uv run mypy apps packages tests && git diff --check` | Pass | 정적 검사(static check) 통과 |
| `npm run e2e` | Pass | Playwright Chromium 1 test passed, 기본 로컬 앱 대상 |

## 운영 상태

- API: `http://127.0.0.1:8000`
- Web: `http://127.0.0.1:5173`
- PostgreSQL, API, Web, Worker 모두 running 상태에서 검증했다.
