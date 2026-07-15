from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import httpx

from goodmoneying_upbit_gateway.auth import Credentials
from goodmoneying_upbit_gateway.catalog import load_catalog, rest_endpoint_by_id
from goodmoneying_upbit_gateway.executor import (
    UpbitExecutor,
    UpstreamConnectionError,
    UpstreamProtocolError,
    UpstreamTimeout,
)
from goodmoneying_upbit_gateway.rate_limit import GroupRateLimiter, rate_limits_from_catalog
from goodmoneying_upbit_gateway.safety import PolicyBlocked

SAFE_LIVE_REQUESTS: tuple[tuple[str, Mapping[str, object]], ...] = (
    ("rest.list-trading-pairs", {}),
    ("rest.get-balance", {}),
    (
        "rest.order-test",
        {"market": "KRW-BTC", "side": "bid", "price": "1000", "ord_type": "price"},
    ),
)


def parse_key_file(path: Path) -> Credentials:
    """라벨이 있는 한 파일에서 값을 읽되 호출자에게 키 이름이나 원문을 돌려주지 않는다."""
    if not path.is_file() or path.is_symlink():
        raise ValueError("키 파일은 심볼릭 링크가 아닌 일반 파일이어야 합니다.")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        separator = "=" if "=" in line else ":" if ":" in line else None
        if separator is None:
            continue
        label, value = line.split(separator, maxsplit=1)
        normalized = re.sub(r"[^a-z]", "", label.lower())
        if "access" in normalized and "key" in normalized:
            values["access"] = value.strip()
        elif "secret" in normalized and "key" in normalized:
            values["secret"] = value.strip()
    if not values.get("access") or not values.get("secret"):
        raise ValueError("키 파일에서 Access Key와 Secret Key 라벨을 모두 찾을 수 없습니다.")
    return Credentials(access_key=values["access"], secret_key=values["secret"])


def run_safe_verification(
    key_file: Path,
    *,
    http_client: httpx.Client,
    base_url: str = "https://api.upbit.com",
) -> dict[str, Any]:
    credentials = parse_key_file(key_file)
    catalog = load_catalog()
    executor = UpbitExecutor(
        http_client=http_client,
        credentials_provider=lambda: credentials,
        limiter=GroupRateLimiter(limits=rate_limits_from_catalog(catalog)),
        base_url=base_url,
    )
    allowed_results: list[dict[str, object]] = []
    for endpoint_id, parameters in SAFE_LIVE_REQUESTS:
        endpoint = rest_endpoint_by_id(catalog, endpoint_id)
        if endpoint is None:
            raise RuntimeError(f"카탈로그에 안전 검증 기능이 없습니다: {endpoint_id}")
        try:
            result = executor.execute(endpoint, parameters)
        except (UpstreamTimeout, UpstreamProtocolError, UpstreamConnectionError) as exc:
            allowed_results.append(
                {
                    "endpoint_id": endpoint_id,
                    "status_code": None,
                    "error": type(exc).__name__,
                }
            )
        else:
            rate_limit = cast(dict[str, object], result.envelope["rate_limit"])
            summary: dict[str, object] = {
                "endpoint_id": endpoint_id,
                "status_code": result.status_code,
                "rate_limit_group": rate_limit["group"],
                "remaining_sec": rate_limit["remaining_sec"],
            }
            response = cast(dict[str, object], result.envelope["response"])
            body = response.get("body")
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict) and isinstance(error.get("name"), str):
                    summary["error_name"] = error["name"]
            allowed_results.append(summary)

    blocked = [
        endpoint
        for endpoint in cast(list[dict[str, Any]], catalog["rest_endpoints"])
        if endpoint["safety"] == "blocked"
    ]
    locally_blocked = 0
    for endpoint in blocked:
        try:
            executor.execute(endpoint, {})
        except PolicyBlocked:
            locally_blocked += 1
        else:
            endpoint_id = endpoint["endpoint_id"]
            raise RuntimeError(f"차단 기능이 상향 실행 경계를 통과했습니다: {endpoint_id}")

    return {
        "inventory": {
            "rest_total": len(cast(list[object], catalog["rest_endpoints"])),
            "websocket_streams": len(cast(list[object], catalog["websocket_streams"])),
            "blocked_rest": len(blocked),
        },
        "allowed_results": allowed_results,
        "blocked_verification": {
            "catalog_count": len(blocked),
            "locally_blocked": locally_blocked,
            "upstream_calls": 0,
        },
    }
