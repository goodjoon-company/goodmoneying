import base64
import json
from datetime import UTC, datetime, timedelta
from inspect import getsource
from pathlib import Path
from typing import Any, cast

import pytest

from goodmoneying_shared import dataset_version_store
from goodmoneying_shared.dataset_version_store import (
    DatasetCursorMismatchError,
    _decode_list_cursor,
    _decode_series_cursor,
    _encode_list_cursor,
    _encode_series_cursor,
    _project_quality_event_ranges,
)
from goodmoneying_shared.dataset_versions import canonical_payload_hash

START = datetime(2026, 7, 17, tzinfo=UTC)
END = START + timedelta(hours=3)


def _event(event_id: int, start_at: datetime, end_at: datetime, status: str) -> dict[str, object]:
    return {
        "id": event_id,
        "range_start_at": start_at,
        "range_end_at": end_at,
        "new_status": status,
        "evidence": {},
    }


def test_coverage_A_B_A_동일범위는_ceiling내_최신_A로_투영한다() -> None:
    events = [
        _event(1, START, END, "observed"),
        _event(2, START, END, "unavailable"),
        _event(3, START, END, "observed"),
    ]

    assert _project_quality_event_ranges(events, START, END) == [(START, END, events[2])]


def test_coverage_부분_overlap은_경계를_split하고_내부_gap을_보존한다() -> None:
    events = [
        _event(1, START, START + timedelta(hours=1), "observed"),
        _event(2, START + timedelta(hours=2), END, "unavailable"),
    ]

    projected = _project_quality_event_ranges(events, START, END)

    assert [(start, end, event["id"] if event else None) for start, end, event in projected] == [
        (START, START + timedelta(hours=1), 1),
        (START + timedelta(hours=1), START + timedelta(hours=2), None),
        (START + timedelta(hours=2), END, 2),
    ]


def test_missing_fail은_no_trade를_허용하고_실제_불완전_coverage만_차단한다() -> None:
    predicate = getattr(dataset_version_store, "_coverage_blocks_fail_publication", None)

    assert callable(predicate)
    assert predicate({"status": "no_trade", "observed_count": 0, "expected_count": 60}) is False
    assert predicate({"status": "available", "observed_count": 59, "expected_count": 60}) is True
    for status in ("missing", "unavailable", "unverified"):
        assert predicate({"status": status, "observed_count": 0, "expected_count": 60}) is True


@pytest.mark.parametrize(
    ("data_kind", "unit", "requested", "expected"),
    (
        ("candle", "1m", None, "source-candle-v1"),
        ("candle", "3m", None, "candle-rollup-v2"),
        ("candle", "1d", None, "daily-source-preferred-v1"),
        ("indicator", "1m", None, "indicator-v1"),
        ("market_statistic", "1m", None, "market-statistics-v1"),
        ("microstructure", "1m", None, "microstructure-v1"),
    ),
)
def test_data_kind와_unit은_명시적_계산버전으로_해석한다(
    data_kind: str, unit: str, requested: str | None, expected: str
) -> None:
    resolver = getattr(dataset_version_store, "_resolve_calculation_version", None)

    assert callable(resolver)
    assert resolver(data_kind, unit, requested) == expected


def test_지원하지_않는_계산버전은_안정된_code로_거부한다() -> None:
    resolver = getattr(dataset_version_store, "_resolve_calculation_version", None)

    assert callable(resolver)
    with pytest.raises(ValueError, match="calculation_version_mismatch"):
        resolver("candle", "1m", "unknown-version")


def test_series_cursor는_버전_series_조회범위를_고정한다() -> None:
    cursor = _encode_series_cursor(
        dataset_version_id=11,
        series_id=101,
        from_at=START,
        to_at=END,
        last_occurred_at=START + timedelta(hours=1),
    )

    assert _decode_series_cursor(
        cursor,
        dataset_version_id=11,
        series_id=101,
        from_at=START,
        to_at=END,
    ) == START + timedelta(hours=1)
    with pytest.raises(DatasetCursorMismatchError):
        _decode_series_cursor(
            cursor,
            dataset_version_id=12,
            series_id=101,
            from_at=START,
            to_at=END,
        )


def test_dataset_version_cursor는_변조를_거부한다() -> None:
    cursor = _encode_list_cursor(ceiling=15, last_id=9)

    assert _decode_list_cursor(cursor) == {"ceiling": 15, "lastId": 9}
    with pytest.raises(DatasetCursorMismatchError):
        _decode_list_cursor(cursor[:-1] + ("A" if cursor[-1] != "A" else "B"))


