from __future__ import annotations

import asyncio
from typing import Any

import pytest

from goodmoneying_upbit_gateway.catalog import load_catalog
from goodmoneying_upbit_gateway.websocket_protocol import (
    InvalidWebSocketControl,
    WebSocketRateLimiter,
    build_list_request,
    build_subscription_request,
    decode_upstream_frame,
    redact_websocket_value,
    validate_subscription,
)


@pytest.mark.parametrize(
    ("endpoint_id", "parameters", "expected_type"),
    [
        ("websocket.ticker", {"codes": ["KRW-BTC"]}, "ticker"),
        ("websocket.trade", {"codes": ["KRW-BTC"]}, "trade"),
        ("websocket.orderbook", {"codes": ["KRW-BTC.5"], "level": 10000}, "orderbook"),
        ("websocket.candle-1s", {"codes": ["KRW-BTC"]}, "candle.1s"),
        ("websocket.candle-240m", {"codes": ["KRW-BTC"]}, "candle.240m"),
        ("websocket.my-asset", {}, "myAsset"),
        ("websocket.my-order", {"codes": []}, "myOrder"),
    ],
)
def test_catalog_driven_subscription_supports_every_stream_shape(
    endpoint_id: str, parameters: dict[str, Any], expected_type: str
) -> None:
    subscription = validate_subscription(load_catalog(), endpoint_id, parameters)

    assert subscription["type"] == expected_type


def test_public_subscription_normalizes_codes_and_preserves_official_flags() -> None:
    subscription = validate_subscription(
        load_catalog(),
        "websocket.orderbook",
        {
            "codes": [" krw-btc.15 ", "KRW-ETH"],
            "level": 10000,
            "is_only_snapshot": True,
            "is_only_realtime": False,
        },
    )

    assert subscription == {
        "type": "orderbook",
        "codes": ["KRW-BTC.15", "KRW-ETH"],
        "level": 10000,
        "is_only_snapshot": True,
        "is_only_realtime": False,
    }


@pytest.mark.parametrize(
    ("endpoint_id", "parameters", "message"),
    [
        ("websocket.ticker", {}, "codes"),
        ("websocket.ticker", {"codes": ["BTC"]}, "대문자 페어 코드"),
        ("websocket.my-asset", {"codes": ["KRW-BTC"]}, "지원하지 않는"),
        ("websocket.trade", {"codes": ["KRW-BTC"], "level": 1}, "지원하지 않는"),
        (
            "websocket.trade",
            {"codes": ["KRW-BTC"], "is_only_snapshot": True, "is_only_realtime": True},
            "동시에",
        ),
        ("websocket.unknown", {}, "카탈로그"),
    ],
)
def test_invalid_subscription_is_rejected_before_upstream(
    endpoint_id: str, parameters: dict[str, Any], message: str
) -> None:
    with pytest.raises(InvalidWebSocketControl, match=message):
        validate_subscription(load_catalog(), endpoint_id, parameters)


@pytest.mark.parametrize("format_name", ["DEFAULT", "SIMPLE", "JSON_LIST", "SIMPLE_LIST"])
def test_subscription_and_list_requests_follow_official_array_order(format_name: str) -> None:
    subscriptions: list[dict[str, Any]] = [
        {"type": "ticker", "codes": ["KRW-BTC"]},
        {"type": "trade", "codes": ["KRW-BTC"], "is_only_realtime": True},
    ]

    request = build_subscription_request("ticket-1", subscriptions, format_name)
    listing = build_list_request("ticket-1", format_name)

    assert request[0] == {"ticket": "ticket-1"}
    assert request[1:3] == subscriptions
    assert request[-1] == {"format": format_name}
    assert listing == [
        {"ticket": "ticket-1"},
        {"method": "LIST_SUBSCRIPTIONS"},
        {"format": format_name},
    ]


def test_binary_json_and_json_list_frames_are_decoded_without_losing_raw() -> None:
    decoded = decode_upstream_frame(b'[{"type":"ticker","code":"KRW-BTC"}]')

    assert decoded.payload == [{"type": "ticker", "code": "KRW-BTC"}]
    assert decoded.raw == '[{"type":"ticker","code":"KRW-BTC"}]'
    assert decoded.binary is True


def test_malformed_binary_frame_has_a_bounded_protocol_error() -> None:
    with pytest.raises(InvalidWebSocketControl, match="UTF-8"):
        decode_upstream_frame(b"\xff\xfe")
    with pytest.raises(InvalidWebSocketControl, match="JSON"):
        decode_upstream_frame(b"not-json")


def test_redaction_never_returns_credentials_or_bearer_tokens() -> None:
    value = {
        "access_key": "access-value",
        "secret_key": "secret-value",
        "authorization": "Bearer jwt-value",
        "message": "Authorization: Bearer embedded-value",
    }

    redacted = str(redact_websocket_value(value))

    for secret in ("access-value", "secret-value", "jwt-value", "embedded-value"):
        assert secret not in redacted
    assert "[REDACTED]" in redacted


def test_message_rate_limiter_enforces_both_official_windows() -> None:
    now = 0.0
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    limiter = WebSocketRateLimiter(
        per_second=5,
        per_minute=100,
        clock=lambda: now,
        sleep=sleep,
    )

    async def exercise() -> None:
        for _ in range(6):
            await limiter.acquire()
        for _ in range(95):
            await limiter.acquire()

    asyncio.run(exercise())

    assert sleeps[0] == pytest.approx(1.0)
    assert sum(sleeps) >= 60.0
