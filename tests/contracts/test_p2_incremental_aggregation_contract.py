from pathlib import Path

MIGRATION = Path(
    "docs/contracts/db/migrations/20260717001000_p2_incremental_rollup_recomputation.sql"
)


def test_p2_증분_집계_전진_마이그레이션은_추적과_내구성_계약을_정의한다() -> None:
    assert MIGRATION.exists()
    sql = MIGRATION.read_text()

    assert "CREATE TABLE candle_rollup_invalidations" in sql
    assert "CREATE TABLE candle_rollup_recompute_jobs" in sql
    assert "source_revision_through_id" in sql
    assert "quality_event_through_id" in sql
    assert "coverage_snapshot" in sql
    assert "range_start_at < range_end_at" in sql
    assert "output_bucket_count BETWEEN 1 AND 512" in sql
    assert "pending', 'running', 'retry_wait', 'succeeded', 'dead_letter', 'cancelled" in sql
    for field in (
        "idempotency_key",
        "attempt_count",
        "max_attempts",
        "next_retry_at",
        "lease_owner",
        "lease_expires_at",
        "last_error_code",
        "dead_letter_reason",
    ):
        assert field in sql
    assert "FOR EACH ROW EXECUTE FUNCTION reject_candle_rollup_invalidation_mutation" in sql
    assert "candle_rollup_recompute_jobs_claim_idx" in sql
    assert "ADD COLUMN id BIGINT GENERATED ALWAYS AS IDENTITY" in sql
    assert "candle_rollups_revision_uk" in sql
    assert "UNIQUE NULLS NOT DISTINCT" in sql
    assert "source_revision_through_id, quality_event_through_id" in sql
    assert "coverage_snapshot_hash" in sql
    assert "result_content_hash" in sql
    assert "reject_candle_rollup_mutation" in sql
    assert "candle_rollups is append-only" in sql
    assert "current_rollup_quality_ceiling" in sql
    assert "REVOKE UPDATE, DELETE ON TABLE candle_rollups" in sql


def test_p2_증분_집계_010_마이그레이션은_후속_마이그레이션과_함께_보존된다() -> None:
    migrations = sorted(Path("docs/contracts/db/migrations").glob("*.sql"))

    assert MIGRATION in migrations
    assert migrations.index(MIGRATION) == 10
    assert len(migrations) >= 11