@pytest.mark.parametrize(
    "payload",
    (
        {"ceiling": "15", "lastId": 9},
        {"ceiling": 15},
        {"ceiling": 15, "lastId": 9, "unexpected": True},
    ),
)
def test_dataset_version_cursor는_유효한_checksum이어도_잘못된_구조를_거부한다(
    payload: dict[str, object],
) -> None:
    envelope = {
        "payload": payload,
        "hash": canonical_payload_hash(cast(Any, payload)),
    }
    cursor = base64.urlsafe_b64encode(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")

    with pytest.raises(DatasetCursorMismatchError, match="유효하지 않은"):
        _decode_list_cursor(cursor)


def test_coverage_count는_임의_초_split에서도_UTC_정규_grid를_보존한다() -> None:
    counter = dataset_version_store._expected_bucket_count
    split = START + timedelta(seconds=30)
    end = START + timedelta(minutes=2)

    assert counter(START, split, "1m") == 1
    assert counter(split, end, "1m") == 1
    assert counter(START, split, "1m") + counter(split, end, "1m") == 2


@pytest.mark.parametrize(
    ("start", "split", "end", "unit"),
    (
        (
            datetime(2026, 7, 20, tzinfo=UTC),
            datetime(2026, 7, 20, 0, 0, 30, tzinfo=UTC),
            datetime(2026, 8, 3, tzinfo=UTC),
            "1w",
        ),
        (
            datetime(2027, 1, 1, tzinfo=UTC),
            datetime(2027, 1, 1, 0, 0, 30, tzinfo=UTC),
            datetime(2027, 3, 1, tzinfo=UTC),
            "1M",
        ),
    ),
)
def test_주월_bucket도_임의_split에서_정규_grid_합계를_보존한다(
    start: datetime, split: datetime, end: datetime, unit: str
) -> None:
    counter = dataset_version_store._expected_bucket_count

    assert counter(start, split, unit) == 1
    assert counter(split, end, unit) == 1
    assert counter(start, split, unit) + counter(split, end, unit) == 2


@pytest.mark.parametrize(
    ("data_kind", "unit", "expected"),
    (
        ("candle", "1m", (("source_candle", "1m"),)),
        ("candle", "3m", (("source_candle", "1m"),)),
        ("candle", "1d", (("source_candle", "1d"),)),
        ("indicator", "15m", (("source_candle", "1m"),)),
        ("market_statistic", "1h", (("source_candle", "1m"),)),
    ),
)
def test_품질_권위는_실제_source_candle_수집_unit으로_매핑한다(
    data_kind: str, unit: str, expected: tuple[tuple[str, str], ...]
) -> None:
    authority = dataset_version_store._quality_authorities

    assert authority({"data_kind": data_kind, "unit": unit}) == expected


def test_발행_구현은_temp_stage_streaming과_set_based_insert를_사용한다() -> None:
    source = Path(dataset_version_store.__file__).read_text()

    assert "ON COMMIT DROP" in source
    assert "fetchmany(MAX_PUBLICATION_CHUNK_SIZE)" in source
    assert "INSERT INTO dataset_publication_members" in source
    assert "INSERT INTO dataset_version_candles" in source and "SELECT" in source
    assert "member_rows:" not in source
    assert "def _insert_typed_member" not in source


def test_build_응답은_retry_lifecycle을_노출한다() -> None:
    response = dataset_version_store._build_response(
        {
            "id": 1,
            "request_id": "request",
            "idempotency_key": "idem",
            "actor_id": "actor",
            "requested_at": START,
            "frozen_at": START,
            "status": "retry_wait",
            "dataset_version_id": None,
            "last_error_code": "temporary",
            "last_error_message": "retry",
            "attempt_count": 2,
            "max_attempts": 3,
            "next_retry_at": END,
            "dead_letter_reason": None,
        }
    )

    assert response["attemptCount"] == 2
    assert response["maxAttempts"] == 3
    assert response["nextRetryAt"] == END
    assert response["deadLetterReason"] is None


class _HeartbeatResult:
    def __init__(self, current: bool) -> None:
        self._current = current

    def fetchone(self) -> dict[str, int] | None:
        return {"id": 1} if self._current else None


class _HeartbeatConnection:
    def __init__(self, repository: _HeartbeatRepository) -> None:
        self._repository = repository

    def __enter__(self) -> _HeartbeatConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, parameters: tuple[object, ...]) -> _HeartbeatResult:
        self._repository.executed.append((sql, parameters))
        return _HeartbeatResult(self._repository.current)


class _HeartbeatRepository:
    def __init__(self) -> None:
        self.current = True
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def _connect(self) -> _HeartbeatConnection:
        return _HeartbeatConnection(self)


def test_heartbeat는_별도_연결에서_owner_generation_expiry를_fencing한다() -> None:
    repository = _HeartbeatRepository()
    heartbeat = dataset_version_store._DatasetBuildLeaseHeartbeat(
        repository, build_id=7, worker_id="worker-a", generation=3
    )

    heartbeat.renew()
    sql, parameters = repository.executed[-1]
    assert "lease_generation=%s" in sql
    assert "lease_expires_at > clock_timestamp()" in sql
    assert parameters[-3:] == (7, "worker-a", 3)

    repository.current = False
    with pytest.raises(dataset_version_store.DatasetBuildLeaseLostError, match="fencing"):
        heartbeat.renew()


def test_발행_transaction은_heartbeat와_충돌하지_않는_READ_COMMITTED다() -> None:
    source = getsource(dataset_version_store.publish_next_build)

    assert "REPEATABLE READ" not in source
    assert "lease_expires_at > clock_timestamp()" in source


def test_일봉_stage는_NULL_ceiling을_무제한으로_해석하지_않는다() -> None:
    executed: list[tuple[str, tuple[object, ...]]] = []

    class Connection:
        def execute(self, sql: str, parameters: tuple[object, ...]) -> None:
            executed.append((sql, parameters))

    dataset_version_store._stage_daily_candles(
        Connection(),
        {
            "as_of": END,
            "input_start_at": START,
            "end_at": END,
        },
        {
            "id": 1,
            "instrument_id": 2,
            "data_kind": "candle",
            "exchange": "UPBIT",
            "market_code": "KRW-BTC",
            "unit": "1d",
            "quality_event_through_id": None,
            "source_revision_through_id": None,
            "candle_rollup_through_id": None,
        },
    )

    stage_sql = executed[0][0]
    assert stage_sql.count("IS NOT NULL AND") == 3
    assert "IS NULL OR revision.id" not in stage_sql
    assert "IS NULL OR rollup.id" not in stage_sql
