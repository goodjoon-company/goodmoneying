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
        "/v1/dashboard/overview",
        "/v1/dashboard/targets",
        "/v1/dashboard/coverage",
        "/v1/dashboard/collection-activity",
        "/v1/dashboard/realtime-heatmap",
        "/v1/dashboard/storage-breakdown",
        "/v1/dashboard/operations-trend",
        "/v1/dashboard/missing-ranges",
        "/v1/dashboard/audit-log-summary",
        "/v1/candidate-universe",
        "/v1/collection-targets",
        "/v1/collection-targets/{instrumentId}/coverage-segments",
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


def test_openapi_contract_groups_operations_with_described_tags() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())

    tag_descriptions = {tag["name"]: tag.get("description", "") for tag in contract["tags"]}
    expected_tags = {
        "상태(Health)",
        "대시보드(Dashboard)",
        "수집(Collection)",
        "시장(Market)",
        "상품(Instrument)",
        "백필(Backfill)",
        "알림(Notification)",
    }
    assert set(tag_descriptions) == expected_tags
    assert all(tag_descriptions[tag] for tag in expected_tags)

    expected_operation_tags = {
        ("get", "/health"): ["상태(Health)"],
        ("get", "/v1/dashboard/summary"): ["대시보드(Dashboard)"],
        ("get", "/v1/dashboard/overview"): ["대시보드(Dashboard)"],
        ("get", "/v1/dashboard/targets"): ["대시보드(Dashboard)"],
        ("get", "/v1/dashboard/coverage"): ["대시보드(Dashboard)"],
        ("get", "/v1/dashboard/collection-activity"): ["대시보드(Dashboard)"],
        ("get", "/v1/dashboard/realtime-heatmap"): ["대시보드(Dashboard)"],
        ("get", "/v1/dashboard/storage-breakdown"): ["대시보드(Dashboard)"],
        ("get", "/v1/dashboard/operations-trend"): ["대시보드(Dashboard)"],
        ("get", "/v1/dashboard/missing-ranges"): ["대시보드(Dashboard)"],
        ("get", "/v1/dashboard/audit-log-summary"): ["대시보드(Dashboard)"],
        ("get", "/v1/candidate-universe"): ["수집(Collection)"],
        ("put", "/v1/collection-targets"): ["수집(Collection)"],
        ("get", "/v1/collection-targets/{instrumentId}/coverage-segments"): ["수집(Collection)"],
        ("get", "/v1/market-list"): ["시장(Market)"],
        ("get", "/v1/instruments/{instrumentId}"): ["상품(Instrument)"],
        ("get", "/v1/instruments/{instrumentId}/candles"): ["상품(Instrument)"],
        ("get", "/v1/instruments/{instrumentId}/ticker-snapshots"): ["상품(Instrument)"],
        ("get", "/v1/instruments/{instrumentId}/orderbook-summaries"): ["상품(Instrument)"],
        ("get", "/v1/collection-runs"): ["수집(Collection)"],
        ("post", "/v1/backfill/plans"): ["백필(Backfill)"],
        ("get", "/v1/backfill/jobs"): ["백필(Backfill)"],
        ("post", "/v1/backfill/jobs"): ["백필(Backfill)"],
        ("post", "/v1/backfill/jobs/{jobId}/{action}"): ["백필(Backfill)"],
        ("get", "/v1/notifications"): ["알림(Notification)"],
    }
    for (method, path), tags in expected_operation_tags.items():
        assert contract["paths"][path][method]["tags"] == tags


def test_openapi_component_schemas_have_descriptions() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())

    missing_descriptions = [
        name
        for name, schema in contract["components"]["schemas"].items()
        if not schema.get("description")
    ]

    assert missing_descriptions == []


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
        "changeRate",
        "accTradePrice24hDisplay",
        "tickerFreshnessLabel",
        "coveragePercent",
        "storageBytesDisplay",
    ]:
        assert field in target_row["required"]

    market_row = schemas["MarketListRow"]
    assert "accTradePrice24hDisplay" in market_row["required"]

    data_status = schemas["CollectionDataStatus"]
    assert "storedRowCount" in data_status["required"]

    lazy_segments = schemas["CollectionCoverageSegmentsResponse"]
    assert "items" in lazy_segments["required"]

    for schema_name in [
        "CollectionActivityBucket",
        "RealtimeCollectionHeatmapCell",
        "RealtimeCollectionHeatmapRow",
        "StorageBreakdownItem",
        "OperationsTrendPoint",
        "MissingRangeSummary",
        "AuditLogSummary",
    ]:
        assert schema_name in schemas
    for field in [
        "collectionActivity",
        "realtimeCollectionHeatmap",
        "storageBreakdown",
        "operationsTrend",
        "missingRangeTop",
        "auditLogSummary",
    ]:
        assert field in dashboard["required"]

    candidate = schemas["CandidateUniverseEntry"]
    assert "qualityDetail" in candidate["required"]

    request_schema = schemas["UpdateCollectionTargetsRequest"]
    instrument_ids = request_schema["properties"]["instrumentIds"]
    assert instrument_ids["maxItems"] == 50
    assert instrument_ids.get("minItems", 0) <= 1


