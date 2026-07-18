# 2026-07-18 P5-4 risk evaluation 검증

## 범위

- `RiskEvaluationWorker`가 `created` 주문 의도(Order Intent)를 위험 평가한다.
- `risk_limits`의 활성 한도와 `kill_switches` 최신 상태로 `approved|risk_rejected`를 결정한다.
- 승인된 paper 주문은 `paper_execution_jobs`에 연결한다.
- kill switch가 arm된 뒤에는 승인된 paper job도 claim·completion에서 신규 모의 체결을 만들지 않는다.
- 실제 Upbit 주문, private WebSocket, 주문 테스트 API는 사용하지 않는다.

## RED

- `uv run pytest tests/worker/test_risk_evaluation_worker.py tests/worker/test_paper_execution_worker.py tests/contracts/test_p5_risk_evaluation_contract.py -q`
  - 결과: `2 errors`
  - 원인: `goodmoneying_worker.risk_evaluation_worker` 모듈 없음, `PaperExecutionBlockedError` 없음

## GREEN 및 부분 검증

- `uv run pytest tests/worker/test_risk_evaluation_worker.py tests/worker/test_paper_execution_worker.py tests/contracts/test_p5_risk_evaluation_contract.py -q`
  - 결과: `9 passed`
- `uv run ruff check packages/shared/goodmoneying_shared/portfolio_bot_store.py apps/worker/goodmoneying_worker/risk_evaluation_worker.py apps/worker/goodmoneying_worker/paper_execution_worker.py tests/worker/test_risk_evaluation_worker.py tests/worker/test_paper_execution_worker.py tests/e2e/test_live_postgres_risk_evaluation.py tests/e2e/test_live_postgres_portfolio_bot_risk.py tests/contracts/test_p5_risk_evaluation_contract.py`
  - 결과: `All checks passed!`
- `uv run mypy apps/worker/goodmoneying_worker/risk_evaluation_worker.py apps/worker/goodmoneying_worker/paper_execution_worker.py packages/shared/goodmoneying_shared/portfolio_bot_store.py tests/worker/test_risk_evaluation_worker.py tests/worker/test_paper_execution_worker.py tests/e2e/test_live_postgres_risk_evaluation.py tests/e2e/test_live_postgres_portfolio_bot_risk.py`
  - 결과: `Success: no issues found in 7 source files`
- `uv run pytest tests/worker/test_risk_evaluation_worker.py tests/worker/test_paper_execution_worker.py tests/contracts/test_p5_risk_evaluation_contract.py tests/e2e/test_live_postgres_risk_evaluation.py tests/e2e/test_live_postgres_portfolio_bot_risk.py -q`
  - 결과: `9 passed, 3 skipped`

## DB migration E2E

- `tests/e2e/run_dbmate_migration_e2e.sh`
  - 1차 결과: 실패
  - 원인: P5 live 테스트를 전체 DB migration E2E에 포함하자 기존 P5-1 fixture가 `backtest_runs_terminal_finished_check`를 만족하지 못함
  - 조치: P5-1 backtest fixture에 `finished_at`을 추가
- `tests/e2e/run_dbmate_migration_e2e.sh`
  - 2차 결과: 실패
  - 원인: P5-1 backtest fixture의 상수 `input_hash`가 전체 suite에서 전역 unique 제약과 충돌
  - 조치: key 기반 `input_hash`, `result_hash`, `parameter_hash`로 변경
- `tests/e2e/run_dbmate_migration_e2e.sh`
  - 최종 결과: `130 passed in 42.50s`
  - 요약: `versions=21 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`

## 코드 리뷰 반영

- 리뷰 지적: kill switch arm과 paper claim/completion 사이에 공유 fencing이 부족했다.
  - 조치: risk evaluation, paper claim, paper completion transaction에서 `kill_switches` table을 `SHARE MODE`로 잠그고 최신 switch를 검사한다.
- 리뷰 지적: completion 차단 시 `retry_wait` 업데이트가 예외 rollback으로 사라질 수 있었다.
  - 조치: blocked completion은 예외 대신 `retry_wait` summary를 반환하고 `KILL_SWITCH_ARMED`를 커밋한다.
