# 2026-07-18 P6-4 myOrder 대사 입력 계약 검증

## 범위

- private `myOrder` event를 내부 대사 입력으로 해석하는 순수 parser를 추가한다.
- initial snapshot 없음과 무이벤트 정상 처리를 fail-safe 대사 계획으로 고정한다.
- `prevented`와 부분 체결 필드를 보존하고 동일 주문 재제출을 금지한다.

## RED

- `uv run pytest tests/shared/test_upbit_myorder.py tests/contracts/test_p6_myorder_contract.py -q`
  - 결과: 수집 오류
  - 원인: `goodmoneying_shared.upbit_myorder` 모듈이 없었다.

## GREEN

- `uv run pytest tests/shared/test_upbit_myorder.py tests/contracts/test_p6_myorder_contract.py -q`
  - 결과: `6 passed`
- `uv run ruff check packages/shared/goodmoneying_shared/upbit_myorder.py tests/shared/test_upbit_myorder.py tests/contracts/test_p6_myorder_contract.py`
  - 결과: `All checks passed!`
- `uv run mypy packages/shared/goodmoneying_shared/upbit_myorder.py tests/shared/test_upbit_myorder.py tests/contracts/test_p6_myorder_contract.py`
  - 1차 결과: Literal 반환 타입 오류 2건
  - 수정 후 결과: `Success: no issues found in 3 source files`

## 안전 경계

- 실제 private WebSocket 연결 없음
- 실제 Upbit REST 조회, 주문 제출, 취소 없음
- 주문 outbox 생성 없음
- parser는 비밀·권한·계좌 연결을 요구하지 않음
