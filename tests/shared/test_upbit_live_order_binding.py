from __future__ import annotations

import pytest

from goodmoneying_shared.upbit_live_order_binding import (
    InvalidUpbitLiveOrderBinding,
    build_upbit_live_order_binding_candidate,
)

VALID_IDENTIFIER = "gm1_" + "a" * 52


def test_live_order_binding은_uuid와_identifier를_대사_가능한_증적으로_정규화한다() -> None:
    candidate = build_upbit_live_order_binding_candidate(
        order_uuid="21e4a5a2-8c3b-4f2a-b7ef-3c1111111111",
        identifier=VALID_IDENTIFIER,
        source="order_submit_response",
    )

    assert candidate.upbit_order_uuid == "21e4a5a2-8c3b-4f2a-b7ef-3c1111111111"
    assert candidate.upbit_identifier == VALID_IDENTIFIER
    assert candidate.source == "order_submit_response"
    assert candidate.can_reconcile is True
    assert candidate.can_submit is False
    assert candidate.can_cancel is False
    assert candidate.can_resubmit is False


def test_live_order_binding은_order_test_source와_잘못된_identifier를_거부한다() -> None:
    with pytest.raises(InvalidUpbitLiveOrderBinding):
        build_upbit_live_order_binding_candidate(
            order_uuid="21e4a5a2-8c3b-4f2a-b7ef-3c1111111111",
            identifier="test-order-identifier",
            source="order_submit_response",
        )

    with pytest.raises(InvalidUpbitLiveOrderBinding):
        build_upbit_live_order_binding_candidate(
            order_uuid="21e4a5a2-8c3b-4f2a-b7ef-3c1111111111",
            identifier=VALID_IDENTIFIER,
            source="order_test_response",
        )

    with pytest.raises(InvalidUpbitLiveOrderBinding):
        build_upbit_live_order_binding_candidate(
            order_uuid="upbit-live-not-uuid",
            identifier=VALID_IDENTIFIER,
            source="rest_order_snapshot",
        )
