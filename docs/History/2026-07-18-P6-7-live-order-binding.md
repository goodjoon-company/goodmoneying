# 2026-07-18 P6-7 live 주문 결합 변경 이력

## 변경 요약

- `exchange_orders.execution_mode='live'`를 DB 계약에 추가했다.
- live `exchange_orders`는 같은 트랜잭션 안에서 binding이 생성될 때만 커밋되도록 지연 제약 트리거(deferrable constraint trigger)를 추가했다.
- `upbit_live_exchange_order_bindings`로 Upbit 주문 UUID와 내부 `gm1_` identifier를 내부 `exchange_orders`에 결합하는 append-only 증적을 추가했다.
- 결합 증적은 `exchange_orders`, `live_order_identifiers`, `upbit_order_outbox`의 계좌·주문 의도·identifier 일치를 강제한다.
- Upbit 주문 UUID는 표준 UUID 형식으로 제한한다.
- order-test 응답 UUID·identifier는 live exchange order로 결합할 수 없다.
- 결합 증적 생성 시 `live_order_identifiers.status`를 `submitted`로 전이한다.

## 안전 경계

- 실제 `POST /v1/orders` 호출 없음
- 실제 주문 취소, REST client, private WebSocket 추가 없음
- binding 없는 live `exchange_orders` 커밋 불가
- live 주문 대사 적용과 submit worker는 후속 범위
