# Upbit myOrder event 내부 대사 입력 계약

P6-4는 private `myOrder` WebSocket event를 내부 대사(reconciliation) 입력으로 해석하는 계약만 정의한다. 실제 private 연결, live 주문 제출, 취소, REST 조회 실행은 후속 slice 범위다.

## 공식 동작 기준

- `myOrder`는 initial snapshot을 보내지 않는다.
- 연결 직후 무이벤트는 정상이며 주문이 없거나 변경이 없다는 의미일 수 있다.
- 무이벤트는 성공·실패로 확정하지 않고 REST snapshot 대사를 수행한다.
- 대사 결과가 불확실하면 동일 주문을 재주문하지 않는다.

## 보존해야 하는 필드

- 주문 식별자: `uuid`, `identifier`, `code`
- 상태: `wait`, `watch`, `trade`, `done`, `cancel`, `prevented`, `rejected`
- 체결 수량: `volume`, `remaining_volume`, `executed_volume`
- 수수료·maker: `trade_fee`, `is_maker`
- SMP(Self-Match Prevention) 결과: `prevented_volume`, `prevented_locked`

`trade_fee`와 `is_maker`는 nullable이다. `prevented_volume`, `prevented_locked`는 `state=prevented`를 실패로 단순 변환하지 않고 보존한다.

## 내부 계획

- event 목록이 비어 있으면 `observed_status=no_event`, `rest_snapshot_required=true`, `can_resubmit=false`다.
- `state=trade`이고 `remaining_volume > 0`이면 `observed_status=partial_fill`이다.
- terminal 또는 SMP 상태도 REST snapshot 대사 대상이며 `can_resubmit=false`다.
- 이 계약은 주문 outbox를 만들지 않고 Upbit 상향 호출을 수행하지 않는다.
