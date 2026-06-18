from __future__ import annotations

from pathlib import Path

SCHEMA_PATH = Path("docs/contracts/db/schema.sql")


def test_db_contract_declares_m1_tables_and_constraints() -> None:
    schema = SCHEMA_PATH.read_text()

    for table in [
        "instruments",
        "candidate_universe_snapshots",
        "candidate_universe_entries",
        "collection_targets",
        "collection_plans",
        "collection_coverage_snapshots",
        "collection_coverage_segments",
        "collection_runs",
        "target_collection_results",
        "source_candles",
        "ticker_snapshots",
        "orderbook_summaries",
        "missing_ranges",
        "backfill_jobs",
        "audit_logs",
        "notification_events",
    ]:
        assert f"CREATE TABLE {table}" in schema

    assert "CONSTRAINT source_candles_uk UNIQUE" in schema
    assert "CONSTRAINT ticker_snapshots_uk UNIQUE" in schema
    assert "CONSTRAINT orderbook_summaries_uk UNIQUE" in schema
    assert "CONSTRAINT collection_plans_instrument_uk UNIQUE" in schema
    assert "CONSTRAINT collection_coverage_segments_percent_ck CHECK" in schema
    assert "TIMESTAMPTZ" in schema
    assert "NUMERIC" in schema
