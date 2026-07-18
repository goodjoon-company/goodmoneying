from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, time
from decimal import Decimal, InvalidOperation
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
    if alternatives and not any(
        all(_has_parameter_value(values, name) for name in option)
        for option in alternatives
    ):
        raise InvalidParameters(
            f"필수 파라미터 조합 중 하나가 필요합니다: {alternatives}."
        )
    mutually_exclusive = cast(list[list[str]], endpoint.get("mutually_exclusive", []))
    for group in mutually_exclusive:
        present = [name for name in group if _has_parameter_value(values, name)]
        if len(present) > 1:
            raise InvalidParameters(
                f"파라미터를 동시에 사용할 수 없습니다: {present}."
            )
    _validate_forbidden_value_combinations(endpoint, values)
    for name, value in values.items():
        specification = specifications[name]
        if not _matches_parameter_schema(value, specification):
            raise InvalidParameters(f"{name} 파라미터가 카탈로그 계약과 다릅니다.")
    _validate_parameter_ranges(specifications, values)


def _has_parameter_value(values: Mapping[str, Any], name: str) -> bool:
    if name not in values:
        return False
    value = values[name]
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def _validate_forbidden_value_combinations(
    endpoint: Mapping[str, Any],
    values: Mapping[str, Any],
) -> None:
    rules = cast(list[dict[str, Any]], endpoint.get("forbidden_value_combinations", []))
    for rule in rules:
        when = rule.get("when")
        forbid = rule.get("forbid")
        if not isinstance(when, Mapping) or not isinstance(forbid, list):
            continue
        if not all(values.get(name) == expected for name, expected in when.items()):
            continue
        present = [
            str(name)
            for name in forbid
            if isinstance(name, str) and _has_parameter_value(values, name)
        ]
        if present:
            trigger = ", ".join(f"{name}={value}" for name, value in when.items())
            raise InvalidParameters(
                f"{trigger} 조건에서는 파라미터를 동시에 사용할 수 없습니다: {present}."
            )


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

    if isinstance(value, str):
        minimum_length = specification.get("min_length")
        maximum_length = specification.get("max_length")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            return False
        if isinstance(maximum_length, int) and len(value) > maximum_length:
            return False
        if not _matches_string_format(value, specification):
            return False
    if isinstance(value, list):
        if any(isinstance(item, str) and not item.strip() for item in value):
            return False
        maximum_items = specification.get("max_items")
        if isinstance(maximum_items, int) and len(value) > maximum_items:
            return False
        if specification.get("unique_items") and len(value) != len(set(value)):
            return False

    allowed = specification.get("enum")
    if allowed is not None and value not in allowed:
        return False
    minimum = specification.get("minimum")
    maximum = specification.get("maximum")
    comparable_value: int | float | Decimal | None = None
    if isinstance(value, int | float) and not isinstance(value, bool):
        comparable_value = value
    elif isinstance(value, str) and specification.get("format") in {
        "integer-string",
        "decimal-string",
    }:
        comparable_value = Decimal(value)
    if comparable_value is not None:
        if minimum is not None and comparable_value < minimum:
            return False
        if maximum is not None and comparable_value > maximum:
            return False
    return True


def _matches_string_format(value: str, specification: Mapping[str, Any]) -> bool:
    format_name = specification.get("format")
    if format_name == "csv":
        items = [item.strip() for item in value.split(",")]
        if any(not item for item in items):
            return False
        maximum_items = specification.get("max_items")
        if isinstance(maximum_items, int) and len(items) > maximum_items:
            return False
        if specification.get("unique_items") and len(items) != len(set(items)):
            return False
        return bool(items)
    if format_name == "integer-string":
        return re.fullmatch(r"-?\d+", value) is not None
    if format_name == "decimal-string":
        try:
            return Decimal(value).is_finite()
        except InvalidOperation:
            return False
    if format_name == "date-time":
        return _parse_datetime(value) is not None
    if format_name == "time":
        if re.fullmatch(r"\d{2}:\d{2}:\d{2}", value) is None:
            return False
        try:
            time.fromisoformat(value)
        except ValueError:
            return False
    return True


def _parse_datetime(value: str) -> datetime | None:
    if re.fullmatch(r"\d{13}", value):
        try:
            return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _validate_parameter_ranges(
    specifications: Mapping[str, Mapping[str, Any]],
    values: Mapping[str, Any],
) -> None:
    checked: set[frozenset[str]] = set()
    for name, specification in specifications.items():
        partner = specification.get("range_with")
        maximum_seconds = specification.get("range_max_seconds")
        if not isinstance(partner, str) or not isinstance(maximum_seconds, int):
            continue
        pair = frozenset((name, partner))
        if pair in checked or name not in values or partner not in values:
            continue
        checked.add(pair)
        first = _parse_datetime(str(values[name]))
        second = _parse_datetime(str(values[partner]))
        if first is None or second is None:
            raise InvalidParameters(f"{name} 날짜·시간 범위가 올바르지 않습니다.")
        start, end = (first, second) if name.startswith("start") else (second, first)
        duration = (end - start).total_seconds()
        if duration < 0 or duration > maximum_seconds:
            raise InvalidParameters(
                f"{name}·{partner} 날짜·시간 범위는 {maximum_seconds}초 이하여야 합니다."
            )


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