def test_openapi_contract_exposes_real_metric_principles_and_excludes_synthetic_metrics() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]

    dashboard = schemas["DashboardSummary"]
    assert "metricPrinciples" in dashboard["required"]
    assert (
        dashboard["properties"]["metricPrinciples"]["items"]["$ref"]
        == "#/components/schemas/MetricPrinciple"
    )

    metric_principle = schemas["MetricPrinciple"]
    assert set(metric_principle["required"]) == {
        "metricKey",
        "label",
        "displayStatus",
        "evidenceStatus",
        "reason",
    }
    assert metric_principle["properties"]["displayStatus"]["enum"] == ["displayed", "excluded"]

    totals = dashboard["properties"]["totals"]
    if "$ref" in totals:
        totals = schemas[totals["$ref"].rsplit("/", maxsplit=1)[-1]]
    prohibited_fields = {"rateLimitRemainingPercent", "duplicateRows24h"}
    assert prohibited_fields.isdisjoint(totals["required"])
    assert prohibited_fields.isdisjoint(totals["properties"])
    for field in [
        "storageRowsToday",
        "realtimeRowsLastMinute",
        "backfillRowsLastMinute",
    ]:
        assert field in totals["required"]
        assert field in totals["properties"]


def test_openapi_contract_exposes_dashboard_panel_endpoints() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]

    expected_response_schemas = {
        "/v1/dashboard/overview": "DashboardOverviewResponse",
        "/v1/dashboard/targets": "DashboardTargetsResponse",
        "/v1/dashboard/coverage": "DashboardCoverageResponse",
        "/v1/dashboard/collection-activity": "DashboardCollectionActivityResponse",
        "/v1/dashboard/realtime-heatmap": "DashboardRealtimeHeatmapResponse",
        "/v1/dashboard/storage-breakdown": "DashboardStorageBreakdownResponse",
        "/v1/dashboard/operations-trend": "DashboardOperationsTrendResponse",
        "/v1/dashboard/missing-ranges": "DashboardMissingRangesResponse",
        "/v1/dashboard/audit-log-summary": "DashboardAuditLogSummaryResponse",
    }
    for path, schema_name in expected_response_schemas.items():
        operation = contract["paths"][path]["get"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{schema_name}"
        }
        assert schema_name in schemas
        assert "recommendedRefreshSeconds" in schemas[schema_name]["required"]
        assert "refreshedAt" in schemas[schema_name]["required"]

    for path in [
        "/v1/dashboard/targets",
        "/v1/dashboard/coverage",
        "/v1/dashboard/realtime-heatmap",
        "/v1/dashboard/missing-ranges",
    ]:
        parameters = {
            _resolve_parameter(contract, parameter)["name"]: _resolve_parameter(contract, parameter)
            for parameter in contract["paths"][path]["get"]["parameters"]
        }
        assert parameters["limit"]["schema"]["default"] == 50
        assert parameters["limit"]["schema"]["minimum"] == 1
        assert parameters["limit"]["schema"]["maximum"] == 100
        assert parameters["offset"]["schema"]["default"] == 0
        assert parameters["offset"]["schema"]["minimum"] == 0

    assert schemas["DashboardOverviewResponse"]["properties"]["totals"]["$ref"] == (
        "#/components/schemas/DashboardTotals"
    )
    assert schemas["DashboardTargetsResponse"]["properties"]["items"]["items"]["$ref"] == (
        "#/components/schemas/CollectionDashboardTarget"
    )
    assert schemas["DashboardCoverageResponse"]["properties"]["items"]["items"]["$ref"] == (
        "#/components/schemas/CoverageStatus"
    )


def _resolve_parameter(contract: dict, parameter: dict) -> dict:
    if "$ref" not in parameter:
        return parameter
    name = parameter["$ref"].rsplit("/", maxsplit=1)[-1]
    return contract["components"]["parameters"][name]


def test_fastapi_implements_contract_paths() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    app = create_app()
    implemented = {route.path for route in app.routes if isinstance(route, APIRoute)}

    assert set(contract["paths"]) <= implemented
