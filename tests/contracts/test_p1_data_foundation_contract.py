from __future__ import annotations

from pathlib import Path

import yaml

MIGRATION = Path(
    "docs/contracts/db/migrations/20260717000100_system_trading_data_foundation.sql"
)
COVERAGE_MIGRATION = Path(
    "docs/contracts/db/migrations/20260717000400_coverage_five_states_quality_events.sql"
)
SOURCE_ORDERBOOK_MIGRATION = Path(
    "docs/contracts/db/migrations/20260717000500_source_orderbook_evidence.sql"
)
FETCH_MANIFEST_MIGRATION = Path(
    "docs/contracts/db/migrations/20260717000300_fetch_manifest_raw_response.sql"
)
FINAL_FIXES_MIGRATION = Path(
    "docs/contracts/db/migrations/20260717000600_p1_review_safety_contracts.sql"
)
RECOVERY_MIGRATION = Path(
    "docs/contracts/db/migrations/20260717000700_p1_recovery_readiness.sql"
)


def test_p1_migration_is_expand_only_and_preserves_legacy_rows() -> None:
    sql = MIGRATION.read_text()

    assert sql.startswith("-- migrate:up\n")
    for table in (
        "markets",
        "market_status_history",
        "collection_policies",
        "collection_target_specs",
        "coverage_intervals",
        "fetch_manifests",
        "data_quality_events",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "legacy_instrument_id" in sql
    assert "INSERT INTO markets" in sql
    assert "FROM instruments" in sql
    assert "SET TIME ZONE 'UTC'" in sql
    coverage_sql = COVERAGE_MIGRATION.read_text()
    assert "available" in coverage_sql
    assert "no_trade" in coverage_sql
    assert "missing" in coverage_sql
    assert "unavailable" in coverage_sql
    assert "unverified" in coverage_sql
    assert "fetch_manifest_id" in coverage_sql
    assert "idempotency_key" in sql
    assert "lease_expires_at" in sql
    assert "FOR UPDATE SKIP LOCKED" not in sql
    down = sql.split("-- migrate:down", maxsplit=1)[1]
    assert "DROP TABLE" not in down
    assert "DELETE FROM" not in down


def test_quality_event_loss_is_corrected_only_by_forward_migration() -> None:
    coverage_sql = COVERAGE_MIGRATION.read_text()
    fetch_sql = FETCH_MANIFEST_MIGRATION.read_text()
    recovery_sql = RECOVERY_MIGRATION.read_text()

    assert "DELETE FROM data_quality_events" in coverage_sql
    assert "UNIQUE (target_spec_id, fingerprint)" in coverage_sql
    assert "DELETE FROM data_quality_events" not in recovery_sql
    assert "UNIQUE (target_spec_id, event_type, detected_at, fingerprint)" in recovery_sql
    assert "p1_audit_recovery_gate" in recovery_sql
    assert "DROP COLUMN" not in fetch_sql.split("-- migrate:down", maxsplit=1)[1]


def test_p1_final_safety_contracts_are_persistent_and_fail_closed() -> None:
    sql = FINAL_FIXES_MIGRATION.read_text()

    assert "market_status_history" in sql and "fetch_manifest_id" in sql
    assert "command_idempotency_records" in sql
    assert "payload_hash" in sql and "result_payload" in sql
    assert "backfill_safety_gate" in sql
    assert "backup_verified_at" in sql
    assert "free_capacity_bytes" in sql
    assert "required_capacity_bytes" in sql
    assert "approved_sha" in sql
    assert "enabled BOOLEAN NOT NULL DEFAULT false" in sql
    assert "required_capacity_bytes > 0" in sql
    assert "status IN ('running', 'gated', 'failed')" in sql


def test_p1_contract_prevents_duplicate_status_targets_jobs_and_coverage() -> None:
    sql = MIGRATION.read_text()

    assert "markets_exchange_market_code_uk" in sql
    assert "market_status_history_market_from_uk" in sql
    assert "collection_target_specs_natural_uk" in sql
    assert "backfill_jobs_idempotency_key_uk" in sql
    assert "coverage_intervals_natural_uk" in sql
    assert "EXCLUDE USING gist" in sql


def test_schema_snapshot_records_p1_migration() -> None:
    schema = Path("docs/contracts/db/schema.sql").read_text()

    assert "CREATE TABLE public.markets" in schema
    assert "CREATE TABLE public.coverage_intervals" in schema
    assert "('20260717000100')" in schema


def test_p1_source_orderbook_contract_separates_receipts_snapshots_and_levels() -> None:
    sql = SOURCE_ORDERBOOK_MIGRATION.read_text()

    assert sql.startswith("-- migrate:up\n")
    for table in (
        "source_receipts",
        "orderbook_snapshots",
        "orderbook_snapshot_levels",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "source_receipts_connection_frame_uk" in sql
    assert "UNIQUE (connection_id, frame_sequence)" in sql
    assert "orderbook_snapshots_economic_state_uk" in sql
    assert "UNIQUE (instrument_id, source, occurred_at, payload_checksum)" in sql
    assert "PRIMARY KEY (snapshot_id, level_index)" in sql
    assert "raw_payload JSONB NOT NULL" in sql
    assert "ON DELETE CASCADE" in sql
    assert "source_receipts_timestamp_ck" not in sql
    assert "orderbook_snapshots_timestamp_ck" not in sql
    down = sql.split("-- migrate:down", maxsplit=1)[1]
    assert "DROP TABLE" not in down
    assert "DELETE FROM" not in down


def test_schema_snapshot_records_source_orderbook_evidence() -> None:
    schema = Path("docs/contracts/db/schema.sql").read_text()

    assert "CREATE TABLE public.source_receipts" in schema
    assert "CREATE TABLE public.orderbook_snapshots" in schema
    assert "CREATE TABLE public.orderbook_snapshot_levels" in schema
    assert "('20260717000500')" in schema


def test_system_trading_replay_uses_actual_source_receipt_identity() -> None:
    architecture = Path("docs/02_Architecture/system-trading-domain.md").read_text()

    assert "receipt_sequence" not in architecture
    assert "(received_at, source_receipts.id)" in architecture
    assert "호가 `stable_sequence`는 `source_receipts.id`" in architecture
    assert "connection_id" in architecture
    assert "frame_sequence" in architecture


def test_p1_openapi_exposes_data_foundation_and_policy_control() -> None:
    contract = yaml.safe_load(Path("docs/contracts/api/openapi.yaml").read_text())

    assert "/v1/data-foundation" in contract["paths"]
    assert "/v1/data-foundation/markets/{marketCode}" in contract["paths"]
    coverage = contract["components"]["schemas"]["CoverageCounts"]
    assert set(coverage["required"]) == {
        "available",
        "no_trade",
        "missing",
        "unavailable",
        "unverified",
    }
    policy = contract["components"]["schemas"]["MarketCollectionPolicy"]
    assert policy["properties"]["candleUnit"]["const"] == "1m"
    assert policy["properties"]["dataTypes"]["minItems"] == 1
    command = contract["components"]["schemas"]["UpdateMarketTargetStateRequest"]
    assert command["properties"]["reason"]["pattern"] == r".*\S.*"


def test_local_runtime_manages_market_sync_worker() -> None:
    script = Path("dev.sh").read_text()

    assert "market-sync-worker" in script
    assert "goodmoneying_worker.data_foundation_worker" in script
