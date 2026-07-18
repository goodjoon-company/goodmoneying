from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from goodmoneying_api.main import create_app
from goodmoneying_shared.realtime_stream import StreamCursorContext, encode_stream_cursor
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_worker.collector import seed_repository
from goodmoneying_worker.upbit_client import FixtureUpbitClient


def seeded_repository_and_client() -> tuple[SQLiteOperationsRepository, TestClient]:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    return repository, TestClient(create_app(repository))


def test_analysis_internal_stream_wraps_messages_with_sequence_cursor_and_heartbeat() -> None:
    repository, client = seeded_repository_and_client()
    instrument_id = repository.list_active_targets()[0].id
    topic = f"analysis.instrument:{instrument_id}:1d:365"

    with client.websocket_connect("/v1/realtime/analysis/stream") as websocket:
        websocket.send_json(
            {
                "schema_version": "1.0",
                "message_type": "subscribe",
                "topic": topic,
                "scope": "operator:local",
                "payload": {"instrumentId": instrument_id, "unit": "1d", "rangeDays": 365},
            }
        )
        messages = [websocket.receive_json() for _ in range(7)]

    assert [message["message_type"] for message in messages[:6]] == [
        "subscribed",
        "event",
        "event",
        "event",
        "event",
        "event",
    ]
    assert messages[-1]["message_type"] == "heartbeat"
    assert [message["sequence"] for message in messages[:6]] == [1, 2, 3, 4, 5, 6]
    assert messages[-1]["sequence"] == 6
    assert messages[-1]["payload"]["lastSequence"] == 6
    assert messages[-1]["payload"]["serverTime"]
    assert all(message["topic"] == topic for message in messages)
    assert all(message["scope"] == "operator:local" for message in messages)
    assert all(message["cursor"] for message in messages)
    assert messages[1]["payload"]["type"] == "analysis.instrument"
    assert messages[2]["payload"]["type"] == "analysis.chart"


def test_analysis_internal_stream_rejects_invalid_resume_cursor_with_snapshot_required() -> None:
    repository, client = seeded_repository_and_client()
    instrument_id = repository.list_active_targets()[0].id
    issued_at = datetime(2026, 7, 18, 1, 0, tzinfo=UTC)
    topic = f"analysis.instrument:{instrument_id}:1d:365"
    stale_cursor = encode_stream_cursor(
        StreamCursorContext(
            topic=topic,
            scope="operator:local",
            snapshot_version="analysis-snapshot-v1",
            sequence=12,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=1),
        ),
        secret="local-dev-stream-cursor-secret",
    )

    with client.websocket_connect("/v1/realtime/analysis/stream") as websocket:
        websocket.send_json(
            {
                "schema_version": "1.0",
                "message_type": "subscribe",
                "topic": topic,
                "scope": "operator:local",
                "resume_cursor": stale_cursor,
                "payload": {"instrumentId": instrument_id, "unit": "1d", "rangeDays": 365},
            }
        )
        message = websocket.receive_json()

    assert message["message_type"] == "snapshot_required"
    assert message["payload"]["code"] == "CURSOR_EXPIRED"
    assert message["payload"]["snapshotTopic"] == topic
