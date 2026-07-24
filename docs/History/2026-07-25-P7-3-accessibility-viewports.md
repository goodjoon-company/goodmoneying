# 2026-07-25 P7-3 접근성·viewport gate

## 변경 요약

- `tests/e2e/p7-accessibility.spec.ts`를 추가해 WCAG 2.2 AA proxy와 7개 viewport overflow를 자동 검증한다.
- `package.json`에 `p7:accessibility`, `p7:viewports` script를 추가했다.
- `docs/contracts/quality/p7-quality-evidence.yaml`의 접근성 gate 2개를 `passed`로 전환했다.

## 남은 작업

dependency/image/secret/auth/input, load/soak/chaos, backup/restore, unresolved artifact release gate는 아직 planned 상태다.

## 검증

- `uv run pytest tests/scripts/test_p7_accessibility_gates.py -q` → RED, `planned` 상태 때문에 실패
- `uv run pytest tests/scripts/test_p7_accessibility_gates.py tests/scripts/test_p7_performance_gates.py tests/scripts/test_p7_quality_gates.py tests/scripts/test_github_workflows.py -q` → `17 passed`
- `npm run p7:accessibility` → `1 passed (2.5s)`
- `npm run p7:viewports` → `1 passed (5.4s)`
- `npx playwright test tests/e2e/p7-accessibility.spec.ts` → `2 passed (6.5s)`
- `uv run pytest -q` → `821 passed, 156 skipped, 1 warning in 63.24s`
- `uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests` → `Success: no issues found in 198 source files`
- `uv run ruff check .` → `All checks passed!`
- `npm test` → `29 passed`, `181 passed`
- `npm run e2e` → `27 passed (49.0s)`, API·웹 시험 서버 종료 확인
- `npm run build` → build 통과, 기존 Vite chunk warning 유지
- `git diff --check` → 통과

## 코드 리뷰

- 요구사항·아키텍처·계약·Task·Test·History 기준으로 로컬 리뷰를 수행했다.
- Critical/Important/Minor 지적 없음.
- WCAG gate는 자동화 가능한 proxy 범위로 기록했으며 전체 수동 WCAG 인증으로 과장하지 않는다.
