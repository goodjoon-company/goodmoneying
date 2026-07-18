# Upbit live 주문 결합 계약

상태: 승인됨(Accepted)

근거 공식 문서:

- Upbit `POST /v1/orders` 주문 생성은 주문 UUID와 사용자가 지정한 `identifier`를 응답한다.
- `identifier`는 계정 전체 주문 기준으로 유일하고 최대 64자다.
- 이 계약은 응답·조회·개인 주문 이벤트에서 관측한 UUID와 내부 `gm1_` identifier를 내부 `exchange_orders`에 결합하는 저장 계약이다.

## 범위

- `order_submit_response`, `rest_order_snapshot`, `myorder_event`에서 관측한 실제 주문 UUID와 identifier를 저장한다.
- 내부 `exchange_orders.execution_mode='live'` 행과 `live_order_identifiers`, `upbit_order_outbox`의 계좌·주문 의도·identifier 일치를 강제한다.
- `exchange_orders.execution_mode='live'` 행은 같은 트랜잭션 안에서 `upbit_live_exchange_order_bindings`와 결합되어야 커밋될 수 있다.
- 결합 증적이 생성되면 해당 `live_order_identifiers.status`를 `submitted`로 전이한다.
- 결합 증적은 append-only다.

## 안전 경계

- `POST /v1/orders 호출 없음`
- 주문 취소 호출 없음
- private WebSocket 연결 없음
- 출금 권한 요구 없음
- order-test 응답 UUID·identifier를 live exchange order로 결합할 수 없음
- 동일 주문 재제출은 허용하지 않으며 adapter 판단은 `can_resubmit=False`다.

## DB 불변식

- `upbit_live_exchange_order_bindings.exchange_order_id`는 `exchange_orders.execution_mode='live'` 행만 참조하며, live `exchange_orders`는 binding 없이 커밋될 수 없다.
- `exchange_orders.simulated_order_key`는 P6-7 범위에서 live 주문의 내부 결합 key로 재사용하며 Upbit `identifier`와 같아야 한다.
- `upbit_order_uuid`는 표준 UUID 형식이어야 한다.
- `upbit_identifier`는 내부 deterministic `gm1_` identifier여야 한다.
- `exchange_account_id`, `order_intent_id`, `live_order_identifier_id`, `upbit_order_outbox_id`는 모두 같은 주문 의도와 계좌에 귀속돼야 한다.
- `upbit_order_outbox.status`는 `ready`여야 한다.
- 같은 계좌에서 같은 Upbit UUID 또는 identifier는 한 번만 결합할 수 있다.
- CI·AI·service actor는 결합 증적을 생성할 수 없다.
