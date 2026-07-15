from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlparse
from uuid import uuid4

from goodmoneying_upbit_gateway.auth import (
    CredentialConfigurationError,
    Credentials,
    create_jwt,
    load_credentials,
)
from goodmoneying_upbit_gateway.websocket_protocol import (
    InvalidWebSocketControl,
    WebSocketRateLimiter,
    build_list_request,
    build_subscription_request,
    decode_upstream_frame,
    redact_websocket_value,
    validate_control_message,
    validate_format,
    validate_subscription,
    validate_ticket,
)


class InvalidUpstreamConfiguration(ValueError):
    pass


class Downstream(Protocol):
    async def send_json(self, event: dict[str, Any]) -> None: ...


class Upstream(Protocol):
    async def send(self, value: str) -> None: ...

    async def close(self) -> None: ...

    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


type Connector = Callable[[str, dict[str, str]], Awaitable[Upstream]]
type CredentialsProvider = Callable[[], Credentials]
type Visibility = Literal["public", "private"]


@dataclass(frozen=True)
class WebSocketUpstreamSettings:
    public_url: str
    private_url: str

    @classmethod
    def production(cls, catalog: Mapping[str, Any]) -> WebSocketUpstreamSettings:
        urls = cast(Mapping[str, str], catalog["websocket_urls"])
        return cls(public_url=urls["public"], private_url=urls["private"])

    @classmethod
    def from_environment(
        cls, catalog: Mapping[str, Any], environ: Mapping[str, str]
    ) -> WebSocketUpstreamSettings:
        production = cls.production(catalog)
        public = environ.get("UPBIT_GATEWAY_WEBSOCKET_PUBLIC_URL", production.public_url)
        private = environ.get("UPBIT_GATEWAY_WEBSOCKET_PRIVATE_URL", production.private_url)
        changed = (public, private) != (production.public_url, production.private_url)
        allowed = environ.get("UPBIT_GATEWAY_ALLOW_LOOPBACK_TEST") == "true"
        if changed and not allowed:
            raise InvalidUpstreamConfiguration(
                "상향 URL 변경은 명시적인 루프백 테스트 플래그가 필요합니다."
            )
        if changed and not all(_is_loopback_websocket_url(url) for url in (public, private)):
            raise InvalidUpstreamConfiguration(
                "테스트 상향 URL은 루프백 웹소켓(WebSocket)만 허용합니다."
            )
        return cls(public_url=public, private_url=private)


def _is_loopback_websocket_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "ws" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


async def default_websocket_connector(url: str, headers: dict[str, str]) -> Upstream:
    from websockets.asyncio.client import connect

    return cast(
        Upstream,
        await connect(
            url,
            additional_headers=headers or None,
            origin=None,
            ping_interval=30,
            ping_timeout=20,
            close_timeout=3,
            max_size=262_144,
        ),
    )


