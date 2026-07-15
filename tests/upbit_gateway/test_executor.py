from collections.abc import Callable
from datetime import datetime
from uuid import UUID

import httpx
import pytest

from goodmoneying_upbit_gateway.auth import Credentials, query_hash
from goodmoneying_upbit_gateway.catalog import endpoint_by_id, load_catalog
from goodmoneying_upbit_gateway.executor import (
    UpbitExecutor,
    UpstreamConnectionError,
    UpstreamProtocolError,
    UpstreamTimeout,
)
from goodmoneying_upbit_gateway.rate_limit import GroupRateLimiter
from goodmoneying_upbit_gateway.safety import PolicyBlocked


def _endpoint(endpoint_id: str) -> dict[str, object]:
    endpoint = endpoint_by_id(load_catalog(), endpoint_id)
    assert endpoint is not None
    return endpoint


def _executor(handler: Callable[[httpx.Request], httpx.Response]) -> UpbitExecutor:
    return UpbitExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        credentials_provider=lambda: Credentials("fake-access", "s" * 64),
        limiter=GroupRateLimiter(),
        base_url="http://127.0.0.1:8123",
        allow_loopback_test=True,
    )


def test_blocked_request_stops_before_credentials_rate_limit_and_network() -> None:
    called: list[str] = []

    def credentials_provider() -> Credentials:
        called.append("credentials")
        return Credentials("x", "y")

    executor = UpbitExecutor(
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError()))
        ),
        credentials_provider=credentials_provider,
        limiter=GroupRateLimiter(),
        base_url="https://api.upbit.com",
    )

    with pytest.raises(PolicyBlocked):
        executor.execute(_endpoint("rest.new-order"), {})
    assert called == []


@pytest.mark.parametrize(
    "status", [200, 400, 401, 403, 404, 418, 429, 500, 502, 503, 504]
)
def test_executor_preserves_json_upstream_status_and_observes_remaining_req(status: int) -> None:
    executor = _executor(
        lambda request: httpx.Response(
            status,
            json={"status": status, "authorization": request.headers.get("Authorization")},
            headers={"Remaining-Req": "group=market; min=600; sec=9"},
        )
    )
    result = executor.execute(_endpoint("rest.list-trading-pairs"), {})

    assert result.status_code == status
    assert result.envelope["response"]["body"] == {"status": status, "authorization": "***"}
    assert result.envelope["rate_limit"] == {
        "group": "market",
        "remaining_sec": 9,
        "retry_after": None,
    }
    UUID(result.envelope["trace_id"])
    datetime.fromisoformat(result.envelope["received_at"])
    assert result.envelope["duration_ms"] >= 0


def test_executor_maps_timeout_and_non_json_without_leaking_exception_url() -> None:
    timeout_executor = _executor(
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("secret URL", request=request))
    )
    with pytest.raises(UpstreamTimeout, match="시간") as timeout:
        timeout_executor.execute(_endpoint("rest.list-trading-pairs"), {})
    assert timeout.value.__cause__ is None

    non_json_executor = _executor(lambda request: httpx.Response(200, text="not-json"))
    with pytest.raises(UpstreamProtocolError, match="JSON"):
        non_json_executor.execute(_endpoint("rest.list-trading-pairs"), {})

    connection_executor = _executor(
        lambda request: (_ for _ in ()).throw(
            httpx.ConnectError("fake-sensitive-url", request=request)
        )
    )
    with pytest.raises(UpstreamConnectionError, match="연결") as connection:
        connection_executor.execute(_endpoint("rest.list-trading-pairs"), {})
    assert connection.value.__cause__ is None
    assert "fake-sensitive-url" not in str(connection.value)


def test_exchange_read_sends_hs512_bearer_but_trace_masks_it() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    result = _executor(handler).execute(_endpoint("rest.get-pocket-information"), {})

    assert seen[0].headers["Authorization"].startswith("Bearer ")
    assert "Authorization" not in str(result.envelope)
    assert "fake-access" not in str(result.envelope)


def test_exchange_trace_masks_query_hash_even_when_upstream_reflects_it() -> None:
    raw_hash = query_hash("market=KRW-BTC&side=bid&price=1000&ord_type=price")
    executor = _executor(lambda request: httpx.Response(201, json={"echo": raw_hash}))

    result = executor.execute(
        _endpoint("rest.order-test"),
        {"market": "KRW-BTC", "side": "bid", "price": "1000", "ord_type": "price"},
    )

    assert result.envelope["response"]["body"] == {"echo": "***"}
    assert raw_hash not in str(result.envelope)


def test_browser_origin_is_stripped_without_reducing_quotation_throughput() -> None:
    acquired: list[str] = []

    class SpyLimiter:
        def acquire(self, group: str) -> None:
            acquired.append(group)

        def observe(self, value: str | None) -> None:
            pass

        def defer(self, group: str, seconds: float) -> None:
            pass

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    executor = UpbitExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        credentials_provider=lambda: Credentials("fake-access", "s" * 64),
        limiter=SpyLimiter(),
        base_url="http://127.0.0.1:8123",
        allow_loopback_test=True,
    )
    executor.execute(
        _endpoint("rest.list-trading-pairs"),
        {},
        incoming_headers={"Origin": "https://browser.example"},
    )

    assert acquired == ["market"]
    assert "Origin" not in seen[0].headers


def test_ten_browser_origin_requests_keep_official_market_group_throughput() -> None:
    now = [0.0]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    executor = UpbitExecutor(
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
        ),
        credentials_provider=lambda: Credentials("fake-access", "s" * 64),
        limiter=GroupRateLimiter(clock=lambda: now[0], sleep=sleep),
        base_url="http://127.0.0.1:8123",
        allow_loopback_test=True,
    )

    for _ in range(10):
        executor.execute(
            _endpoint("rest.list-trading-pairs"),
            {},
            incoming_headers={"Origin": "https://browser.example"},
        )

    assert waits == []


def test_repeated_418_never_shortens_existing_penalty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            418,
            json={"error": {"message": "Blocked for 120 seconds."}},
            headers={"Retry-After": "30"},
        )

    deferred: list[float] = []

    class SpyLimiter:
        def acquire(self, group: str) -> None:
            pass

        def observe(self, value: str | None) -> None:
            pass

        def defer(self, group: str, seconds: float) -> None:
            deferred.append(seconds)

    executor = UpbitExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        credentials_provider=lambda: Credentials("fake-access", "s" * 64),
        limiter=SpyLimiter(),
        base_url="http://127.0.0.1:8123",
        allow_loopback_test=True,
    )
    executor.execute(_endpoint("rest.list-trading-pairs"), {})

    assert deferred == [120.0]
