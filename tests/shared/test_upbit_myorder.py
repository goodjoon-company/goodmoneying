from __future__ import annotations

from decimal import Decimal

import pytest

from goodmoneying_shared.upbit_myorder import (
    InvalidMyOrderEvent,
    parse_myorder_event,
    plan_myorder_reconciliation,
)


def test_myOrder는_초기_snapshot_없음을_정상_무이벤트로_처리한다() -> None:
    plan = plan_myorder_reconciliation([])

    assert plan.observed_status == "no_event"
    assert plan.rest_snapshot_required is True
    assert plan.can_resubmit is False
    assert plan.reason == "no_initial_snapshot"


def test_myOrder_prevented는_SMP_필드를_보존하고_재주문을_금지한다() -> None:
    event = parse_myorder_event(
        {
            "type": "myOrder",
            "code": "KRW-BTC",
            "uuid": "exchange-uuid",
            "identifier": "gm1_abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqr",
            "state": "prevented",
            "prevented_volume": "0.25",
            "prevented_locked": "25000",
            "trade_fee": None,
            "is_maker": None,
        }
    )

    assert event.state == "prevented"
    assert event.prevented_volume == Decimal("0.25")
    assert event.prevented_locked == Decimal("25000")
    assert event.trade_fee is None
    assert event.is_maker is None

    plan = plan_myorder_reconciliation([event])

    assert plan.observed_status == "prevented"
    assert plan.rest_snapshot_required is True
    assert plan.can_resubmit is False


def test_myOrder_부분_체결은_fee와_maker를_보존한다() -> None:
    event = parse_myorder_event(
        {
            "type": "myOrder",
            "code": "KRW-BTC",
            "uuid": "exchange-uuid",
            "identifier": "gm1_abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqr",
            "state": "trade",
            "volume": "1",
            "remaining_volume": "0.4",
            "executed_volume": "0.6",
            "trade_fee": "12.3",
            "is_maker": True,
        }
    )

    assert event.state == "trade"
    assert event.remaining_volume == Decimal("0.4")
    assert event.executed_volume == Decimal("0.6")
    assert event.trade_fee == Decimal("12.3")
    assert event.is_maker is True
    assert plan_myorder_reconciliation([event]).observed_status == "partial_fill"


def test_myOrder_알수없는_상태는_거부한다() -> None:
    with pytest.raises(InvalidMyOrderEvent):
        parse_myorder_event({"type": "myOrder", "state": "unknown"})
