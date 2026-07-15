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
    validate_parameters,
)

CATALOG = load_catalog()
REST_ARRAY_PARAMETERS = [
    (endpoint, parameter)
    for endpoint in CATALOG["rest_endpoints"]
    for parameter in endpoint["parameters"]
    if parameter["type"] == "array"
]


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


def test_exchange_query_derives_safe_wire_and_unencoded_hash_from_same_tokens() -> None:
    credentials = Credentials("fake-access", "s" * 64)
    parameters = OrderedDict(
        [
            (
                "uuids[]",
                [
                    "2026-07-16T03:00:00+09:00",
                    "id&uuid=extra",
                    "#fragment",
                ],
            ),
            ("include_expired", True),
        ]
    )
    request = build_upstream_request(
        _endpoint("rest.get-pocket-api-keys"),
        parameters,
        base_url="https://api.upbit.com",
        credentials=credentials,
        incoming_headers={},
        nonce_factory=lambda: "fixed-nonce",
    )

    hash_query = (
        "uuids[]=2026-07-16T03:00:00+09:00&uuids[]=id&uuid=extra"
        "&uuids[]=#fragment&include_expired=true"
    )
    wire_query = (
        "uuids[]=2026-07-16T03%3A00%3A00%2B09%3A00"
        "&uuids[]=id%26uuid%3Dextra&uuids[]=%23fragment&include_expired=true"
    )
    token = request.headers["Authorization"].removeprefix("Bearer ")
    payload = jwt.decode(token, credentials.secret_key, algorithms=["HS512"])

    assert request.url.query.decode() == wire_query
    assert payload["query_hash"] == query_hash(hash_query)


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


@pytest.mark.parametrize(
    ("endpoint", "parameter"),
    REST_ARRAY_PARAMETERS,
    ids=[
        f"{endpoint['endpoint_id']}:{parameter['name']}"
        for endpoint, parameter in REST_ARRAY_PARAMETERS
    ],
)
def test_every_rest_array_parameter_enforces_catalog_item_schema(
    endpoint: dict[str, object], parameter: dict[str, object]
) -> None:
    name = str(parameter["name"])

    validate_parameters(endpoint, {name: ["valid-1", "valid-2"]})
    for invalid in (
        {"nested": "value"},
        [["nested"]],
        [{"nested": "value"}],
        [1],
        [True],
    ):
        with pytest.raises(InvalidParameters, match=name.replace("[", r"\[")):
            validate_parameters(endpoint, {name: invalid})


def test_scalar_enum_range_and_numeric_finiteness_follow_catalog_schema() -> None:
    minute_candles = _endpoint("rest.list-candles-minutes")
    validate_parameters(minute_candles, {"unit": 1, "market": "KRW-BTC", "count": 200})
    for invalid in (
        {"unit": 2, "market": "KRW-BTC"},
        {"unit": 1, "market": "KRW-BTC", "count": 0},
        {"unit": 1, "market": "KRW-BTC", "count": 201},
    ):
        with pytest.raises(InvalidParameters):
            validate_parameters(minute_candles, invalid)

    numeric_parameter = next(
        parameter
        for stream in CATALOG["websocket_streams"]
        for parameter in stream["parameters"]
        if parameter["type"] == "number"
    )
    synthetic_endpoint = {
        "parameters": [{**numeric_parameter, "location": "query"}],
    }
    validate_parameters(synthetic_endpoint, {numeric_parameter["name"]: 0.5})
    for invalid_number in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(InvalidParameters):
            validate_parameters(
                synthetic_endpoint,
                {numeric_parameter["name"]: invalid_number},
            )
