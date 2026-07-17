from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from goodmoneying_shared import aggregation as aggregation_module
from goodmoneying_shared.aggregation import (
    AGGREGATION_UNITS,
    aggregate_candles,
    rollup_bucket_start,
)
from goodmoneying_shared.models import SourceCandle
from goodmoneying_shared.postgres_repository import _derive_candles as derive_postgres_candles
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository

ALL_CANDLE_UNITS = ("1m", "3m", "5m", "10m", "15m", "30m", "1h", "4h", "1d", "1w", "1M")


@pytest.mark.parametrize(
    ("unit", "source_at", "expected"),
    [
        ("1m", "2026-07-17T12:37:49+00:00", "2026-07-17T12:37:00+00:00"),
        ("3m", "2026-07-17T12:37:49+00:00", "2026-07-17T12:36:00+00:00"),
        ("5m", "2026-07-17T12:37:49+00:00", "2026-07-17T12:35:00+00:00"),
        ("10m", "2026-07-17T12:37:49+00:00", "2026-07-17T12:30:00+00:00"),
        ("15m", "2026-07-17T12:37:49+00:00", "2026-07-17T12:30:00+00:00"),
        ("30m", "2026-07-17T12:37:49+00:00", "2026-07-17T12:30:00+00:00"),
        ("1h", "2026-07-17T12:37:49+00:00", "2026-07-17T12:00:00+00:00"),
        ("4h", "2026-07-17T14:37:49+00:00", "2026-07-17T12:00:00+00:00"),
        ("1d", "2026-07-17T12:37:49+00:00", "2026-07-17T00:00:00+00:00"),
        ("1w", "2026-07-19T12:37:49+00:00", "2026-07-13T00:00:00+00:00"),
        ("1M", "2026-07-31T12:37:49+00:00", "2026-07-01T00:00:00+00:00"),
    ],
)
def test_every_candle_unit_uses_deterministic_utc_boundaries(
    unit: str, source_at: str, expected: str
) -> None:
    bucket = rollup_bucket_start(unit, datetime.fromisoformat(source_at))

    assert bucket == datetime.fromisoformat(expected)
    assert bucket.tzinfo is UTC


def test_all_user_candle_units_are_supported() -> None:
    assert tuple(AGGREGATION_UNITS) == ALL_CANDLE_UNITS


def test_legacy_repository_derivation_preserves_lineage_metadata() -> None:
    source = [_candle(0, "100"), _candle(1, "101")]
    sqlite_repository = SQLiteOperationsRepository()

    for candles in (
        sqlite_repository._derive_candles("3m", source),
        derive_postgres_candles("3m", source),
    ):
        assert len(candles) == 1
        assert candles[0].source_as_of == source[-1].collected_at
        assert candles[0].knowledge_at == source[-1].collected_at
        assert len(candles[0].input_content_hash) == 64


def test_same_decimal_inputs_and_calculation_version_have_same_content_hash() -> None:
    source = [_candle(0, "100.1000"), _candle(1, "100.2000")]

    first = aggregate_candles("3m", source)
    second = aggregate_candles("3m", list(reversed(source)))

    assert first[0].open == Decimal("100.1000")
    assert first[0].close == Decimal("100.2000")
    assert getattr(first[0], "calculation_version", None) == "candle-rollup-v2"
    assert getattr(first[0], "input_content_hash", None) == getattr(
        second[0], "input_content_hash", object()
    )


def test_rollup_content_hash_ignores_surrogate_revision_ids_and_decimal_scale() -> None:
    first_source = SourceCandle(
        **{**_candle(0, "100").__dict__, "revision_id": 10, "input_content_hash": "same"}
    )
    rebuilt_source = SourceCandle(
        **{
            **_candle(0, "100.000").__dict__,
            "revision_id": 999,
            "input_content_hash": "same",
        }
    )

    assert (
        aggregate_candles("3m", [first_source])[0].input_content_hash
        == aggregate_candles("3m", [rebuilt_source])[0].input_content_hash
    )


def test_aggregation_accepts_coverage_semantics_instead_of_row_count_only() -> None:
    parameters = inspect.signature(aggregate_candles).parameters

    assert "coverage" in parameters


