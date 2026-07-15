from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import uuid4

import httpx

from goodmoneying_upbit_gateway.auth import Credentials, query_hash
from goodmoneying_upbit_gateway.client import (
    build_auth_query_string,
    build_upstream_request,
    validate_base_url,
    validate_parameters,
)
from goodmoneying_upbit_gateway.rate_limit import parse_penalty_seconds, parse_remaining_req
from goodmoneying_upbit_gateway.safety import SafetyLevel, SafetyPolicy
from goodmoneying_upbit_gateway.trace import sanitize


class UpstreamTimeout(RuntimeError):
    pass


class UpstreamProtocolError(RuntimeError):
    pass


class UpstreamConnectionError(RuntimeError):
    pass


class RateLimiter(Protocol):
    def acquire(self, group: str) -> None: ...

    def observe(self, value: str | None) -> None: ...

    def defer(self, group: str, seconds: float) -> None: ...


@dataclass(frozen=True)
class ExecutionResult:
    status_code: int
    envelope: dict[str, Any]


class UpbitExecutor:
    def __init__(
        self,
        *,
        http_client: httpx.Client,
        credentials_provider: Callable[[], Credentials],
        limiter: RateLimiter,
        base_url: str,
        allow_loopback_test: bool = False,
    ) -> None:
        self._client = http_client
        self._credentials_provider = credentials_provider
        self._limiter = limiter
        self._base_url = validate_base_url(base_url, allow_loopback_test=allow_loopback_test)
        self._allow_loopback_test = allow_loopback_test
        self._safety = SafetyPolicy()

    def execute(
        self,
        endpoint: Mapping[str, Any],
        parameters: Mapping[str, Any],
        *,
        incoming_headers: Mapping[str, str] | None = None,
    ) -> ExecutionResult:
        self._safety.ensure_upstream_allowed(cast(SafetyLevel, endpoint["safety"]))
        started_at = time.monotonic()
        validate_parameters(endpoint, parameters)
        rate_group = cast(str, endpoint["rate_limit_group"])
        self._limiter.acquire(rate_group)
        credentials = (
            self._credentials_provider() if endpoint["category"] == "exchange" else None
        )
        request = build_upstream_request(
            endpoint,
            parameters,
            base_url=self._base_url,
            credentials=credentials,
            incoming_headers=incoming_headers or {},
            allow_loopback_test=self._allow_loopback_test,
        )
        try:
            response = self._client.send(request)
        except httpx.TimeoutException:
            raise UpstreamTimeout("업비트 상향 호출 제한 시간을 초과했습니다.") from None
        except httpx.RequestError:
            raise UpstreamConnectionError("업비트 상향 서버 연결에 실패했습니다.") from None

        remaining_header = response.headers.get("Remaining-Req")
        self._limiter.observe(remaining_header)
        remaining = parse_remaining_req(remaining_header)
        response_group = (
            remaining[0]
            if remaining is not None and remaining[0] == rate_group
            else rate_group
        )
        retry_after = response.headers.get("Retry-After")
        try:
            body = response.json()
        except ValueError:
            if response.status_code == 418:
                self._limiter.defer(
                    response_group, parse_penalty_seconds(retry_after, None)
                )
            raise UpstreamProtocolError("업비트 상향 응답이 JSON 형식이 아닙니다.") from None
        if response.status_code == 429:
            self._limiter.defer(response_group, 1.0)
        elif response.status_code == 418:
            self._limiter.defer(
                response_group, parse_penalty_seconds(retry_after, body)
            )

        sensitive_values = {
            request.headers.get("Authorization", ""),
            request.headers.get("Authorization", "").removeprefix("Bearer "),
        }
        if credentials is not None:
            sensitive_values.update({credentials.access_key, credentials.secret_key})
            auth_query_string = build_auth_query_string(endpoint, parameters)
            if auth_query_string:
                sensitive_values.add(query_hash(auth_query_string))
        envelope = {
            "trace_id": str(uuid4()),
            "endpoint_id": endpoint["endpoint_id"],
            "request": sanitize(
                {
                    "method": endpoint["method"],
                    "path": endpoint["path"],
                    "parameters": parameters,
                },
                sensitive_values=sensitive_values,
            ),
            "response": {
                "status_code": response.status_code,
                "body": sanitize(body, sensitive_values=sensitive_values),
            },
            "rate_limit": {
                "group": remaining[0] if remaining is not None else rate_group,
                "remaining_sec": remaining[1] if remaining is not None else None,
                "retry_after": retry_after,
            },
            "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
            "received_at": datetime.now(UTC).isoformat(),
        }
        return ExecutionResult(status_code=response.status_code, envelope=envelope)
