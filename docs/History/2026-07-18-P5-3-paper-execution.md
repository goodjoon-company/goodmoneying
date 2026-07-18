# 2026-07-18 P5-3 paper execution worker 인계

## 변경 요약

- `paper_execution_jobs` queue migration을 추가했다.
- `PostgresPortfolioBotStore`에 paper execution job claim/complete/fail 메서드를 추가했다.
- `PaperExecutionWorker`를 추가해 claim된 job을 주입형 simulator 결과로 완료한다.
- completion transaction에서 simulated exchange order, paper simulator fill, position projection, order intent 상태를 함께 기록한다.
- P5-3 문서와 DB schema snapshot을 갱신했다.

## 안전 경계

- 실제 Upbit 주문 제출·취소·조회는 호출하지 않는다.
- private WebSocket과 주문 테스트 API는 사용하지 않는다.
- P5-3은 `bot_instances.execution_mode='paper'`이고 `order_intents.status='approved'`인 작업만 claim한다.
- shadow 관찰, risk worker, reconciliation worker, Bot Workshop UI는 후속 P5 범위다.

## 검증

- `uv run pytest tests/worker/test_paper_execution_worker.py tests/contracts/test_p5_paper_execution_contract.py tests/e2e/test_live_postgres_paper_execution.py -q` → `6 passed, 1 skipped`
- 임시 PostgreSQL live E2E → `tests/e2e/test_live_postgres_paper_execution.py`, `1 passed`
- `GOODMONEYING_UPDATE_DB_SNAPSHOT=1 tests/e2e/run_dbmate_migration_e2e.sh` → `versions=21`, `API=200`, `snapshot=동일`, live PostgreSQL `124 passed`

## 후속 작업

- P5-4 risk evaluation과 kill switch 차단
- P5-5 reconciliation과 position projection 보강
- P5-6 Bot Workshop UI와 E2E
