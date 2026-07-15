# 2026-07-16 업비트 Quotation API 작업대 구현

Date: 2026-07-16
Status: Completed
Related Issue: [#21](https://github.com/goodjoon-company/goodmoneying/issues/21)
Verification: [검증 증적](../Test/2026-07-16-업비트-Quotation-API-작업대-검증.md)

## 변경 요약

- 기존 브라우저 직접 Upbit 호출과 전역 10초 대기 큐를 제거하고 `/upbit-gateway` 카탈로그·실행 경계만 사용한다.
- 사이드바에 `업비트 API 테스트` 아래 Quotation·Exchange·WebSocket 2레벨 메뉴를 추가했다.
- Quotation 활성 12개와 사용 중단 1개를 페어·캔들·체결·현재가·호가 탭에 노출한다.
- 카탈로그의 열거형(enum)·날짜·숫자·배열·불리언(boolean) 타입과 필수·선택 조건으로 입력 폼을 생성한다.
- 페어 목록·캔들 차트·체결 표·현재가 카드·호가 사다리와 원본 추적 대화상자를 제공한다.
- 캔들 가장자리 이동 시 과거·미래 페이지를 이어 받고 `to`·`count`·중복 제거·현재 시각 종료·요청 취소·화면 범위 유지를 적용한다.
- Vite와 Nginx에 동일 출처 `/upbit-gateway` 프록시(proxy)를 연결하고 브라우저 번들에 키를 포함하지 않는다.

## #22·#23 확장 계약

통합 이슈 #24는 `UpbitApiWorkbench`의 다음 공개 경계를 사용한다.

- `moduleId`: `quotation | exchange | websocket` 중 현재 2레벨 메뉴를 선택한다.
- `market`·`onMarketChange`: 세 모듈에서 공유할 제어형 공통 거래쌍을 주입하고 변경을 상위 셸로 알린다.
- `extensions`: `WorkbenchModuleExtension[]`으로 #22 Exchange 또는 #23 WebSocket 컴포넌트를 실제 슬롯에 주입한다.
- 주입 컴포넌트는 `WorkbenchExtensionProps`의 `context`와 `onContextChange`를 받아 거래쌍·마켓(Quote)·기준 자산(Base)을 공유한다.
- `WorkbenchCommonSelection`은 확장 모듈에서도 같은 선택 UI를 재사용하는 공개 컴포넌트다.
- 슬롯이 연결되지 않으면 Issue 번호가 있는 명시적 대기 화면을 표시한다.

## 운영과 테스트 영향

- 개발 Vite는 기본 `http://127.0.0.1:8001`, Docker Web은 `GOODMONEYING_UPBIT_GATEWAY_INTERNAL_URL`의 게이트웨이로 전달한다.
- Playwright는 실제 Upbit를 가로채지 않고 격리 FastAPI에 실제 카탈로그와 가짜 추적 응답을 제공한다.
- E2E는 실제 `api.upbit.com` 네트워크 요청이 0건인지 별도로 기록한다.
- 제품·아키텍처·ADR 단일 기준 문서는 #24 통합 브랜치의 소유권과 충돌하지 않도록 이 브랜치에서 수정하지 않았다.
