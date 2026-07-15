import json
from collections import OrderedDict

import jwt
import pytest

from goodmoneying_upbit_gateway.auth import Credentials, query_hash
from goodmoneying_upbit_gateway.catalog import endpoint_by_id, load_catalog
from goodmoneying_upbit_gateway.client import (
    InvalidBaseUrl,
    InvalidParameters,
    build_upstream_request,
    validate_base_url,
)


def _endpoint(endpoint_id: str) -> dict[str, object]:
    endpoint = endpoint_by_id(load_catalog(), endpoint_id)
    assert endpoint is not None
    return endpoint


def test_base_url_is_fixed_except_explicit_loopback_test_mode() -> None:
    assert validate_base_url("https://api.upbit.com", allow_loopback_test=False) == (
        "https://api.upbit.com"
    )
    assert validate_base_url("http://127.0.0.1:8123", allow_loopback_test=True) == (
        "http://127.0.0.1:8123"
    )
    with pytest.raises(InvalidBaseUrl):
        validate_base_url("https://evil.example", allow_loopback_test=True)
    with pytest.raises(InvalidBaseUrl):
        validate_base_url("http://127.0.0.1:8123", allow_loopback_test=False)
    for unsafe in (
        "http://localhost:8123",
        "http://127.0.0.1:8123/prefix",
        "http://127.0.0.1:8123?target=evil",
        "http://user:password@127.0.0.1:8123",
    ):
        with pytest.raises(InvalidBaseUrl):
            validate_base_url(unsafe, allow_loopback_test=True)


def test_public_request_preserves_catalog_order_and_never_forwards_origin() -> None:
    request = build_upstream_request(
        _endpoint("rest.list-candles-minutes"),
        OrderedDict([("market", "KRW-BTC"), ("unit", 1), ("count", 2)]),
        base_url="https://api.upbit.com",
        credentials=None,
        incoming_headers={"Origin": "https://browser.example"},
    )

    assert str(request.url) == (
        "https://api.upbit.com/v1/candles/minutes/1?market=KRW-BTC&count=2"
    )
    assert "authorization" not in request.headers
    assert "origin" not in request.headers


def test_exchange_test_request_hashes_body_in_original_input_order() -> None:
    credentials = Credentials("fake-access", "s" * 64)
    parameters = OrderedDict(
        [("side", "bid"), ("market", "KRW-BTC"), ("price", "1000"), ("ord_type", "price")]
    )
    request = build_upstream_request(
        _endpoint("rest.order-test"),
        parameters,
        base_url="https://api.upbit.com",
        credentials=credentials,
        incoming_headers={},
        nonce_factory=lambda: "fixed-nonce",
    )

    token = request.headers["Authorization"].removeprefix("Bearer ")
    payload = jwt.decode(token, credentials.secret_key, algorithms=["HS512"])
    assert payload["query_hash"] == query_hash(
        "side=bid&market=KRW-BTC&price=1000&ord_type=price"
    )
    assert list(json.loads(request.read()).keys()) == list(parameters)


def test_catalog_parameter_validation_rejects_missing_and_unknown_values() -> None:
    with pytest.raises(InvalidParameters):
        build_upstream_request(
            _endpoint("rest.list-candles-minutes"),
            {"market": "KRW-BTC"},
            base_url="https://api.upbit.com",
            credentials=None,
            incoming_headers={},
        )


def test_catalog_parameter_validation_enforces_required_alternative_groups() -> None:
    with pytest.raises(InvalidParameters, match="조합"):
        build_upstream_request(
            _endpoint("rest.get-order"),
            {},
            base_url="https://api.upbit.com",
            credentials=Credentials("fake-access", "s" * 64),
            incoming_headers={},
        )

    request = build_upstream_request(
        _endpoint("rest.get-order"),
        {"identifier": "fake-identifier"},
        base_url="https://api.upbit.com",
        credentials=Credentials("fake-access", "s" * 64),
        incoming_headers={},
    )
    assert "identifier=fake-identifier" in str(request.url)
    with pytest.raises(InvalidParameters):
        build_upstream_request(
            _endpoint("rest.list-trading-pairs"),
            {"unexpected": "value"},
            base_url="https://api.upbit.com",
            credentials=None,
            incoming_headers={},
        )
