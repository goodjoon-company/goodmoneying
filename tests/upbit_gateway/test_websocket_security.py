from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from goodmoneying_upbit_gateway.main import create_app
from goodmoneying_upbit_gateway.websocket_security import WebSocketSecuritySettings


def _client(settings: WebSocketSecuritySettings) -> TestClient:
    return TestClient(cast(FastAPI, cast(Any, create_app)(websocket_security=settings)))


def _connect(client: TestClient, headers: dict[str, str]) -> Any:
    return client.websocket_connect("/v1/websocket", headers=headers)


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "http://testserver", "x-operator-token": "wrong"},
        {"origin": "https://evil.example", "x-operator-token": "operator"},
        {"x-operator-token": "operator"},
    ],
)
def test_downstream_websocket_rejects_wrong_token_cross_origin_and_missing_origin(
    headers: dict[str, str],
) -> None:
    client = _client(WebSocketSecuritySettings(operator_token="operator", allowed_origins=()))

    with pytest.raises(WebSocketDisconnect) as exc_info, _connect(client, headers):
        pass

    assert exc_info.value.code == 1008
    assert "토큰" not in exc_info.value.reason
    assert "origin" not in exc_info.value.reason.lower()


def test_downstream_websocket_accepts_authenticated_same_origin() -> None:
    client = _client(WebSocketSecuritySettings(
        operator_token="operator", allowed_origins=("http://testserver",)
    ))

    with _connect(
        client,
        {"origin": "http://testserver", "x-operator-token": "operator"},
    ) as websocket:
        websocket.send_json({"action": "unknown", "request_id": "same-origin"})
        response = websocket.receive_json()

    assert response["code"] == "INVALID_CONTROL"


def test_downstream_websocket_accepts_only_explicit_origin_and_rejects_forwarded_host() -> None:
    explicit = _client(
        WebSocketSecuritySettings(
            operator_token="operator",
            allowed_origins=("https://app.example",),
        )
    )
    forwarded = _client(WebSocketSecuritySettings(operator_token="operator", allowed_origins=()))

    with _connect(
        explicit,
        {"origin": "https://app.example", "x-operator-token": "operator"},
    ) as websocket:
        websocket.send_json({"action": "unknown", "request_id": "explicit"})
        explicit_response = websocket.receive_json()
    with pytest.raises(WebSocketDisconnect), _connect(
        forwarded,
        {
            "origin": "https://money.example",
            "host": "upbit-gateway:8001",
            "x-forwarded-host": "money.example",
            "x-forwarded-proto": "https",
            "x-operator-token": "operator",
        },
    ):
        pass

    assert explicit_response["code"] == "INVALID_CONTROL"


def test_security_settings_parse_trimmed_explicit_origins_and_operator_token() -> None:
    settings = WebSocketSecuritySettings.from_environment(
        {
            "UPBIT_GATEWAY_OPERATOR_TOKEN": "separate-token",
            "UPBIT_GATEWAY_ALLOWED_ORIGINS": " https://one.example,https://two.example ",
        }
    )

    assert settings == WebSocketSecuritySettings(
        operator_token="separate-token",
        allowed_origins=("https://one.example", "https://two.example"),
    )


def test_security_settings_fail_closed_without_operator_token_or_allowed_origin() -> None:
    settings = WebSocketSecuritySettings.from_environment({})

    assert settings.operator_token == ""
    assert settings.allowed_origins == ()
    assert settings.authorizes(
        {"origin": "http://testserver", "x-operator-token": "local-dev-token"},
        websocket_scheme="ws",
    ) is False
