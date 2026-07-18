from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

MyOrderState = Literal[
    "wait",
    "watch",
    "trade",
    "done",
    "cancel",
    "prevented",
    "rejected",
]
ObservedMyOrderStatus = Literal[
    "no_event",
    "working",
    "partial_fill",
    "done",
    "cancel",
    "prevented",
    "rejected",
]


class InvalidMyOrderEvent(ValueError):
    """Upbit myOrder event가 내부 대사 입력 계약을 만족하지 않는다."""


@dataclass(frozen=True)
class UpbitMyOrderEvent:
    code: str | None
    uuid: str | None
    identifier: str | None
    state: MyOrderState
    volume: Decimal | None
    remaining_volume: Decimal | None
    executed_volume: Decimal | None
    trade_fee: Decimal | None
    is_maker: bool | None
    prevented_volume: Decimal | None
    prevented_locked: Decimal | None
    raw: dict[str, object]


@dataclass(frozen=True)
class MyOrderReconciliationPlan:
    observed_status: ObservedMyOrderStatus
    rest_snapshot_required: bool
    can_resubmit: bool
    reason: str


def parse_myorder_event(payload: dict[str, object]) -> UpbitMyOrderEvent:
    if payload.get("type") != "myOrder":
        raise InvalidMyOrderEvent("myOrder event type이 아니다.")
    state = _state(payload.get("state"))
    return UpbitMyOrderEvent(
        code=_optional_str(payload.get("code")),
        uuid=_optional_str(payload.get("uuid")),
        identifier=_optional_str(payload.get("identifier")),
        state=state,
        volume=_optional_decimal(payload.get("volume")),
        remaining_volume=_optional_decimal(payload.get("remaining_volume")),
        executed_volume=_optional_decimal(payload.get("executed_volume")),
        trade_fee=_optional_decimal(payload.get("trade_fee")),
        is_maker=_optional_bool(payload.get("is_maker")),
        prevented_volume=_optional_decimal(payload.get("prevented_volume")),
        prevented_locked=_optional_decimal(payload.get("prevented_locked")),
        raw=dict(payload),
    )


def plan_myorder_reconciliation(
    events: list[UpbitMyOrderEvent],
) -> MyOrderReconciliationPlan:
    if not events:
        return MyOrderReconciliationPlan(
            observed_status="no_event",
            rest_snapshot_required=True,
            can_resubmit=False,
            reason="no_initial_snapshot",
        )
    latest = events[-1]
    return MyOrderReconciliationPlan(
        observed_status=_observed_status(latest),
        rest_snapshot_required=True,
        can_resubmit=False,
        reason="myorder_event_requires_rest_reconciliation",
    )


def _observed_status(event: UpbitMyOrderEvent) -> ObservedMyOrderStatus:
    if event.state == "trade" and (event.remaining_volume or Decimal("0")) > 0:
        return "partial_fill"
    if event.state in {"wait", "watch", "trade"}:
        return "working"
    if event.state in {"done", "cancel", "prevented", "rejected"}:
        return cast(ObservedMyOrderStatus, event.state)
    raise InvalidMyOrderEvent(f"지원하지 않는 myOrder 상태: {event.state}")


def _state(value: object) -> MyOrderState:
    if not isinstance(value, str):
        raise InvalidMyOrderEvent("myOrder state는 문자열이어야 한다.")
    if value not in {"wait", "watch", "trade", "done", "cancel", "prevented", "rejected"}:
        raise InvalidMyOrderEvent(f"지원하지 않는 myOrder state: {value}")
    return cast(MyOrderState, value)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        raise InvalidMyOrderEvent("myOrder 문자열 필드가 비어 있거나 문자열이 아니다.")
    return value


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise InvalidMyOrderEvent("myOrder boolean 필드가 boolean이 아니다.")
    return value


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidMyOrderEvent("myOrder 숫자 필드가 decimal로 변환되지 않는다.") from exc
    if decimal < 0:
        raise InvalidMyOrderEvent("myOrder 숫자 필드는 음수일 수 없다.")
    return decimal
