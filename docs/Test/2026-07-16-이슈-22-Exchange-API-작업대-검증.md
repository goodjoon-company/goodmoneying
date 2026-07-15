# 이슈 #22 Exchange API 작업대 검증

Date: 2026-07-16
Related Issue: [#22](https://github.com/goodjoon-company/goodmoneying/issues/22)
Related Product: `docs/01_Product.md` P2.2, GM-PROD-030~033
Related Contract: `docs/contracts/upbit/upbit-api-catalog.yaml`, `docs/contracts/api/upbit-gateway.openapi.yaml`
Related ADR: `docs/ADR/ADR-0011-업비트-API-게이트웨이와-비파괴-테스트-경계.md`

## 공식 기준과 범위

2026-07-16 업비트 개발자 센터 v1.6.3의 `llms.txt`와 주문 생성 테스트·요청 수 제한(Rate Limit) 공식 문서를 다시 확인했다. 현재 활성 Exchange REST 엔드포인트(endpoint)는 포켓 7개, 계정 1개, 주문 11개, 출금 7개, 입금 7개, Travel Rule 3개, 서비스 2개로 총 38개다. 안전 등급은 `read` 23개, `test` 1개, `blocked` 14개다. 포켓 개요는 설명 문서이며 엔드포인트가 아니므로 화면 인벤토리에 넣지 않았다.

프론트엔드(Frontend)는 기존 `App`, 사이드바(sidebar), 공통 작업대 파일을 수정하지 않고 `apps/web/src/features/upbitExchange/`에 격리했다. #21 통합 셸(shell)이 이후 `ExchangeWorkbenchExtensionProps`의 게이트웨이(gateway)·마켓 어댑터(adapter)·추적 훅(hook)을 연결한다.

## TDD RED

| 명령 | 기대한 실패 |
|---|---|
| `npm --workspace apps/web test -- src/features/upbitExchange/gateway.test.ts src/features/upbitExchange/ExchangeWorkbench.test.tsx` | 신규 `gateway`와 `ExchangeWorkbench` 모듈을 찾지 못해 테스트 스위트(test suite) 2개 실패 |
| `uv run pytest -q tests/upbit_gateway/test_app.py -k health tests/contracts/test_upbit_gateway_contract.py -k 'health or gateway_openapi'` | `/health`와 OpenAPI에 `credentials_configured`가 없어 3개 실패 |
| 이슈 전용 포트의 `npx playwright test tests/e2e/upbit-exchange.spec.ts --project=chromium --workers=1` | 독립 E2E 경로가 없어 `Exchange API 작업대`를 찾지 못함 |
| `uv run pytest -q tests/upbit_gateway/test_app.py -k invalid_credential_files` | 존재하지 않는 파일 경로 한 쌍을 설정됨으로 판정해 1개 실패 |
| 공통 마켓 주입·보존 단위 테스트 | 부모가 주입한 마켓이 초기 입력에 반영되지 않아 1개 실패 |
| 기능 전환 중 지연 응답 단위 테스트 | 이전 기능의 추적 응답이 새 기능 화면에 표시되어 1개 실패 |
| 응답 `endpoint_id` 불일치 단위 테스트 | 요청 기능과 다른 출처의 응답을 결과로 표시해 1개 실패 |

고정 기본 포트 `18000`은 병렬 작업이 사용 중이어서 E2E 기능 실행 전에 충돌했다. 기능 실패와 분리하기 위해 최종 격리 E2E는 `E2E_API_BASE_URL=http://127.0.0.1:28022`, `E2E_WEB_BASE_URL=http://127.0.0.1:25122`를 사용했다.

## GREEN과 자동화 검증

| 명령 | 결과 |
|---|---|
| 신규 웹 단위 테스트(Unit Test) 2개 파일 | 18개 통과 |
| 게이트웨이 상태·OpenAPI 표적 테스트 | 3개 통과, 기존 FastAPI 테스트 클라이언트 경고 1건 |
| 유효·부재·잘못된 파일 자격 증명 상태 테스트 | 3개 통과 |
| 격리 Playwright E2E | Chromium 6개 통과, 2.3초 |
| `npm test` | 14개 파일, 86개 테스트 통과, 8.67초 |
| `npm run build` | TypeScript 빌드와 Vite 프로덕션 빌드 통과, 1,835개 모듈 변환, 149밀리초 |
| `uv run pytest -q` | 289개 통과, 3개 건너뜀, 기존 FastAPI 테스트 클라이언트 경고 1건, 45.66초 |
| `uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests` | 67개 소스 파일 오류 없음 |
| `uv run ruff check .` | `All checks passed!` |
| 이슈 전용 포트의 `npm run e2e` | 전체 Chromium E2E 11개 통과, 1.0분, API·웹 테스트 서버 종료 확인 |
| `git diff --check` | 공백 오류 없음 |

## E2E 시나리오

1. 38개 기능이 7개 그룹 수량과 일치하고 조회 결과·원본 추적 대화상자(dialog)가 연결되는지 확인한다.
2. 실제 주문은 위험 배너, 타입 입력, 최종 요청 미리보기만 보이며 가짜 게이트웨이 호출과 가짜 상향 호출이 모두 0인지 확인한다.
3. 공식 주문 생성 테스트는 별도 확인 대화상자 없이 가짜 상향 서버에 정확히 한 번 요청하는지 확인한다.
4. 400·401·418·422·429·5xx와 자격 증명 부재 503을 사용자 메시지로 구분하고 안전하지 않은 원본 오류 상세가 문서 객체 모델(DOM)에 없는지 확인한다.
5. 390px 뷰포트(viewport)에서 본문 가로 넘침이 없고 탭·기능 선택을 키보드로 실행할 수 있는지 확인한다.

실제 API Key, 실제 업비트 서버, 실제 주문·취소·이전·입출금·Travel Rule 검증은 사용하지 않았다.

## 요구사항·아키텍처·계약·보안·디자인 자체 리뷰

- 요구사항: Issue #22, P2.2, GM-PROD-030~033의 Exchange 범위와 비파괴 정책을 대조했다. 앱 셸 연결은 #21 통합 범위로 남겨 기존 화면 파일을 수정하지 않았다.
- 아키텍처(Architecture): `docs/02_Architecture/upbit-api-gateway.md`의 카탈로그 식별자, 마스킹 추적, 브라우저 키 부재, `blocked` 선판정 경계를 유지했다.
- 계약(Contract): 카탈로그 38개를 그대로 소비하며 복사 목록을 제품 코드에 넣지 않았다. OpenAPI와 FastAPI `/health`에 값 없는 `credentials_configured: boolean`만 추가하고 런타임 스키마 동등성 테스트를 유지했다. DB 계약 변경은 없다.
- 보안(Security): 브라우저 입력에는 비밀번호 필드가 없고 키·JWT·Authorization 헤더를 요청·응답·오류에 넣지 않는다. 503을 포함한 오류 본문 원문을 DOM에 렌더링하지 않는다. `blocked`는 프론트엔드 실행 함수를 호출하지 않으며 기존 게이트웨이 실행기도 자격 증명·제한기·네트워크 전에 차단한다.
- 디자인(Design): 7개 탭, 가로 스크롤 기능 목록, 2열 요청·응답 패널, 위험 빨간 배너, 테스트 초록 배지, 표·상태 카드·추적 대화상자를 사용했다. 780px 이하 단일 열과 390px 본문 넘침·키보드·접근 가능한 이름을 Playwright로 검증했다.
- 운영(Operations): 서버 상태는 유효한 직접 키 쌍 또는 읽을 수 있는 유효 파일 쌍만 `설정됨`으로 표시한다. 값이나 소스 경로는 반환하지 않는다.

Critical·Important 발견사항은 없다. 자체 리뷰 중 찾은 유효하지 않은 파일 상태 오판을 수정했고, 부모 리뷰에서 확인된 공통 마켓 초기화·기능 전환 시 지연 응답·응답 출처 불일치를 각각 RED/GREEN으로 보강했다. 공통 마켓은 제어·비제어 방식 모두 지원하고 기능 전환 후에도 보존한다. 요청 세대(generation)와 `endpoint_id`를 함께 검증해 이전 기능 또는 다른 출처의 결과·추적을 폐기한다. 공용 Product·Architecture·ADR·refinement와 카탈로그는 병렬 이슈 충돌 방지를 위해 수정하지 않았고, 현재 설계 준수 결과만 이 문서에 남겼다.

## Goodjoon Workflow 게이트 기록

- intake: GitHub Issue #22를 실행 단위로 사용하고 별도 Task·병렬 계획 문서를 만들지 않았다. 외부 스킬 점검은 `.goodjoon-workflow/external-skills-state.json`에 `external-skills: ok`로 기록됐다.
- architecture: OpenAPI 상태 계약만 확장했고 DB·메시지 계약은 바꾸지 않았다. 기존 ADR-0011의 비파괴 결정을 변경하지 않는다.
- test: RED/GREEN, 성공·실패, 단위·계약·E2E·빌드·정적 검사 증거를 이 문서에 기록했다.
- review: 요구사항·아키텍처·계약·보안·성능·운영·반응형·접근성을 자체 리뷰했다.
- handoff: 실제 키·업비트 서버·상태 변경은 사용하지 않았고, push·merge·Issue close는 수행하지 않는다.
