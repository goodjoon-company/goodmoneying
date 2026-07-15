# 2026-07-16 업비트 웹소켓(WebSocket) 중계 검증

Date: 2026-07-16
Result: Pass
Related Issue: [#23](https://github.com/goodjoon-company/goodmoneying/issues/23)
Related ADR: [ADR-0011](../ADR/ADR-0011-업비트-API-게이트웨이와-비파괴-테스트-경계.md)

## 검증 대상

- 카탈로그의 공개 12개·비공개 2개 스트림과 `LIST_SUBSCRIPTIONS`
- `ticket`, `type`, `codes`, `is_only_snapshot`, `is_only_realtime`, `DEFAULT`·`SIMPLE`·`JSON_LIST`·`SIMPLE_LIST`
- 공개·비공개 고정 상향 URL, 서버 전용 JWT와 `Authorization` 헤더, 브라우저·프레임·로그 비노출
- 하향 웹소켓(WebSocket)의 운영자 토큰·같은 출처 또는 명시적 허용 목록 검증과 루프백 포트 바인딩(binding)
- 연결 초당 5회와 연결별 메시지 초당 5회·분당 100회의 카탈로그 기반 요청 제한(rate limit)
- 이진 프레임(binary frame) UTF-8 JSON 해독, 잘못된 프레임·Upbit 오류, 추적 출처(provenance)와 최근 200개 raw 프레임 상한
- 일시 정지, 구독 해제, 목록 조회, 수동·자동 재연결, 지수형 재시도 지연(backoff), 세대(generation) 기반 중복 재구독·작업 정리
- 공개·비공개 연결·프레임 상태 분리, 공개 현재가·체결·호가·캔들 및 구조화한 비공개 내 자산·내 주문, 키보드 탭·대화상자 접근성
- 헤더의 공개·비공개 연결 상태 동시 표시와 raw 대화상자(dialog)의 Tab·Shift+Tab 초점 순환
- 명시적 테스트 플래그가 있는 루프백(loopback) 가짜 상류만 허용하는 실제 프로세스 통합과 브라우저→Vite 프록시→게이트웨이→가짜 Upbit E2E

## 공식 기준 재확인

2026-07-16 작업 시점에 [웹소켓 사용 및 에러 안내](https://docs.upbit.com/kr/reference/websocket-guide), [인증](https://docs.upbit.com/kr/reference/auth), [요청 수 제한](https://docs.upbit.com/kr/reference/rate-limits), [구독 중인 스트림 목록 조회](https://docs.upbit.com/kr/reference/list-subscriptions), 각 데이터 타입 공식 문서를 다시 확인했다. 공개·비공개 URL, Bearer JWT, 요청 배열 순서, 네 가지 포맷, 주요 오류 코드, 120초 유휴 종료와 PING/PONG, 연결 5회/초와 메시지 5회/초·100회/분을 카탈로그·구현·테스트에 대조했다.

실제 Upbit 키나 서버는 사용하지 않았다. 모든 인증 검증은 가짜 접근 키와 64바이트 가짜 비밀 키, 루프백 가짜 상류 프로세스만 사용했다. 비공개 가짜 상류는 인증 헤더와 구독·목록 조회만 검증하고 실제 자산·주문 이벤트를 만들지 않았다.

## 테스트 주도 개발 증적

프로토콜, 세션, FastAPI 경로, 실제 프로세스, React 기능, 브라우저 하네스, 계약 순서로 실패 테스트를 먼저 작성했다. 첫 실행은 각각 모듈 누락(`ModuleNotFoundError`), 경로 부재로 인한 웹소켓 종료, 컴포넌트·프로토콜 모듈 해석 실패, 계약의 `$defs`·OpenAPI 확장 누락으로 실패했다. 최소 구현 뒤 같은 범위를 통과시켰다.

리뷰에서 발견한 초기·수동 재연결의 상향 연결 예외 전파와 공통 페어 선택의 단방향 고립도 실패 테스트로 재현했다. 연결 예외는 상향 URL과 `Authorization: Bearer`가 포함된 원문 `RuntimeError`로 세션 밖에 전파됐고, 공통 `marketCode`는 화면 선택에 반영되지 않았다. 고정된 `UPSTREAM_CONNECTION_ERROR` 복구 가능 이벤트로 원문을 마스킹하고, 선택적 제어 속성 `marketCode`·`onMarketCodeChange`를 추가한 뒤 실패·재시도·탭 전환·재연결 후에도 세션과 공통 코드가 유지됨을 확인했다.

독립 리뷰 후에는 무인증 하향 연결, 교차 출처 요청, 프록시 전달 헤더 위조, Upbit의 전체 구독 교체 의미론, JSON Schema와 런타임 제어 검증 불일치, 공개·비공개 소켓 상태 결합, 재시도 소진 뒤 종료 상태 누락을 각각 실패 테스트로 고정했다. Nginx·Vite가 서버에서만 운영자 토큰을 주입하고 게이트웨이는 명시적으로 신뢰한 프록시 전달 헤더만 사용하도록 수정했다. 모든 구독 요청은 전체 희망 구독 스냅샷(snapshot)을 보내며 가짜 상류도 교체 의미론으로 검증한다.

최종 재리뷰에서는 활성 탭의 상태 하나만 보이는 헤더, 대화상자 내부 초점 이탈, 자동 재연결 소진 뒤 같은 세션의 제어 수용, Nginx 기본 60초 유휴 시간 제한을 각각 실패 테스트로 재현했다. 헤더는 공개·비공개 상태를 별도 라벨과 배지로 항상 함께 표시하고, raw 대화상자는 닫기 버튼과 키보드로 읽을 수 있는 raw 프레임 사이에서 Tab·Shift+Tab 초점이 순환하도록 수정했다. 복구 불가능한 세션은 이후 제어를 `SESSION_CLOSED`로 거부하고 새 하향 연결만 허용하며, 업비트 WebSocket 프록시는 읽기·쓰기 유휴 시간 제한을 1시간으로 늘렸다. 고립 브라우저 E2E에서도 두 상태의 독립성과 양방향 초점 순환을 재검증했다.

## 자동화 검증 결과

| 명령 | 결과 | 핵심 증적 |
|---|---|---|
| `uv run pytest -q tests/contracts/test_upbit_gateway_contract.py tests/upbit_gateway tests/scripts/test_upbit_gateway_container.py` | Pass | 14개 스트림, 엄격한 제어 스키마, 전체 구독 스냅샷, 하향 인증·출처, 네 포맷, 재연결 종료, 프록시 구성 |
| `uv run pytest -q tests/upbit_gateway/test_process_e2e.py` | Pass, `4 passed` | 실제 게이트웨이·가짜 Upbit 프로세스, 공개 이진 프레임, 비공개 JWT, 잘못된 프레임·오류·자동 재연결·메시지 제한·비밀값 검색 |
| `uv run pytest -q` | Pass, `331 passed, 3 skipped` | 전체 Python 회귀, 기존 TestClient 폐기 예정 경고 1건 |
| `uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests` | Pass | 타입 오류 0건 |
| `uv run ruff check .` | Pass | 정적 검사 오류 0건 |
| `npm test` | Pass, `81 tests` | 연결·프레임 분리, 공개·비공개 동시 상태, 자산·주문 구조화, 출처 표시, 탭·대화상자 초점 접근성을 포함한 전체 프런트엔드 회귀 |
| `npm run build` | Pass | TypeScript와 Vite 운영 빌드 성공 |
| `npx playwright test tests/e2e/upbit-websocket-workbench.spec.ts` | Pass, `2 passed` | 고립 화면 회귀와 실제 브라우저→프록시→게이트웨이→가짜 Upbit 공개·비공개 경로 |
| `npx playwright test` | Pass, `7 passed` | 전체 브라우저 E2E 회귀 |
| `git diff --check`와 키·JWT 패턴 검색 | Pass | 공백 오류와 실제 비밀정보 0건 |

`goodjoon-workflow` 외부 기술 점검 게이트는 저장소에 `harness/config/goodjoon-workflow-harness.json`이 없어 실행 전 실패했다. 저장소에 없는 하네스 설정을 임의 생성하지 않았으며 이 공백은 기능 검증과 별도로 기록한다.

## 코드·보안·설계 리뷰

Issue #23, 제품 P2.2와 GM-PROD-030~033, 업비트 게이트웨이 설계, 카탈로그, 웹소켓 JSON Schema, ADR-0011을 기준으로 자체 리뷰했다. 공용 제품·아키텍처·ADR 문서는 병렬 #24 통합 경계와 충돌하지 않도록 수정하지 않고 기존 결정 준수만 검토했다.

리뷰에서 상향 연결 예외 원문의 URL·헤더 노출, 재연결 중 세션 종료 가능성, raw payload의 민감 키 반사, 연결별 메시지 제한기의 재연결 후 공유, 상향 정상 종료의 자동 재연결 누락, 공통 페어 선택의 상위 동기화 누락과 하향 인증 부재를 발견했다. 고정 오류 이벤트, 파싱 뒤 재직렬화 마스킹, 상향 연결마다 새 메시지 제한기, 정상 종료와 예외의 동일 재연결 흐름, 제어 가능한 공통 페어 속성, 같은 출처 프록시 인증 경계와 회귀 테스트로 수정했다.

최종 대조에서 브라우저에는 키 입력·토큰 헤더·URL 토큰이 없고 비공개 JWT는 서버 상향 핸드셰이크에만 존재하며 `Origin`은 전달하지 않는다. 프록시는 운영자 토큰을 덮어써서 주입하고, 게이트웨이는 토큰과 같은 출처 또는 허용 목록을 모두 확인한다. 프로덕션 URL은 카탈로그로 고정되고 테스트 재정의는 `ws://127.0.0.1`·`localhost`·`::1`과 명시적 플래그가 모두 있어야 한다. 구독 요청과 해제는 전체 희망 구독 스냅샷으로 상향 상태를 일치시키고, 세대 번호가 이전 수신 작업의 중복 재연결을 막는다. 치명적(Critical)·중요(Important) 미해결 발견사항은 0건이다.

## 미검증 항목

- 실제 Upbit 계정·키·인터넷 상향 연결은 보안 범위에 따라 의도적으로 실행하지 않았다.
- 공용 작업대 화면과의 최종 메뉴·레이아웃 통합은 병렬 Issue #24의 소유 파일 범위다.
- REST 작업대와의 공통 화면 결합은 Issue #21, 전체 작업대 배포·QA·인계는 Issue #24에서 완료한다.

## 결론

가짜 자격 증명과 루프백 상류만 사용한 자동화 검증에서 공개·비공개 웹소켓 중계, 공식 요청 제한, 재연결·정리, 비밀정보 경계, 고립된 시각화와 공통 페어 제어가 계약과 Issue #23 완료 조건에 일치한다.
