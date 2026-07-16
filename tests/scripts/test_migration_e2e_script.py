from pathlib import Path


def test_dbmate_migration_e2e_uses_empty_postgres_and_cleans_up() -> None:
    script_path = Path("tests/e2e/run_dbmate_migration_e2e.sh")

    assert script_path.is_file()
    script = script_path.read_text()
    assert "postgres:17" in script
    assert "apps/migrations/Dockerfile" in script
    assert "apps/api/Dockerfile" in script
    assert "trap cleanup EXIT" in script
    assert "DBMATE_STRICT=true" in script
    assert "export POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB" in script
    assert script.count("--no-dump-schema migrate") == 4
    assert "goodmoneying_upgrade" in script
    assert "same-fingerprint" in script
    assert "upgrade_event_count" in script
    assert "INSERT INTO instruments" in script
    assert "schema_migrations" in script
    assert "/health" in script
    assert "/v1/dashboard/summary" in script
    assert '"$ROOT_DIR/dev.sh" db dump' in script
    assert "diff -u" in script
    assert "SHOW timezone" in script
    assert "EXPECTED_MIGRATION_COUNT" in script
    assert script.count('"$version_count" == "$EXPECTED_MIGRATION_COUNT"') == 2
    assert '"$timezone" == "UTC"' in script
    assert "E2E 통과" in script
    assert 'docker logs "$DB_CONTAINER"' in script
    assert 'docker restart "$DB_CONTAINER"' in script
    assert "재시작 후 PostgreSQL" in script
