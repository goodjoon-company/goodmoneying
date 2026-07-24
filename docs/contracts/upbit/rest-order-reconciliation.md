# Upbit REST 주문 snapshot 대사 계약

상태: P6-9 구현 기준
검증일: 2026-07-25

## 목적

private `myOrder`는 initial snapshot을 보내지 않으므로, 서비스는 주문조회 권한이 있는 Upbit REST 주문 조회 응답을 내부 대사(reconciliation) 입력으로 사용한다. 이 계약은 네트워크 호출(client)이 아니라 이미 수신한 REST snapshot을 내부 원장(ledger)에 적용하기 전의 정규화(normalization) 규칙이다.

## 공식 기준

- `GET /v1/order`: UUID 또는 identifier로 단일 주문을 조회한다. 둘 중 하나는 필요하며, 둘 다 있으면 UUID 기준이다. 주문조회 권한이 필요하고 Exchange default 요청 제한 그룹을 쓴다.
- `GET /v1/orders/uuids`: UUID 목록 또는 identifier 목록 중 하나로 최대 100개 주문을 조회한다. 두 배열은 동시에 쓰지 않는다. 주문조회 권한이 필요하다.
- `GET /v1/orders/open`: `wait|watch` 체결 대기(open) 주문 목록을 조회한다. `state`와 `states[]`는 동시에 쓰지 않는다. 주문조회 권한이 필요하다.
- `GET /v1/orders/closed`: `done|cancel` 종료(closed) 주문 목록을 조회한다. 조회 기간 window는 최대 7일이다. 주문조회 권한이 필요하다.

SMP(Self-Match Prevention) 필드인 `smp_type`, `prevented_volume`, `prevented_locked`는 snapshot 증거(evidence)에 보존한다.

## 내부 정규화

`parse_upbit_rest_order_snapshot()`은 단일 REST 주문 객체를 다음 내부 입력으로 정규화한다.

- `uuid`, `identifier`, `market`, `side`, `state`, `paid_fee`, `prevented_volume`, `prevented_locked`, `trades_count`, `trades`, `knowledge_at`, `source_endpoint`
- `side=bid`는 내부 `buy`, `side=ask`는 내부 `sell`이다.
- Decimal 문자열은 음수일 수 없다.
- `trades_count`와 `trades` 개수가 다르면 snapshot을 거부한다.
- 체결량이 있는 terminal snapshot은 `trades` 없이 원장 fill로 확정하지 않는다.
- 다중 체결에서 REST 응답이 order-level `paid_fee`만 제공하면 각 trade의 `funds` 비율로 fee를 분배하고 마지막 fill에서 잔여 오차를 흡수한다.

## 원장 적용 규칙

- `done|cancel|prevented|rejected` terminal snapshot만 기존 `reconcile_exchange_order()` 입력으로 변환한다.
- `wait|watch|trade` 진행 중 snapshot은 `observe_only`로 처리하고 기존 terminal 대사 원장을 변경하지 않는다.
- 모든 plan은 `can_resubmit=false`다. snapshot 확인 후에도 동일 주문을 재주문하지 않는다.
- 이 adapter는 실제 REST 호출, private WebSocket 연결, 주문 제출, 주문 취소를 포함하지 않는다.
- `paper|shadow` 주문은 이 계약만으로 기존 대사 원장에 적용한다.
- `live` 주문은 [Upbit live 주문 대사 적용 계약](live-order-reconciliation.md)에 따라 `upbit_live_exchange_order_bindings`와 REST snapshot UUID·identifier가 일치할 때만 적용 증적을 남긴다.

## 멱등성과 증거

원장 적용 시 `reconciliation_runs.evidence`와 각 `order_fills.evidence`에는 다음을 남긴다.

- `source=upbit-rest-order-snapshot`
- `sourceEndpoint`
- `market`
- `orderUuid`
- `identifier`
- `state`
- `paidFee`
- `preventedVolume`
- `preventedLocked`
- `tradesCount`
- fill별 `tradeUuid`

같은 `run_key`와 같은 payload는 기존 P5-5 대사 멱등성(idempotency) 규칙으로 흡수한다. 같은 `run_key`에 다른 snapshot을 넣으면 기존 대사 충돌 규칙에 따라 거부한다.
