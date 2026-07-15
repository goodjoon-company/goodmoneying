import json
from pathlib import Path
from typing import Any, cast

import yaml
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

CATALOG_PATH = Path("docs/contracts/upbit/upbit-api-catalog.yaml")
OPENAPI_PATH = Path("docs/contracts/api/upbit-gateway.openapi.yaml")
WEBSOCKET_SCHEMA_PATH = Path("docs/contracts/api/upbit-gateway-websocket.schema.json")

EXPECTED_REST_IDS = {
    f"rest.{slug}"
    for slug in [
        "list-trading-pairs", "list-candles-seconds", "list-candles-minutes",
        "list-candles-days", "list-candles-weeks", "list-candles-months",
        "list-candles-years", "list-pair-trades", "list-tickers", "list-quote-tickers",
        "list-orderbooks", "list-orderbook-instruments", "get-pocket-information",
        "get-pocket-api-keys", "get-sub-pocket-balance", "post-universal-transfer",
        "get-universal-transfer", "post-transfer", "get-transfer", "get-balance",
        "available-order-information", "new-order", "order-test", "get-order",
        "list-orders-by-ids", "list-open-orders", "list-closed-orders", "cancel-order",
        "cancel-orders-by-ids", "batch-cancel-orders", "cancel-and-new-order",
        "available-withdrawal-information", "list-withdrawal-addresses", "withdraw",
        "withdraw-krw", "get-withdrawal", "list-withdrawals", "cancel-withdrawal",
        "available-deposit-information", "create-deposit-address", "get-deposit-address",
        "list-deposit-addresses", "deposit-krw", "get-deposit", "list-deposits",
        "list-travelrule-vasps", "verify-travelrule-by-uuid",
        "verify-travelrule-by-txid", "get-service-status", "list-api-keys",
        "list-orderbook-levels",
    ]
}

EXPECTED_BLOCKED_IDS = {
    f"rest.{slug}"
    for slug in [
        "post-universal-transfer", "post-transfer", "new-order", "cancel-order",
        "cancel-orders-by-ids", "batch-cancel-orders", "cancel-and-new-order", "withdraw",
        "withdraw-krw", "cancel-withdrawal", "create-deposit-address", "deposit-krw",
        "verify-travelrule-by-uuid", "verify-travelrule-by-txid",
    ]
}

EXPECTED_WEBSOCKET_TYPES = {
    "ticker",
    "trade",
    "orderbook",
    "candle.1s",
    "candle.1m",
    "candle.3m",
    "candle.5m",
    "candle.10m",
    "candle.15m",
    "candle.30m",
    "candle.60m",
    "candle.240m",
    "myAsset",
    "myOrder",
    "LIST_SUBSCRIPTIONS",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text()))


def test_catalog_covers_official_v1_6_3_rest_and_websocket_inventory() -> None:
    catalog = _load_yaml(CATALOG_PATH)

    assert catalog["catalog_version"] == "1.6.3"
    assert catalog["official_baseline"] == "https://docs.upbit.com/kr/llms.txt"
    assert {endpoint["endpoint_id"] for endpoint in catalog["rest_endpoints"]} == EXPECTED_REST_IDS
    assert {stream["type"] for stream in catalog["websocket_streams"]} == (
        EXPECTED_WEBSOCKET_TYPES
    )


def test_catalog_defines_typed_parameters_rate_limits_and_official_sources() -> None:
    catalog = _load_yaml(CATALOG_PATH)
    allowed_rate_groups = {
        "market",
        "candle",
        "trade",
        "ticker",
        "orderbook",
        "exchange-default",
        "order",
        "order-test",
        "order-cancel-all",
    }

    for endpoint in catalog["rest_endpoints"]:
        assert endpoint["method"] in {"GET", "POST", "DELETE"}
        assert endpoint["path"].startswith("/v1/")
        assert endpoint["rate_limit_group"] in allowed_rate_groups
        assert endpoint["safety"] in {"read", "test", "blocked"}
        assert endpoint["source_url"].startswith("https://docs.upbit.com/kr/reference/")
        assert isinstance(endpoint["parameters"], list)
        for parameter in endpoint["parameters"]:
            assert parameter["location"] in {"path", "query", "body"}
            assert parameter["type"] in {"string", "integer", "number", "boolean", "array"}
            assert isinstance(parameter["required"], bool)

    by_id = {endpoint["endpoint_id"]: endpoint for endpoint in catalog["rest_endpoints"]}
    assert by_id["rest.list-candles-minutes"]["parameters"][0] == {
        "name": "unit",
        "location": "path",
        "type": "integer",
        "required": True,
        "enum": [1, 3, 5, 10, 15, 30, 60, 240],
    }
    assert by_id["rest.order-test"]["safety"] == "test"
    assert by_id["rest.order-test"]["rate_limit_group"] == "order-test"


def test_catalog_blocks_every_state_changing_operation_except_order_test() -> None:
    catalog = _load_yaml(CATALOG_PATH)
    by_id = {endpoint["endpoint_id"]: endpoint for endpoint in catalog["rest_endpoints"]}

    assert {endpoint_id for endpoint_id, item in by_id.items() if item["safety"] == "blocked"} == (
        EXPECTED_BLOCKED_IDS
    )
    assert {endpoint_id for endpoint_id, item in by_id.items() if item["safety"] == "test"} == {
        "rest.order-test"
    }
    assert all(
        item["safety"] == "blocked"
        for item in by_id.values()
        if item["method"] in {"POST", "DELETE"} and item["endpoint_id"] != "rest.order-test"
    )


def test_websocket_catalog_defines_typed_parameters_limits_and_gateway_operations() -> None:
    catalog = _load_yaml(CATALOG_PATH)

    assert catalog["gateway_websocket_operations"] == [
        "connect",
        "subscribe",
        "pause",
        "unsubscribe",
        "reconnect",
    ]
    assert catalog["websocket_formats"] == ["DEFAULT", "SIMPLE", "JSON_LIST", "SIMPLE_LIST"]
    assert catalog["rate_limits"]["websocket-connect"] == {
        "scope": "ip_or_pocket",
        "requests": 5,
        "seconds": 1,
    }
    assert catalog["rate_limits"]["websocket-message"]["requests_per_minute"] == 100
    for stream in catalog["websocket_streams"]:
        assert stream["safety"] == "read"
        assert stream["source_url"].startswith("https://docs.upbit.com/kr/reference/")
        for parameter in stream["parameters"]:
            assert parameter["type"] in {"string", "boolean", "number", "array"}
            assert isinstance(parameter["required"], bool)


def test_gateway_openapi_accepts_endpoint_id_and_never_arbitrary_url() -> None:
    contract = _load_yaml(OPENAPI_PATH)
    serialized = yaml.safe_dump(contract, allow_unicode=True)

    assert contract["openapi"] == "3.1.0"
    assert {"/health", "/v1/catalog", "/v1/requests"} <= set(contract["paths"])
    request_schema = contract["components"]["schemas"]["GatewayRequest"]
    assert request_schema["required"] == ["endpoint_id", "parameters"]
    assert "endpoint_id" in request_schema["properties"]
    assert "url" not in serialized.lower()


def test_gateway_websocket_schema_is_valid_and_covers_trace_events() -> None:
    schema = cast(dict[str, Any], json.loads(WEBSOCKET_SCHEMA_PATH.read_text()))

    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    variants = schema["oneOf"]
    event_types = {
        variant["properties"]["type"]["const"]
        for variant in variants
        if "const" in variant["properties"]["type"]
    }
    assert event_types == {"connection", "subscription", "frame", "error"}
