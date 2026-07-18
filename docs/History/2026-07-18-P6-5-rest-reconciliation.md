# 2026-07-18 P6-5 REST snapshot 대사 변경 이력

## 변경 요약

- P6-5에서 Upbit REST 주문 snapshot을 내부 원장 대사 입력으로 정규화하는 shared adapter를 추가했다.
- `GET /v1/order`, `GET /v1/orders/open`, `GET /v1/orders/closed`, `GET /v1/orders/uuids` 조회 경계를 계약 문서에 고정했다.
- terminal snapshot만 기존 P5-5 `reconcile_exchange_order()`에 적용하고, 진행 중 snapshot은 observe-only로 처리한다.

## 안전 경계

- 실제 Upbit REST client, private WebSocket 연결, 주문 제출, 주문 취소 없음
- `paper|shadow` 대사 경계를 `live`로 확장하지 않음
- 모든 REST snapshot plan은 `can_resubmit=false`

## 후속 작업

- 안전 주문 adapter에서 live 주문 UUID·identifier와 내부 `exchange_orders` 결합 계약을 추가한다.
- 주문·조회 권한 준비도와 출금 권한 미사용 검증을 추가한다.
