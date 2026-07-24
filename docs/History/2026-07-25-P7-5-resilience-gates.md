# P7-5 resilience gates

## 변경

- `scripts/p7_resilience_probe.py`를 추가해 load, soak, chaos local probe를 구현했다.
- `scripts/p7_load_probe.py`, `scripts/p7_soak_probe.py`, `scripts/p7_chaos_probe.py` wrapper를 추가했다.
- `resilience.load`, `resilience.soak`, `resilience.chaos`를 `passed`로 전환했다.
- `package.json`에 `p7:load`, `p7:soak`, `p7:chaos` script를 추가했다.

## 검증

- `uv run pytest tests/scripts/test_p7_resilience_gates.py -q`
- `npm run p7:load` → 120 requests, failures 0, p95 8.082ms, max 22.562ms
- `npm run p7:soak` → 10초, 18 iterations, failures 0, drift 1.273MB, peak 3.892MB
- `npm run p7:chaos` → statuses `[200, 500, 200, 200]`, observed failures 1, recovered requests 3
- `uv run ruff check .` → 통과
- `uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests` → 200개 source file 통과
- `uv run pytest -q` → 832 passed, 156 skipped, 1 warning
- `npm run e2e` → 27 passed, API·웹 시험 서버 종료 확인
- `git diff --check` → 통과
- `uv run python scripts/verify_p7_quality_gates.py --mode release --manifest docs/contracts/quality/p7-quality-evidence.yaml` → 예상 실패: `recovery.backup_restore`, `hygiene.unresolved_artifacts`

## 리뷰

- Critical: 없음
- Important: 없음
- Minor: 없음

local profile은 운영 외부 부하를 피하기 위해 seeded SQLite repository와 FastAPI TestClient를 사용한다. 운영 환경의 장시간 soak와 장애 훈련은 P8 배포·운영 검증에서 별도 관측 증적으로 보강해야 한다.
