# 2026-07-16 업비트 Quotation API 작업대 검증

Date: 2026-07-16
Result: Pass
Related Issue: [#21](https://github.com/goodjoon-company/goodmoneying/issues/21)
Related ADR: [ADR-0011](../ADR/ADR-0011-업비트-API-게이트웨이와-비파괴-테스트-경계.md)

## 검증 범위

- Upbit 공식 카탈로그의 시세 조회(Quotation) REST 활성 12개와 사용 중단(deprecated) 1개 노출
- 페어·캔들·체결·현재가·호가 기능 탭과 카탈로그 기반 타입 입력 폼
- 공통 거래쌍·마켓(Quote)·기준 자산(Base) 전파 및 #22·#23 확장 컴포넌트 주입 경계
- 페어 목록, 캔들 차트와 표, 체결 표, 현재가 카드, 호가 사다리와 정책 표
- 요청·원본 응답·요청 제한·공식 출처를 표시하는 추적 대화상자(dialog)
- 캔들 과거·미래 연속 조회의 `to`·`count`, 중복 제거, 오래된 요청 취소, 현재 시각 종료, 과거 추가 시 화면 범위 유지
- 공통 거래쌍 파라미터의 단일 입력 원천, 방향별 캔들 종단 표시, 오류 상태·냉각(cooldown), 추적 대화상자 포커스 트랩(focus trap)
- 브라우저의 Upbit 직접 호출과 인증 헤더 부재
- 가짜 게이트웨이 기반 데스크톱·모바일 E2E(End-to-End)

## 테스트 주도 개발 증적

| 단계 | 명령 | 결과 |
|---|---|---|
| RED | `npm --workspace apps/web run test -- src/components/upbit-api-test/workbench.test.ts src/components/upbit-api-test/pagination.test.ts src/components/upbit-api-test/client.test.ts` | 공통 모듈 3개가 없어 3개 시험 묶음(suite) 실패 |
| GREEN | 같은 명령 | 3개 파일, 5개 테스트 통과 |
| RED | `npm --workspace apps/web run test -- src/components/upbit-api-test/UpbitApiWorkbench.test.tsx` | 작업대 컴포넌트가 없어 1개 시험 묶음 실패 |
| GREEN | 같은 명령 | 초기 2개 테스트 통과 |
| RED/GREEN | `npm --workspace apps/web run test -- src/components/UpbitCandleChart.test.tsx` | 과거 캔들 추가 시 논리 범위 유지 실패 1건 확인 후 2개 통과 |
| RED/GREEN | `npm --workspace apps/web run test -- src/components/upbit-api-test/pagination.test.ts src/components/upbit-api-test/workbench.test.ts src/components/upbit-api-test/UpbitApiWorkbench.test.tsx` | 일봉 `count` 전진, 날짜 표시, 배열 위젯, 오래된 응답 차단 4건 실패 확인 후 9개 통과 |
| RED | `npm test -- src/components/upbit-api-test/workbench.test.ts src/components/upbit-api-test/UpbitApiWorkbench.test.tsx` | 공통 파라미터 중복, 양방향 캔들 종단, 429 냉각, 포커스 복귀·트랩 5건 실패 확인 |
| RED | `npm test -- src/components/upbit-api-test/trace.test.ts` | 상태별 오류·`Retry-After` 변환 모듈 부재로 실패 확인 |
| GREEN | `npm test -- src/components/upbit-api-test/workbench.test.ts src/components/upbit-api-test/trace.test.ts src/components/upbit-api-test/UpbitApiWorkbench.test.tsx` | 3개 파일, 15개 테스트 통과 |

## 자동화 검증 결과

| 명령 | 결과 | 핵심 증적 |
|---|---|---|
| `npm test` | Pass | 16개 파일, 73개 테스트 통과 |
| `npm run build` | Pass | TypeScript와 Vite 운영 빌드 성공 |
| `npm run e2e` | Pass | Chromium 5개 E2E 통과, API·웹 시험 서버 종료 확인 |
| `uv run pytest -q` | Pass | 287개 통과, 3개 건너뜀, 기존 Starlette 경고 1건 |
| `uv run ruff check .` | Pass | 오류 0건 |
| `uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests` | Pass | 타입 오류 0건 |
| `git diff --check` | Pass | 공백 오류 0건 |
| 직접 호출 검색 | Pass | 브라우저 구현의 `api.upbit.com`, `Authorization`, 과거 전역 10초 큐 0건 |

## 공식 문서 재검토

2026-07-16에 [API 개요](https://docs.upbit.com/kr/reference/api-overview), [요청 수 제한](https://docs.upbit.com/kr/reference/rate-limits), [일 캔들 조회](https://docs.upbit.com/kr/reference/list-candles-days)를 다시 확인했다. Quotation REST 기능군, 그룹별 초당 10회 제한, `Remaining-Req`, 429·418, `to` 이전 캔들·최대 `count=200` 계약을 기준으로 구현했다. 브라우저 `Origin` 직접 호출에 적용되는 10초당 1회 정책은 게이트웨이 경계로 대체했다.

## 리뷰와 디자인 QA

- `GWF-HARNESS-REVIEW-001`: Issue #21과 제품 P2.2·GM-PROD-030~031을 대조했다.
- `GWF-HARNESS-REVIEW-002`: `docs/02_Architecture/upbit-api-gateway.md`의 브라우저·게이트웨이 경계를 대조했다.
- `GWF-HARNESS-REVIEW-003`: REST 카탈로그와 게이트웨이 OpenAPI의 타입·추적 봉투를 대조했다.
- `GWF-HARNESS-REVIEW-004`: ADR-0011의 임의 URL·키 비노출·비파괴 경계를 대조했다.
- `GWF-HARNESS-REVIEW-005`: 취소·요청 제한·날짜·페이지 경계와 가짜 E2E를 검토했다.
- `GWF-HARNESS-REVIEW-006`: 1440px·390px 주요 화면의 2열·1열 전환, 44px 모바일 입력·탭, 수평 넘침, 초점 표시, 대화상자 Escape 닫기를 Playwright로 확인했다.
- `GWF-HARNESS-REVIEW-007`: 치명적(Critical)·중요(Important) 미해결 발견사항 0건이다. 독립 리뷰 에이전트는 동시 실행 한도가 모두 사용 중이라 같은 체크리스트로 직접 재검토했다.
- `GWF-HARNESS-REVIEW-008`: 독립 루트 리뷰에서 발견한 중요(Important) 3건과 경미(Minor) 1건을 테스트 우선으로 수정했다.
- `GWF-HARNESS-REVIEW-009`: 공통 입력 단일 기준, 빈·중복 페이지 종단, 400·418·429 오류와 냉각, 추적 대화상자 포커스 트랩·복귀를 재검토했다.
- `GWF-HARNESS-REVIEW-010`: Playwright에서 과거 `to` 조회 뒤 미래 페이지의 새 `to`, 중복 0건, 종단 고정을 검증했다.

`gstack:design-review`의 전체 자동 절차는 깨끗한 작업 트리와 단계별 커밋·대화형 결정을 요구해, 구현 리뷰 전 커밋 금지와 질문 없이 진행하라는 이슈 조건에 맞지 않았다. 대신 실제 Chromium E2E의 데스크톱·모바일 렌더링, 접근성 이름, 상호작용, 수평 넘침과 콘솔 오류를 동등 범위로 검증했다.
