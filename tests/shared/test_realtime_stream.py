from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from goodmoneying_shared.realtime_stream import (
    StreamCursorContext,
    StreamCursorError,
    decode_stream_cursor,
    encode_stream_cursor,
)

NOW = datetime(2026, 7, 18, 6, 30, tzinfo=UTC)
SECRET = "p2-stream-test-secret"


def _context(sequence: int = 41) -> StreamCursorContext:
    return StreamCursorContext(
        topic="analysis.instrument:1:1m:365",
        scope="operator:local",
        sequence=sequence,
        snapshot_version="analysis-snapshot-v1",
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=24),
    )


def test_스트림_cursor는_topic_scope_sequence와_만료를_서명해_복원한다() -> None:
    cursor = encode_stream_cursor(_context(), SECRET)

    decoded = decode_stream_cursor(
        cursor,
        SECRET,
        topic="analysis.instrument:1:1m:365",
        scope="operator:local",
        now=NOW,
    )

    assert decoded == _context()


def test_스트림_cursor는_위변조와_문맥_불일치를_거부한다() -> None:
    cursor = encode_stream_cursor(_context(), SECRET)
    tampered = cursor[:-2] + "aa"

    with pytest.raises(StreamCursorError, match="서명"):
        decode_stream_cursor(
            tampered,
            SECRET,
            topic="analysis.instrument:1:1m:365",
            scope="operator:local",
            now=NOW,
        )

    with pytest.raises(StreamCursorError, match="문맥"):
        decode_stream_cursor(
            cursor,
            SECRET,
            topic="analysis.instrument:2:1m:365",
            scope="operator:local",
            now=NOW,
        )


def test_스트림_cursor는_만료되면_resume에_사용할_수_없다() -> None:
    cursor = encode_stream_cursor(_context(), SECRET)

    with pytest.raises(StreamCursorError, match="만료"):
        decode_stream_cursor(
            cursor,
            SECRET,
            topic="analysis.instrument:1:1m:365",
            scope="operator:local",
            now=NOW + timedelta(days=2),
        )
