from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

StreamMessageType = Literal[
    "subscribed",
    "event",
    "heartbeat",
    "snapshot_required",
    "slow_consumer",
    "error",
]


class StreamCursorError(ValueError):
    """resume cursor가 위변조·만료·문맥 불일치로 사용할 수 없을 때 발생한다."""


class CursorTamperedError(StreamCursorError):
    """커서 서명(signature)이 맞지 않거나 형식이 깨졌을 때 발생한다."""


class CursorMismatchError(StreamCursorError):
    """커서의 topic/scope가 현재 구독 문맥과 다를 때 발생한다."""


class CursorExpiredError(StreamCursorError):
    """커서 보존 시간이 지났을 때 발생한다."""


@dataclass(frozen=True)
class StreamCursorContext:
    topic: str
    scope: str
    sequence: int
    snapshot_version: str
    issued_at: datetime
    expires_at: datetime


@dataclass
class RealtimeEnvelopeBuilder:
    topic: str
    scope: str
    cursor_secret: str
    snapshot_version: str = "analysis-snapshot-v1"
    sequence: int = 0
    cursor_ttl: timedelta = timedelta(hours=24)

    def make(
        self,
        *,
        message_type: StreamMessageType,
        payload: dict[str, object],
        now: datetime,
        increment_sequence: bool | None = None,
    ) -> dict[str, object]:
        if increment_sequence is None:
            increment_sequence = message_type in {"subscribed", "event"}
        if increment_sequence:
            self.sequence += 1
        published_at = _as_utc(now)
        cursor = encode_stream_cursor(
            StreamCursorContext(
                topic=self.topic,
                scope=self.scope,
                sequence=self.sequence,
                snapshot_version=self.snapshot_version,
                issued_at=published_at,
                expires_at=published_at + self.cursor_ttl,
            ),
            self.cursor_secret,
        )
        return {
            "schema_version": "1.0",
            "topic": self.topic,
            "scope": self.scope,
            "event_id": str(uuid4()),
            "sequence": self.sequence,
            "cursor": cursor,
            "occurred_at": published_at.isoformat().replace("+00:00", "Z"),
            "published_at": published_at.isoformat().replace("+00:00", "Z"),
            "message_type": message_type,
            "payload": payload,
        }

    def resume_from(self, cursor_context: StreamCursorContext) -> None:
        if cursor_context.topic != self.topic or cursor_context.scope != self.scope:
            raise StreamCursorError("stream cursor 문맥이 현재 구독과 다릅니다.")
        self.sequence = cursor_context.sequence


def encode_stream_cursor(context: StreamCursorContext, secret: str) -> str:
    payload = _canonical_json(
        {
            "topic": context.topic,
            "scope": context.scope,
            "sequence": context.sequence,
            "snapshotVersion": context.snapshot_version,
            "issuedAt": _as_utc(context.issued_at).isoformat().replace("+00:00", "Z"),
            "expiresAt": _as_utc(context.expires_at).isoformat().replace("+00:00", "Z"),
        }
    )
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return f"{_base64_url_encode(payload)}.{_base64_url_encode(signature)}"


def decode_stream_cursor(
    cursor: str,
    secret: str,
    *,
    topic: str | None = None,
    scope: str | None = None,
    expected_topic: str | None = None,
    expected_scope: str | None = None,
    now: datetime,
) -> StreamCursorContext:
    topic = expected_topic if expected_topic is not None else topic
    scope = expected_scope if expected_scope is not None else scope
    if topic is None or scope is None:
        raise CursorMismatchError("stream cursor 검증 topic/scope가 필요합니다.")
    try:
        payload_token, signature_token = cursor.split(".", 1)
        payload = _base64_url_decode(payload_token)
        signature = _base64_url_decode(signature_token)
    except ValueError as exc:
        raise CursorTamperedError("stream cursor 형식이 올바르지 않습니다.") from exc
    expected_signature = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise CursorTamperedError("stream cursor 서명이 올바르지 않습니다.")
    try:
        raw = json.loads(payload)
        context = StreamCursorContext(
            topic=str(raw["topic"]),
            scope=str(raw["scope"]),
            sequence=int(raw["sequence"]),
            snapshot_version=str(raw.get("snapshotVersion", raw.get("snapshot_version"))),
            issued_at=_parse_utc(str(raw.get("issuedAt", raw.get("issued_at")))),
            expires_at=_parse_utc(str(raw.get("expiresAt", raw.get("expires_at")))),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CursorTamperedError("stream cursor payload가 올바르지 않습니다.") from exc
    if context.topic != topic or context.scope != scope:
        raise CursorMismatchError("stream cursor 문맥이 현재 구독과 다릅니다.")
    if context.expires_at <= _as_utc(now):
        raise CursorExpiredError("stream cursor가 만료되었습니다.")
    if context.sequence < 0:
        raise CursorTamperedError("stream cursor sequence가 올바르지 않습니다.")
    return context


def build_stream_envelope(
    *,
    topic: str,
    scope: str,
    sequence: int,
    cursor: str,
    message_type: StreamMessageType,
    payload: dict[str, object],
    now: datetime,
) -> dict[str, object]:
    published_at = _as_utc(now).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "1.0",
        "topic": topic,
        "scope": scope,
        "event_id": str(uuid4()),
        "sequence": sequence,
        "cursor": cursor,
        "occurred_at": published_at,
        "published_at": published_at,
        "message_type": message_type,
        "payload": payload,
    }


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _base64_url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _base64_url_decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode(payload + padding)


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
