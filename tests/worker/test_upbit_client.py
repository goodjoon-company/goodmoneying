from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from goodmoneying_worker.upbit_client import LiveUpbitClient, UpbitApiError


def test_rest_ticker_and_orderbook_rows_preserve_exchange_timestamp() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/market/all":
            return httpx.Response(200, json=[{"market": "KRW-BTC"}])
        if request.url.path == "/v1/ticker":
            return httpx.Response(
                200,
                json=[
                    {
                        "market": "KRW-BTC",
                        "trade_price": 100,
                        "acc_trade_price_24h": 1000,
                        "signed_change_rate": 0.01,
                        "timestamp": 1767225600123,
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "market": "KRW-BTC",
                    "timestamp": 1767225600456,
                    "orderbook_units": [
                        {"bid_price": 99, "bid_size": 2, "ask_price": 101, "ask_size": 1}
                    ],
                }
            ],
        )

    client = LiveUpbitClient(
        http_client=httpx.Client(
            base_url=LiveUpbitClient.BASE_URL,
            transport=httpx.MockTransport(handler),
        ),
        min_request_interval_seconds=0,
    )

    assert client.get_krw_tickers()[0]["timestamp"] == "1767225600123"
    assert client.get_orderbooks(["KRW-BTC"])[0]["timestamp"] == "1767225600456"


def test_minute_candle_pages_keep_actual_request_and_raw_response_per_page() -> None:
    calls = 0
    raw_pages = [
        [_raw_candle("2025-12-31T15:02:00", 102), _raw_candle("2025-12-31T15:01:00", 101)],
        [_raw_candle("2025-12-31T15:00:00", 100)],
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        payload = raw_pages[calls]
        calls += 1
        return httpx.Response(200, json=payload)

    client = LiveUpbitClient(
        http_client=httpx.Client(
            base_url=LiveUpbitClient.BASE_URL,
            transport=httpx.MockTransport(handler),
        ),
        min_request_interval_seconds=0,
    )

    pages = list(
        client.fetch_minute_candle_pages(
            "KRW-BTC",
            datetime(2025, 12, 31, 15, 0, tzinfo=UTC),
            datetime(2025, 12, 31, 15, 3, tzinfo=UTC),
        )
    )

    assert len(pages) == 2
    assert [page.evidence.response_payload for page in pages] == raw_pages
    assert all(page.evidence.endpoint == "/v1/candles/minutes/1" for page in pages)
    assert pages[0].evidence.request_parameters == {
        "market": "KRW-BTC",
        "to": "2025-12-31T15:03:00Z",
        "count": 200,
    }
    assert pages[1].evidence.request_parameters["to"] == "2025-12-31T15:01:00Z"
    assert all(page.evidence.response_status == 200 for page in pages)
    assert all(page.evidence.requested_at <= page.evidence.responded_at for page in pages)


def test_empty_minute_candle_response_is_yielded_as_evidence_page() -> None:
    client = LiveUpbitClient(
        http_client=httpx.Client(
            base_url=LiveUpbitClient.BASE_URL,
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=[])),
        ),
        min_request_interval_seconds=0,
    )

    pages = list(
        client.fetch_minute_candle_pages(
            "KRW-BTC",
            datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        )
    )

    assert len(pages) == 1
    assert pages[0].rows == []
    assert pages[0].evidence.response_payload == []
    assert pages[0].evidence.response_status == 200


def test_failed_minute_candle_request_keeps_error_payload_and_request_evidence() -> None:
    raw_error = {"error": {"name": "too_many_requests", "message": "요청 수 제한"}}
    client = LiveUpbitClient(
        http_client=httpx.Client(
            base_url=LiveUpbitClient.BASE_URL,
            transport=httpx.MockTransport(lambda _request: httpx.Response(429, json=raw_error)),
        ),
        min_request_interval_seconds=0,
    )

    with pytest.raises(UpbitApiError) as captured:
        list(
            client.fetch_minute_candle_pages(
                "KRW-BTC",
                datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            )
        )

    evidence = captured.value.evidence
    assert evidence is not None
    assert evidence.endpoint == "/v1/candles/minutes/1"
    assert evidence.request_parameters["market"] == "KRW-BTC"
    assert evidence.response_status == 429
    assert evidence.response_payload == raw_error


def test_transport_error_keeps_actual_request_and_explicit_no_response_evidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream timed out", request=request)

    client = LiveUpbitClient(
        http_client=httpx.Client(
            base_url=LiveUpbitClient.BASE_URL,
            transport=httpx.MockTransport(handler),
        ),
        min_request_interval_seconds=0,
    )

    with pytest.raises(UpbitApiError) as captured:
        list(
            client.fetch_minute_candle_pages(
                "KRW-BTC",
                datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            )
        )

    error = captured.value
    evidence = error.evidence
    assert error.status_code is None
    assert error.retry_after_seconds is None
    assert evidence is not None
    assert evidence.endpoint == "/v1/candles/minutes/1"
    assert evidence.request_parameters == {
        "market": "KRW-BTC",
        "to": "2026-01-01T00:01:00Z",
        "count": 200,
    }
    assert evidence.requested_at <= evidence.responded_at
    assert evidence.response_status is None
    assert evidence.response_payload is None
    assert evidence.error_type == "ReadTimeout"
    assert evidence.error_message == "upstream timed out"


@pytest.mark.parametrize(
    ("status_code", "expected_retry_after_seconds"),
    [(418, None), (429, 1.0)],
)
def test_rate_limit_without_server_delay_keeps_status_specific_fallback(
    status_code: int,
    expected_retry_after_seconds: float | None,
) -> None:
    client = LiveUpbitClient(
        http_client=httpx.Client(
            base_url=LiveUpbitClient.BASE_URL,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    status_code,
                    json={"error": {"message": "요청 수 제한"}},
                )
            ),
        ),
        min_request_interval_seconds=0,
    )

    with pytest.raises(UpbitApiError) as captured:
        list(
            client.fetch_minute_candle_pages(
                "KRW-BTC",
                datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            )
        )

    assert captured.value.retry_after_seconds == expected_retry_after_seconds


def _raw_candle(candle_time_utc: str, close: int) -> dict[str, object]:
    return {
        "market": "KRW-BTC",
        "candle_date_time_utc": candle_time_utc,
        "opening_price": close - 1,
        "high_price": close + 1,
        "low_price": close - 2,
        "trade_price": close,
        "candle_acc_trade_volume": 1.5,
        "candle_acc_trade_price": close * 1.5,
    }
