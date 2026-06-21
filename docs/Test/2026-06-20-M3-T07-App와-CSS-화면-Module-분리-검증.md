# 2026-06-20 M3-T07 App와 CSS 화면 Module 분리 검증

Related Task: `docs/Task/M3-T07-2026-06-20-001-App와-CSS-화면-Module-분리.md`

## 목적

운영 화면의 `App.tsx`와 CSS entrypoint 분리 뒤 기존 사용자 흐름, TypeScript build, 브라우저 E2E(End-to-End)가 유지되는지 검증한다.

## 환경

- 브랜치: `codex/design_mod_v0.1`
- 위치: `/Users/goodjoon/project/goodjoon/goodmoneying`
- 실행일: 2026-06-20

## RED 확인

| 명령 | 결과 | 기대 실패 |
|---|---|---|
| `npm --workspace apps/web run test -- frontendArchitecture.test.ts` | Fail | `App.tsx`에 `function Dashboard`, `function Targets`, `function Markets`, `function DetailModal`이 남아 있고 화면 Module/CSS Module이 없음 |

## 최종 자동화 검증

| 명령 | 결과 | 증거 |
|---|---|---|
| `npm --workspace apps/web run test` | Pass | 7 files, 26 tests 통과 |
| `npm run build` | Pass | `tsc -b && vite build`, Vite production build 통과 |
| `npm run e2e` | Pass | Chromium 1 test 통과 |
| `git diff --check` | Pass | 공백 오류 없음 |

## 구조 검증

| 항목 | 결과 |
|---|---|
| `apps/web/src/App.tsx` | 13줄, Query Provider와 `OperationsConsole` 조립만 담당 |
| `apps/web/src/components/OperationsConsole.tsx` | 운영 콘솔 shell, 메뉴, section routing 담당 |
| `apps/web/src/components/Dashboard.tsx` | 운영 상태 대시보드와 코인별 수집 상태 담당 |
| `apps/web/src/components/Targets.tsx` | 수집 대상/백필 계획 화면 담당 |
| `apps/web/src/components/Markets.tsx` | 시장 리스트 화면 담당 |
| `apps/web/src/components/Detail.tsx` | 상세 레이어와 TradingView 차트 담당 |
| `apps/web/src/components/ScalabilityReadiness.tsx` | 확장성 점검 화면 담당 |
| `apps/web/src/components/common.tsx` | 공통 표시 UI와 상태 표시 helper 담당 |
| `apps/web/src/styles.css` | 9줄, 역할별 stylesheet import만 담당 |
| `apps/web/src/styles/` | base, shell, common, data-tables, modals, shell-fidelity, dashboard, collection-table, responsive로 분리 |

## 코드 리뷰

- 제품 정합성: `docs/01_Product.md`의 운영 상태, 수집 대상/설정, 시장 리스트, 코인 상세 사용자 흐름을 유지한다.
- 아키텍처 정합성: `docs/02_Architecture.md`의 React + HTTP 폴링(Polling) 운영 화면 구조와 화면용 View Model 방향을 변경하지 않았다.
- 계약 정합성: `docs/contracts/api/openapi.yaml` API 필드와 요청/응답 계약 변경은 없다.
- 테스트 정합성: 구조 테스트를 RED로 먼저 추가했고, 최종 unit/build/E2E가 통과했다.
- 남은 리스크: `Dashboard.tsx`가 504줄로 가장 큰 화면 Module이다. 다음 단계에서는 수집 상태 행(row), 운영 KPI, 차트 surface를 내부 Module로 추가 분리할 수 있다.
