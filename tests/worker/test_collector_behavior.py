from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from goodmoneying_shared.models import SourceCandle
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_worker.collector import UpbitCollectionWorker
from goodmoneying_worker.main import create_upbit_client_from_environment
from goodmoneying_worker.upbit_client import (
    FixtureUpbitClient,
    LiveUpbitClient,
    UpbitApiError,
    UpbitRateLimiter,
    _retry_delay,
)


def test_fixture_worker_collects_m1_market_data() -> None:
    repository = SQLiteOperationsRepository()
    worker = UpbitCollectionWorker(repository, FixtureUpbitClient())

    worker.refresh_candidate_universe()
    written = worker.collect_incremental()

    active_targets = repository.list_active_targets()
    assert len(active_targets) == 50
    assert written > 50
    assert len(repository.market_list()) == 50
    assert repository.latest_ticker(active_targets[0].id) is not None
    assert repository.latest_orderbook(active_targets[0].id) is not None
    assert repository.collection_runs(limit=10)[0].status == "succeeded"


def test_candidate_refresh_replaces_stale_fixture_targets_with_latest_top_50() -> None:
    repository = SQLiteOperationsRepository()

    UpbitCollectionWorker(repository, FixtureUpbitClient()).refresh_candidate_universe()
    live_markets = [f"KRW-LIVE{index:03d}" for index in range(1, 101)]

    UpbitCollectionWorker(repository, RankedTickerClient(live_markets)).refresh_candidate_universe()

    active_market_codes = [item.market_code for item in repository.list_active_targets()]
    assert active_market_codes == live_markets[:50]


def test_worker_uses_fixture_client_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOODMONEYING_LIVE_UPBIT", raising=False)

    client = create_upbit_client_from_environment()

    assert isinstance(client, FixtureUpbitClient)


def test_worker_uses_live_client_when_live_profile_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOODMONEYING_LIVE_UPBIT", "1")

    client = create_upbit_client_from_environment()

    assert isinstance(client, LiveUpbitClient)


