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
