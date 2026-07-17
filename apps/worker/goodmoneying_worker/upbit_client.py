from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol, cast

import httpx

from goodmoneying_shared.data_foundation import MarketCatalogItem
from goodmoneying_shared.models import FetchedCandlePage, FetchEvidence
from goodmoneying_shared.time import KST
from goodmoneying_worker.fixtures import (
    fixture_candle_rows,
    fixture_orderbook_rows,
    fixture_ticker_rows,
)

CandlePage = FetchedCandlePage | list[dict[str, str]]


class UpbitClient(Protocol):
    def get_market_catalog(self) -> list[MarketCatalogItem]: ...

    def get_krw_tickers(self) -> list[dict[str, str]]: ...

    def get_orderbooks(self, markets: list[str]) -> list[dict[str, str]]: ...

    def get_minute_candles(self, markets: list[str]) -> list[dict[str, str]]: ...

    def fetch_minute_candles(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> list[dict[str, str]]: ...

    def fetch_minute_candle_pages(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> Iterable[CandlePage]: ...


class UpbitApiError(RuntimeError):
    def __init__(
        self,
        status_code: int | None,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        evidence: FetchEvidence | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        self.evidence = evidence
        self.error_type = error_type or (evidence.error_type if evidence is not None else None)


class UpbitResponseShapeError(ValueError):
    def __init__(self, message: str, *, evidence: FetchEvidence) -> None:
        super().__init__(message)
        self.evidence = evidence


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

    def get_market_catalog(self) -> list[MarketCatalogItem]:
        return [
            MarketCatalogItem(
                market_code=row["market"],
                korean_name=row["display_name"],
                english_name=row["display_name"],
                market_warning="NONE",
                tradable=True,
            )
            for row in fixture_ticker_rows(self._market_count)
        ]

    def get_krw_tickers(self) -> list[dict[str, str]]:
        return fixture_ticker_rows(self._market_count)

    def get_orderbooks(self, markets: list[str]) -> list[dict[str, str]]:
        return fixture_orderbook_rows(markets)

    def get_minute_candles(self, markets: list[str]) -> list[dict[str, str]]:
        return fixture_candle_rows(markets)

    def fetch_minute_candles(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> list[dict[str, str]]:
        return [
            row
            for page in self.fetch_minute_candle_pages(market, start_at, end_at)
            for row in (page.rows if isinstance(page, FetchedCandlePage) else page)
        ]

    def fetch_minute_candle_pages(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> Iterable[CandlePage]:
        minutes = max(1, int((end_at - start_at).total_seconds() // 60))
        requested_at = datetime.now(UTC)
        rows = [
            row
            for row in fixture_candle_rows([market], minutes=minutes)
            if start_at <= datetime.fromisoformat(row["candle_start_at"]).astimezone(KST) < end_at
        ]
        yield FetchedCandlePage(
            rows=rows,
            evidence=FetchEvidence(
                endpoint="/v1/candles/minutes/1",
                request_parameters={
                    "market": market,
                    "to": end_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                    "count": 200,
                },
                requested_at=requested_at,
                responded_at=datetime.now(UTC),
                response_status=200,
                response_payload=rows,
                requested_range_start_at=start_at.astimezone(UTC),
                requested_range_end_at=end_at.astimezone(UTC),
            ),
        )


class LiveUpbitClient:
    BASE_URL = "https://api.upbit.com/v1"

    def __init__(
        self,
        timeout: float = 10.0,
        http_client: httpx.Client | None = None,
        min_request_interval_seconds: float = 0.12,
    ) -> None:
        self._client = http_client or httpx.Client(base_url=self.BASE_URL, timeout=timeout)
        self._rate_limiter = UpbitRateLimiter(min_request_interval_seconds)
        self.last_market_catalog_evidence: FetchEvidence | None = None

    def get_market_catalog(self) -> list[MarketCatalogItem]:
        response, evidence = self._get_json_with_evidence(
            "/market/all",
            endpoint="/v1/market/all",
            params={"is_details": "true"},
        )
        self.last_market_catalog_evidence = evidence
        if not isinstance(response, list) or not response:
            raise ValueError("업비트 시장 목록 성공 응답이 비어 있어 동기화를 중단한다.")
        try:
            return [
                MarketCatalogItem(
                    market_code=str(item["market"]),
                    korean_name=str(item.get("korean_name") or item["market"]),
                    english_name=str(item.get("english_name") or item["market"]),
                    market_warning=_market_warning(item),
                    tradable=True,
                    market_event=_market_event(item),
                )
                for item in response
            ]
        except (KeyError, TypeError, ValueError) as exc:
            self.last_market_catalog_evidence = replace(
                evidence,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

    def get_krw_tickers(self) -> list[dict[str, str]]:
        markets_response = self._get_json("/market/all", params={"is_details": "true"})
        market_codes = [
            item["market"] for item in markets_response if str(item["market"]).startswith("KRW-")
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
                "timestamp": str(by_market[market_code].get("timestamp") or ""),
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
                    "timestamp": str(item.get("timestamp") or ""),
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
                        "candle_start_at": _parse_upbit_candle_time(
                            item["candle_date_time_utc"]
                        ).isoformat(),
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
        rows_by_started_at: dict[datetime, dict[str, str]] = {}
        for page in self.fetch_minute_candle_pages(market, start_at, end_at):
            for row in page.rows:
                rows_by_started_at[
                    datetime.fromisoformat(row["candle_start_at"]).astimezone(UTC)
                ] = row
        return [rows_by_started_at[started_at] for started_at in sorted(rows_by_started_at)]

    def fetch_minute_candle_pages(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> Iterable[FetchedCandlePage]:
        if start_at >= end_at:
            raise ValueError("캔들 조회 종료 시각은 시작 시각보다 뒤여야 한다.")
        cursor = end_at.astimezone(UTC)
        start_at_utc = start_at.astimezone(UTC)
        end_at_utc = end_at.astimezone(UTC)
        while cursor > start_at_utc:
            endpoint = "/v1/candles/minutes/1"
            parameters: dict[str, str | int] = {
                "market": market,
                "to": cursor.isoformat().replace("+00:00", "Z"),
                "count": 200,
            }
            payload, evidence = self._get_json_with_evidence(
                "/candles/minutes/1", endpoint=endpoint, params=parameters
            )
            evidence = replace(
                evidence,
                requested_range_start_at=start_at_utc,
                requested_range_end_at=cursor,
            )
            if not payload:
                yield FetchedCandlePage(rows=[], evidence=evidence)
                break
            page_times = [
                _parse_upbit_candle_time(item["candle_date_time_utc"]) for item in payload
            ]
            page_rows_by_started_at: dict[datetime, dict[str, str]] = {}
            for item, candle_start_at in zip(payload, page_times, strict=True):
                if start_at_utc <= candle_start_at < end_at_utc:
                    page_rows_by_started_at[candle_start_at] = _upbit_candle_to_row(market, item)
            if page_rows_by_started_at:
                yield FetchedCandlePage(
                    rows=[
                        page_rows_by_started_at[started_at]
                        for started_at in sorted(page_rows_by_started_at)
                    ],
                    evidence=evidence,
                )
            else:
                yield FetchedCandlePage(rows=[], evidence=evidence)
            oldest = min(page_times)
            if oldest <= start_at_utc:
                break
            if oldest >= cursor:
                break
            cursor = oldest.astimezone(UTC)

    def _get_json(self, path: str, params: dict[str, str | int]) -> list[dict[str, Any]]:
        self._rate_limiter.wait()
        response = self._client.get(path, params=params)
        self._rate_limiter.observe_remaining_req(response.headers.get("Remaining-Req"))
        if response.status_code < 400:
            return list(response.json())
        retry_after_seconds = _api_retry_delay(response)
        raise UpbitApiError(
            status_code=response.status_code,
            message=_response_error_message(response),
            retry_after_seconds=retry_after_seconds,
        )

    def _get_json_with_evidence(
        self,
        path: str,
        *,
        endpoint: str,
        params: dict[str, str | int],
    ) -> tuple[list[dict[str, Any]], FetchEvidence]:
        self._rate_limiter.wait()
        requested_at = datetime.now(UTC)
        try:
            response = self._client.get(path, params=params)
        except httpx.TransportError as exc:
            responded_at = datetime.now(UTC)
            error_type = type(exc).__name__
            transport_error_message = str(exc) or error_type
            evidence = FetchEvidence(
                endpoint=endpoint,
                request_parameters=dict(params),
                requested_at=requested_at,
                responded_at=responded_at,
                response_status=None,
                response_payload=None,
                error_type=error_type,
                error_message=transport_error_message,
            )
            raise UpbitApiError(
                status_code=None,
                message=transport_error_message,
                evidence=evidence,
                error_type=error_type,
            ) from exc
        responded_at = datetime.now(UTC)
        self._rate_limiter.observe_remaining_req(response.headers.get("Remaining-Req"))
        response_payload = _raw_response_payload(response)
        error_message = _response_error_message(response) if response.status_code >= 400 else None
        evidence = FetchEvidence(
            endpoint=endpoint,
            request_parameters=dict(params),
            requested_at=requested_at,
            responded_at=responded_at,
            response_status=response.status_code,
            response_payload=response_payload,
            error_type=("HTTPStatusError" if response.status_code >= 400 else None),
            error_message=error_message,
        )
        if response.status_code < 400:
            if not isinstance(response_payload, list):
                failed_evidence = replace(
                    evidence,
                    error_type="UpbitResponseShapeError",
                    error_message="업비트 목록 응답이 JSON 배열이 아니다.",
                )
                raise UpbitResponseShapeError(
                    "업비트 목록 응답이 JSON 배열이 아니다.", evidence=failed_evidence
                )
            return list(response_payload), evidence
        retry_after_seconds = _api_retry_delay(response)
        raise UpbitApiError(
            status_code=response.status_code,
            message=error_message or f"Upbit API returned {response.status_code}",
            retry_after_seconds=retry_after_seconds,
            evidence=evidence,
            error_type="HTTPStatusError",
        )


def _market_event(item: dict[str, Any]) -> dict[str, object]:
    event = item.get("market_event")
    if not isinstance(event, dict):
        raise ValueError("상세 시장 응답에 market_event가 없다.")
    if not isinstance(event.get("warning"), bool) or not isinstance(event.get("caution"), dict):
        raise ValueError("market_event.warning/caution 형식이 올바르지 않다.")
    caution = event["caution"]
    if not all(isinstance(key, str) and isinstance(value, bool) for key, value in caution.items()):
        raise ValueError("market_event.caution 값은 boolean이어야 한다.")
    return cast(dict[str, object], event)


def _market_warning(item: dict[str, Any]) -> str:
    event = _market_event(item)
    if bool(event["warning"]):
        return "WARNING"
    caution = cast(dict[str, bool], event["caution"])
    return "CAUTION" if any(caution.values()) else "NONE"


def _parse_upbit_candle_time(value: object) -> datetime:
    return datetime.fromisoformat(str(value)).replace(tzinfo=UTC).astimezone(KST)


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


def _raw_response_payload(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return response.text


def _api_retry_delay(response: httpx.Response) -> float | None:
    if response.status_code == 418:
        return _retry_delay(response, None)
    if response.status_code == 429:
        return _retry_delay(response, 1.0)
    return None


def _retry_delay(response: httpx.Response, default_seconds: float | None) -> float | None:
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
