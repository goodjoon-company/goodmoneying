from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from goodmoneying_shared.upbit_rest_reconciliation import (
    InvalidUpbitRestOrderSnapshot,
    build_upbit_rest_reconciliation_request,
    parse_upbit_rest_order_snapshot,
    plan_upbit_rest_snapshot_reconciliation,
)


def test_Upbit_REST_종료_주문_snapshot은_기존_대사_fill로_정규화된다() -> None:
    knowledge_at = datetime(2026, 7, 18, 12, tzinfo=UTC)
    snapshot = parse_upbit_rest_order_snapshot(
        {
            "market": "KRW-BTC",
            "uuid": "upbit-order-uuid",
            "identifier": "gm1_abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqr",
            "side": "bid",
            "state": "done",
            "created_at": "2026-07-18T20:59:59+09:00",
            "volume": "1",
            "remaining_volume": "0",
            "executed_volume": "1",
            "paid_fee": "700",
            "smp_type": "cancel_taker",
            "prevented_volume": "0",
            "prevented_locked": "0",
            "trades_count": 1,
            "trades": [
                {
                    "uuid": "trade-uuid-1",
                    "price": "140000000",
                    "volume": "1",
                    "funds": "140000000",
                    "side": "bid",
                    "created_at": "2026-07-18T21:00:00+09:00",
                }
            ],
        },
        knowledge_at=knowledge_at,
        source_endpoint="GET /v1/order",
    )

    plan = plan_upbit_rest_snapshot_reconciliation(snapshot)
    request = build_upbit_rest_reconciliation_request(snapshot)

    assert plan.ledger_action == "apply"
    assert plan.can_resubmit is False
    assert request["observed_status"] == "done"
    assert request["fills"] == [
        {
            "fillSequence": 1,
            "side": "buy",
            "filledQuantity": Decimal("1"),
            "fillPrice": Decimal("140000000"),
            "feePaid": Decimal("700"),
                "occurredAt": datetime(2026, 7, 18, 12, tzinfo=UTC),
            "knowledgeAt": knowledge_at,
            "evidence": {
                "source": "upbit-rest-order-snapshot",
                "sourceEndpoint": "GET /v1/order",
                "orderUuid": "upbit-order-uuid",
                "identifier": "gm1_abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqr",
                "tradeUuid": "trade-uuid-1",
                "market": "KRW-BTC",
                "state": "done",
                "paidFee": "700",
                "smpType": "cancel_taker",
                "preventedVolume": "0",
                "preventedLocked": "0",
                "tradesCount": 1,
            },
        }
    ]


def test_Upbit_REST_체결대기_snapshot은_원장_변경_없이_관측만_허용한다() -> None:
    snapshot = parse_upbit_rest_order_snapshot(
        {
            "market": "KRW-BTC",
            "uuid": "upbit-order-uuid",
            "identifier": "gm1_abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqr",
            "side": "ask",
            "state": "wait",
            "created_at": "2026-07-18T21:00:00+09:00",
            "remaining_volume": "0.5",
            "executed_volume": "0",
            "paid_fee": "0",
            "prevented_volume": "0",
            "prevented_locked": "0",
            "trades_count": 0,
        },
        knowledge_at=datetime(2026, 7, 18, 12, tzinfo=UTC),
        source_endpoint="GET /v1/orders/open",
    )

    plan = plan_upbit_rest_snapshot_reconciliation(snapshot)

    assert plan.ledger_action == "observe_only"
    assert plan.observed_status == "working"
    assert plan.can_resubmit is False
    with pytest.raises(InvalidUpbitRestOrderSnapshot):
        build_upbit_rest_reconciliation_request(snapshot)


