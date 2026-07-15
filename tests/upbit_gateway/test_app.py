import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from goodmoneying_upbit_gateway.auth import Credentials
from goodmoneying_upbit_gateway.executor import UpbitExecutor
from goodmoneying_upbit_gateway.rate_limit import GroupRateLimiter


def _client(executor: UpbitExecutor | None = None) -> TestClient:
    module = importlib.import_module("goodmoneying_upbit_gateway.main")
    create_app = cast(Any, module.create_app)
    app = cast(FastAPI, create_app(executor=executor))
    return TestClient(app)


def _fake_executor(status_code: int = 200) -> UpbitExecutor:
    return UpbitExecutor(
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    status_code, json={"markets": ["KRW-BTC"]}
                )
            )
        ),
        credentials_provider=lambda: Credentials("fake-access", "s" * 64),
        limiter=GroupRateLimiter(),
        base_url="http://127.0.0.1:8123",
        allow_loopback_test=True,
    )


def test_health_reports_service_catalog_version_and_secret_free_credential_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "health-access-must-not-leak")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "health-secret-must-not-leak")
    response = _client().get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "upbit-gateway",
        "catalog_version": "1.6.3",
        "credentials_configured": True,
    }
    assert "health-access-must-not-leak" not in response.text
    assert "health-secret-must-not-leak" not in response.text


def test_health_reports_credentials_absent_without_browser_key_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "UPBIT_ACCESS_KEY",
        "UPBIT_SECRET_KEY",
        "UPBIT_ACCESS_KEY_FILE",
        "UPBIT_SECRET_KEY_FILE",
    ):
        monkeypatch.delenv(key, raising=False)

    response = _client().get("/health")

    assert response.status_code == 200
    assert response.json()["credentials_configured"] is False


def test_health_reports_invalid_credential_files_as_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("UPBIT_ACCESS_KEY_FILE", str(tmp_path / "missing-access"))
    monkeypatch.setenv("UPBIT_SECRET_KEY_FILE", str(tmp_path / "missing-secret"))

    response = _client().get("/health")

    assert response.status_code == 200
    assert response.json()["credentials_configured"] is False


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


def test_execution_route_returns_trace_envelope_from_upstream() -> None:
    response = _client(_fake_executor()).post(
        "/v1/requests",
        json={"endpoint_id": "rest.list-trading-pairs", "parameters": {}},
    )

    assert response.status_code == 200
    assert response.json()["endpoint_id"] == "rest.list-trading-pairs"
    assert response.json()["response"]["body"] == {"markets": ["KRW-BTC"]}


def test_execution_route_distinguishes_blocked_unknown_and_invalid() -> None:
    client = _client(_fake_executor())

    blocked = client.post(
        "/v1/requests",
        json={"endpoint_id": "rest.new-order", "parameters": {}},
    )
    implemented = client.post(
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
    assert implemented.status_code == 200
    assert (unknown.status_code, unknown.json()["detail"]["code"]) == (404, "UNKNOWN_ENDPOINT")
    assert invalid.status_code == 422


def test_execution_route_rejects_websocket_ids_as_rest_contract_errors() -> None:
    client = _client(_fake_executor())

    for endpoint_id in ("websocket.ticker", "websocket.list-subscriptions"):
        response = client.post(
            "/v1/requests",
            json={"endpoint_id": endpoint_id, "parameters": {}},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_REQUEST"


def test_execution_route_rejects_invalid_array_items_as_local_422() -> None:
    response = _client(_fake_executor()).post(
        "/v1/requests",
        json={
            "endpoint_id": "rest.get-pocket-api-keys",
            "parameters": {"uuids[]": [{"nested": "value"}]},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_PARAMETERS"


@pytest.mark.parametrize("status_code", [403, 404, 422, 500, 501, 502, 503, 504, 505])
def test_execution_route_distinguishes_upstream_status_envelope_from_local_error(
    status_code: int,
) -> None:
    response = _client(_fake_executor(status_code)).post(
        "/v1/requests",
        json={"endpoint_id": "rest.list-trading-pairs", "parameters": {}},
    )

    assert response.status_code == status_code
    assert response.json()["response"]["status_code"] == status_code
    assert "trace_id" in response.json()


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
