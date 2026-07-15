from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from goodmoneying_upbit_gateway.catalog import endpoint_by_id

WEBSOCKET_FORMATS = {"DEFAULT", "SIMPLE", "JSON_LIST", "SIMPLE_LIST"}
PAIR_CODE_PATTERN = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+(?:\.(?:1|5|15|30))?$")
BEARER_PATTERN = re.compile(r"(?i)(authorization\s*[:=]\s*)?bearer\s+[^\s,'\"}]+")
SENSITIVE_KEYS = {"access_key", "secret_key", "authorization", "jwt", "token"}


class InvalidWebSocketControl(ValueError):
    """브라우저 또는 상향 웹소켓(WebSocket) 계약 위반."""


@dataclass(frozen=True)
class DecodedFrame:
    payload: Any
    raw: str
    binary: bool


def validate_format(value: Any) -> str:
    if not isinstance(value, str) or value not in WEBSOCKET_FORMATS:
        raise InvalidWebSocketControl("지원하지 않는 웹소켓(WebSocket) format입니다.")
    return value


def validate_ticket(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise InvalidWebSocketControl("ticket은 1~128자 문자열이어야 합니다.")
    return value.strip()


def validate_control_message(control: Mapping[str, Any]) -> None:
    action = control.get("action")
    specifications: dict[str, tuple[set[str], set[str]]] = {
        "connect": (
            {"action", "request_id", "visibility", "ticket", "format"},
            {"action", "request_id", "visibility", "ticket", "format"},
        ),
        "subscribe": (
            {"action", "request_id", "endpoint_id", "parameters"},
            {"action", "request_id", "endpoint_id", "parameters"},
        ),
        "pause": (
            {"action", "request_id", "paused"},
            {"action", "request_id", "paused"},
        ),
        "unsubscribe": (
            {"action", "request_id", "endpoint_id"},
            {"action", "request_id", "endpoint_id"},
        ),
        "reconnect": ({"action", "request_id"}, {"action", "request_id"}),
        "list": ({"action", "request_id"}, {"action", "request_id"}),
    }
    if not isinstance(action, str) or action not in specifications:
        raise InvalidWebSocketControl("지원하지 않는 action입니다.")
    required, allowed = specifications[action]
    missing = required - set(control)
    extra = set(control) - allowed
    if missing:
        raise InvalidWebSocketControl(f"필수 필드가 없습니다: {', '.join(sorted(missing))}")
    if extra:
        raise InvalidWebSocketControl(f"지원하지 않는 필드입니다: {', '.join(sorted(extra))}")
    request_id = control["request_id"]
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise InvalidWebSocketControl("request_id는 1~128자 문자열이어야 합니다.")
    if action == "connect":
        if control["visibility"] not in {"public", "private"}:
            raise InvalidWebSocketControl("visibility는 public 또는 private이어야 합니다.")
        validate_ticket(control["ticket"])
        validate_format(control["format"])
    elif action == "subscribe":
        endpoint_id = control["endpoint_id"]
        if not isinstance(endpoint_id, str) or not endpoint_id.startswith("websocket."):
            raise InvalidWebSocketControl("endpoint_id는 websocket.으로 시작해야 합니다.")
        if not isinstance(control["parameters"], Mapping):
            raise InvalidWebSocketControl("parameters는 객체여야 합니다.")
    elif action == "pause" and not isinstance(control["paused"], bool):
        raise InvalidWebSocketControl("paused는 boolean이어야 합니다.")
    elif action == "unsubscribe":
        endpoint_id = control["endpoint_id"]
        if not isinstance(endpoint_id, str) or not endpoint_id.startswith("websocket."):
            raise InvalidWebSocketControl("endpoint_id는 websocket.으로 시작해야 합니다.")


def validate_subscription(
    catalog: dict[str, Any], endpoint_id: str, parameters: Mapping[str, Any]
) -> dict[str, Any]:
    endpoint = endpoint_by_id(catalog, endpoint_id)
    if endpoint is None or endpoint not in catalog["websocket_streams"]:
        raise InvalidWebSocketControl("카탈로그에 없는 웹소켓(WebSocket) 스트림입니다.")
    if not isinstance(parameters, Mapping):
        raise InvalidWebSocketControl("parameters는 객체여야 합니다.")

    specifications = {
        cast(str, parameter["name"]): parameter
        for parameter in cast(list[dict[str, Any]], endpoint["parameters"])
    }
    unknown = set(parameters) - set(specifications)
    if unknown:
        raise InvalidWebSocketControl(
            f"{', '.join(sorted(unknown))} 파라미터는 이 스트림이 지원하지 않는 값입니다."
        )
    result: dict[str, Any] = {"type": endpoint["type"]}
    for name, specification in specifications.items():
        if name not in parameters:
            if specification["required"]:
                raise InvalidWebSocketControl(f"필수 파라미터 {name}가 없습니다.")
            continue
        result[name] = _validate_parameter(name, parameters[name], specification)

    if result.get("is_only_snapshot") is True and result.get("is_only_realtime") is True:
        raise InvalidWebSocketControl(
            "is_only_snapshot과 is_only_realtime은 동시에 true일 수 없습니다."
        )
    return result


def _validate_parameter(name: str, value: Any, specification: Mapping[str, Any]) -> Any:
    kind = specification["type"]
    if kind == "array":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise InvalidWebSocketControl(f"{name}는 문자열 배열이어야 합니다.")
        normalized = [item.strip().upper() for item in value]
        if (
            name == "codes"
            and normalized
            and not all(PAIR_CODE_PATTERN.fullmatch(item) for item in normalized)
        ):
            raise InvalidWebSocketControl("codes는 KRW-BTC 같은 대문자 페어 코드여야 합니다.")
        if specification["required"] and not normalized:
            raise InvalidWebSocketControl(f"필수 파라미터 {name}가 비어 있습니다.")
        return normalized
    if kind == "boolean" and not isinstance(value, bool):
        raise InvalidWebSocketControl(f"{name}는 boolean이어야 합니다.")
    if kind == "number" and (not isinstance(value, int | float) or isinstance(value, bool)):
        raise InvalidWebSocketControl(f"{name}는 number이어야 합니다.")
    if kind == "string" and not isinstance(value, str):
        raise InvalidWebSocketControl(f"{name}는 string이어야 합니다.")
    return value


def build_subscription_request(
    ticket: str, subscriptions: list[dict[str, Any]], format_name: str
) -> list[dict[str, Any]]:
    if not subscriptions:
        raise InvalidWebSocketControl("전송할 구독이 없습니다.")
    return [
        {"ticket": validate_ticket(ticket)},
        *subscriptions,
        {"format": validate_format(format_name)},
    ]


def build_list_request(ticket: str, format_name: str) -> list[dict[str, Any]]:
    return [
        {"ticket": validate_ticket(ticket)},
        {"method": "LIST_SUBSCRIPTIONS"},
        {"format": validate_format(format_name)},
    ]


def decode_upstream_frame(value: str | bytes, *, max_raw_bytes: int = 262_144) -> DecodedFrame:
    if isinstance(value, bytes):
        binary = True
        try:
            raw = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidWebSocketControl("상향 binary frame이 UTF-8이 아닙니다.") from exc
    else:
        binary = False
        raw = value
    if len(raw.encode("utf-8")) > max_raw_bytes:
        raise InvalidWebSocketControl("상향 frame이 허용 크기를 초과했습니다.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidWebSocketControl("상향 frame이 JSON이 아닙니다.") from exc
    return DecodedFrame(payload=payload, raw=raw, binary=binary)


def redact_websocket_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if str(key).lower() in SENSITIVE_KEYS
            else redact_websocket_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_websocket_value(item) for item in value]
    if isinstance(value, str):
        return BEARER_PATTERN.sub("[REDACTED]", value)
    return value


class WebSocketRateLimiter:
    """연결별 초·분 슬라이딩 윈도(sliding window)를 함께 적용한다."""

    def __init__(
        self,
        *,
        per_second: int,
        per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._per_second = per_second
        self._per_minute = per_minute
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = self._clock()
                while self._calls and now - self._calls[0] >= 60:
                    self._calls.popleft()
                second_calls = [item for item in self._calls if now - item < 1]
                waits: list[float] = []
                if len(second_calls) >= self._per_second:
                    waits.append(1 - (now - second_calls[0]))
                if len(self._calls) >= self._per_minute:
                    waits.append(60 - (now - self._calls[0]))
                if not waits:
                    self._calls.append(now)
                    return
                wait_seconds = max(waits)
            await self._sleep(max(wait_seconds, 0.001))
