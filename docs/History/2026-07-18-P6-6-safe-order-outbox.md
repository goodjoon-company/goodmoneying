# 2026-07-18 P6-6 안전 주문 outbox 변경 이력

## 변경 요약

- Upbit API Key 권한 준비도 증적과 안전 주문 outbox DB 계약을 추가했다.
- 출금 권한이 있는 API Key는 준비도 증적으로 저장할 수 없게 했다.
- outbox는 실제 제출 시도를 기록하지 않고 `submit_attempt_count=0`으로 고정했다.
- `ready` outbox는 승인 완료(`approved`) 주문 의도(order intent)만 허용한다.
- 권한 증적(permission attestation)을 참조하는 outbox는 `blocked` 상태라도 같은 거래소 계좌(exchange account)에 귀속돼야 한다.
- shared adapter는 live capability, 권한 만료, 출금 권한, kill switch를 fail-closed로 평가한다.

## 안전 경계

- 실제 `POST /v1/orders` 호출 없음
- 실제 주문 취소, REST client, private WebSocket 추가 없음
- CI·AI·service actor의 권한 증적/outbox 생성 거부
