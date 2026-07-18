# P3-2 Strategy Studio

Related Task: [P3](../Task/P3.md), GitHub Issue #30

## 변경 요약

- Operations Console에 `Strategy Studio` 1차 메뉴를 추가하고 Strategy Studio 화면을 연결했다.
- 전략 그래프(Strategy Graph)는 시각 포인터 뷰와 `table` 기반 텍스트 대안을 함께 제공한다.
- 동적 edge 목록은 별도 의미적 목록으로 제공해 포인터 뷰의 이미지 대체 이름만으로 손실될 수 있는 연결 정보를 보완한다.
- 키보드 대체 편집기는 출력 신호 이름 변경, 순환 오류 edge 추가, 순환 오류 edge 제거를 마우스 없이 수행한다.
- 서버 검증 결과는 색상에만 의존하지 않고 안정 코드, node 위치, edge 위치, 메시지를 `role="alert"` 또는 `role="status"`로 표시한다.
- 서버 검증 응답은 요청 당시 graph snapshot과 현재 graph snapshot이 일치할 때만 반영해 늦게 도착한 과거 성공 응답이 현재 graph를 게시 가능하게 만들지 못하게 한다.
- 검증 요청과 게시 요청 실패는 각각 alert로 표시한다.
- 게시 실패 후 재시도는 같은 전략 정의와 같은 게시 멱등 키(idempotency key)를 재사용하고, 게시 성공 후에는 초안 변경 전까지 재게시 버튼을 비활성화한다.
- 검증을 통과한 graph만 신규 전략 정의와 불변 전략 version으로 게시한다.
- seeded E2E API에 in-memory 전략 저장소 fixture를 추가하되, production validator 응답을 보정하지 않고 브라우저 E2E가 실제 REST 검증·게시 경계를 통과한다.

## 설계 정리

- Strategy Studio는 P3-1에서 확정한 `/v1/strategy-graphs/validate`, `/v1/strategies`, `/v1/strategies/{strategyId}/versions` 계약을 그대로 소비한다.
- UI 편집 상태는 클라이언트 graph 초안이며, canonical hash와 최종 검증 판정은 서버 검증기를 단일 기준(source of truth)으로 둔다.
- 출력 신호 이름은 별도 UI 전용 필드가 아니라 `StrategyGraphOutput.port`와 해당 output port 이름을 함께 변경해 계약 graph 안에 남긴다.
- 접근성 검증은 포인터 뷰만 두지 않고 텍스트 대안과 키보드 흐름을 같은 E2E 시나리오에서 확인한다.

## 검증

- 표적 검증은 [P3-2 Strategy Studio 검증](../Test/2026-07-18-P3-2-Strategy-Studio-검증.md)에 기록한다.
- 전체 Python 회귀, 전체 Playwright E2E, Docker compose build까지 [P3-2 Strategy Studio 검증](../Test/2026-07-18-P3-2-Strategy-Studio-검증.md)에 기록한다.
- 원격 CI 증적은 브랜치 푸시 뒤 추가 기록한다.

## 후속

- 백테스트 실행, 체결 모델, 봇·주문·위험 연결은 P4 이후 범위로 유지한다.
- 주식시장 기능과 타 거래소 실제 연동은 현재 목표 범위에서 제외한다.
