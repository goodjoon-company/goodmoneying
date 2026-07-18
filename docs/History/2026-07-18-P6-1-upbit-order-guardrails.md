# 2026-07-18 P6-1 Upbit 주문 guardrail 인계

## 변경 요약

- Gateway validator에 문자열 길이(`min_length`, `max_length`) 검사와 값 기반 금지 조합(`forbidden_value_combinations`) 검사를 추가했다.
- Upbit 주문 카탈로그에서 주문 identifier 최대 64자 제약을 명시했다.
- `time_in_force=post_only`와 `smp_type`, `new_time_in_force=post_only`와 `new_smp_type` 조합을 계약에서 금지했다.
- 문서 카탈로그와 패키지 내장 카탈로그를 동일하게 갱신했다.
- 공식 REST 스냅숏 fixture를 주문 identifier 64자 projection과 맞췄다.
- Product, Architecture, P6 Task, Test 증적을 P6-1 진행 상태로 갱신했다.

## 안전 경계

- 실제 주문 생성(`rest.new-order`)과 취소 계열 endpoint는 계속 `safety: blocked`다.
- 주문 테스트(`rest.order-test`)는 실제 주문·체결을 만들지 않는 공식 테스트 endpoint로만 유지한다.
- private 주문 WebSocket, live capability, 실제 주문 adapter, 출금 권한 관련 변경은 없다.

## 검증

- 상세 증적: `docs/Test/2026-07-18-P6-1-upbit-order-guardrails-검증.md`
- RED: `uv run pytest tests/upbit_gateway/test_client.py::test_order_identifier_length_and_post_only_smp_conflict_are_rejected_locally tests/contracts/test_upbit_gateway_contract.py::test_catalog_defines_parameter_input_constraints_and_defaults -q` → `2 failed`
- GREEN: 같은 명령 → `2 passed`
- 확장 회귀: `uv run pytest tests/upbit_gateway/test_client.py tests/upbit_gateway/test_executor.py tests/contracts/test_upbit_gateway_contract.py -q` → `59 passed`
- 코드 리뷰 반영 targeted: `uv run pytest tests/upbit_gateway/test_client.py::test_order_identifier_length_and_post_only_smp_conflict_are_rejected_locally tests/upbit_gateway/test_process_e2e.py::test_actual_gateway_process_rejects_order_guardrails_before_upstream_call tests/contracts/test_upbit_gateway_contract.py::test_catalog_defines_parameter_input_constraints_and_defaults -q` → `3 passed`
- 코드 리뷰 반영 관련 회귀: `uv run pytest tests/upbit_gateway/test_client.py tests/upbit_gateway/test_executor.py tests/upbit_gateway/test_process_e2e.py tests/contracts/test_upbit_gateway_contract.py -q` → `66 passed`
- Process E2E: `uv run pytest tests/upbit_gateway/test_process_e2e.py -q` → `6 passed`
- 전체 mypy: `uv run mypy apps/api apps/worker packages/shared tests` → `Success: no issues found in 151 source files`
- 전체 pytest: `uv run pytest -q` → `769 passed, 136 skipped, 1 warning in 60.33s`
- whitespace: `git diff --check` → 통과

## 후속 범위

- order-test 증적과 실제 주문 식별자 격리
- `live_disabled` capability gate
- private `myOrder` 무이벤트 정상 처리와 REST 대사
- 안전 주문 adapter, outbox, 출금 권한 미사용 readiness 검증
