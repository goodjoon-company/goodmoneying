# P7-6 release gate

## 변경

- `recovery.backup_restore`를 `passed`로 전환하고 DB migration E2E 증적을 연결했다.
- `hygiene.unresolved_artifacts`를 `passed`로 전환했다.
- unresolved artifact scanner의 allowed path 예외를 제거했다.
- P7 release gate가 현재 manifest에서 통과하도록 했다.

## 검증

- `uv run pytest tests/scripts/test_p7_completion_gates.py tests/scripts/test_p7_quality_gates.py -q`
  - 결과: `6 passed in 0.11s`
- `uv run python scripts/verify_p7_quality_gates.py --mode release --manifest docs/contracts/quality/p7-quality-evidence.yaml`
  - 결과: release mode 통과, gate 14개, evidence path 14개 확인
- `uv run ruff check .`
  - 결과: 통과
- `uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests`
  - 결과: `Success: no issues found in 201 source files`
- `tests/e2e/run_dbmate_migration_e2e.sh`
  - 결과: `155 passed in 35.93s`, dbmate schema snapshot 동일, 집계 상태 동일
- `uv run pytest -q`
  - 결과: `834 passed, 156 skipped, 1 warning in 62.77s`
- `npm test`
  - 결과: `181 passed`
- `npm run build`
  - 결과: 통과. 기존 Vite chunk size warning만 발생
- `npm run e2e`
  - 결과: `27 passed (47.8s)`, API 시험 서버와 웹 시험 서버 종료 확인
- `git diff --check`
  - 결과: 통과

## 리뷰

- Critical: 없음
- Important: 없음
- Minor: 없음

P7 release gate는 운영 배포가 아니라 품질 증적 매니페스트의 완료 조건이다. 운영 배포와 live 상태 확인은 P8에서 별도 실행한다.