def test_live_client_fetches_historical_minute_candles_with_to_pagination() -> None:
    calls: list[httpx.Request] = []
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    end_at = datetime(2026, 1, 1, 0, 4, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            payload = [
                _upbit_candle("KRW-BTC", "2026-01-01T00:03:00", "103"),
                _upbit_candle("KRW-BTC", "2026-01-01T00:02:00", "102"),
            ]
        else:
            payload = [
                _upbit_candle("KRW-BTC", "2026-01-01T00:01:00", "101"),
                _upbit_candle("KRW-BTC", "2026-01-01T00:00:00", "100"),
            ]
        return httpx.Response(200, json=payload, headers={"Remaining-Req": "group=candle; sec=9"})

    client = LiveUpbitClient(
        http_client=httpx.Client(
            base_url=LiveUpbitClient.BASE_URL,
            transport=httpx.MockTransport(handler),
        ),
        min_request_interval_seconds=0,
    )

    rows = client.fetch_minute_candles("KRW-BTC", start_at, end_at)

    assert [row["candle_start_at"] for row in rows] == [
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:01:00+00:00",
        "2026-01-01T00:02:00+00:00",
        "2026-01-01T00:03:00+00:00",
    ]
    assert calls[0].url.params["market"] == "KRW-BTC"
    assert calls[0].url.params["count"] == "200"
    assert calls[0].url.params["to"].startswith("2026-01-01T00:04:00")
    assert calls[1].url.params["to"].startswith("2026-01-01T00:02:00")


def test_live_client_retries_429_before_succeeding() -> None:
    calls = 0
    start_at = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": {"message": "too many requests"}})
        return httpx.Response(
            200,
            json=[_upbit_candle("KRW-BTC", "2026-01-01T00:01:00", "101")],
            headers={"Remaining-Req": "group=candle; sec=9"},
        )

    client = LiveUpbitClient(
        http_client=httpx.Client(
            base_url=LiveUpbitClient.BASE_URL,
            transport=httpx.MockTransport(handler),
        ),
        min_request_interval_seconds=0,
        retry_sleep_seconds=0,
        max_retries=1,
    )

    rows = client.fetch_minute_candles("KRW-BTC", start_at, end_at)

    assert calls == 2
    assert rows[0]["close_price"] == "101.0"


def test_rate_limiter_waits_when_remaining_req_second_quota_is_exhausted() -> None:
    current_time = 10.0
    sleeps: list[float] = []

    def monotonic() -> float:
        return current_time

    def sleep(seconds: float) -> None:
        nonlocal current_time
        sleeps.append(seconds)
        current_time += seconds

    limiter = UpbitRateLimiter(min_interval_seconds=0, monotonic=monotonic, sleep=sleep)

    limiter.observe_remaining_req("group=candle; min=1800; sec=0")
    limiter.wait()

    assert sleeps == [1.0]


def test_retry_delay_uses_418_block_duration_message() -> None:
    response = httpx.Response(
        418,
        json={"error": {"message": "요청 수 제한으로 3초 동안 차단됩니다."}},
    )

    assert _retry_delay(response, default_seconds=1) == 3


def test_live_client_raises_api_error_after_retry_exhaustion() -> None:
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
    client = LiveUpbitClient(
        http_client=httpx.Client(
            base_url=LiveUpbitClient.BASE_URL,
            transport=httpx.MockTransport(lambda request: httpx.Response(429)),
        ),
        min_request_interval_seconds=0,
        retry_sleep_seconds=0,
        max_retries=0,
    )

    with pytest.raises(UpbitApiError):
        client.fetch_minute_candles("KRW-BTC", start_at, end_at)


def test_worker_runs_approved_backfill_job_and_records_progress() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
    client = BackfillOnlyClient(
        [
            _worker_candle(instrument.id, start_at, "100"),
            _worker_candle(instrument.id, start_at + timedelta(minutes=1), "101"),
        ]
    )
    worker = UpbitCollectionWorker(repository, client)
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    repository.approve_backfill_job(plan.plan_id)

    written = worker.run_backfill_once()

    assert written == 2
    assert repository.backfill_jobs()[0].status == "succeeded"
    assert repository.backfill_jobs()[0].progress_percent == 100
    assert len(repository.candles(instrument.id, "1m", start_at, end_at)) == 2


def test_worker_starts_backfill_from_first_missing_candle_after_existing_start() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    end_at = datetime(2026, 1, 1, 0, 4, tzinfo=UTC)
    repository.record_incremental_collection(
        [],
        [],
        [
            _worker_candle(instrument.id, start_at, "100"),
            _worker_candle(instrument.id, start_at + timedelta(minutes=1), "101"),
            _worker_candle(instrument.id, start_at + timedelta(minutes=3), "103"),
        ],
    )
    client = BackfillOnlyClient(
        [
            _worker_candle(instrument.id, start_at + timedelta(minutes=2), "102"),
        ]
    )
    worker = UpbitCollectionWorker(repository, client)
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    repository.approve_backfill_job(plan.plan_id)

    written = worker.run_backfill_once()

    assert written == 1
    assert client.requests == [
        ("KRW-BTC", start_at + timedelta(minutes=2), start_at + timedelta(minutes=3))
    ]
    assert len(repository.candles(instrument.id, "1m", start_at, end_at)) == 4


def test_worker_records_failed_backfill_target_when_client_fails() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
    worker = UpbitCollectionWorker(repository, FailingBackfillClient())
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    repository.approve_backfill_job(plan.plan_id)

    written = worker.run_backfill_once()

    targets = repository.backfill_job_targets(repository.backfill_jobs()[0].id)
    assert written == 0
    assert repository.backfill_jobs()[0].status == "failed"
    assert targets[0].status == "failed"
    assert targets[0].error_code == "UpbitApiError"


def test_worker_stops_before_next_target_when_job_is_stopped() -> None:
    repository = SQLiteOperationsRepository()
    btc = repository.upsert_instrument("KRW-BTC", "비트코인")
    eth = repository.upsert_instrument("KRW-ETH", "이더리움")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [btc.id, eth.id])
    job = repository.approve_backfill_job(plan.plan_id)
    client = StoppingBackfillClient(
        {
            "KRW-BTC": [_worker_candle(btc.id, start_at, "100")],
            "KRW-ETH": [_worker_candle(eth.id, start_at, "200")],
        },
        on_first_fetch=lambda: repository.control_backfill_job(job.id, "stop"),
    )
    worker = UpbitCollectionWorker(repository, client)

    written = worker.run_backfill_once()

    targets = repository.backfill_job_targets(job.id)
    assert written == 1
    assert client.fetch_count == 1
    assert repository.backfill_jobs()[0].status == "stopped"
    assert [target.status for target in targets] == ["succeeded", "pending"]


