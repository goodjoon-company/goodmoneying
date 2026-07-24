# Upbit live 주문 대사(reconciliation) 적용 계약

상태: 승인됨(Accepted)
검증일: 2026-07-25

## 목적

이 계약은 이미 결합된 Upbit live 주문을 REST 주문 snapshot으로 내부 원장(ledger)에 적용하는 경계를 정의한다. 대상은 `upbit_live_exchange_order_bindings`로 UUID와 `gm1_` identifier가 내부 `exchange_orders.execution_mode='live'` 행에 결합된 주문뿐이다.

## 공식 기준

- `GET /v1/order`: UUID 또는 identifier로 단일 주문을 조회한다. UUID와 identifier를 함께 주면 UUID 기준이며 주문조회 권한이 필요하다.
- `GET /v1/orders/uuids`: UUID 배열 또는 identifier 배열 중 하나로 주문을 조회한다. 두 배열을 동시에 쓰지 않는다.
- `GET /v1/orders/open`: `wait|watch` 진행 중 주문 조회다.
- `GET /v1/orders/closed`: `done|cancel` 종료 주문 조회다. 조회 기간 window는 최대 7일이다.
- private `myOrder` WebSocket은 initial snapshot을 보내지 않으므로, terminal 원장 적용은 REST snapshot으로 확정한다.

## 적용 범위

- `source='rest_order_snapshot'`인 terminal REST snapshot만 live 원장 적용 증적으로 저장한다.
- `done|cancel|prevented|rejected` snapshot만 `reconcile_exchange_order()`에 적용한다.
- `wait|watch|trade` snapshot은 binding 일치만 확인하고 observe-only로 반환한다.
- snapshot의 `uuid`와 `identifier`는 `upbit_live_exchange_order_bindings.upbit_order_uuid`, `upbit_identifier`와 모두 일치해야 한다.
- 적용 증적은 `upbit_live_reconciliation_applications`에 append-only로 남긴다.
- 적용 증적은 `reconciliation_runs.evidence`의 `sourceEndpoint`, `orderUuid`, `identifier`, `state`, `canResubmit=false`와 DB trigger로 다시 대조한다.
- live `reconciliation_runs(status='succeeded')`는 같은 트랜잭션 안에서 대응하는 `upbit_live_reconciliation_applications`가 있어야 커밋될 수 있다.

## 안전 경계

- 실제 REST 호출을 만들지 않는다.
- 주문 제출을 하지 않는다.
- 주문 취소를 하지 않는다.
- private WebSocket 연결을 열지 않는다.
- 동일 주문 재제출은 허용하지 않으며 `can_resubmit=false`다.
- 실제 요청·취소 여부는 `actual_request_sent=false`, `actual_order_cancel_sent=false`로만 기록된다.
- CI·AI·service actor는 live 대사 적용 증적을 만들 수 없다.

## 멱등성과 증거

`apply_upbit_live_reconciliation_application()`은 원장 대사와 live 적용 증적을 같은 DB transaction에서 기록한다. 내부의 application request hash는 같은 `idempotency_key`와 같은 payload를 기존 증적으로 흡수한다. 같은 key에 다른 payload가 들어오면 live 대사 적용 멱등성 충돌로 거부한다.

저장 증거에는 다음을 남긴다.

- `live_exchange_order_binding_id`
- `reconciliation_run_id`
- `source_endpoint`
- `observed_upbit_order_uuid`
- `observed_upbit_identifier`
- `observed_state`
- `can_resubmit=false`
- `actual_request_sent=false`
- `actual_order_cancel_sent=false`
