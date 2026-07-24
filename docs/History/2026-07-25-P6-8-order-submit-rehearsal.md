# 2026-07-25 P6-8 주문 제출 리허설 변경 이력

## 변경 요약

- `upbit_order_submit_rehearsals` append-only DB 계약을 추가했다.
- 리허설 증적은 `rest.new-order`, `POST`, `/v1/orders`로 고정하고 실제 요청 전송 여부를 `false`로만 저장한다.
- `request_payload`와 `request_hash`가 outbox와 일치하고, payload의 `identifier`가 reserved live identifier와 일치해야 한다.
- `passed` 리허설은 ready outbox, reserved live identifier, 만료되지 않은 permission attestation, 기존 live binding 부재를 모두 만족해야 한다.
- 응답 UUID와 응답 identifier는 리허설 증적에 저장할 수 없다.
- shared adapter는 주문 payload 정규화, query string, SHA-512 query hash, SHA-256 request hash만 생성한다.
- shared adapter는 공식 주문 생성 허용 필드 외의 key를 거부하며, 시장가 주문(`price|market`)에 `time_in_force`가 포함되면 리허설을 거부한다.
- DB migration E2E 목록에 P6-8 live PostgreSQL 검증을 추가했다.
- P6-8 live PostgreSQL E2E는 adapter가 만든 canonical payload, request hash, query string, query hash를 outbox와 rehearsal insert에 실제로 사용한다.

## 안전 경계

- 실제 `POST /v1/orders` 호출 없음
- 실제 주문 취소, REST client, private WebSocket 추가 없음
- `actual_request_sent=false`, `would_submit=false`, `can_bind_response=false`
- CI·AI·service actor 증적 생성 불가
- 실제 제출 worker와 live 주문 대사 적용은 후속 범위

## 추적성

- Product/Task: `docs/Task/P6.md`
- Architecture: `docs/02_Architecture/system-trading-domain.md`
- DB Contract: `docs/contracts/db/migrations/20260718001300_p6_order_submit_rehearsal.sql`
- Upbit Contract: `docs/contracts/upbit/order-submit-rehearsal.md`
- Test Evidence: `docs/Test/2026-07-25-P6-8-order-submit-rehearsal-검증.md`
