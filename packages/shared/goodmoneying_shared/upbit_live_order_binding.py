from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

UpbitLiveOrderBindingSource = Literal[
    "order_submit_response",
    "rest_order_snapshot",
    "myorder_event",
]

_LIVE_IDENTIFIER = re.compile(r"^gm1_[a-z2-7]{52}$")
_UPBIT_ORDER_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_ALLOWED_SOURCES: frozenset[str] = frozenset(
    {"order_submit_response", "rest_order_snapshot", "myorder_event"}
)


class InvalidUpbitLiveOrderBinding(ValueError):
    """Upbit live 주문 결합 증적이 내부 계약을 만족하지 않는다."""


@dataclass(frozen=True)
class UpbitLiveOrderBindingCandidate:
    upbit_order_uuid: str
    upbit_identifier: str
    source: UpbitLiveOrderBindingSource
    can_reconcile: bool
    can_submit: bool
    can_cancel: bool
    can_resubmit: bool


def build_upbit_live_order_binding_candidate(
    *,
    order_uuid: object,
    identifier: object,
    source: object,
) -> UpbitLiveOrderBindingCandidate:
    normalized_uuid = _order_uuid(order_uuid)
    normalized_identifier = _live_identifier(identifier)
    normalized_source = _source(source)
    return UpbitLiveOrderBindingCandidate(
        upbit_order_uuid=normalized_uuid,
        upbit_identifier=normalized_identifier,
        source=normalized_source,
        can_reconcile=True,
        can_submit=False,
        can_cancel=False,
        can_resubmit=False,
    )


def _non_blank(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidUpbitLiveOrderBinding(f"{field_name} 값이 비어 있다.")
    return value.strip()


def _live_identifier(value: object) -> str:
    identifier = _non_blank(value, "identifier")
    if _LIVE_IDENTIFIER.fullmatch(identifier) is None:
        raise InvalidUpbitLiveOrderBinding(
            "Upbit live 주문 identifier는 내부 gm1_ identifier여야 한다."
        )
    return identifier


def _order_uuid(value: object) -> str:
    order_uuid = _non_blank(value, "order_uuid").lower()
    if _UPBIT_ORDER_UUID.fullmatch(order_uuid) is None:
        raise InvalidUpbitLiveOrderBinding(
            "Upbit live 주문 UUID는 표준 UUID 형식이어야 한다."
        )
    return order_uuid


def _source(value: object) -> UpbitLiveOrderBindingSource:
    source = _non_blank(value, "source")
    if source not in _ALLOWED_SOURCES:
        raise InvalidUpbitLiveOrderBinding(
            "Upbit live 주문 결합 source는 실제 주문 응답 또는 주문조회 증적이어야 한다."
        )
    return source  # type: ignore[return-value]
