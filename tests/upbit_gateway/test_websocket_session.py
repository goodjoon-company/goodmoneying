from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from goodmoneying_upbit_gateway.auth import CredentialConfigurationError, Credentials
from goodmoneying_upbit_gateway.catalog import load_catalog
from goodmoneying_upbit_gateway.websocket_protocol import WebSocketRateLimiter
from goodmoneying_upbit_gateway.websocket_session import (
    DownstreamDisconnected,
    GatewayWebSocketSession,
    InvalidUpstreamConfiguration,
    WebSocketUpstreamSettings,
)


class FakeDownstream:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def send_json(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class ClosedDownstream:
    async def send_json(self, event: dict[str, Any]) -> None:
        raise RuntimeError('Cannot call "send" once a close message has been sent.')


class FakeUpstream:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False
        self.messages: asyncio.Queue[str | bytes | BaseException | None] = asyncio.Queue()

    async def send(self, value: str) -> None:
        self.sent.append(value)

    async def close(self) -> None:
        self.closed = True
        await self.messages.put(None)

    def __aiter__(self) -> AsyncIterator[str | bytes]:
        return self

    async def __anext__(self) -> str | bytes:
        item = await self.messages.get()
        if item is None:
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        return item


class FakeConnector:
    def __init__(self) -> None:
        self.connections: list[FakeUpstream] = []
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def __call__(self, url: str, headers: dict[str, str]) -> FakeUpstream:
        self.calls.append((url, headers))
        connection = FakeUpstream()
        self.connections.append(connection)
        return connection


def _session(
    *,
    credentials_provider: Any = lambda: Credentials("server-access", "s" * 64),
    sleep: Any = asyncio.sleep,
) -> tuple[GatewayWebSocketSession, FakeDownstream, FakeConnector]:
    downstream = FakeDownstream()
    connector = FakeConnector()
    session = GatewayWebSocketSession(
        downstream=downstream,
        catalog=load_catalog(),
        settings=WebSocketUpstreamSettings.production(load_catalog()),
        connector=connector,
        credentials_provider=credentials_provider,
        sleep=sleep,
    )
    return session, downstream, connector


def test_public_connect_subscribe_binary_frame_pause_and_resume() -> None:
    async def scenario() -> tuple[FakeDownstream, FakeConnector]:
        session, downstream, connector = _session()
        await session.handle(
            {
                "action": "connect",
                "request_id": "c1",
                "visibility": "public",
                "ticket": "t1",
                "format": "JSON_LIST",
            }
        )
        await session.handle(
            {
                "action": "subscribe",
                "request_id": "s1",
                "endpoint_id": "websocket.ticker",
                "parameters": {"codes": ["KRW-BTC"], "is_only_realtime": True},
            }
        )
        upstream = connector.connections[0]
        await upstream.messages.put(b'[{"type":"ticker","code":"KRW-BTC","trade_price":100}]')
        await asyncio.sleep(0)
        await session.handle({"action": "pause", "request_id": "p1", "paused": True})
        await upstream.messages.put('{"type":"ticker","code":"KRW-BTC","trade_price":101}')
        await asyncio.sleep(0)
        await session.handle({"action": "pause", "request_id": "p2", "paused": False})
        await session.close()
        return downstream, connector

    downstream, connector = asyncio.run(scenario())

    assert connector.calls == [("wss://api.upbit.com/websocket/v1", {})]
    assert json.loads(connector.connections[0].sent[0]) == [
        {"ticket": "t1"},
        {"type": "ticker", "codes": ["KRW-BTC"], "is_only_realtime": True},
        {"format": "JSON_LIST"},
    ]
    frames = [event for event in downstream.events if event["event"] == "frame"]
    assert len(frames) == 1
    assert frames[0]["payload"][0]["trade_price"] == 100
    assert frames[0]["binary"] is True
    assert frames[0]["provenance"] == {
        "visibility": "public",
        "format": "JSON_LIST",
        "endpoint_ids": ["websocket.ticker"],
    }
    assert frames[0]["sequence"] == 1
    assert "trace_id" in frames[0]
    assert connector.connections[0].closed is True


def test_downstream_disconnect_is_not_retried_as_an_error_response() -> None:
    async def scenario() -> None:
        session = GatewayWebSocketSession(
            downstream=ClosedDownstream(),
            catalog=load_catalog(),
            settings=WebSocketUpstreamSettings.production(load_catalog()),
        )
        with pytest.raises(DownstreamDisconnected):
            await session.handle({"action": "unknown", "request_id": "closed"})
        await session.close(notify=False)

    asyncio.run(scenario())


def test_private_connect_uses_server_only_authorization_and_missing_credentials_is_503() -> None:
    def missing() -> Credentials:
        raise CredentialConfigurationError("접근 키와 비밀 키를 한 쌍으로 설정해야 합니다.")

    async def scenario() -> tuple[FakeDownstream, FakeConnector, FakeDownstream, FakeConnector]:
        private, private_downstream, private_connector = _session()
        await private.handle(
            {
                "action": "connect",
                "request_id": "private",
                "visibility": "private",
                "ticket": "t2",
                "format": "DEFAULT",
            }
        )
        await private.close()
        absent, absent_downstream, absent_connector = _session(credentials_provider=missing)
        await absent.handle(
            {
                "action": "connect",
                "request_id": "absent",
                "visibility": "private",
                "ticket": "t3",
                "format": "DEFAULT",
            }
        )
        await absent.close()
        return private_downstream, private_connector, absent_downstream, absent_connector

    private_downstream, private_connector, absent_downstream, absent_connector = asyncio.run(
        scenario()
    )

    url, headers = private_connector.calls[0]
    assert url == "wss://api.upbit.com/websocket/v1/private"
    assert headers["Authorization"].startswith("Bearer ")
    rendered = json.dumps(private_downstream.events)
    assert "server-access" not in rendered
    assert headers["Authorization"] not in rendered
    assert absent_connector.calls == []
    absent_error = next(event for event in absent_downstream.events if event["event"] == "error")
    assert absent_error == {
        "event": "error",
        "request_id": "absent",
        "code": "CREDENTIALS_NOT_CONFIGURED",
        "message": "비공개 웹소켓(WebSocket) 자격 증명이 서버에 설정되지 않았습니다.",
        "status": 503,
        "recoverable": True,
    }


def test_list_unsubscribe_and_manual_reconnect_preserve_one_desired_snapshot() -> None:
    async def scenario() -> tuple[FakeDownstream, FakeConnector]:
        session, downstream, connector = _session()
        await session.handle(
            {
                "action": "connect",
                "request_id": "c",
                "visibility": "public",
                "ticket": "ticket",
                "format": "SIMPLE",
            }
        )
        for endpoint_id in ("websocket.ticker", "websocket.trade"):
            await session.handle(
                {
                    "action": "subscribe",
                    "request_id": endpoint_id,
                    "endpoint_id": endpoint_id,
                    "parameters": {"codes": ["KRW-BTC"]},
                }
            )
        await session.handle({"action": "list", "request_id": "l"})
        await session.handle(
            {"action": "unsubscribe", "request_id": "u", "endpoint_id": "websocket.trade"}
        )
        await session.handle({"action": "reconnect", "request_id": "r"})
        await session.close()
        return downstream, connector

    downstream, connector = asyncio.run(scenario())

    assert len(connector.connections) == 3
    first_messages = [json.loads(item) for item in connector.connections[0].sent]
    assert first_messages[1] == [
        {"ticket": "ticket"},
        {"type": "ticker", "codes": ["KRW-BTC"]},
        {"type": "trade", "codes": ["KRW-BTC"]},
        {"format": "SIMPLE"},
    ]
    assert first_messages[-1] == [
        {"ticket": "ticket"},
        {"method": "LIST_SUBSCRIPTIONS"},
        {"format": "SIMPLE"},
    ]
    for connection in connector.connections[1:]:
        assert [json.loads(item) for item in connection.sent] == [
            [
                {"ticket": "ticket"},
                {"type": "ticker", "codes": ["KRW-BTC"]},
                {"format": "SIMPLE"},
            ]
        ]
    assert all(connection.closed for connection in connector.connections)
    statuses = [event["state"] for event in downstream.events if event["event"] == "connection"]
    assert "reconnecting" in statuses


@pytest.mark.parametrize(
    "control",
    [
        {
            "action": "connect",
            "request_id": "missing-format",
            "visibility": "public",
            "ticket": "ticket",
        },
        {
            "action": "connect",
            "request_id": "x" * 129,
            "visibility": "public",
            "ticket": "ticket",
            "format": "DEFAULT",
        },
        {
            "action": "connect",
            "request_id": "extra",
            "visibility": "public",
            "ticket": "ticket",
            "format": "DEFAULT",
            "unexpected": True,
        },
    ],
)
def test_runtime_control_rejects_messages_rejected_by_json_schema(
    control: dict[str, Any],
) -> None:
    async def scenario() -> tuple[FakeDownstream, FakeConnector]:
        session, downstream, connector = _session()
        await session.handle(control)
        await session.close()
        return downstream, connector

    downstream, connector = asyncio.run(scenario())

    error = next(event for event in downstream.events if event["event"] == "error")
    assert (error["code"], error["status"]) == ("INVALID_CONTROL", 422)
    assert connector.calls == []


def test_unexpected_disconnect_reconnects_with_backoff_and_resubscribes_once() -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def scenario() -> tuple[GatewayWebSocketSession, FakeDownstream, FakeConnector]:
        session, downstream, connector = _session(sleep=sleep)
        await session.handle(
            {
                "action": "connect",
                "request_id": "c",
                "visibility": "public",
                "ticket": "ticket",
                "format": "DEFAULT",
            }
        )
        await session.handle(
            {
                "action": "subscribe",
                "request_id": "s",
                "endpoint_id": "websocket.ticker",
                "parameters": {"codes": ["KRW-BTC"]},
            }
        )
        await connector.connections[0].messages.put(ConnectionError("network gone"))
        for _ in range(5):
            await asyncio.sleep(0)
        return session, downstream, connector

    session, downstream, connector = asyncio.run(scenario())
    try:
        assert sleeps == [0.25]
        assert len(connector.connections) == 2
        assert len(connector.connections[1].sent) == 1
        assert json.loads(connector.connections[1].sent[0])[1]["type"] == "ticker"
        assert any(
            event["event"] == "connection" and event["state"] == "reconnecting"
            for event in downstream.events
        )
    finally:
        asyncio.run(session.close())


def test_exhausted_automatic_reconnect_reports_masked_error_and_closed_state() -> None:
    sleeps: list[float] = []
    downstream = FakeDownstream()
    first = FakeUpstream()
    attempts = 0

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def connector(url: str, headers: dict[str, str]) -> FakeUpstream:
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            raise RuntimeError(f"failed {url} Authorization=Bearer leaked-token")
        return first

    async def scenario() -> None:
        session = GatewayWebSocketSession(
            downstream=downstream,
            catalog=load_catalog(),
            settings=WebSocketUpstreamSettings.production(load_catalog()),
            connector=connector,
            sleep=sleep,
            connect_limiter=WebSocketRateLimiter(per_second=100, per_minute=100),
        )
        await session.handle(
            {
                "action": "connect",
                "request_id": "connect",
                "visibility": "public",
                "ticket": "ticket",
                "format": "DEFAULT",
            }
        )
        await first.messages.put(ConnectionError("network gone"))
        for _ in range(30):
            await asyncio.sleep(0)
            if downstream.events[-1].get("state") == "closed":
                break
        attempts_before_terminal_control = attempts
        await session.handle(
            {
                "action": "connect",
                "request_id": "terminal-connect",
                "visibility": "public",
                "ticket": "new-ticket",
                "format": "DEFAULT",
            }
        )
        assert attempts == attempts_before_terminal_control
        await session.close(notify=False)

    asyncio.run(scenario())

    assert sleeps == [0.25, 0.5, 1.0, 2.0, 5.0]
    assert attempts == 6
    assert downstream.events[-3:-1] == [
        {
            "event": "error",
            "request_id": None,
            "code": "UPSTREAM_CONNECTION_ERROR",
            "message": "업비트 상향 웹소켓(WebSocket) 재연결에 실패했습니다.",
            "status": 502,
            "recoverable": False,
        },
        {
            "event": "connection",
            "request_id": None,
            "state": "closed",
            "connection_id": downstream.events[0]["connection_id"],
            "visibility": "public",
            "format": "DEFAULT",
        },
    ]
    assert downstream.events[-1] == {
        "event": "error",
        "request_id": "terminal-connect",
        "code": "SESSION_CLOSED",
        "message": "웹소켓(WebSocket) 세션이 종료되었습니다. 새 연결을 여세요.",
        "status": 409,
        "recoverable": False,
    }
    assert "leaked-token" not in json.dumps(downstream.events)
    assert "api.upbit.com" not in json.dumps(downstream.events)


def test_only_explicit_loopback_test_flag_allows_upstream_override() -> None:
    catalog = load_catalog()

    with pytest.raises(InvalidUpstreamConfiguration, match="테스트 플래그"):
        WebSocketUpstreamSettings.from_environment(
            catalog,
            {"UPBIT_GATEWAY_WEBSOCKET_PUBLIC_URL": "ws://127.0.0.1:9000/public"},
        )
    with pytest.raises(InvalidUpstreamConfiguration, match="루프백"):
        WebSocketUpstreamSettings.from_environment(
            catalog,
            {
                "UPBIT_GATEWAY_ALLOW_LOOPBACK_TEST": "true",
                "UPBIT_GATEWAY_WEBSOCKET_PUBLIC_URL": "ws://example.com/public",
            },
        )

    settings = WebSocketUpstreamSettings.from_environment(
        catalog,
        {
            "UPBIT_GATEWAY_ALLOW_LOOPBACK_TEST": "true",
            "UPBIT_GATEWAY_WEBSOCKET_PUBLIC_URL": "ws://127.0.0.1:9000/public",
            "UPBIT_GATEWAY_WEBSOCKET_PRIVATE_URL": "ws://localhost:9000/private",
        },
    )
    assert settings.public_url == "ws://127.0.0.1:9000/public"
    assert settings.private_url == "ws://localhost:9000/private"


def test_connector_failure_is_masked_recoverable_and_does_not_kill_session() -> None:
    downstream = FakeDownstream()
    connections: list[FakeUpstream] = []
    attempts = 0

    async def flaky_connector(url: str, headers: dict[str, str]) -> FakeUpstream:
        nonlocal attempts
        attempts += 1
        if attempts in {1, 3}:
            raise RuntimeError(
                f"failed {url} Authorization={headers.get('Authorization', 'Bearer leaked-token')}"
            )
        connection = FakeUpstream()
        connections.append(connection)
        return connection

    async def scenario() -> None:
        session = GatewayWebSocketSession(
            downstream=downstream,
            catalog=load_catalog(),
            settings=WebSocketUpstreamSettings.production(load_catalog()),
            connector=flaky_connector,
        )
        control = {
            "action": "connect",
            "visibility": "public",
            "ticket": "ticket",
            "format": "DEFAULT",
        }
        await session.handle({**control, "request_id": "failed"})
        await session.handle({**control, "request_id": "retry"})
        await session.handle(
            {
                "action": "subscribe",
                "request_id": "subscribe",
                "endpoint_id": "websocket.ticker",
                "parameters": {"codes": ["KRW-BTC"]},
            }
        )
        await session.handle({"action": "reconnect", "request_id": "reconnect-failed"})
        await session.handle({**control, "request_id": "after-reconnect-failure"})
        await session.close()

    asyncio.run(scenario())

    errors = [event for event in downstream.events if event["event"] == "error"]
    assert errors[0] == {
        "event": "error",
        "request_id": "failed",
        "code": "UPSTREAM_CONNECTION_ERROR",
        "message": "업비트 상향 웹소켓(WebSocket) 연결에 실패했습니다.",
        "status": 502,
        "recoverable": True,
    }
    assert errors[1] == {**errors[0], "request_id": "reconnect-failed"}
    rendered = json.dumps(downstream.events)
    assert "leaked-token" not in rendered
    assert "api.upbit.com" not in rendered
    assert attempts == 4
    assert len(connections[0].sent) == 1
