# Upbit 주문 제출 리허설 계약

상태: 승인됨(Accepted)

근거 공식 문서:

- Upbit 주문 생성은 `POST /v1/orders` JSON 본문으로 수행하며, 주문하기 권한이 필요하다.
- `identifier`는 계정 전체 주문 기준으로 유일하고 최대 64자다.
- `post_only`는 `smp_type`과 함께 사용할 수 없다.
- 주문 생성 테스트(order-test)는 실제 주문을 생성하지 않으며, 테스트 응답 UUID·identifier는 주문 조회나 취소에 사용할 수 없다.

## 범위

- `upbit_order_outbox.status='ready'` 행을 실제 주문 worker가 소비하기 전에 공식 주문 생성 요청 형식으로 정규화한다.
- REST 인증에 필요한 query hash 입력 문자열(query string)과 SHA-512 query hash를 증적으로 저장할 수 있게 한다.
- outbox, live identifier, permission attestation이 같은 거래소 계좌(exchange account)와 주문 의도(order intent)에 귀속되는지 DB에서 검증한다.
- shared adapter는 공식 주문 생성 허용 필드만 리허설하고 알 수 없는 key를 조용히 버리지 않는다.
- 리허설은 append-only 증적이다.

## 안전 경계

- 실제 주문을 전송하지 않는다.
- `actual_request_sent=false`만 저장한다.
- `would_submit=false`만 저장한다.
- `can_bind_response=false`만 저장한다.
- 응답 UUID 또는 응답 identifier를 저장할 수 없다.
- 리허설은 `live_order_identifiers.status='reserved'` 상태만 통과할 수 있다.
- 이미 `upbit_live_exchange_order_bindings`에 결합된 outbox 또는 live identifier는 다시 리허설할 수 없다.
- CI·AI·service actor는 리허설 증적을 생성할 수 없다.
- 시장가 주문(`ord_type='price'|'market'`)은 `time_in_force`와 함께 리허설할 수 없다.

## DB 불변식

- `upbit_order_submit_rehearsals.upbit_order_outbox_id`는 unique이며 한 outbox에 하나의 리허설 증적만 남긴다.
- `request_payload`와 `request_hash`는 참조한 outbox의 값과 같아야 한다.
- `request_payload.identifier`는 참조한 `live_order_identifiers.identifier`와 같아야 한다.
- `rehearsal_status='passed'`는 ready outbox, reserved live identifier, 만료되지 않은 permission attestation만 허용한다.
- `endpoint_key='rest.new-order'`, `http_method='POST'`, `request_path='/v1/orders'`만 허용한다.
- `query_hash`는 SHA-512 hex 128자 형식이어야 한다.
