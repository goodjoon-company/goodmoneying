from pathlib import Path

MIGRATION = Path("docs/contracts/db/migrations/20260717001300_p2_dataset_versions.sql")
STORE = Path("packages/shared/goodmoneying_shared/dataset_version_store.py")


def test_P2_5_migration은_pending_build와_불변_version을_분리한다() -> None:
    sql = MIGRATION.read_text()

    for table in (
        "dataset_builds",
        "dataset_build_series",
        "dataset_build_market_status_snapshots",
        "dataset_build_coverage_snapshots",
        "dataset_versions",
        "dataset_version_series",
        "dataset_version_candles",
        "dataset_version_indicators",
        "dataset_version_market_statistics",
        "dataset_version_microstructures",
        "dataset_version_market_status_snapshots",
        "dataset_version_coverage_snapshots",
    ):
        assert f"CREATE TABLE {table}" in sql
    assert (
        "status IN "
        "('pending','running','retry_wait','succeeded','failed','dead_letter','cancelled')"
        in sql
    )
    assert "FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id)" in sql
    assert "reject_dataset_version_mutation" in sql
    for audit_column in (
        "request_id",
        "actor_id",
        "requested_at",
        "reason",
        "frozen_at",
        "lease_generation",
        "max_attempts",
        "next_retry_at",
        "dead_letter_reason",
    ):
        assert audit_column in sql


def test_P2_5_series는_asOf와_모든_projection_ceiling을_고정한다() -> None:
    sql = MIGRATION.read_text()

    for column in (
        "as_of",
        "source_revision_through_id",
        "candle_rollup_through_id",
        "quality_event_through_id",
        "indicator_materialization_through_id",
        "market_statistic_through_id",
        "microstructure_materialization_through_id",
        "market_status_history_through_id",
        "orderbook_snapshot_through_id",
        "trade_event_through_id",
        "source_receipt_through_id",
        "connection_quality_through_id",
    ):
        assert column in sql
    assert "REFERENCES source_candle_revisions(id)" in sql
    assert "REFERENCES candle_rollups(id)" in sql
    assert "REFERENCES indicator_materializations(id)" in sql
    assert "REFERENCES market_statistics(id)" in sql
    assert "REFERENCES microstructure_materializations(id)" in sql


def test_P2_5_candle_member는_source_revision과_rollup중_정확히_하나를_가진다() -> None:
    sql = MIGRATION.read_text()

    assert "source_candle_revision_id BIGINT REFERENCES source_candle_revisions(id)" in sql
    assert "candle_rollup_id BIGINT REFERENCES candle_rollups(id)" in sql
    assert "(source_candle_revision_id IS NOT NULL)::integer +" in sql
    assert "(candle_rollup_id IS NOT NULL)::integer = 1" in sql
    assert "knowledge_at TIMESTAMPTZ NOT NULL" in sql
    assert "source_as_of TIMESTAMPTZ NOT NULL" in sql


def test_P2_5_저장소는_repeatable_read와_내용충돌_동시성을_명시한다() -> None:
    source = STORE.read_text()

    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" in source
    assert "pg_advisory_xact_lock" in source
    assert "DatasetIdempotencyConflictError" in source
    assert "knowledge_at <=" in source
    assert "id <=" in source


def test_P2_5_v1_fill과_missing_policy는_DB에서도_제한한다() -> None:
    sql = MIGRATION.read_text()
    candle_member = sql.split("CREATE TABLE dataset_version_candles (", 1)[1].split(
        "CREATE TABLE dataset_version_indicators (", 1
    )[0]
    build_coverage = sql.split("CREATE TABLE dataset_build_coverage_snapshots (", 1)[1].split(
        "CREATE TABLE dataset_versions (", 1
    )[0]
    version_coverage = sql.split("CREATE TABLE dataset_version_coverage_snapshots (", 1)[
        1
    ].split("CREATE INDEX dataset_builds_claim_idx", 1)[0]

    assert "fill_policy IN ('none','no_trade_carry_forward_v1')" in sql
    assert "missing_policy IN ('fail','null','drop')" in sql
    assert "no_trade_carry_forward_v1" in sql
    assert "data_kind = 'candle'" in sql
    assert "knowledge_at TIMESTAMPTZ NOT NULL" in build_coverage
    assert "knowledge_at TIMESTAMPTZ NOT NULL" in version_coverage
    assert "fill_method" not in candle_member


def test_P2_5_게시_seal과_child_복합_identity를_DB가_강제한다() -> None:
    sql = MIGRATION.read_text()

    assert "sealed_at TIMESTAMPTZ" in sql
    assert "reject_sealed_dataset_version_child_insert" in sql
    assert "WHERE sealed_at IS NOT NULL" in STORE.read_text()
    assert "UNIQUE (dataset_version_id, id)" in sql
    assert "FOREIGN KEY (dataset_version_id, dataset_version_series_id)" in sql
    assert "validate_dataset_version_typed_member" in sql
    assert "NEW.content_hash IS DISTINCT FROM source_content_hash" in sql
    assert "NEW.knowledge_at IS DISTINCT FROM source_knowledge_at" in sql
    assert "NEW.source_as_of IS DISTINCT FROM source_as_of" in sql
    assert "definition_set_hash, calculation_version" in sql


def test_P2_5_typed_member는_고정된_원천_frontier와_asOf를_넘지_못한다() -> None:
    sql = MIGRATION.read_text()
    trigger = sql.split(
        "CREATE OR REPLACE FUNCTION validate_dataset_version_typed_member()", 1
    )[1].split("CREATE TRIGGER dataset_build_series_append_only_update", 1)[0]

    for ceiling in (
        "source_revision_through_id",
        "candle_rollup_through_id",
        "indicator_materialization_through_id",
        "market_statistic_through_id",
        "microstructure_materialization_through_id",
    ):
        assert ceiling in trigger
    assert "source_id" in trigger
    assert "source_id > source_ceiling" in trigger
    assert "source_ceiling IS NULL" in trigger
    assert "source_knowledge_at > parent_as_of" in trigger


def test_P2_5_재시도_수명주기와_claim_fencing을_계약화한다() -> None:
    sql = MIGRATION.read_text()
    source = STORE.read_text()

    for value in ("retry_wait", "dead_letter", "max_attempts", "next_retry_at"):
        assert value in sql
        assert value in source
    assert "unexpected_publication_error" in source
    assert "lease_generation" in source
