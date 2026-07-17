from pathlib import Path

import yaml

MIGRATION = Path("docs/contracts/db/migrations/20260717001100_p2_versioned_indicators.sql")
OPENAPI = Path("docs/contracts/api/openapi.yaml")


def test_P2_3_migration은_지표_정의_물질화_값_통계를_불변_계보로_정의한다() -> None:
    sql = MIGRATION.read_text()
    for table in (
        "indicator_definitions",
        "indicator_definition_versions",
        "indicator_materializations",
        "indicator_values",
        "indicator_value_rollups",
        "market_statistics",
        "indicator_invalidations",
    ):
        assert f"CREATE TABLE {table}" in sql
    assert "source_revision_through_id" in sql
    assert "quality_event_through_id" in sql
    assert "knowledge_at" in sql
    assert "source_as_of" in sql
    assert "reject_indicator_immutable_mutation" in sql
    assert "enqueue_indicator_invalidation" in sql
    assert "close_return_1 NUMERIC" in sql
    assert "realized_volatility_20 NUMERIC" in sql
    assert "indicator_checkpoint_state JSONB" in sql
    assert "statistic_checkpoint_state JSONB" in sql
    assert "(progress_at IS NULL) =" in sql
    assert "(indicator_checkpoint_state IS NULL)" in sql


def test_P2_3_upgrade는_기존_이력을_상품_주기별_한_무효화로_시드한다() -> None:
    sql = MIGRATION.read_text()

    assert "WITH source_bounds AS" in sql
    assert "), source_frontier AS" in sql
    assert "GROUP BY instrument_id" in sql
    assert "WITH rollup_frontier AS" in sql
    assert "GROUP BY instrument_id, candle_unit" in sql
    assert "FROM source_frontier" in sql
    assert "FROM rollup_frontier" in sql


def test_P2_3_OpenAPI는_범위_asOf_정의버전_cursor를_가진_지표와_통계를_노출한다() -> None:
    document = yaml.safe_load(OPENAPI.read_text())
    for path in (
        "/v1/instruments/{instrumentId}/indicators",
        "/v1/instruments/{instrumentId}/market-statistics",
    ):
        operation = document["paths"][path]["get"]
        names = {item["name"] for item in operation["parameters"] if "name" in item}
        version_parameter = (
            "definitionSetHash" if path.endswith("/indicators") else "calculationVersion"
        )
        assert {"unit", "from", "to", "asOf", version_parameter, "pageSize", "cursor"} <= names
    point = document["components"]["schemas"]["IndicatorPoint"]
    assert {
        "startedAt",
        "values",
        "statuses",
        "definitionVersions",
        "materializationId",
        "sourceRevisionThroughId",
        "qualityEventThroughId",
        "knowledgeAt",
        "sourceAsOf",
    } <= set(point["required"])
