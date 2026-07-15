import importlib
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client() -> TestClient:
    module = importlib.import_module("goodmoneying_upbit_gateway.main")
    create_app = cast(Any, module.create_app)
    app = cast(FastAPI, create_app())
    return TestClient(app)


def test_health_reports_service_and_catalog_version() -> None:
    response = _client().get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "upbit-gateway",
        "catalog_version": "1.6.3",
    }


def test_catalog_returns_contract_without_contacting_upbit() -> None:
    response = _client().get("/v1/catalog")

    assert response.status_code == 200
    payload = response.json()
    assert payload["catalog_version"] == "1.6.3"
    assert len(payload["rest_endpoints"]) == 51
    assert len(payload["websocket_streams"]) == 15
    assert all("source_url" in endpoint for endpoint in payload["rest_endpoints"])


def test_execution_route_is_not_implemented_in_contract_skeleton() -> None:
    response = _client().post(
        "/v1/requests",
        json={"endpoint_id": "rest.list-trading-pairs", "parameters": {}},
    )

    assert response.status_code == 501
    assert response.json() == {
        "detail": {
            "code": "UPSTREAM_NOT_IMPLEMENTED",
            "message": "Issue #19 범위에서는 업비트 상향 호출을 수행하지 않습니다.",
        }
    }
