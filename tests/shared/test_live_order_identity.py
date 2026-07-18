from __future__ import annotations

import pytest

from goodmoneying_shared.live_order_identity import (
    derive_upbit_live_order_identifier,
    is_upbit_live_order_identifier,
)


def test_upbit_live_order_identifier는_공식_최대보다_짧고_결정적이다() -> None:
    first = derive_upbit_live_order_identifier(
        "upbit-account-main", "bot-42:intent-2026-07-18T16:00:00Z"
    )
    repeated = derive_upbit_live_order_identifier(
        "upbit-account-main", "bot-42:intent-2026-07-18T16:00:00Z"
    )
    other = derive_upbit_live_order_identifier(
        "upbit-account-main", "bot-42:intent-2026-07-18T16:05:00Z"
    )

    assert first == repeated
    assert first != other
    assert first.startswith("gm1_")
    assert len(first) == 56
    assert is_upbit_live_order_identifier(first)
    assert len(first) <= 64


def test_upbit_live_order_identifier는_공백_입력을_거부하고_test_identifier와_구분된다() -> None:
    for account_stable_id, idempotency_key in (
        ("", "intent-1"),
        ("account-1", ""),
        ("  ", "intent-1"),
        ("account-1", "  "),
        (" account-1", "intent-1"),
        ("account-1", " intent-1"),
    ):
        with pytest.raises(ValueError):
            derive_upbit_live_order_identifier(account_stable_id, idempotency_key)

    assert not is_upbit_live_order_identifier("test-order-uuid")
    assert not is_upbit_live_order_identifier("gm1_" + "a" * 51)
    assert not is_upbit_live_order_identifier("gm1_" + "a" * 53)
