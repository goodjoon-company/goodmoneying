from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from goodmoneying_shared.upbit_live_reconciliation import (
    InvalidUpbitLiveReconciliationApplication,
    apply_upbit_live_rest_order_snapshot,
)

NOW = datetime(2026, 7, 18, 20, tzinfo=UTC)
IDENTIFIER = "gm1_" + "b" * 52
ORDER_UUID = "12345678-1234-4234-9234-123456789abc"


def test_live_REST_terminal_snapshot은_binding_일치_후_원장과_application_증적에_적용된다() -> None:
    store = _FakeLiveReconciliationStore()

    result = apply_upbit_live_rest_order_snapshot(
        store,
        live_binding=_binding(),
        exchange_order_id=45,
        run_key="p6-live-run-1",
        actor_id="operator:test",
        reason="P6 live reconciliation",
        snapshot_payload=_done_snapshot(),
        knowledge_at=NOW,
        source_endpoint="GET /v1/order",
    )

    assert result["status"] == "succeeded"
    assert result["canResubmit"] is False
    assert result["actualRequestSent"] is False
    assert result["actualOrderCancelSent"] is False
    assert result["liveReconciliationApplicationId"] == 77
    assert store.apply_arguments["exchange_order_id"] == 45
    assert store.apply_arguments["observed_status"] == "done"
    assert store.apply_arguments["live_exchange_order_binding_id"] == 12
    assert store.apply_arguments["observed_upbit_order_uuid"] == ORDER_UUID
    assert store.apply_arguments["observed_upbit_identifier"] == IDENTIFIER
    assert store.apply_arguments["source_endpoint"] == "GET /v1/order"
    assert store.apply_arguments["can_resubmit"] is False
    assert store.apply_arguments["actual_request_sent"] is False
    assert store.apply_arguments["actual_order_cancel_sent"] is False


def test_live_REST_snapshot은_binding_UUID_identifier_불일치시_원장을_변경하지_않는다() -> None:
    store = _FakeLiveReconciliationStore()
    payload = {**_done_snapshot(), "uuid": str(uuid4())}

    with pytest.raises(InvalidUpbitLiveReconciliationApplication, match="UUID"):
        apply_upbit_live_rest_order_snapshot(
            store,
            live_binding=_binding(),
            exchange_order_id=45,
            run_key="p6-live-run-2",
            actor_id="operator:test",
            reason="P6 live reconciliation mismatch",
            snapshot_payload=payload,
            knowledge_at=NOW,
            source_endpoint="GET /v1/order",
        )

    assert store.apply_arguments == {}


def test_live_REST_working_snapshot은_observe_only이고_live_application을_남기지_않는다() -> None:
    store = _FakeLiveReconciliationStore()
    payload = {
        **_done_snapshot(),
        "state": "wait",
        "remaining_volume": "2",
        "executed_volume": "0",
        "paid_fee": "0",
        "trades_count": 0,
        "trades": [],
    }

    result = apply_upbit_live_rest_order_snapshot(
        store,
        live_binding=_binding(),
        exchange_order_id=45,
        run_key="p6-live-run-3",
        actor_id="operator:test",
        reason="P6 live reconciliation observe",
        snapshot_payload=payload,
        knowledge_at=NOW,
        source_endpoint="GET /v1/order",
    )

    assert result == {
        "status": "observed",
        "exchangeOrderId": 45,
        "observedStatus": "working",
        "canResubmit": False,
        "sourceEndpoint": "GET /v1/order",
        "liveBindingMatched": True,
    }
    assert store.apply_arguments == {}


def _binding() -> dict[str, object]:
    return {
        "id": 12,
        "exchange_account_id": 34,
        "order_intent_id": 56,
        "exchange_order_id": 45,
        "upbit_order_uuid": ORDER_UUID,
        "upbit_identifier": IDENTIFIER,
    }


def _done_snapshot() -> dict[str, object]:
    return {
        "market": "KRW-BTC",
        "uuid": ORDER_UUID,
        "identifier": IDENTIFIER,
        "side": "bid",
        "state": "done",
        "created_at": "2026-07-18T20:59:59+09:00",
        "volume": "2",
        "remaining_volume": "0",
        "executed_volume": "2",
        "paid_fee": "24",
        "trades_count": 1,
        "trades": [
            {
                "uuid": "trade-12345678",
                "price": "120",
                "volume": "2",
                "funds": "240",
                "side": "bid",
                "created_at": "2026-07-18T21:00:00+09:00",
            }
        ],
    }


class _FakeLiveReconciliationStore:
    def __init__(self) -> None:
        self.apply_arguments: dict[str, object] = {}

    def apply_upbit_live_reconciliation_application(
        self, **arguments: object
    ) -> dict[str, object]:
        self.apply_arguments = arguments
        return {
            "reconciliationRunId": 500,
            "exchangeOrderId": arguments["exchange_order_id"],
            "runKey": arguments["run_key"],
            "status": "succeeded",
            "observedStatus": arguments["observed_status"],
            "observedFillCount": 1,
            "completedAt": NOW,
            "liveReconciliationApplicationId": 77,
            "liveReconciliationApplicationStatus": "recorded",
        }
