# 이슈 #22 Exchange API 작업대 인계

Date: 2026-07-16
Related Issue: [#22](https://github.com/goodjoon-company/goodmoneying/issues/22)

## 변경 요약

- 카탈로그의 활성 Exchange REST 38개를 포켓·계정·주문·출금·입금·Travel Rule·서비스 탭으로 제공하는 격리 작업대(workbench)를 추가했다.
- 문자열·정수·불리언(boolean)·배열(array)·날짜·열거형(enum) 동적 입력과 공통 마켓 개념 어댑터(interface)를 제공한다. 부모가 주입한 마켓을 초기값으로 사용하고 기능·그룹 전환 후에도 보존하며 변경을 부모에 알릴 수 있다.
- 조회 결과는 계정 잔고·주문·입출금 표와 서비스 상태 카드로 표시하고, 마스킹된 원본 추적 봉투(Trace Envelope)를 대화상자로 확인한다.
- 공식 주문 생성 테스트는 `비파괴 테스트`로 명시해 확인 없이 실행할 수 있다. 실제 주문·취소·자산 이전·입출금·Travel Rule 검증은 빨간 위험 배너와 로컬 미리보기만 제공하고 프론트엔드에서 게이트웨이를 호출하지 않는다.
- 게이트웨이 `/health`는 키 값이나 경로 없이 유효한 서버 자격 증명의 설정 여부만 `credentials_configured` 불리언으로 반환한다. 부재·부분 설정·혼용·유효하지 않은 파일은 `false`다.
- 기능 전환 중 도착한 이전 요청 응답은 요청 세대(generation)로 폐기하고, 추적 봉투의 `endpoint_id`가 요청 기능과 다르면 안전한 오류만 표시해 결과 출처(provenance)를 보장한다.
- 400·401·418·422·429·5xx도 마스킹된 추적 봉투(Trace Envelope)를 보존해 친화 오류와 원본 추적을 함께 제공한다. 추적 계약을 충족하지 않는 일반 오류 객체는 보존하지 않는다.
- 카탈로그의 `any_of_required`를 타입과 폼에 반영하고 `get-order`, `cancel-order`, `get-withdrawal`, `get-deposit`의 대체 필수 조합을 안내·검증한다. 명시적으로 선택한 `false` 불리언도 요청에 포함한다.
- 차단 요청 미리보기는 공통 마켓을 포함하고, 탭 방향키·`aria-controls`·모달 포커스 진입·Escape 닫기·호출 버튼 복귀를 지원한다.
- `apps/web/src/features/upbitExchange/e2e.html`은 기존 `App`을 수정하지 않는 격리 Playwright 하네스다. 실제 키나 업비트 서버 대신 가짜 게이트웨이만 사용한다.

## 통합 경계

- 이 브랜치는 `App`, 사이드바, 공통 작업대 파일을 수정하지 않는다.
- #21 또는 #24 통합에서 `ExchangeWorkbenchExtensionProps`에 실제 게이트웨이 클라이언트와 공통 마켓 개념 어댑터, 제어 마켓 값·변경 콜백(callback), 추적 열기 훅(hook)을 주입한다.
- `createHttpExchangeGateway(baseUrl)`은 `/health`, `/v1/catalog`, `/v1/requests`만 호출하며 브라우저에서 키를 받는 API를 제공하지 않는다.
- 이 브랜치의 E2E는 격리 경로와 가짜 게이트웨이를 사용한다. #24 병합 전에는 #21 제품 메뉴 연결 상태에서 브라우저 HTTP 클라이언트 → 시험 FastAPI → 가짜 업비트 상향 서버를 통과하는 통합 E2E를 반드시 실행해야 한다.

## 검증과 리뷰

TDD RED/GREEN, 웹 단위 테스트, 게이트웨이·OpenAPI 계약, Playwright E2E, 빌드, 타입 검사(type check), 린트(lint), 보안·디자인·계약 자체 리뷰 근거는 `docs/Test/2026-07-16-이슈-22-Exchange-API-작업대-검증.md`에 있다.

## 남은 범위와 위험

- 공용 셸의 2레벨 메뉴 연결과 Quotation·WebSocket 작업대 통합은 #21·#23·#24가 소유한다.
- 제품 메뉴·FastAPI·가짜 상향 서버를 한 번에 통과하는 통합 E2E는 #24 병합 선행 조건이며, 완료 전에는 제품 통합 완료로 선언하지 않는다.
- 실제 운영 자격 증명을 이용한 인증 읽기와 공식 주문 테스트는 이 브랜치에서 실행하지 않았다. 허용 IP·권한·실제 요청 제한을 포함한 운영 검증은 별도 승인과 안전한 환경이 필요하다.
- 공식 Upbit 카탈로그는 바뀔 수 있으므로 통합 시 최신 `llms.txt`와 카탈로그 계약 테스트를 다시 확인한다.
- 롤백(rollback)은 이 커밋을 되돌리면 된다. 기능이 공용 `App`에 연결되지 않아 기존 사용자 화면 런타임에는 영향이 없다.
