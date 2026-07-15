from __future__ import annotations

import json
from pathlib import Path

MIGRATIONS_DIR = Path("docs/contracts/db/migrations")
INITIAL_MIGRATION = MIGRATIONS_DIR / "20260715000100_initial_schema.sql"


def test_dbmate_is_pinned_and_initial_migration_is_the_db_change_source() -> None:
    package = json.loads(Path("package.json").read_text())
    contract = Path("docs/contracts/db/README.md").read_text()
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    versions = [migration.name.split("_", maxsplit=1)[0] for migration in migrations]

    assert package["devDependencies"]["dbmate"] == "2.34.1"
    assert INITIAL_MIGRATION.is_file()
    assert INITIAL_MIGRATION in migrations
    assert versions == sorted(set(versions))
    assert "migrations/" in contract
    assert "단일 기준(source of truth)" in contract
    assert "schema.sql" in contract
    assert "직접 수정하지 않는다" in contract


def test_initial_migration_contains_current_schema_and_non_destructive_baseline() -> None:
    migration = INITIAL_MIGRATION.read_text()

    assert migration.startswith("-- migrate:up\n")
    assert "CREATE TABLE IF NOT EXISTS instruments" in migration
    assert "CREATE TABLE IF NOT EXISTS candle_aggregation_jobs" in migration
    assert "ALTER DATABASE %I SET timezone TO %L" in migration
    assert "current_database()" in migration
    assert migration.rstrip().endswith(
        "-- 기준선은 기존 데이터를 삭제할 수 있으므로 migrate:down을 실행하지 않는다."
    )
    assert "-- migrate:down" in migration
    down = migration.split("-- migrate:down", maxsplit=1)[1]
    assert "DROP TABLE" not in down


def test_architecture_declares_migrations_as_source_and_runtime_ddl_is_forbidden() -> None:
    architecture = Path("docs/02_Architecture.md").read_text()
    pipeline = Path("docs/02_Architecture/upbit-collection-pipeline.md").read_text()

    for document in (architecture, pipeline):
        assert "docs/contracts/db/migrations/" in document
        assert "런타임" in document
        assert "DDL" in document
    assert "반복 적용 가능한 DDL" not in architecture
    assert "schema.sql을 적용" not in pipeline


def test_readme_documents_dev_script_migration_workflow() -> None:
    readme = Path("README.md").read_text()

    for command in (
        "./dev.sh db new",
        "./dev.sh db migrate",
        "./dev.sh db status",
        "./dev.sh db dump",
        "./dev.sh db rollback",
    ):
        assert command in readme
    assert "docs/contracts/db/migrations/" in readme
    assert "schema.sql" in readme
    assert "직접 수정" in readme
    assert "app start" in readme
    assert "기준선" in readme