def test_no_trade_completes_a_bucket_but_missing_keeps_it_partial() -> None:
    coverage_type = getattr(aggregation_module, "CoverageSlice", None)
    assert coverage_type is not None
    start = datetime(2026, 7, 17, tzinfo=UTC)
    source = [_candle(0, "100"), _candle(1, "101")]

    no_trade = aggregate_candles(
        "3m",
        source,
        coverage=[
            coverage_type(start + timedelta(minutes=2), start + timedelta(minutes=3), "no_trade")
        ],
    )[0]
    missing = aggregate_candles(
        "3m",
        source,
        coverage=[
            coverage_type(start + timedelta(minutes=2), start + timedelta(minutes=3), "missing")
        ],
    )[0]

    assert no_trade.completeness == "complete"
    assert no_trade.quality == "available"
    assert missing.completeness == "partial"
    assert missing.quality == "missing"


def test_midnight_minute_bucket_uses_minutes_for_coverage() -> None:
    coverage_type = aggregation_module.CoverageSlice
    start = datetime(2026, 7, 17, tzinfo=UTC)
    result = aggregate_candles(
        "3m",
        [_candle(0, "100"), _candle(1, "101")],
        coverage=[
            coverage_type(start + timedelta(minutes=2), start + timedelta(minutes=3), "no_trade")
        ],
    )

    assert result[0].completeness == "complete"


def test_partial_no_trade_coverage_leaves_unclassified_slots_unverified() -> None:
    start = datetime(2026, 7, 17, tzinfo=UTC)
    result = aggregate_candles(
        "5m",
        [_candle(0, "100"), _candle(1, "101")],
        coverage=[
            aggregation_module.CoverageSlice(
                start + timedelta(minutes=2), start + timedelta(minutes=3), "no_trade"
            )
        ],
    )[0]

    assert result.completeness == "partial"
    assert result.quality == "unverified"


def test_sqlite_source_candle_revisions_are_idempotent_and_append_only() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.refresh_candidate_universe([("KRW-P2REV", "개정", "100")])[0].instrument
    first = _candle(0, "100")
    first = SourceCandle(**{**first.__dict__, "instrument_id": instrument.id})
    changed = SourceCandle(
        **{
            **first.__dict__,
            "close_price": Decimal("101"),
            "collected_at": first.collected_at + timedelta(seconds=1),
        }
    )

    repository.record_incremental_collection([], [], [first])
    repository.record_incremental_collection([], [], [first])
    repository.record_incremental_collection([], [], [changed])

    table = repository._execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'source_candle_revisions'"
    ).fetchone()
    assert table is not None
    revisions = repository._execute(
        """
        SELECT revision_number, close_price, input_content_hash
        FROM source_candle_revisions ORDER BY revision_number
        """
    ).fetchall()
    assert [(row["revision_number"], row["close_price"]) for row in revisions] == [
        (1, "100"),
        (2, "101"),
    ]
    assert len({row["input_content_hash"] for row in revisions}) == 2


def test_sqlite_revision_ledger_preserves_batch_and_late_projection_order() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.refresh_candidate_universe([("KRW-P2ORDER", "순서", "100")])[
        0
    ].instrument
    first = SourceCandle(**{**_candle(0, "100").__dict__, "instrument_id": instrument.id})
    latest = SourceCandle(
        **{
            **first.__dict__,
            "close_price": Decimal("102"),
            "collected_at": first.collected_at + timedelta(seconds=2),
        }
    )
    late = SourceCandle(
        **{
            **first.__dict__,
            "close_price": Decimal("101"),
            "collected_at": first.collected_at + timedelta(seconds=1),
        }
    )

    repository.record_incremental_collection([], [], [first, latest])
    repository.record_incremental_collection([], [], [late])

    revisions = repository._execute(
        """
        SELECT id, revision_number, close_price, input_content_hash
        FROM source_candle_revisions ORDER BY revision_number
        """
    ).fetchall()
    projection = repository._execute(
        "SELECT close_price, collected_at FROM source_candles WHERE instrument_id = ?",
        (instrument.id,),
    ).fetchone()
    assert [(row["revision_number"], row["close_price"]) for row in revisions] == [
        (1, "100"),
        (2, "102"),
        (3, "101"),
    ]
    assert projection["close_price"] == "102"
    assert _as_utc(projection["collected_at"]) == latest.collected_at
    repository.materialize_candle_rollups(instrument.id, "3m")
    rollup = repository.candle_rollups(
        instrument.id,
        "3m",
        first.candle_start_at,
        first.candle_start_at + timedelta(minutes=3),
    )[0]
    projection_revision = revisions[1]
    expected_source = SourceCandle(
        **{
            **latest.__dict__,
            "revision_id": projection_revision["id"],
            "input_content_hash": projection_revision["input_content_hash"],
        }
    )
    assert rollup.close == latest.close_price
    assert rollup.input_revision_ids == (projection_revision["id"],)
    assert (
        rollup.input_content_hash
        == aggregate_candles("3m", [expected_source])[0].input_content_hash
    )


