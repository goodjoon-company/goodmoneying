# 2026-07-18 P5-6 Bot Workshop UI 인계

## 변경 요약

- Operations Console 1차 메뉴에 `Bot Workshop`을 추가했다.
- `bot-workshop` section metadata와 `REST 준비` 갱신 기준을 추가했다.
- `apps/web/src/features/botWorkshop/`에 Bot Workshop React component, component test, CSS를 추가했다.
- Bot Workshop은 Portfolio allocation, 봇 승격 단계, paper/shadow 주문 파이프라인, kill switch/checklist, reconciliation evidence를 읽기 전용으로 표시한다.
- App 통합 테스트와 Playwright E2E를 추가해 메뉴 진입, live 안전 잠금, 실제 주문/live action 부재, 390px overflow 없음, runtime error 없음을 검증한다.
- Product, Architecture, UI Flow/Spec, P5 Task 문서를 P5 완료 상태로 현행화했다.
- 코드 리뷰 Minor 반영으로 6개 승격 단계 전체, 파이프라인 후반, 주식시장 노출 방어 정규식 검증, 아키텍처 기준일 정합성을 보강했다.

## 안전 경계

- 새 DB/API 계약은 없다.
- 실제 Upbit 주문 제출·취소·조회, private WebSocket, 주문 테스트 API, live 활성화 경로를 추가하지 않았다.
- live_ready/live는 성공 상태가 아니라 안전 잠금 상태로 표시한다.

## 검증

- 상세 증적: `docs/Test/2026-07-18-P5-6-bot-workshop-ui-검증.md`
- Targeted component: `npm --workspace apps/web run test -- BotWorkshop` → `2 passed`
- Targeted App integration: `npm --workspace apps/web run test -- App.test.tsx` → `20 passed`
- Targeted Playwright: `npx playwright test tests/e2e/p5-bot-workshop.spec.ts` → `1 passed`

## 후속 범위

- P6 private 주문·체결·잔고 대사와 live-ready 검증
- P7/P8 상용 운영 품질과 배포 gate