def test_Upbit_REST_다중_체결은_paid_fee를_체결금액_비율로_분배한다() -> None:
    snapshot = parse_upbit_rest_order_snapshot(
        {
            "market": "KRW-BTC",
            "uuid": "upbit-order-uuid",
            "side": "bid",
            "state": "done",
            "created_at": "2026-07-18T20:59:59+09:00",
            "remaining_volume": "0",
            "executed_volume": "3",
            "paid_fee": "30",
            "prevented_volume": "0",
            "prevented_locked": "0",
            "trades_count": 2,
            "trades": [
                {
                    "uuid": "trade-2",
                    "price": "20",
                    "volume": "1",
                    "funds": "20",
                    "side": "bid",
                    "created_at": "2026-07-18T21:00:02+09:00",
                },
                {
                    "uuid": "trade-1",
                    "price": "10",
                    "volume": "1",
                    "funds": "10",
                    "side": "bid",
                    "created_at": "2026-07-18T21:00:01+09:00",
                },
            ],
        },
        knowledge_at=datetime(2026, 7, 18, 12, tzinfo=UTC),
        source_endpoint="GET /v1/order",
    )

    request = build_upbit_rest_reconciliation_request(snapshot)

    fills = request["fills"]
    assert isinstance(fills, list)
    typed_fills = [fill for fill in fills if isinstance(fill, dict)]

    assert [fill["fillSequence"] for fill in typed_fills] == [1, 2]
    assert [fill["feePaid"] for fill in typed_fills] == [
        Decimal("10"),
        Decimal("20"),
    ]


def test_Upbit_REST_trades_count와_trades_개수가_다르면_거부한다() -> None:
    with pytest.raises(InvalidUpbitRestOrderSnapshot):
        parse_upbit_rest_order_snapshot(
            {
                "market": "KRW-BTC",
                "uuid": "upbit-order-uuid",
                "side": "bid",
                "state": "done",
                "created_at": "2026-07-18T21:00:00+09:00",
                "remaining_volume": "0",
                "executed_volume": "1",
                "paid_fee": "0",
                "prevented_volume": "0",
                "prevented_locked": "0",
                "trades_count": 2,
                "trades": [],
            },
            knowledge_at=datetime(2026, 7, 18, 12, tzinfo=UTC),
            source_endpoint="GET /v1/order",
        )


def test_Upbit_REST_주문조회_endpoint_이외_source는_거부한다() -> None:
    with pytest.raises(InvalidUpbitRestOrderSnapshot):
        parse_upbit_rest_order_snapshot(
            {
                "market": "KRW-BTC",
                "uuid": "upbit-order-uuid",
                "side": "bid",
                "state": "wait",
                "created_at": "2026-07-18T21:00:00+09:00",
                "trades_count": 0,
            },
            knowledge_at=datetime(2026, 7, 18, 12, tzinfo=UTC),
            source_endpoint="POST /v1/orders",
        )


def test_Upbit_REST_trades가_list가_아니면_계약_예외로_거부한다() -> None:
    with pytest.raises(InvalidUpbitRestOrderSnapshot):
        parse_upbit_rest_order_snapshot(
            {
                "market": "KRW-BTC",
                "uuid": "upbit-order-uuid",
                "side": "bid",
                "state": "done",
                "created_at": "2026-07-18T21:00:00+09:00",
                "executed_volume": "1",
                "paid_fee": "0",
                "trades_count": 1,
                "trades": {"uuid": "not-a-list"},
            },
            knowledge_at=datetime(2026, 7, 18, 12, tzinfo=UTC),
            source_endpoint="GET /v1/order",
        )


def test_Upbit_REST_trade_원소가_object가_아니면_계약_예외로_거부한다() -> None:
    with pytest.raises(InvalidUpbitRestOrderSnapshot):
        parse_upbit_rest_order_snapshot(
            {
                "market": "KRW-BTC",
                "uuid": "upbit-order-uuid",
                "side": "bid",
                "state": "done",
                "created_at": "2026-07-18T21:00:00+09:00",
                "executed_volume": "1",
                "paid_fee": "0",
                "trades_count": 1,
                "trades": ["not-an-object"],
            },
            knowledge_at=datetime(2026, 7, 18, 12, tzinfo=UTC),
            source_endpoint="GET /v1/order",
        )