- 리뷰 지적: `RiskEvaluationWorker`가 런타임 worker로 연결되지 않았다.
  - 조치: `risk_evaluation_collection_worker`, `docker-compose.yml`, `dev.sh`, prod-home compose와 healthcheck를 추가했다.
- 리뷰 지적: claim 후 arm 후 completion E2E가 없었다.
  - 조치: live PostgreSQL E2E에 claim 후 kill switch arm 뒤 completion이 exchange order/fill을 만들지 않고 job을 `retry_wait`로 되돌리는 검증을 추가했다.

## 리뷰 반영 후 부분 검증

- `uv run pytest tests/scripts/test_deploy_profile.py tests/scripts/test_dev_script.py tests/contracts/test_timezone_contract.py tests/worker/test_paper_execution_worker.py tests/worker/test_risk_evaluation_worker.py tests/contracts/test_p5_risk_evaluation_contract.py tests/e2e/test_live_postgres_risk_evaluation.py -q`
  - 결과: `65 passed, 2 skipped`
- `tests/e2e/run_dbmate_migration_e2e.sh`
  - 결과: `130 passed in 39.71s`
  - 요약: `versions=21 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`

## 재리뷰 반영

- 재리뷰 지적: kill switch 차단이 마지막 시도에서 발생하면 `attempt_count=max_attempts` 상태의 `retry_wait` job이 선두에서 계속 선택되어 queue가 정체될 수 있었다.
  - RED: `uv run pytest tests/contracts/test_p5_risk_evaluation_contract.py -q`
  - 결과: `2 failed, 3 passed`
  - 조치: kill switch completion 차단은 실행 실패가 아니므로 `attempt_count=GREATEST(attempt_count - 1, 0)`로 시도 예산을 환급하고, live PostgreSQL E2E에서 `max_attempts=1` job이 release 후 재청구되는지 검증했다.
- 재리뷰 지적: 위험 평가는 `requested_notional`을 우선하지만 paper fill은 `requested_quantity`를 우선해 모순된 이중 입력이 한도를 우회할 수 있었다.
  - RED: `uv run pytest tests/contracts/test_p5_risk_evaluation_contract.py -q`
  - 결과: `2 failed, 3 passed`
  - 조치: 위험 평가는 실제 paper fill과 같은 `requested_quantity * limit_price`를 우선 계산하고, `requested_notional`이 함께 있으면서 계산값과 다르면 실패 폐쇄형으로 `limit_rejected` 처리한다.
- GREEN: `uv run pytest tests/contracts/test_p5_risk_evaluation_contract.py tests/worker/test_risk_evaluation_worker.py tests/worker/test_paper_execution_worker.py -q`
  - 결과: `12 passed`
- `tests/e2e/run_dbmate_migration_e2e.sh`
  - 1차 결과: 실패
  - 원인: 테스트 해제 상태값을 DB 계약의 `released`가 아니라 `disarmed`로 사용했다.
  - 조치: live E2E test fixture를 계약 값 `released`로 수정했다.
- `tests/e2e/run_dbmate_migration_e2e.sh`
  - 최종 결과: `132 passed in 42.10s`
  - 요약: `versions=21 data_rows=1 timezone=UTC API=200 snapshot=동일 집계상태=동일`

## 전체 회귀와 빌드

- `uv run ruff check .`
  - 결과: `All checks passed!`
- `uv run mypy apps/api apps/worker packages/shared tests`
  - 결과: `Success: no issues found in 149 source files`
- `uv run pytest -q`
  - 결과: `763 passed, 133 skipped, 1 warning in 61.53s`
- `npm test`
  - 결과: `28 passed`, `178 passed`
- `npm run build`
  - 결과: 성공
  - 비고: 기존 Vite chunk size warning 유지
- `git diff --check`
  - 결과: 통과
- `docker compose config >/tmp/goodmoneying-compose-config.txt && grep -q risk-evaluation-worker /tmp/goodmoneying-compose-config.txt`
  - 결과: 통과
- `uv run python` 기반 compose YAML load 검사
  - 결과: `compose yaml ok`
- 최종 코드 리뷰
  - 결과: 이전 Important 2건 해결, 새 Critical/Important 없음, 승인

## 후속 검증

원격 CI 결과는 커밋·푸시 후 추가 기록한다.
