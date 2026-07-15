# 2026-07-16 업비트 웹소켓(WebSocket) 중계 도입

Date: 2026-07-16
Status: Completed
Related Issue: [#23](https://github.com/goodjoon-company/goodmoneying/issues/23)
Related ADR: [ADR-0011](../ADR/ADR-0011-업비트-API-게이트웨이와-비파괴-테스트-경계.md)
Verification: [검증 증적](../Test/2026-07-16-업비트-WebSocket-중계-검증.md)

## 변경 요약

- `/v1/websocket` 브라우저 제어 경계와 공개·비공개 Upbit 상향 세션을 추가했다.
- 카탈로그의 14개 스트림, `LIST_SUBSCRIPTIONS`, 네 가지 포맷과 타입별 파라미터를 검증해 공식 배열 메시지로 직렬화한다.
- 연결 초당 5회, 연결별 메시지 초당 5회·분당 100회를 카탈로그에서 구성하고 PING/PONG, 이진 JSON, 오류, 지수형 재연결과 단일 재구독을 구현했다.
- 비공개 JWT는 서버의 `Authorization` 헤더에만 주입하고 자격 증명 부재는 복구 가능한 503 성격의 이벤트로 알린다. 연결 예외 원문, 키, JWT, 상향 URL은 브라우저·raw 추적·로그로 반사하지 않는다.
- 하향 연결은 운영자 토큰과 명시적 출처 허용 목록을 모두 검증한다. 전달 `Host` 동등성은 DNS 재바인딩(DNS rebinding)에 악용될 수 있어 인증 근거로 사용하지 않는다. Vite·Nginx 프록시는 운영자 토큰을 서버에서만 주입하고 브라우저 번들과 URL에는 노출하지 않는다.
- 구독 추가 시 현재 희망 구독 전체를 Upbit에 다시 보내 교체 의미론과 일치시키며, 자동 재연결 재시도 소진 뒤 복구 불가능 오류와 `closed` 상태를 연속 통지하고 이후 제어는 새 하향 연결에서만 허용한다.
- Nginx의 업비트 WebSocket 프록시 읽기·쓰기 유휴 시간 제한을 1시간으로 늘려 조용한 공개·비공개 구독이 기본 60초 뒤 끊기지 않게 했다.
- 최근 200개 프레임에 추적 ID, 연결 ID, 순서, 수신 시각, 가시성, 포맷, endpoint 출처와 마스킹된 raw를 제공한다.
- `apps/web/src/features/upbitWebSocket/`에 공개 현재가·체결·호가·캔들, 비공개 내 자산·내 주문 탭과 연결·구독·일시 정지·목록·재연결·해제·raw 추적 제어를 재사용 가능한 내보내기 컴포넌트로 추가했다.
- 공개·비공개 소켓과 프레임을 독립 보존하고 두 연결 상태를 헤더에 동시에 표시하며 선택 연결 해제·프레임 지우기, 자산·주문 구조화 표시, raw 출처, 키보드 탭·대화상자 초점 순환 접근성을 제공한다.
- 상위 공통 거래쌍 모델을 `markets`로 받고 선택 값을 `marketCode`로 제어하며 `onMarketCodeChange`로 변경을 통지한다. 탭 전환과 재연결 뒤에도 같은 공통 코드를 구독에 사용한다.
- 실제 `WebSocket API 테스트` 2레벨 메뉴에 작업대를 연결하고 공개·비공개 상태, 가짜 Upbit 공개 스트림, raw 출처를 제품 화면 E2E로 검증했다.
- 개발 Vite의 `/api`와 `/upbit-gateway` 프록시가 운영자 토큰을 서버 측에서 주입한다. `VITE_OPERATOR_TOKEN`과 브라우저 직접 API 주소를 제거해 개발 번들에도 운영자 토큰이 들어가지 않는다.

## 계약과 검증

- `docs/contracts/api/upbit-gateway-websocket.schema.json`에 여섯 브라우저 제어와 네 서버 이벤트를 기계 검증 가능하게 정의했다.
- `docs/contracts/api/upbit-gateway.openapi.yaml`은 웹소켓 경로와 메시지 스키마 위치만 확장 필드로 연결하고 프레임 정의를 복제하지 않는다.
- 실제 uvicorn 게이트웨이와 가짜 Upbit 웹소켓 프로세스, 고립 화면과 브라우저→Vite 프록시→게이트웨이→가짜 Upbit 전체 경로를 Playwright로 자동 검증했다.
- 공용 작업대의 `WebSocket API` 메뉴에 컴포넌트를 연결하고 REST 작업대와 공통 페어 상태를 공유한다. 제품·아키텍처·ADR의 최종 통합 상태는 Issue #24에서 동기화한다.

## 리스크와 후속 작업

- 프로세스 메모리 연결 제한기는 단일 게이트웨이 인스턴스 경계다. 다중 복제 운영은 공유 제한 저장소와 포켓 단위 조정이 필요하다.
- 실제 Upbit 비공개 이벤트는 계정 자산·주문 변경 때만 오므로 자동화 검증에서는 의도적으로 생성하지 않았다.
- Issue #24에서 배포 구성, Quotation·Exchange·WebSocket 교차 기능 QA, 최종 문서·인계를 완료해야 한다.
