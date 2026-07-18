from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from goodmoneying_api.main import _stream_send_timeout_seconds, create_app
from goodmoneying_shared.realtime_stream import (
    StreamCursorContext,
    decode_stream_cursor,
    encode_stream_cursor,
)
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


def test_analysis_stream_rejects_non_object_and_topic_mismatch_without_disconnect() -> None:
    repository, client = seeded_repository_and_client()
    instrument_id = repository.list_active_targets()[0].id

    with client.websocket_connect("/v1/realtime/analysis/stream") as websocket:
        websocket.send_json(["not", "an", "object"])
        malformed = websocket.receive_json()
        websocket.send_json(
            {
                "schema_version": "1.0",
                "message_type": "subscribe",
                "topic": "analysis.instrument:999:1d:365",
                "scope": "operator:local",
                "payload": {"instrumentId": instrument_id, "unit": "1d", "rangeDays": 365},
            }
        )
        mismatch = websocket.receive_json()

    assert malformed["message_type"] == "error"
    assert malformed["payload"]["code"] == "INVALID_MESSAGE"
    assert mismatch["message_type"] == "error"
    assert mismatch["payload"]["code"] == "INVALID_TOPIC"


def test_analysis_stream_snapshot_rest_endpoint_returns_recovery_snapshot_and_cursor() -> None:
    repository, client = seeded_repository_and_client()
    instrument_id = repository.list_active_targets()[0].id
    topic = f"analysis.instrument:{instrument_id}:1d:365"

    response = client.get(
        "/v1/realtime/analysis/snapshot",
        params={"instrumentId": instrument_id, "unit": "1d", "rangeDays": 365},
    )

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["schema_version"] == "1.0"
    assert snapshot["topic"] == topic
    assert snapshot["scope"] == "operator:local"
    assert snapshot["sequence"] >= 1
    assert snapshot["cursor"]
    assert snapshot["snapshotVersion"].startswith("analysis-snapshot-v1:")
    assert snapshot["payload"]["type"] == "analysis.snapshot"
    assert snapshot["payload"]["instrument"]["id"] == instrument_id
    assert snapshot["payload"]["unit"] == "1d"
    assert snapshot["payload"]["candles"]
    assert snapshot["payload"]["market"]["ticker"]
    decoded = decode_stream_cursor(
        snapshot["cursor"],
        "local-dev-stream-cursor-secret",
        topic=topic,
        scope="operator:local",
        now=datetime.now(UTC),
    )
    assert decoded.sequence == snapshot["sequence"]


def test_analysis_stream_resume_cursor_after_rest_snapshot_skips_initial_snapshot_replay() -> None:
    repository, client = seeded_repository_and_client()
    instrument_id = repository.list_active_targets()[0].id
    topic = f"analysis.instrument:{instrument_id}:1d:365"
    snapshot = client.get(
        "/v1/realtime/analysis/snapshot",
        params={"instrumentId": instrument_id, "unit": "1d", "rangeDays": 365},
    ).json()

    with client.websocket_connect("/v1/realtime/analysis/stream") as websocket:
        websocket.send_json(
            {
                "schema_version": "1.0",
                "message_type": "subscribe",
                "topic": topic,
                "scope": "operator:local",
                "resumeCursor": snapshot["cursor"],
                "payload": {"instrumentId": instrument_id, "unit": "1d", "rangeDays": 365},
            }
        )
        subscribed = websocket.receive_json()
        heartbeat = websocket.receive_json()

    assert subscribed["message_type"] == "subscribed"
    assert subscribed["sequence"] == snapshot["sequence"] + 1
    assert heartbeat["message_type"] == "heartbeat"
    assert heartbeat["sequence"] == subscribed["sequence"]
    assert heartbeat["payload"]["lastSequence"] == subscribed["sequence"]


def test_analysis_stream_rejects_unknown_snapshot_version_cursor_with_snapshot_required() -> None:
    repository, client = seeded_repository_and_client()
    instrument_id = repository.list_active_targets()[0].id
    topic = f"analysis.instrument:{instrument_id}:1d:365"
    issued_at = datetime.now(UTC)
    unknown_version_cursor = encode_stream_cursor(
        StreamCursorContext(
            topic=topic,
            scope="operator:local",
            snapshot_version="analysis-snapshot-v99",
            sequence=1,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(hours=1),
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
                "resumeCursor": unknown_version_cursor,
                "payload": {"instrumentId": instrument_id, "unit": "1d", "rangeDays": 365},
            }
        )
        message = websocket.receive_json()

    assert message["message_type"] == "snapshot_required"
    assert message["payload"]["code"] == "CURSOR_INVALID"


def test_analysis_stream_snapshot_rest_endpoint_returns_contract_error_for_unwatched_coin() -> None:
    _repository, client = seeded_repository_and_client()

    response = client.get(
        "/v1/realtime/analysis/snapshot",
        params={"instrumentId": 999_999, "unit": "1d", "rangeDays": 365},
    )

    assert response.status_code == 403
    assert response.json() == {
        "code": "NOT_WATCHLISTED",
        "message": "관심목록에 있는 코인만 분석할 수 있습니다.",
    }


def test_analysis_stream_handler_has_slow_consumer_backpressure_guard(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOODMONEYING_STREAM_SEND_TIMEOUT_SECONDS", "0.25")
    source = inspect.getsource(create_app)

    assert _stream_send_timeout_seconds() == 0.25
    assert "message_type=\"slow_consumer\"" in source
    assert "await wait_for(websocket.send_json(message)" in source
    assert "timeout=min(send_timeout_seconds, 0.25)" in source
    assert "await websocket.close(code=1013)" in source
