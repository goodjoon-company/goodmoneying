from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from goodmoneying_shared.upbit_order_submit_rehearsal import (
    InvalidUpbitOrderSubmitRehearsal,
    build_upbit_order_submit_rehearsal,
)

NOW = datetime(2026, 7, 18, 19, tzinfo=UTC)
IDENTIFIER = "gm1_" + "a" * 52


def test_order_submit_rehearsal은_공식_주문_본문과_query_hash를_결정적으로_준비한다() -> None:
    rehearsal = build_upbit_order_submit_rehearsal(
        {
            "market": "KRW-BTC",
            "side": "bid",
            "ord_type": "limit",
            "volume": "0.1",
            "price": "1000",
            "identifier": IDENTIFIER,
            "time_in_force": "post_only",
        },
        rehearsed_at=NOW,
    )

    expected_query_string = (
        f"market=KRW-BTC&side=bid&volume=0.1&price=1000&ord_type=limit"
        f"&identifier={IDENTIFIER}&time_in_force=post_only"
    )

    assert rehearsal.endpoint_key == "rest.new-order"
    assert rehearsal.http_method == "POST"
    assert rehearsal.request_path == "/v1/orders"
    assert rehearsal.canonical_payload == {
        "market": "KRW-BTC",
        "side": "bid",
        "volume": "0.1",
        "price": "1000",
        "ord_type": "limit",
        "identifier": IDENTIFIER,
        "time_in_force": "post_only",
    }
    assert rehearsal.query_string == expected_query_string
    assert rehearsal.query_hash == hashlib.sha512(
        expected_query_string.encode("utf-8")
    ).hexdigest()
    assert rehearsal.request_hash == hashlib.sha256(
        (
            '{"identifier":"'
            + IDENTIFIER
            + '","market":"KRW-BTC","ord_type":"limit","price":"1000",'
            + '"side":"bid","time_in_force":"post_only","volume":"0.1"}'
        ).encode("utf-8")
    ).hexdigest()
    assert rehearsal.actual_request_sent is False
    assert rehearsal.would_submit is False
    assert rehearsal.can_bind_response is False


def test_order_submit_rehearsal은_실제_제출_전_위험한_주문_본문을_거부한다() -> None:
    with pytest.raises(InvalidUpbitOrderSubmitRehearsal, match="post_only"):
        build_upbit_order_submit_rehearsal(
            {
                "market": "KRW-BTC",
                "side": "bid",
                "ord_type": "limit",
                "volume": "0.1",
                "price": "1000",
                "identifier": IDENTIFIER,
                "time_in_force": "post_only",
                "smp_type": "cancel_taker",
            },
            rehearsed_at=NOW,
        )

    with pytest.raises(InvalidUpbitOrderSubmitRehearsal, match="identifier"):
        build_upbit_order_submit_rehearsal(
            {
                "market": "KRW-BTC",
                "side": "bid",
                "ord_type": "limit",
                "volume": "0.1",
                "price": "1000",
                "identifier": "outside-id",
            },
            rehearsed_at=NOW,
        )


def test_order_submit_rehearsal은_알수없는_주문_필드를_조용히_버리지_않는다() -> None:
    with pytest.raises(InvalidUpbitOrderSubmitRehearsal, match="알 수 없는"):
        build_upbit_order_submit_rehearsal(
            {
                "market": "KRW-BTC",
                "side": "bid",
                "ord_type": "limit",
                "volume": "0.1",
                "price": "1000",
                "identifier": IDENTIFIER,
                "dry_run": "true",
            },
            rehearsed_at=NOW,
        )


def test_order_submit_rehearsal은_시장가_주문에_time_in_force를_허용하지_않는다() -> None:
    with pytest.raises(InvalidUpbitOrderSubmitRehearsal, match="시장가"):
        build_upbit_order_submit_rehearsal(
            {
                "market": "KRW-BTC",
                "side": "bid",
                "ord_type": "price",
                "price": "1000",
                "identifier": IDENTIFIER,
                "time_in_force": "ioc",
            },
            rehearsed_at=NOW,
        )

    with pytest.raises(InvalidUpbitOrderSubmitRehearsal, match="시장가"):
        build_upbit_order_submit_rehearsal(
            {
                "market": "KRW-BTC",
                "side": "ask",
                "ord_type": "market",
                "volume": "0.1",
                "identifier": IDENTIFIER,
                "time_in_force": "fok",
            },
            rehearsed_at=NOW,
        )
