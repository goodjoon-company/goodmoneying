# 2026-07-18 P6-4 myOrder 대사 입력 계약 인계

## 변경 요약

- `docs/contracts/upbit/myorder-event.md` 계약을 추가했다.
- `parse_myorder_event()`와 `plan_myorder_reconciliation()` shared utility를 추가했다.
- 무이벤트를 `no_event`, REST snapshot 필요, 재주문 금지로 평가한다.
- `state=prevented`의 `prevented_volume`, `prevented_locked`와 nullable `trade_fee`, `is_maker`를 보존한다.
- `state=trade`와 잔량이 남은 event를 `partial_fill`로 분류한다.
- Product, Architecture, contracts README, P6 Task 문서를 P6-4 상태로 현행화했다.

## 안전 경계

- 실제 private WebSocket 연결을 추가하지 않았다.
- 실제 Upbit REST 조회, 주문 제출, 취소, 주문 outbox를 만들지 않았다.
- 모든 대사 계획은 `can_resubmit=false`다.

## 검증

- 상세 증적: `docs/Test/2026-07-18-P6-4-myorder-input-검증.md`
- RED: `uv run pytest tests/shared/test_upbit_myorder.py tests/contracts/test_p6_myorder_contract.py -q` → 모듈 부재로 수집 실패
- GREEN: 같은 명령 → `6 passed`
- Ruff: 관련 파일 `All checks passed!`
- Mypy: 관련 파일 `Success: no issues found in 3 source files`

## 후속 범위

- private `myOrder` REST snapshot 적용과 내부 원장 반영
- 안전 주문 adapter와 outbox
