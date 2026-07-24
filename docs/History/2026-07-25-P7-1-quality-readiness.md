# 2026-07-25 P7-1 품질 readiness gate

## 변경 요약

- `docs/contracts/quality/p7-quality-evidence.yaml`을 P7 품질 증적 매니페스트(Quality Evidence Manifest)로 추가했다.
- `scripts/verify_p7_quality_gates.py`가 readiness mode와 release mode를 분리해 검증한다.
- CI에 `P7 quality readiness gate` 단계를 추가했다.
- `docs/Task/P7.md`, 제품 요구사항(PRD), 아키텍처(Architecture), 계약 색인, UI QA 문서를 P7-1 상태에 맞춰 동기화했다.

## 검증

- `uv run pytest tests/scripts/test_p7_quality_gates.py tests/scripts/test_github_workflows.py -q` → `15 passed in 0.05s`
- `uv run python scripts/verify_p7_quality_gates.py --mode readiness --manifest docs/contracts/quality/p7-quality-evidence.yaml` → `ok=true`, `gates=14`
- `uv run python scripts/verify_p7_quality_gates.py --mode release --manifest docs/contracts/quality/p7-quality-evidence.yaml` → `P7 release gate 미통과`
- `uv run ruff check .` → `All checks passed!`
- `uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests` → `Success: no issues found in 196 source files`
- `uv run pytest -q` → `819 passed, 156 skipped, 1 warning in 62.57s`
- `npm test` → `29 passed`, `181 passed`
- `npm run build` → build 통과, 기존 Vite chunk warning 유지
- `npm run e2e` → `22 passed`, API·웹 시험 서버 종료 확인

## 코드 리뷰

- 요구사항·아키텍처·계약·Task·Test·History 기준으로 로컬 리뷰를 수행했다.
- Critical/Important/Minor 지적 없음.

## 남은 작업

P7 완료가 아니다. 매니페스트의 planned gate를 후속 수직 조각에서 실제 측정·스캔·리허설 증적으로 바꾸고 release mode를 통과시켜야 한다.
