from datetime import UTC, datetime

from goodmoneying_shared.postgres_repository import _rollup_candle


def test_PostgreSQL_rollup_mapper가_지표에_필요한_불변_frontier를_보존한다() -> None:
    occurred_at = datetime(2026, 7, 17, tzinfo=UTC)
    candle = _rollup_candle(
        {
            "id": 701,
            "candle_start_at": occurred_at,
            "open_price": "1",
            "high_price": "2",
            "low_price": "1",
            "close_price": "2",
            "trade_volume": "3",
            "trade_amount": "4",
            "completeness": "complete",
            "calculation_version": "candle-rollup-v2",
            "source_as_of": occurred_at,
            "knowledge_at": occurred_at,
            "input_content_hash": "a" * 64,
            "coverage_snapshot_hash": "b" * 64,
            "quality": "available",
            "input_revision_ids": [5, 6],
            "source_revision_through_id": 6,
            "quality_event_through_id": 19,
        }
    )

    assert candle.rollup_id == 701
    assert candle.source_revision_through_id == 6
    assert candle.quality_event_through_id == 19
    assert candle.coverage_snapshot_hash == "b" * 64