class GatewayWebSocketSession:
    def __init__(
        self,
        *,
        downstream: Downstream,
        catalog: dict[str, Any],
        settings: WebSocketUpstreamSettings,
        connector: Connector = default_websocket_connector,
        credentials_provider: CredentialsProvider = lambda: load_credentials(os.environ),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        connect_limiter: WebSocketRateLimiter | None = None,
    ) -> None:
        self._downstream = downstream
        self._catalog = catalog
        self._settings = settings
        self._connector = connector
        self._credentials_provider = credentials_provider
        self._sleep = sleep
        limits = cast(dict[str, Any], catalog["rate_limits"])
        message_limit = cast(dict[str, int], limits["websocket-message"])
        self._message_limit = message_limit
        connection_limit = cast(dict[str, int], limits["websocket-connect"])
        self._connect_limiter = connect_limiter or WebSocketRateLimiter(
            per_second=connection_limit["requests"],
            per_minute=connection_limit["requests"] * 60,
        )
        self._message_limiter = self._new_message_limiter()
        self._send_lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()
        self._upstream: Upstream | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._generation = 0
        self._closed = False
        self._paused = False
        self._visibility: Visibility | None = None
        self._ticket: str | None = None
        self._format = "DEFAULT"
        self._subscriptions: dict[str, dict[str, Any]] = {}
        self._connection_id: str | None = None
        self._sequence = 0

    async def handle(self, control: Mapping[str, Any]) -> None:
        request_id = control.get("request_id")
        try:
            validate_control_message(control)
            action = control.get("action")
            if action not in self._catalog["gateway_websocket_operations"]:
                raise InvalidWebSocketControl("지원하지 않는 action입니다.")
            validated_request_id = cast(str, request_id)
            async with self._control_lock:
                if action == "connect":
                    await self._connect(control, validated_request_id)
                elif action == "subscribe":
                    await self._subscribe(control, validated_request_id)
                elif action == "pause":
                    await self._pause(control, validated_request_id)
                elif action == "unsubscribe":
                    await self._unsubscribe(control, validated_request_id)
                elif action == "reconnect":
                    await self._manual_reconnect(validated_request_id)
                else:
                    await self._list(validated_request_id)
        except InvalidWebSocketControl as exc:
            await self._error(
                request_id=request_id if isinstance(request_id, str) else None,
                code="INVALID_CONTROL",
                message=str(exc),
                status=422,
                recoverable=True,
            )
        except Exception:
            await self._error(
                request_id=request_id if isinstance(request_id, str) else None,
                code="UPSTREAM_CONNECTION_ERROR",
                message="업비트 상향 웹소켓(WebSocket) 연결에 실패했습니다.",
                status=502,
                recoverable=True,
            )

    async def _connect(self, control: Mapping[str, Any], request_id: str) -> None:
        visibility = control.get("visibility")
        if visibility not in {"public", "private"}:
            raise InvalidWebSocketControl("visibility는 public 또는 private이어야 합니다.")
        self._visibility = cast(Visibility, visibility)
        self._ticket = validate_ticket(control.get("ticket"))
        self._format = validate_format(control.get("format", "DEFAULT"))
        self._subscriptions.clear()
        self._paused = False
        try:
            await self._replace_upstream(request_id=request_id, resubscribe=False)
        except CredentialConfigurationError:
            await self._error(
                request_id=request_id,
                code="CREDENTIALS_NOT_CONFIGURED",
                message="비공개 웹소켓(WebSocket) 자격 증명이 서버에 설정되지 않았습니다.",
                status=503,
                recoverable=True,
            )

    async def _subscribe(self, control: Mapping[str, Any], request_id: str) -> None:
        self._require_connected()
        endpoint_id = control.get("endpoint_id")
        if not isinstance(endpoint_id, str):
            raise InvalidWebSocketControl("endpoint_id가 필요합니다.")
        parameters = control.get("parameters", {})
        if not isinstance(parameters, Mapping):
            raise InvalidWebSocketControl("parameters는 객체여야 합니다.")
        subscription = validate_subscription(self._catalog, endpoint_id, parameters)
        endpoint = next(
            item
            for item in self._catalog["websocket_streams"]
            if item["endpoint_id"] == endpoint_id
        )
        if endpoint["visibility"] != self._visibility:
            raise InvalidWebSocketControl("현재 연결 가시성과 스트림 가시성이 다릅니다.")
        self._subscriptions[endpoint_id] = subscription
        await self._send_upstream(
            build_subscription_request(
                self._required_ticket(), list(self._subscriptions.values()), self._format
            )
        )
        await self._send(
            {
                "event": "subscription",
                "request_id": request_id,
                "action": "subscribed",
                "endpoint_id": endpoint_id,
                "subscriptions": list(self._subscriptions),
            }
        )

    async def _pause(self, control: Mapping[str, Any], request_id: str) -> None:
        self._require_connected()
        paused = control.get("paused")
        if not isinstance(paused, bool):
            raise InvalidWebSocketControl("pause action에는 paused boolean이 필요합니다.")
        self._paused = paused
        await self._connection_event("paused" if paused else "connected", request_id)

    async def _unsubscribe(self, control: Mapping[str, Any], request_id: str) -> None:
        self._require_connected()
        endpoint_id = control.get("endpoint_id")
        if not isinstance(endpoint_id, str) or endpoint_id not in self._subscriptions:
            raise InvalidWebSocketControl("현재 구독 중인 endpoint_id가 아닙니다.")
        del self._subscriptions[endpoint_id]
        await self._connection_event("reconnecting", request_id)
        await self._replace_upstream(request_id=request_id, resubscribe=True)
        await self._send(
            {
                "event": "subscription",
                "request_id": request_id,
                "action": "unsubscribed",
                "endpoint_id": endpoint_id,
                "subscriptions": list(self._subscriptions),
            }
        )

    async def _manual_reconnect(self, request_id: str) -> None:
        self._require_connected()
        await self._connection_event("reconnecting", request_id)
        await self._replace_upstream(request_id=request_id, resubscribe=True)

    async def _list(self, request_id: str) -> None:
        self._require_connected()
        await self._send_upstream(build_list_request(self._required_ticket(), self._format))
        await self._send(
            {
                "event": "subscription",
                "request_id": request_id,
                "action": "list-requested",
                "subscriptions": list(self._subscriptions),
            }
        )

    def _require_connected(self) -> None:
        if self._upstream is None:
            raise InvalidWebSocketControl("먼저 connect action으로 상향 연결을 열어야 합니다.")

    def _required_ticket(self) -> str:
        if self._ticket is None:
            raise InvalidWebSocketControl("연결 ticket이 설정되지 않았습니다.")
        return self._ticket

    async def _replace_upstream(self, *, request_id: str | None, resubscribe: bool) -> None:
        self._generation += 1
        generation = self._generation
        await self._dispose_upstream()
        visibility = self._visibility
        if visibility is None:
            raise InvalidWebSocketControl("연결 visibility가 설정되지 않았습니다.")
        url = self._settings.public_url
        headers: dict[str, str] = {}
        if visibility == "private":
            credentials = self._credentials_provider()
            headers["Authorization"] = f"Bearer {create_jwt(credentials, '')}"
            url = self._settings.private_url
        await self._connect_limiter.acquire()
        upstream = await self._connector(url, headers)
        self._upstream = upstream
        self._message_limiter = self._new_message_limiter()
        self._connection_id = str(uuid4())
        self._sequence = 0
        if resubscribe and self._subscriptions:
            await self._send_upstream(
                build_subscription_request(
                    self._required_ticket(), list(self._subscriptions.values()), self._format
                )
            )
        self._reader_task = asyncio.create_task(self._read_upstream(upstream, generation))
        await self._connection_event("connected", request_id)

    async def _dispose_upstream(self) -> None:
        reader = self._reader_task
        self._reader_task = None
        current = asyncio.current_task()
        if reader is not None and reader is not current and not reader.done():
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader
        upstream = self._upstream
        self._upstream = None
        if upstream is not None:
            await upstream.close()

    async def _send_upstream(self, payload: list[dict[str, Any]]) -> None:
        upstream = self._upstream
        if upstream is None:
            raise InvalidWebSocketControl("상향 연결이 열려 있지 않습니다.")
        await self._message_limiter.acquire()
        await upstream.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    async def _read_upstream(self, upstream: Upstream, generation: int) -> None:
        try:
            async for raw in upstream:
                try:
                    frame = decode_upstream_frame(raw)
                except InvalidWebSocketControl as exc:
                    await self._error(
                        request_id=None,
                        code="UPSTREAM_PROTOCOL_ERROR",
                        message=str(exc),
                        status=502,
                        recoverable=True,
                    )
                    continue
                payload = redact_websocket_value(frame.payload)
                if isinstance(payload, Mapping) and isinstance(payload.get("error"), Mapping):
                    error = cast(Mapping[str, Any], payload["error"])
                    await self._error(
                        request_id=None,
                        code=str(error.get("name", "UPBIT")),
                        message=str(error.get("message", "업비트 웹소켓(WebSocket) 오류")),
                        status=502,
                        recoverable=True,
                    )
                    continue
                if self._paused:
                    continue
                self._sequence += 1
                safe_raw = frame.raw
                if payload != frame.payload:
                    safe_raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                await self._send(
                    {
                        "event": "frame",
                        "trace_id": str(uuid4()),
                        "connection_id": self._connection_id,
                        "sequence": self._sequence,
                        "received_at": datetime.now(UTC).isoformat(),
                        "payload": payload,
                        "raw": cast(str, redact_websocket_value(safe_raw)),
                        "binary": frame.binary,
                        "provenance": {
                            "visibility": self._visibility,
                            "format": self._format,
                            "endpoint_ids": list(self._subscriptions),
                        },
                    }
                )
            if generation == self._generation and not self._closed:
                await self._auto_reconnect(generation)
        except asyncio.CancelledError:
            raise
        except Exception:
            if generation == self._generation and not self._closed:
                await self._auto_reconnect(generation)

    async def _auto_reconnect(self, generation: int) -> None:
        delays = (0.25, 0.5, 1.0, 2.0, 5.0)
        for delay in delays:
            if self._closed or generation != self._generation:
                return
            await self._connection_event("reconnecting", None, retry_in=delay)
            await self._sleep(delay)
            if self._closed or generation != self._generation:
                return
            try:
                await self._replace_upstream(request_id=None, resubscribe=True)
                return
            except Exception:
                generation = self._generation
        await self._error(
            request_id=None,
            code="UPSTREAM_CONNECTION_ERROR",
            message="업비트 상향 웹소켓(WebSocket) 재연결에 실패했습니다.",
            status=502,
            recoverable=False,
        )
        await self._connection_event("closed", None)

    async def _connection_event(
        self, state: str, request_id: str | None, *, retry_in: float | None = None
    ) -> None:
        event: dict[str, Any] = {
            "event": "connection",
            "request_id": request_id,
            "state": state,
            "connection_id": self._connection_id,
            "visibility": self._visibility,
            "format": self._format,
        }
        if retry_in is not None:
            event["retry_in_seconds"] = retry_in
        await self._send(event)

    async def _error(
        self,
        *,
        request_id: str | None,
        code: str,
        message: str,
        status: int,
        recoverable: bool,
    ) -> None:
        await self._send(
            {
                "event": "error",
                "request_id": request_id,
                "code": code,
                "message": cast(str, redact_websocket_value(message)),
                "status": status,
                "recoverable": recoverable,
            }
        )

    async def _send(self, event: dict[str, Any]) -> None:
        async with self._send_lock:
            await self._downstream.send_json(event)

    def _new_message_limiter(self) -> WebSocketRateLimiter:
        return WebSocketRateLimiter(
            per_second=self._message_limit["requests"],
            per_minute=self._message_limit["requests_per_minute"],
        )

    async def close(self, *, notify: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        await self._dispose_upstream()
        self._subscriptions.clear()
        if notify:
            await self._connection_event("closed", None)
