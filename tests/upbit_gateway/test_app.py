import importlib
import subprocess
import sys
from pathlib import Path
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
    assert payload["rest_inventory"] == {
        "active_count": 50,
        "deprecated_count": 1,
        "total_count": 51,
    }
    assert len(payload["websocket_streams"]) == 14
    assert payload["websocket_operations"][0]["method"] == "LIST_SUBSCRIPTIONS"
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


def test_execution_route_distinguishes_blocked_unimplemented_unknown_and_invalid() -> None:
    client = _client()

    blocked = client.post(
        "/v1/requests",
        json={"endpoint_id": "rest.new-order", "parameters": {}},
    )
    unimplemented = client.post(
        "/v1/requests",
        json={"endpoint_id": "rest.list-trading-pairs", "parameters": {}},
    )
    unknown = client.post(
        "/v1/requests",
        json={"endpoint_id": "rest.does-not-exist", "parameters": {}},
    )
    invalid = client.post(
        "/v1/requests",
        json={"endpoint_id": "https://api.upbit.com/v1/market/all", "parameters": {}},
    )

    assert (blocked.status_code, blocked.json()["detail"]["code"]) == (403, "POLICY_BLOCKED")
    assert (unimplemented.status_code, unimplemented.json()["detail"]["code"]) == (
        501,
        "UPSTREAM_NOT_IMPLEMENTED",
    )
    assert (unknown.status_code, unknown.json()["detail"]["code"]) == (404, "UNKNOWN_ENDPOINT")
    assert invalid.status_code == 422


def test_package_import_and_catalog_loading_are_independent_of_current_directory(
    tmp_path: Path,
) -> None:
    script = """
import os
import sys
os.chdir(sys.argv[1])
from goodmoneying_upbit_gateway.catalog import load_catalog
catalog = load_catalog()
assert catalog["catalog_version"] == "1.6.3"
"""

    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
