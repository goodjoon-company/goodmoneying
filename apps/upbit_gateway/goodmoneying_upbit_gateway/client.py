from __future__ import annotations

import ipaddress
from collections.abc import Callable, Mapping
from math import isfinite
from typing import Any, cast
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from goodmoneying_upbit_gateway.auth import (
    Credentials,
    ParameterValue,
    build_query_string,
    build_query_strings,
    create_jwt,
)

OFFICIAL_BASE_URL = "https://api.upbit.com"


class InvalidBaseUrl(ValueError):
    pass


class InvalidParameters(ValueError):
    pass


def validate_base_url(value: str, *, allow_loopback_test: bool) -> str:
    normalized = value.rstrip("/")
    if normalized == OFFICIAL_BASE_URL:
        return normalized
    parsed = urlparse(normalized)
    if not allow_loopback_test or parsed.scheme != "http" or parsed.hostname is None:
        raise InvalidBaseUrl(
            "상향 기본 URL은 공식 Upbit 또는 명시된 루프백 테스트 서버만 허용합니다."
        )
    if (
        parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise InvalidBaseUrl("상향 테스트 URL은 경로·쿼리·인증 정보가 없는 주소여야 합니다.")
    try:
        is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        _ = parsed.port
    except ValueError as exc:
        raise InvalidBaseUrl("상향 테스트 URL은 유효한 루프백 IP 주소여야 합니다.") from exc
    if not is_loopback:
        raise InvalidBaseUrl("상향 테스트 URL은 인증 정보 없는 루프백 주소여야 합니다.")
    return normalized


def validate_parameters(endpoint: Mapping[str, Any], values: Mapping[str, Any]) -> None:
    specifications = {
        cast(str, parameter["name"]): parameter
        for parameter in cast(list[dict[str, Any]], endpoint["parameters"])
    }
    unknown = set(values) - set(specifications)
    missing = {
        name
        for name, specification in specifications.items()
        if specification.get("required") and name not in values
    }
    if unknown or missing:
        raise InvalidParameters(
            "카탈로그 파라미터가 올바르지 않습니다"
            f"(누락={sorted(missing)}, 초과={sorted(unknown)})."
        )
    alternatives = cast(list[list[str]], endpoint.get("any_of_required", []))
    if alternatives and not any(all(name in values for name in option) for option in alternatives):
        raise InvalidParameters(
            f"필수 파라미터 조합 중 하나가 필요합니다: {alternatives}."
        )
    for name, value in values.items():
        specification = specifications[name]
        if not _matches_parameter_schema(value, specification):
            raise InvalidParameters(f"{name} 파라미터가 카탈로그 계약과 다릅니다.")


def _matches_parameter_schema(value: Any, specification: Mapping[str, Any]) -> bool:
    expected = specification.get("type")
    if expected == "string":
        valid_type = isinstance(value, str)
    elif expected == "integer":
        valid_type = isinstance(value, int) and not isinstance(value, bool)
    elif expected == "number":
        valid_type = (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and _is_finite_number(value)
        )
    elif expected == "boolean":
        valid_type = isinstance(value, bool)
    elif expected == "array":
        if not isinstance(value, list):
            return False
        item_schema = specification.get("items")
        if isinstance(item_schema, str):
            item_schema = {"type": item_schema}
        if not isinstance(item_schema, Mapping):
            return False
        valid_type = all(_matches_parameter_schema(item, item_schema) for item in value)
    else:
        valid_type = False
    if not valid_type:
        return False

    allowed = specification.get("enum")
    if allowed is not None and value not in allowed:
        return False
    minimum = specification.get("minimum")
    maximum = specification.get("maximum")
    if isinstance(value, int | float) and not isinstance(value, bool):
        if minimum is not None and value < minimum:
            return False
        if maximum is not None and value > maximum:
            return False
    return True


def _is_finite_number(value: int | float) -> bool:
    try:
        return isfinite(value)
    except OverflowError:
        return False


def build_upstream_request(
    endpoint: Mapping[str, Any],
    parameters: Mapping[str, Any],
    *,
    base_url: str,
    credentials: Credentials | None,
    incoming_headers: Mapping[str, str],
    allow_loopback_test: bool = False,
    nonce_factory: Callable[[], object] = uuid4,
) -> httpx.Request:
    del incoming_headers  # Origin 등 브라우저 헤더는 의도적으로 상향 전달하지 않는다.
    validate_parameters(endpoint, parameters)
    specifications = {
        cast(str, parameter["name"]): parameter
        for parameter in cast(list[dict[str, Any]], endpoint["parameters"])
    }
    path = cast(str, endpoint["path"])
    query: list[tuple[str, ParameterValue]] = []
    body: dict[str, Any] = {}
    for name, value in parameters.items():
        location = specifications[name]["location"]
        if location == "path":
            path = path.replace("{" + name + "}", str(value))
        elif location == "query":
            parameter_value = cast(ParameterValue, value)
            query.append((name, parameter_value))
        elif location == "body":
            body[name] = value

    headers: dict[str, str] = {"Accept": "application/json"}
    query_strings = build_query_strings(query)
    auth_query_string = query_strings.hash_query or build_auth_query_string(
        endpoint, parameters
    )
    if endpoint["category"] == "exchange":
        if credentials is None:
            raise InvalidParameters("거래소 조회·테스트 호출에는 API 인증 정보가 필요합니다.")
        token = create_jwt(
            credentials,
            auth_query_string,
            nonce_factory=nonce_factory,
        )
        headers["Authorization"] = f"Bearer {token}"

    url = f"{validate_base_url(base_url, allow_loopback_test=allow_loopback_test)}{path}"
    if query_strings.wire_query:
        url = f"{url}?{query_strings.wire_query}"
    return httpx.Request(
        method=cast(str, endpoint["method"]),
        url=url,
        json=body or None,
        headers=headers,
    )


def build_auth_query_string(
    endpoint: Mapping[str, Any], parameters: Mapping[str, Any]
) -> str:
    specifications = {
        cast(str, parameter["name"]): parameter
        for parameter in cast(list[dict[str, Any]], endpoint["parameters"])
    }
    auth_parameters = [
        (name, cast(ParameterValue, value))
        for name, value in parameters.items()
        if specifications[name]["location"] in {"query", "body"}
    ]
    return build_query_string(auth_parameters)
