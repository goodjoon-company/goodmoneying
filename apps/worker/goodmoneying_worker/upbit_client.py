from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from goodmoneying_worker.fixtures import (
    fixture_candle_rows,
    fixture_orderbook_rows,
    fixture_ticker_rows,
)


class UpbitClient(Protocol):
    def get_krw_tickers(self) -> list[dict[str, str]]: ...

    def get_orderbooks(self, markets: list[str]) -> list[dict[str, str]]: ...

    def get_minute_candles(self, markets: list[str]) -> list[dict[str, str]]: ...

    def fetch_minute_candles(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> list[dict[str, str]]: ...


class UpbitApiError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class UpbitRateLimiter:
    def __init__(
        self,
        min_interval_seconds: float = 0.12,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min_interval_seconds = min_interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._last_request_at = 0.0
        self._defer_until = 0.0

    def wait(self) -> None:
        now = self._monotonic()
        interval_until = (
            self._last_request_at + self._min_interval_seconds
            if self._min_interval_seconds > 0
            else 0.0
        )
        sleep_for = max(interval_until, self._defer_until) - now
        if sleep_for > 0:
            self._sleep(sleep_for)
        self._last_request_at = self._monotonic()

    def observe_remaining_req(self, header_value: str | None) -> None:
        remaining = _remaining_req_second_quota(header_value)
        if remaining is None:
            return
        if remaining <= 0:
            self._defer_until = max(self._defer_until, self._monotonic() + 1.0)


class FixtureUpbitClient:
    def __init__(self, market_count: int = 100) -> None:
        self._market_count = market_count

    def get_krw_tickers(self) -> list[dict[str, str]]:
        return fixture_ticker_rows(self._market_count)

    def get_orderbooks(self, markets: list[str]) -> list[dict[str, str]]:
        return fixture_orderbook_rows(markets)

    def get_minute_candles(self, markets: list[str]) -> list[dict[str, str]]:
        return fixture_candle_rows(markets)

    def fetch_minute_candles(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> list[dict[str, str]]:
        minutes = max(1, int((end_at - start_at).total_seconds() // 60))
        return [
            row
            for row in fixture_candle_rows([market], minutes=minutes)
            if start_at <= datetime.fromisoformat(row["candle_start_at"]).astimezone(UTC) < end_at
        ]


class LiveUpbitClient:
    BASE_URL = "https://api.upbit.com/v1"

    def __init__(
        self,
        timeout: float = 10.0,
        http_client: httpx.Client | None = None,
        min_request_interval_seconds: float = 0.12,
        retry_sleep_seconds: float = 1.0,
        max_retries: int = 3,
    ) -> None:
        self._client = http_client or httpx.Client(base_url=self.BASE_URL, timeout=timeout)
        self._rate_limiter = UpbitRateLimiter(min_request_interval_seconds)
        self._retry_sleep_seconds = retry_sleep_seconds
        self._max_retries = max_retries

    def get_krw_tickers(self) -> list[dict[str, str]]:
        markets_response = self._get_json("/market/all", params={"isDetails": "false"})
        market_codes = [
            item["market"]
            for item in markets_response
            if str(item["market"]).startswith("KRW-")
        ]
        ticker_response = self._get_json("/ticker", params={"markets": ",".join(market_codes)})
        by_market = {item["market"]: item for item in ticker_response}
        return [
            {
                "market": market_code,
                "display_name": market_code,
                "trade_price": str(by_market[market_code]["trade_price"]),
                "acc_trade_price_24h": str(by_market[market_code]["acc_trade_price_24h"]),
                "signed_change_rate": str(by_market[market_code].get("signed_change_rate") or "0"),
            }
            for market_code in market_codes
            if market_code in by_market
        ]

    def get_orderbooks(self, markets: list[str]) -> list[dict[str, str]]:
        response = self._get_json("/orderbook", params={"markets": ",".join(markets)})
        rows: list[dict[str, str]] = []
        for item in response:
            units = item["orderbook_units"][:10]
            best = units[0]
            bid_depth = sum(unit["bid_size"] for unit in units)
            ask_depth = sum(unit["ask_size"] for unit in units)
            denominator = bid_depth + ask_depth
            imbalance = (bid_depth - ask_depth) / denominator if denominator else 0
            rows.append(
                {
                    "market": item["market"],
                    "best_bid_price": str(best["bid_price"]),
                    "best_bid_size": str(best["bid_size"]),
                    "best_ask_price": str(best["ask_price"]),
                    "best_ask_size": str(best["ask_size"]),
                    "spread": str(best["ask_price"] - best["bid_price"]),
                    "bid_depth_10": str(bid_depth),
                    "ask_depth_10": str(ask_depth),
                    "imbalance_10": str(imbalance),
                }
            )
        return rows

    def get_minute_candles(self, markets: list[str]) -> list[dict[str, str]]:
        if not os.getenv("GOODMONEYING_LIVE_UPBIT"):
            raise RuntimeError(
                "live 업비트 캔들 호출은 GOODMONEYING_LIVE_UPBIT=1 일 때만 허용된다."
            )
        rows: list[dict[str, str]] = []
        for market in markets:
            response = self._get_json(
                "/candles/minutes/1",
                params={"market": market, "count": 5},
            )
            for item in response:
                rows.append(
                    {
                        "market": market,
                        "candle_unit": "1m",
                        "candle_start_at": item["candle_date_time_utc"] + "+00:00",
                        "open_price": str(item["opening_price"]),
                        "high_price": str(item["high_price"]),
                        "low_price": str(item["low_price"]),
                        "close_price": str(item["trade_price"]),
                        "trade_volume": str(item["candle_acc_trade_volume"]),
                        "trade_amount": str(item["candle_acc_trade_price"]),
                    }
                )
        return rows

    def fetch_minute_candles(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> list[dict[str, str]]:
        if start_at >= end_at:
            raise ValueError("캔들 조회 종료 시각은 시작 시각보다 뒤여야 한다.")
        rows_by_started_at: dict[datetime, dict[str, str]] = {}
        cursor = end_at.astimezone(UTC)
        start_at_utc = start_at.astimezone(UTC)
        end_at_utc = end_at.astimezone(UTC)
        while cursor > start_at_utc:
            payload = self._get_json(
                "/candles/minutes/1",
                params={
                    "market": market,
                    "to": cursor.isoformat().replace("+00:00", "Z"),
                    "count": 200,
                },
            )
            if not payload:
                break
            page_times = [
                _parse_upbit_candle_time(item["candle_date_time_utc"]) for item in payload
            ]
            for item, candle_start_at in zip(payload, page_times, strict=True):
                if start_at_utc <= candle_start_at < end_at_utc:
                    rows_by_started_at[candle_start_at] = _upbit_candle_to_row(market, item)
            oldest = min(page_times)
            if oldest <= start_at_utc:
                break
            if oldest >= cursor:
                break
            cursor = oldest
        return [
            rows_by_started_at[started_at]
            for started_at in sorted(rows_by_started_at)
        ]

    def _get_json(self, path: str, params: dict[str, str | int]) -> list[dict[str, Any]]:
        attempts = 0
        while True:
            self._rate_limiter.wait()
            response = self._client.get(path, params=params)
            self._rate_limiter.observe_remaining_req(response.headers.get("Remaining-Req"))
            if response.status_code < 400:
                return list(response.json())
            if response.status_code not in {418, 429} or attempts >= self._max_retries:
                raise UpbitApiError(
                    status_code=response.status_code,
                    message=_response_error_message(response),
                )
            attempts += 1
            time.sleep(_retry_delay(response, self._retry_sleep_seconds))


def _parse_upbit_candle_time(value: object) -> datetime:
    return datetime.fromisoformat(str(value)).replace(tzinfo=UTC)


def _upbit_candle_to_row(market: str, item: dict[str, Any]) -> dict[str, str]:
    return {
        "market": market,
        "candle_unit": "1m",
        "candle_start_at": _parse_upbit_candle_time(item["candle_date_time_utc"]).isoformat(),
        "open_price": str(item["opening_price"]),
        "high_price": str(item["high_price"]),
        "low_price": str(item["low_price"]),
        "close_price": str(item["trade_price"]),
        "trade_volume": str(item["candle_acc_trade_volume"]),
        "trade_amount": str(item["candle_acc_trade_price"]),
    }


def _response_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or f"Upbit API returned {response.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if payload.get("message"):
            return str(payload["message"])
    return f"Upbit API returned {response.status_code}"


def _retry_delay(response: httpx.Response, default_seconds: float) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    block_duration = _response_block_duration(response)
    return block_duration if block_duration is not None else default_seconds


def _remaining_req_second_quota(header_value: str | None) -> int | None:
    if not header_value:
        return None
    for part in header_value.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == "sec":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _response_block_duration(response: httpx.Response) -> float | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    candidates: list[object] = [
        payload.get("retry_after"),
        payload.get("retryAfter"),
        payload.get("duration"),
        payload.get("message"),
    ]
    error = payload.get("error")
    if isinstance(error, dict):
        candidates.extend(
            [
                error.get("retry_after"),
                error.get("retryAfter"),
                error.get("duration"),
                error.get("message"),
            ]
        )
    for candidate in candidates:
        seconds = _seconds_from_value(candidate)
        if seconds is not None:
            return seconds
    return None


def _seconds_from_value(value: object) -> float | None:
    if isinstance(value, int | float):
        return max(0.0, float(value))
    if not isinstance(value, str):
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    match = re.search(r"(\d+(?:\.\d+)?)\s*초", value)
    return max(0.0, float(match.group(1))) if match else None
