from __future__ import annotations

from pathlib import Path

SCHEMA_PATH = Path("docs/contracts/db/schema.sql")
BASELINE_MIGRATION_PATH = Path(
    "docs/contracts/db/migrations/20260715000100_initial_schema.sql"
)


def test_db_contract_declares_m1_tables_and_constraints() -> None:
    schema = BASELINE_MIGRATION_PATH.read_text()

    tables = [
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
    ]
    for table in tables:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema

    assert "CONSTRAINT source_candles_uk UNIQUE" in schema
    assert "CONSTRAINT ticker_snapshots_uk UNIQUE" in schema
    assert "CONSTRAINT orderbook_summaries_uk UNIQUE" in schema
    assert "CONSTRAINT collection_plans_instrument_uk UNIQUE" in schema
    assert "CONSTRAINT collection_coverage_segments_percent_ck CHECK" in schema
    assert "TIMESTAMPTZ" in schema
    assert "NUMERIC" in schema


def test_db_contract_is_idempotent_for_existing_development_database() -> None:
    schema = BASELINE_MIGRATION_PATH.read_text()

    assert "CREATE TABLE instruments (" not in schema
    assert "CREATE INDEX source_candles_instrument_time_idx" not in schema
    assert "CREATE INDEX IF NOT EXISTS source_candles_instrument_time_idx" in schema
    assert "CREATE INDEX IF NOT EXISTS collection_plans_status_idx" in schema


def test_db_contract_migrates_trade_event_data_type_constraints() -> None:
    schema = BASELINE_MIGRATION_PATH.read_text()

    assert "ALTER TABLE collection_runs" in schema
    assert "DROP CONSTRAINT IF EXISTS collection_runs_data_type_ck" in schema
    assert "ADD CONSTRAINT collection_runs_data_type_ck" in schema
    assert "ALTER TABLE target_collection_results" in schema
    assert "DROP CONSTRAINT IF EXISTS target_collection_results_data_type_ck" in schema
    assert "ADD CONSTRAINT target_collection_results_data_type_ck" in schema
    assert "'trade_event'" in schema


def test_db_schema_snapshot_is_generated_by_dbmate_and_records_version() -> None:
    schema = SCHEMA_PATH.read_text()

    assert "CREATE TABLE public.instruments" in schema
    assert "-- Dbmate schema migrations" in schema
    assert "INSERT INTO public.schema_migrations (version) VALUES" in schema
    assert "('20260715000100')" in schema
