from __future__ import annotations

from pathlib import Path

import yaml

OPENAPI = Path("docs/contracts/api/openapi.yaml")


def test_P4_3_OpenAPI는_백테스트_run_조회_계약을_노출한다() -> None:
    document = yaml.safe_load(OPENAPI.read_text())
    paths = document["paths"]

    operation = paths["/v1/backtest-runs/{backtestRunId}"]["get"]

    assert operation["tags"] == ["백테스트(Backtest)"]
    assert operation["operationId"] == "getBacktestRun"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BacktestRun"
    }
    assert operation["responses"]["404"]["$ref"] == "#/components/responses/NotFound"

    schemas = document["components"]["schemas"]
    assert set(schemas["BacktestRun"]["required"]) == {
        "backtestRunId",
        "strategyVersionId",
        "datasetVersionId",
        "status",
        "inputHash",
        "resultHash",
        "metrics",
        "trades",
        "artifacts",
    }
    assert schemas["BacktestMetric"]["properties"]["metricValue"]["type"] == "string"
    assert schemas["BacktestTrade"]["properties"]["filledQuantity"]["type"] == "string"
    assert schemas["BacktestArtifact"]["properties"]["artifactType"]["type"] == "string"


def test_P4_4_OpenAPI는_백테스트_run_목록과_안정_cursor를_노출한다() -> None:
    document = yaml.safe_load(OPENAPI.read_text())
    paths = document["paths"]

    operation = paths["/v1/backtest-runs"]["get"]

    assert operation["tags"] == ["백테스트(Backtest)"]
    assert operation["operationId"] == "listBacktestRuns"
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert parameters["pageSize"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 25,
    }
    assert parameters["cursor"]["schema"] == {"type": ["string", "null"]}
    assert "불투명 커서" in parameters["cursor"]["description"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BacktestRuns"
    }
    assert operation["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }

    schemas = document["components"]["schemas"]
    assert schemas["BacktestRuns"]["required"] == ["items", "nextCursor"]
    assert schemas["BacktestRuns"]["properties"]["items"]["items"] == {
        "$ref": "#/components/schemas/BacktestRunSummary"
    }
    assert schemas["BacktestRuns"]["properties"]["nextCursor"]["type"] == ["string", "null"]
    assert set(schemas["BacktestRunSummary"]["required"]) == {
        "backtestRunId",
        "strategyVersionId",
        "datasetVersionId",
        "engineVersion",
        "status",
        "inputHash",
        "resultHash",
        "requestedAt",
        "startedAt",
        "finishedAt",
    }
