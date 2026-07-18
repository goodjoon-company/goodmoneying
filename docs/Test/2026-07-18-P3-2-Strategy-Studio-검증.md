# P3-2 Strategy Studio 검증

Related Task: [P3](../Task/P3.md), GitHub Issue #30

## 범위

- Strategy Studio 메뉴와 화면 진입점
- 전략 그래프(Strategy Graph) 포인터 뷰와 텍스트 대안
- 동적 edge 목록의 의미적 텍스트 대안
- 키보드 대체 편집기
- 서버 검증 오류의 안정 코드·위치 텍스트 표시
- 변경 전 graph의 늦은 검증 성공 응답 무시
- 검증·게시 요청 실패 alert 표시
- 게시 실패 후 같은 전략 정의와 같은 게시 멱등 키(idempotency key) 재사용, 게시 성공 후 재게시 방지
- 검증 통과 graph의 불변 전략 version 게시 UI
- seeded E2E API의 in-memory 전략 저장소 fixture

## RED 확인

| 명령 | 결과 | 증적 |
|---|---|---|
| `npm --workspace apps/web run test -- src/api.test.ts src/App.test.tsx src/features/strategyStudio/StrategyStudio.test.tsx` | FAIL | `validateStrategyGraph is not a function`, `StrategyStudio` import 실패, `Strategy Studio` 메뉴 없음 |
| `npx playwright test tests/e2e/p3-strategy-studio.spec.ts` | FAIL | `getByRole('button', { name: 'Strategy Studio' })` timeout |

## GREEN 검증

| 명령 | 결과 | 증적 |
|---|---|---|
| `git diff --check` | PASS | exit 0 |
| `npm --workspace apps/web run test -- src/api.test.ts src/App.test.tsx src/features/strategyStudio/StrategyStudio.test.tsx` | PASS | `3 passed`, `35 passed` |
| `uv run pytest tests/api/test_strategy_versions_api.py tests/contracts/test_p3_strategy_contract.py tests/contracts/test_api_contract.py -q` | PASS | `16 passed, 1 warning` |
| `npx playwright test tests/e2e/p3-strategy-studio.spec.ts` | PASS | `1 passed` |
| `npm --workspace apps/web run build` | PASS | `✓ built in 178ms`, Docker build 내부 재검증 `✓ built in 182ms`; Vite chunk size warning only |
| `uv run ruff check .` | PASS | `All checks passed!` |
| `uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests` | PASS | `Success: no issues found in 132 source files` |
| `npm test` | PASS | `27 passed`, `168 passed` |
| `uv run pytest -q` | PASS | `675 passed`, `122 skipped`, `1 warning` |
| `npm run e2e` | PASS | `20 passed`, `API 시험 서버 종료 확인: 127.0.0.1:18000`, `웹 시험 서버 종료 확인: 127.0.0.1:15173` |
| `docker compose build` | PASS | `goodmoneying-systematic-trading-platform-api`, `web`, `upbit-gateway`, `realtime-collection-worker`, `market-sync-worker`, `backfill-collection-worker`, `candle-aggregation-worker` Built |
| GitHub Actions CI | PASS | run `29639122553`, commit `9874cdafd35e1969b85877b3295d0fdc1fd6274a`, `verify in 5m49s` |

## 미검증 항목

- Backtest 실행, 봇 연결, 주문·위험 연결은 P4 이후 범위다.
