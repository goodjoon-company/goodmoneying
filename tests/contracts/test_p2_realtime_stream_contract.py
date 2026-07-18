from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

from goodmoneying_shared.realtime_stream import (
    RealtimeEnvelopeBuilder,
    StreamCursorContext,
    StreamCursorError,
    decode_stream_cursor,
    encode_stream_cursor,
)

STREAM_CONTRACT = Path("docs/contracts/api/internal-realtime-stream.schema.json")
OPENAPI_CONTRACT = Path("docs/contracts/api/openapi.yaml")


def test_internal_realtime_stream_schema_validates_core_message_types() -> None:
    validator = Draft202012Validator(
        json.loads(STREAM_CONTRACT.read_text()),
        format_checker=FormatChecker(),
    )
    issued_at = datetime(2026, 7, 18, 1, 0, tzinfo=UTC)
    builder = RealtimeEnvelopeBuilder(
        topic="analysis.instrument:1:1d:365",
        scope="operator:local",
        cursor_secret="test-secret",
    )

    messages = [
        builder.make(
            message_type="subscribed",
            payload={"type": "analysis.session", "subscriptionId": "subscription-1"},
            now=issued_at,
        ),
        builder.make(
            message_type="event",
            payload={"type": "analysis.instrument", "instrument": {"id": 1}},
            now=issued_at,
        ),
        builder.make(
            message_type="heartbeat",
            payload={
                "type": "stream.heartbeat",
                "lastSequence": 1,
                "serverTime": issued_at.isoformat().replace("+00:00", "Z"),
            },
            now=issued_at,
            increment_sequence=False,
        ),
        builder.make(
            message_type="snapshot_required",
            payload={
                "type": "analysis.snapshot_required",
                "code": "CURSOR_INVALID",
                "message": "snapshot 복구가 필요합니다.",
                "snapshotTopic": "analysis.instrument:1:1d:365",
            },
            now=issued_at,
            increment_sequence=False,
        ),
        builder.make(
            message_type="slow_consumer",
            payload={
                "type": "stream.slow_consumer",
                "code": "SLOW_CONSUMER",
                "message": "클라이언트 수신이 지연되어 REST snapshot 복구가 필요합니다.",
                "lastSequence": 1,
            },
            now=issued_at,
            increment_sequence=False,
        ),
        builder.make(
            message_type="error",
            payload={
                "type": "analysis.error",
                "code": "INVALID_MESSAGE",
                "message": "구독 메시지가 필요합니다.",
            },
            now=issued_at,
            increment_sequence=False,
        ),
    ]

    for message in messages:
        assert list(validator.iter_errors(message)) == []


def test_stream_cursor_is_opaque_signed_and_context_bound() -> None:
    issued_at = datetime(2026, 7, 18, 1, 0, tzinfo=UTC)
    context = StreamCursorContext(
        topic="analysis.instrument:1:1d:365",
        scope="operator:local",
        snapshot_version="analysis-snapshot-v1",
        sequence=42,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(hours=24),
    )

    cursor = encode_stream_cursor(context, "test-secret")

    assert "analysis.instrument" not in cursor
    assert decode_stream_cursor(
        cursor,
        "test-secret",
        topic=context.topic,
        scope=context.scope,
        now=issued_at + timedelta(minutes=1),
    ) == context

    tampered = cursor[:-2] + ("aa" if cursor[-2:] != "aa" else "bb")
    for candidate, topic, now in (
        (tampered, context.topic, issued_at + timedelta(minutes=1)),
        (cursor, "analysis.instrument:2:1d:365", issued_at + timedelta(minutes=1)),
        (cursor, context.topic, issued_at + timedelta(days=2)),
    ):
        try:
            decode_stream_cursor(
                candidate,
                "test-secret",
                topic=topic,
                scope=context.scope,
                now=now,
            )
        except StreamCursorError:
            pass
        else:
            raise AssertionError("사용할 수 없는 cursor를 거부해야 한다")


def test_P2_8_OpenAPI는_REST_snapshot_cursor_복구_endpoint를_정의한다() -> None:
    document = yaml.safe_load(OPENAPI_CONTRACT.read_text())
    operation = document["paths"]["/v1/realtime/analysis/snapshot"]["get"]
    parameter_names = {parameter["name"] for parameter in operation["parameters"]}
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    schemas = document["components"]["schemas"]

    assert operation["operationId"] == "getRealtimeAnalysisSnapshot"
    assert parameter_names == {"instrumentId", "unit", "rangeDays"}
    assert response_schema == {"$ref": "#/components/schemas/RealtimeAnalysisSnapshotResponse"}
    assert schemas["RealtimeAnalysisSnapshotResponse"]["required"] == [
        "schema_version",
        "topic",
        "scope",
        "sequence",
        "cursor",
        "snapshotVersion",
        "issuedAt",
        "expiresAt",
        "payload",
    ]
    assert schemas["RealtimeAnalysisSnapshotPayload"]["properties"]["type"]["const"] == (
        "analysis.snapshot"
    )