def test_sqlite_revision_idempotency_is_consecutive_and_preserves_a_b_a() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.refresh_candidate_universe([("KRW-P2ABA", "재등장", "100")])[
        0
    ].instrument
    first = SourceCandle(**{**_candle(0, "100").__dict__, "instrument_id": instrument.id})
    second = SourceCandle(
        **{
            **first.__dict__,
            "close_price": Decimal("101"),
            "collected_at": first.collected_at + timedelta(seconds=1),
        }
    )
    returned = SourceCandle(
        **{**first.__dict__, "collected_at": first.collected_at + timedelta(seconds=2)}
    )

    repository.record_incremental_collection([], [], [first, first, second, returned])
    rows = repository._execute(
        "SELECT revision_number, close_price FROM source_candle_revisions ORDER BY revision_number"
    ).fetchall()

    assert [(row["revision_number"], row["close_price"]) for row in rows] == [
        (1, "100"),
        (2, "101"),
        (3, "100"),
    ]


@pytest.mark.parametrize(
    ("status", "expected_completeness", "expected_quality"),
    [("no_trade", "complete", "available"), ("missing", "partial", "missing")],
)
def test_sqlite_materialized_completeness_uses_coverage_status(
    status: str, expected_completeness: str, expected_quality: str
) -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.refresh_candidate_universe([("KRW-P2COVER", "커버리지", "100")])[
        0
    ].instrument
    candles = [
        SourceCandle(
            **{**_candle(offset, str(100 + offset)).__dict__, "instrument_id": instrument.id}
        )
        for offset in (0, 1)
    ]
    repository.record_incremental_collection([], [], candles)
    missing_start = datetime(2026, 7, 17, tzinfo=UTC) + timedelta(minutes=2)
    repository._execute(
        """
        INSERT INTO coverage_intervals (
          instrument_id, candle_unit, range_start_at, range_end_at, status
        ) VALUES (?, '1m', ?, ?, ?)
        """,
        (
            instrument.id,
            missing_start.astimezone().isoformat(),
            (missing_start + timedelta(minutes=1)).astimezone().isoformat(),
            status,
        ),
    )
    repository._conn.commit()

    repository.materialize_candle_rollups(instrument.id, "3m")
    rollup = repository.candle_rollups(
        instrument.id,
        "3m",
        datetime(2026, 7, 17, tzinfo=UTC),
        datetime(2026, 7, 17, tzinfo=UTC) + timedelta(minutes=3),
    )[0]

    assert rollup.completeness == expected_completeness
    assert rollup.quality == expected_quality


def _candle(offset: int, price: str) -> SourceCandle:
    started_at = datetime(2026, 7, 17, tzinfo=UTC) + timedelta(minutes=offset)
    return SourceCandle(
        instrument_id=1,
        candle_unit="1m",
        candle_start_at=started_at,
        open_price=Decimal(price),
        high_price=Decimal(price),
        low_price=Decimal(price),
        close_price=Decimal(price),
        trade_volume=Decimal("0.1000"),
        trade_amount=Decimal("10.01000"),
        collected_at=started_at + timedelta(seconds=5),
    )


def _as_utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)
