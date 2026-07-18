# 2026-07-18 P5-3 paper execution worker 검증

## 범위

- `paper_execution_jobs` queue DB 계약
- `PostgresPortfolioBotStore` paper execution claim/complete/fail
- `PaperExecutionWorker`와 주입형 paper simulator 연결
- simulated `exchange_orders`, `order_fills`, `position_projections`, `order_intents.status='paper_filled'`

## RED

```bash
uv run pytest tests/worker/test_paper_execution_worker.py -q
```

결과: `ModuleNotFoundError: No module named 'goodmoneying_worker.paper_execution_worker'`

```bash
uv run pytest tests/contracts/test_p5_paper_execution_contract.py -q
```

결과: `3 failed`; `20260718000700_p5_paper_execution_jobs.sql`, Store 메서드, 도메인 문서 미존재.

```bash
GOODMONEYING_LIVE_POSTGRES_TEST=1 GOODMONEYING_DATABASE_URL="postgresql://goodmoneying:goodmoneying-e2e@127.0.0.1:<임시포트>/goodmoneying?sslmode=disable" uv run pytest tests/e2e/test_live_postgres_paper_execution.py -q
```

결과: `timestamp too small (before year 1): '-infinity'`. `next_retry_at` 기본값을 Python 변환 가능한 UTC timestamp로 수정.

## GREEN

```bash
uv run pytest tests/worker/test_paper_execution_worker.py tests/contracts/test_p5_paper_execution_contract.py tests/e2e/test_live_postgres_paper_execution.py -q
```

결과: `6 passed, 1 skipped`

```bash
GOODMONEYING_LIVE_POSTGRES_TEST=1 GOODMONEYING_DATABASE_URL="postgresql://goodmoneying:goodmoneying-e2e@127.0.0.1:<임시포트>/goodmoneying?sslmode=disable" uv run pytest tests/e2e/test_live_postgres_paper_execution.py -q
```

결과: 임시 PostgreSQL에 전체 migration 적용 후 `1 passed in 0.13s`

```bash
GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh
```

결과: `dbmate 마이그레이션 E2E 통과: versions=21 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`, live PostgreSQL 회귀 `124 passed in 44.37s`

## 안전 확인

- 실제 Upbit 주문 submit/cancel/read 호출 없음
- private WebSocket 사용 없음
- 주문 테스트 API 사용 없음
- `live-ready/live` 전이 없음