def _upbit_candle(market: str, candle_time_utc: str, close: str) -> dict[str, object]:
    close_number = float(close)
    return {
        "market": market,
        "candle_date_time_utc": candle_time_utc,
        "opening_price": close_number - 1,
        "high_price": close_number + 2,
        "low_price": close_number - 2,
        "trade_price": close_number,
        "candle_acc_trade_volume": 1.5,
        "candle_acc_trade_price": close_number * 1.5,
    }


def _worker_candle(instrument_id: int, candle_start_at: datetime, close: str) -> SourceCandle:
    close_decimal = Decimal(close)
    return SourceCandle(
        instrument_id=instrument_id,
        candle_unit="1m",
        candle_start_at=candle_start_at,
        open_price=close_decimal,
        high_price=close_decimal,
        low_price=close_decimal,
        close_price=close_decimal,
        trade_volume=Decimal("1"),
        trade_amount=close_decimal,
        collected_at=candle_start_at,
    )


class BackfillOnlyClient(FixtureUpbitClient):
    def __init__(self, candles: list[SourceCandle]) -> None:
        super().__init__(market_count=1)
        self._candles = candles
        self.requests: list[tuple[str, datetime, datetime]] = []

    def fetch_minute_candles(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> list[dict[str, str]]:
        self.requests.append((market, start_at, end_at))
        return [
            {
                "market": market,
                "candle_unit": item.candle_unit,
                "candle_start_at": item.candle_start_at.isoformat(),
                "open_price": str(item.open_price),
                "high_price": str(item.high_price),
                "low_price": str(item.low_price),
                "close_price": str(item.close_price),
                "trade_volume": str(item.trade_volume),
                "trade_amount": str(item.trade_amount),
            }
            for item in self._candles
            if start_at <= item.candle_start_at < end_at
        ]


class RankedTickerClient(FixtureUpbitClient):
    def __init__(self, markets: list[str]) -> None:
        super().__init__(market_count=1)
        self._markets = markets

    def get_krw_tickers(self) -> list[dict[str, str]]:
        return [
            {
                "market": market,
                "display_name": market,
                "trade_price": "1000",
                "acc_trade_price_24h": str(1_000_000_000 - index),
                "signed_change_rate": "0.01",
            }
            for index, market in enumerate(self._markets)
        ]


class StoppingBackfillClient(BackfillOnlyClient):
    def __init__(
        self,
        candles_by_market: dict[str, list[SourceCandle]],
        on_first_fetch: Callable[[], object],
    ) -> None:
        super().__init__([])
        self._candles_by_market = candles_by_market
        self._on_first_fetch = on_first_fetch
        self.fetch_count = 0

    def fetch_minute_candles(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> list[dict[str, str]]:
        self.fetch_count += 1
        if self.fetch_count == 1:
            self._on_first_fetch()
        return [
            {
                "market": market,
                "candle_unit": item.candle_unit,
                "candle_start_at": item.candle_start_at.isoformat(),
                "open_price": str(item.open_price),
                "high_price": str(item.high_price),
                "low_price": str(item.low_price),
                "close_price": str(item.close_price),
                "trade_volume": str(item.trade_volume),
                "trade_amount": str(item.trade_amount),
            }
            for item in self._candles_by_market[market]
            if start_at <= item.candle_start_at < end_at
        ]


class FailingBackfillClient(FixtureUpbitClient):
    def fetch_minute_candles(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> list[dict[str, str]]:
        raise UpbitApiError(status_code=429, message="too many requests")
