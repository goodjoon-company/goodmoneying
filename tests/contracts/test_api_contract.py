from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.routing import APIRoute

from goodmoneying_api.main import create_app

CONTRACT_PATH = Path("docs/contracts/api/openapi.yaml")


def test_openapi_contract_contains_m1_paths() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())

    assert contract["openapi"] == "3.1.0"
    assert set(contract["paths"]) >= {
        "/health",
        "/v1/dashboard/summary",
        "/v1/candidate-universe",
        "/v1/collection-targets",
        "/v1/market-list",
        "/v1/instruments/{instrumentId}",
        "/v1/instruments/{instrumentId}/candles",
        "/v1/instruments/{instrumentId}/ticker-snapshots",
        "/v1/instruments/{instrumentId}/orderbook-summaries",
        "/v1/collection-runs",
        "/v1/backfill/plans",
        "/v1/backfill/jobs",
        "/v1/backfill/jobs/{jobId}/{action}",
        "/v1/notifications",
    }


def test_openapi_contract_exposes_m2_collection_dashboard_view_model() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]

    dashboard = schemas["DashboardSummary"]
    assert "targets" in dashboard["required"]
    target_row_ref = dashboard["properties"]["targets"]["items"]["$ref"]
    assert target_row_ref == "#/components/schemas/CollectionDashboardTarget"

    target_row = schemas["CollectionDashboardTarget"]
    for field in [
        "instrument",
        "overallStatus",
        "overallStatusLabel",
        "plan",
        "dataStatuses",
        "coverageSegments",
    ]:
        assert field in target_row["required"]

    market_row = schemas["MarketListRow"]
    assert "accTradePrice24hDisplay" in market_row["required"]

    data_status = schemas["CollectionDataStatus"]
    assert "storedRowCount" in data_status["required"]

    candidate = schemas["CandidateUniverseEntry"]
    assert "qualityDetail" in candidate["required"]

    request_schema = schemas["UpdateCollectionTargetsRequest"]
    instrument_ids = request_schema["properties"]["instrumentIds"]
    assert instrument_ids["maxItems"] == 50
    assert instrument_ids.get("minItems", 0) <= 1


def test_fastapi_implements_contract_paths() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    app = create_app()
    implemented = {route.path for route in app.routes if isinstance(route, APIRoute)}

    assert set(contract["paths"]) <= implemented
