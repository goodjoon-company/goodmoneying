# 2026-07-25 P7-2 성능 gate

## 변경 요약

- `tests/e2e/p7-performance.spec.ts`를 추가해 Web Vitals, 첫 유용 셸, 실시간 event 반영 성능을 Playwright로 측정한다.
- `package.json`에 `p7:web-vitals`, `p7:first-shell`, `p7:realtime-event` script를 추가했다.
- `docs/contracts/quality/p7-quality-evidence.yaml`의 세 performance gate를 `passed`로 전환하고 각각의 `docs/Test/` 증적 파일에 연결했다.

## 안전 경계

- 실거래, 외부 부하, 운영 DB 복구는 수행하지 않는다.
- INP는 브라우저 Event Timing duration을 local proxy로 검증한다. route 전환 완료 시간은 데이터 반영 시간을 섞어 병렬 E2E에서 과대 측정되므로 INP proxy로 사용하지 않는다.

## 남은 작업

접근성, dependency/image/secret/auth/input, load/soak/chaos, backup/restore gate는 아직 planned 상태다.

## 검증

- `uv run pytest tests/scripts/test_p7_performance_gates.py -q` → RED, `planned` 상태 때문에 실패
- `uv run pytest tests/scripts/test_p7_performance_gates.py tests/scripts/test_p7_quality_gates.py tests/scripts/test_github_workflows.py -q` → `16 passed`
- `npm run p7:web-vitals` → `1 passed (2.4s)`
- `npm run p7:first-shell` → `1 passed (3.0s)`, 테스트 본문 측정 대상 623ms
- `npm run p7:realtime-event` → `1 passed (2.4s)`, 테스트 본문 측정 대상 637ms
- `npx playwright test tests/e2e/p7-performance.spec.ts` → `3 passed (4.2s)`
- `uv run pytest -q` → `820 passed, 156 skipped, 1 warning in 61.60s`
- `uv run ruff check .` → `All checks passed!`
- `uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests` → `Success: no issues found in 197 source files`
- `npm run e2e` → `25 passed (42.9s)`, API·웹 시험 서버 종료 확인
- `npm test` → `29 passed`, `181 passed`
- `npm run build` → build 통과, 기존 Vite chunk warning 유지
- `git diff --check` → 통과

## 코드 리뷰

- 요구사항·아키텍처·계약·Task·Test·History 기준으로 로컬 리뷰를 수행했다.
- Critical/Important 지적 없음.
- Minor: Product 상태 문구를 `P7-2 성능 gate 진행`에서 `P7-2 성능 gate 완료`로 명확히 고쳤다.
