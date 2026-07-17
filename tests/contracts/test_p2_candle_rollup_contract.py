from pathlib import Path

import yaml

MIGRATIONS = Path("docs/contracts/db/migrations")
OPENAPI = Path("docs/contracts/api/openapi.yaml")


def test_p2_forward_migration_adds_append_only_revisions_and_rollup_lineage() -> None:
    migration = MIGRATIONS / "20260717000900_p2_candle_rollup_lineage.sql"

    assert migration.exists()
    sql = migration.read_text()
    assert "CREATE TABLE source_candle_revisions" in sql
    assert "revision_number" in sql
    assert "input_content_hash" in sql
    assert "calculation_version" in sql
    assert "source_as_of" in sql
    assert "knowledge_at" in sql
    assert "quality" in sql
    assert "INSERT INTO source_candle_revisions" in sql
    assert "append-only" in sql.lower()


def test_p2_openapi_exposes_paged_lineage_and_quality_metadata() -> None:
    document = yaml.safe_load(OPENAPI.read_text())
    operation = document["paths"]["/v1/instruments/{instrumentId}/candles"]["get"]
    parameter_names = {
        parameter["name"] for parameter in operation["parameters"] if "name" in parameter
    }
    candle = document["components"]["schemas"]["Candle"]
    series = document["components"]["schemas"]["CandleSeriesResponse"]

    assert {"pageSize", "cursor"} <= parameter_names
    assert {
        "calculationVersion",
        "sourceAsOf",
        "knowledgeAt",
        "inputContentHash",
        "quality",
        "completeness",
    } <= set(candle["required"])
    assert "nextCursor" in series["properties"]
