# 2026-07-18 P6-1 Upbit 주문 guardrail 검증

## 범위

- 공식 Upbit 주문 계약의 identifier 최대 64자 제약을 Gateway 카탈로그와 validator에 반영한다.
- `time_in_force=post_only`와 SMP `smp_type` 동시 사용을 상향 호출 전 거부한다.
- 취소 후 재주문(cancel-and-new-order)의 `new_time_in_force=post_only`와 `new_smp_type` 동시 사용을 계약에서 금지한다.
- 실제 주문 endpoint는 계속 차단하고 `order-test`만 테스트 안전 수준으로 유지한다.

## 공식 문서 확인

- `POST /v1/orders`: identifier 최대 64자, `post_only`는 `smp_type`과 함께 사용할 수 없음
- `POST /v1/orders/test`: 실제 주문·체결을 만들지 않으며 반환 UUID·identifier는 조회·취소 대상이 아님
- `myOrder`: 후속 P6 범위에서 무이벤트 정상 처리와 REST 대사를 구현해야 함

## RED

- `uv run pytest tests/upbit_gateway/test_client.py::test_order_identifier_length_and_post_only_smp_conflict_are_rejected_locally tests/contracts/test_upbit_gateway_contract.py::test_catalog_defines_parameter_input_constraints_and_defaults -q`
  - 결과: `2 failed`
  - 원인 1: 65자 identifier가 `InvalidParameters`를 발생시키지 않았다.
  - 원인 2: `rest.new-order`/`rest.order-test` identifier에 `max_length` 계약이 없었다.

## GREEN

- `uv run pytest tests/upbit_gateway/test_client.py::test_order_identifier_length_and_post_only_smp_conflict_are_rejected_locally tests/contracts/test_upbit_gateway_contract.py::test_catalog_defines_parameter_input_constraints_and_defaults -q`
  - 결과: `2 passed`

## 확장 회귀

- 코드 리뷰 반영 후 targeted:
  - `uv run pytest tests/upbit_gateway/test_client.py::test_order_identifier_length_and_post_only_smp_conflict_are_rejected_locally tests/upbit_gateway/test_process_e2e.py::test_actual_gateway_process_rejects_order_guardrails_before_upstream_call tests/contracts/test_upbit_gateway_contract.py::test_catalog_defines_parameter_input_constraints_and_defaults -q`
  - 결과: `3 passed`
- `uv run pytest tests/upbit_gateway/test_client.py tests/upbit_gateway/test_executor.py tests/contracts/test_upbit_gateway_contract.py -q`
  - 중간 결과: `1 failed, 58 passed`
  - 원인: 공식 스냅숏 fixture가 새 identifier 64자 projection을 반영하지 않았다.
- fixture 갱신 후 재실행:
  - `uv run pytest tests/upbit_gateway/test_client.py tests/upbit_gateway/test_executor.py tests/contracts/test_upbit_gateway_contract.py -q`
  - 결과: `59 passed`
- `uv run ruff check apps/upbit_gateway/goodmoneying_upbit_gateway/client.py tests/upbit_gateway/test_client.py tests/contracts/test_upbit_gateway_contract.py`
  - 결과: `All checks passed!`
- `uv run mypy apps/upbit_gateway/goodmoneying_upbit_gateway tests/upbit_gateway tests/contracts`
  - 결과: `Success: no issues found in 50 source files`
- `uv run pytest tests/upbit_gateway/test_process_e2e.py -q`
  - 결과: `6 passed`
- 코드 리뷰 반영 후 관련 회귀:
  - `uv run pytest tests/upbit_gateway/test_client.py tests/upbit_gateway/test_executor.py tests/upbit_gateway/test_process_e2e.py tests/contracts/test_upbit_gateway_contract.py -q`
  - 결과: `66 passed`
- `uv run mypy apps/api apps/worker packages/shared tests`
  - 결과: `Success: no issues found in 151 source files`
- `uv run pytest -q`
  - 중간 결과: `768 passed, 136 skipped, 1 warning in 60.56s`
- 코드 리뷰 반영 후 `uv run pytest -q`
  - 결과: `769 passed, 136 skipped, 1 warning in 60.33s`
- `git diff --check`
  - 결과: 통과

## 안전 경계

- 새 네트워크 호출, credential 조회, 실제 주문 제출은 없다.
- `rest.new-order`, 취소, 취소 후 재주문은 계속 `safety: blocked`다.
- `rest.order-test`는 로컬 파라미터 검증을 통과한 요청만 상향 호출 대상으로 남는다.
