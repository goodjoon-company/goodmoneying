from __future__ import annotations

from pathlib import Path

import yaml

OPENAPI = Path("docs/contracts/api/openapi.yaml")
MIGRATION = Path("docs/contracts/db/migrations/20260718000100_p3_strategy_versions.sql")


def test_P3_1_OpenAPI는_strategy_graph_검증과_불변버전_API를_정의한다() -> None:
    document = yaml.safe_load(OPENAPI.read_text())
    paths = document["paths"]

    assert paths["/v1/strategy-graphs/validate"]["post"]["security"] == [
        {"OperatorToken": []}
    ]
    assert "post" in paths["/v1/strategies"]
    assert "post" in paths["/v1/strategies/{strategyId}/versions"]
    assert "get" in paths["/v1/strategies/{strategyId}/versions"]
    assert "get" in paths["/v1/strategy-versions/{strategyVersionId}"]
    assert (
        paths["/v1/strategies"]["post"]["responses"]["409"]["$ref"]
        == "#/components/responses/Conflict"
    )
    assert (
        paths["/v1/strategies/{strategyId}/versions"]["post"]["responses"]["409"]["$ref"]
        == "#/components/responses/Conflict"
    )
    assert (
        paths["/v1/strategies/{strategyId}/versions"]["get"]["responses"]["409"]["$ref"]
        == "#/components/responses/Conflict"
    )

    schemas = document["components"]["schemas"]
    assert schemas["StrategyGraph"]["required"] == [
        "schema_version",
        "nodes",
        "edges",
        "outputs",
    ]
    assert set(schemas["StrategyValidationError"]["properties"]["code"]["enum"]) == {
        "cycle_detected",
        "port_type_mismatch",
        "timeframe_incompatible",
        "look_ahead_detected",
        "parameter_out_of_range",
        "missing_data_policy_required",
        "insufficient_warmup",
        "missing_output",
    }
    assert schemas["StrategyGraphPort"]["required"] == ["name", "dataType"]
    assert schemas["StrategyVersion"]["properties"]["graphHash"]["pattern"] == "^[0-9a-f]{64}$"


def test_P3_1_DB는_strategy_definition_version_graph를_불변으로_저장한다() -> None:
    sql = MIGRATION.read_text()

    for table in (
        "strategy_definitions",
        "strategy_versions",
        "strategy_graphs",
        "strategy_parameters",
    ):
        assert f"CREATE TABLE {table}" in sql

    assert "UNIQUE (owner_id, name)" in sql
    assert "UNIQUE (strategy_id, version)" in sql
    assert "CHECK (graph_hash ~ '^[0-9a-f]{64}$')" in sql
    assert "UNIQUE (id, graph_hash)" in sql
    assert "FOREIGN KEY (strategy_version_id, graph_hash)" in sql
    assert "CHECK (status IN ('draft','validated','published','retired'))" in sql
    assert "reject_strategy_version_mutation" in sql
    assert "strategy_versions_append_only_update" in sql
    assert "strategy_graphs_append_only_delete" in sql
