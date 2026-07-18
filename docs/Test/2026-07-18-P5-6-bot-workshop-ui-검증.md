# 2026-07-18 P5-6 Bot Workshop UI 검증

## 범위

- Operations Console 1차 메뉴에서 Bot Workshop으로 진입한다.
- Portfolio allocation에서 paper/shadow 운영 rehearsal로 이어지는 상태를 읽기 전용으로 표시한다.
- 봇 승격 단계, 주문 파이프라인, kill switch와 승인 checklist, reconciliation mismatch/outcome_unknown 증적을 표시한다.
- live-ready/live는 안전 잠금 상태이며 실제 주문 제출, live 활성화, private 주문 경로 action을 제공하지 않는다.

## RED

- `npm --workspace apps/web run test -- BotWorkshop`
  - 결과: 중단된 구현 subagent가 production component를 먼저 남겨 RED를 재현하지 못했고, 이후 테스트를 보강해 행동 검증을 강화했다.
- `npm --workspace apps/web run test -- App.test.tsx`
  - 결과: `1 failed | 19 passed`
  - 원인: 상단 hero `h1`과 feature 내부 `h2`가 모두 `Bot Workshop` heading이라 단일 heading 조회가 실패했다.

## GREEN

- `npm --workspace apps/web run test -- BotWorkshop`
  - 결과: `1 passed`, `2 passed`
- `npm --workspace apps/web run test -- App.test.tsx`
  - 결과: `1 passed`, `20 passed`
- `npx playwright test tests/e2e/p5-bot-workshop.spec.ts`
  - 결과: `1 passed`
  - 확인: 390px viewport horizontal overflow `<= 1`, runtime console error와 pageerror 없음

## 코드 리뷰 반영

- 리뷰 결과: Critical/Important 없음, Minor 3건
- 조치: Bot Workshop 단위/E2E 테스트가 6개 승격 단계 전체와 `paper execution job → reconciliation → position projection` 파이프라인 후반을 명시 검증하도록 보강했다.
- 조치: 주식시장 노출 방어 검증을 완전 일치 문자열에서 `/주식|stock/i` 정규식으로 보강했다.
- 조치: `docs/02_Architecture.md` 구현 현황 표 기준일을 2026-07-18로 맞추고, UI-only 준비·잠금 단계가 P5 저장소 계약 상태와 혼동되지 않도록 `docs/ui/02_UI_Spec.md`에 명시했다.
- 재검증:
  - `npm --workspace apps/web run test -- BotWorkshop` → `1 passed`, `2 passed`
  - `npx playwright test tests/e2e/p5-bot-workshop.spec.ts` → `1 passed`
  - `git diff --check` → 통과

## 전체 회귀와 빌드

- `npm test`
  - 결과: `29 passed`, `181 passed`
- `npm run build`
  - 결과: 성공
  - 비고: 기존 Vite chunk size warning 유지
- `uv run ruff check .`
  - 결과: `All checks passed!`
- `uv run mypy apps/api apps/worker packages/shared tests`
  - 결과: `Success: no issues found in 151 source files`
- `uv run pytest -q`
  - 결과: `767 passed, 136 skipped, 1 warning in 60.31s`
- `git diff --check`
  - 결과: 통과

## 안전 경계

- P5-6은 새 DB migration, 새 API endpoint, 실제 Upbit 주문 제출, private WebSocket, 주문 테스트 API 호출을 추가하지 않는다.
- Bot Workshop은 P5 paper/shadow 운영 상태를 설명하는 읽기 전용 UI이며 live 활성화 버튼을 제공하지 않는다.
