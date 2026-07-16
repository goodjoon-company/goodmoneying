from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from goodmoneying_shared.models import FetchedCandlePage, FetchEvidence, SourceCandle
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_shared.time import KST
from goodmoneying_worker.collector import UpbitCollectionWorker
from goodmoneying_worker.runtime import create_upbit_client_from_environment
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
    market_rows = repository.market_list()
    assert len(market_rows) == 100
    assert sum(1 for row in market_rows if row.is_favorite) == 50
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


def test_worker_rejects_implicit_fixture_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOODMONEYING_LIVE_UPBIT", raising=False)

    with pytest.raises(RuntimeError, match="GOODMONEYING_LIVE_UPBIT=1"):
        create_upbit_client_from_environment()


def test_worker_uses_live_client_when_live_profile_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOODMONEYING_LIVE_UPBIT", "1")

    client = create_upbit_client_from_environment()

    assert isinstance(client, LiveUpbitClient)


def test_live_client_fetches_historical_minute_candles_with_to_pagination() -> None:
    calls: list[httpx.Request] = []
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 4, tzinfo=KST)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            payload = [
                _upbit_candle("KRW-BTC", "2025-12-31T15:03:00", "103"),
                _upbit_candle("KRW-BTC", "2025-12-31T15:02:00", "102"),
            ]
        else:
            payload = [
                _upbit_candle("KRW-BTC", "2025-12-31T15:01:00", "101"),
                _upbit_candle("KRW-BTC", "2025-12-31T15:00:00", "100"),
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
        "2026-01-01T00:00:00+09:00",
        "2026-01-01T00:01:00+09:00",
        "2026-01-01T00:02:00+09:00",
        "2026-01-01T00:03:00+09:00",
    ]
    assert calls[0].url.params["market"] == "KRW-BTC"
    assert calls[0].url.params["count"] == "200"
    assert calls[0].url.params["to"].startswith("2025-12-31T15:04:00")
    assert calls[1].url.params["to"].startswith("2025-12-31T15:02:00")


def test_live_client_does_not_retry_429_automatically() -> None:
    calls = 0
    start_at = datetime(2026, 1, 1, 0, 1, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=KST)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": {"message": "too many requests"}})
        return httpx.Response(
            200,
            json=[_upbit_candle("KRW-BTC", "2025-12-31T15:01:00", "101")],
            headers={"Remaining-Req": "group=candle; sec=9"},
        )

    client = LiveUpbitClient(
        http_client=httpx.Client(
            base_url=LiveUpbitClient.BASE_URL,
            transport=httpx.MockTransport(handler),
        ),
        min_request_interval_seconds=0,
    )

    with pytest.raises(UpbitApiError) as captured:
        client.fetch_minute_candles("KRW-BTC", start_at, end_at)

    assert calls == 1
    assert captured.value.status_code == 429


def test_live_client_does_not_retry_418_automatically() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            418,
            json={"error": {"message": "요청 수 제한으로 3초 동안 차단됩니다."}},
        )

    client = LiveUpbitClient(
        http_client=httpx.Client(
            base_url=LiveUpbitClient.BASE_URL,
            transport=httpx.MockTransport(handler),
        ),
        min_request_interval_seconds=0,
    )

    with pytest.raises(UpbitApiError) as captured:
        client.get_krw_tickers()

    assert calls == 1
    assert captured.value.status_code == 418
    assert captured.value.retry_after_seconds == 3


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


def test_live_client_raises_api_error_without_hidden_retry() -> None:
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=KST)
    client = LiveUpbitClient(
        http_client=httpx.Client(
            base_url=LiveUpbitClient.BASE_URL,
            transport=httpx.MockTransport(lambda request: httpx.Response(429)),
        ),
        min_request_interval_seconds=0,
    )

    with pytest.raises(UpbitApiError):
        client.fetch_minute_candles("KRW-BTC", start_at, end_at)


def test_live_client_reads_detailed_market_catalog_without_ticker_filtering() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "market": "KRW-BTC",
                    "korean_name": "비트코인",
                    "english_name": "Bitcoin",
                    "market_warning": "NONE",
                    "market_event": {"trading_suspended": False},
                },
                {
                    "market": "BTC-ETH",
                    "korean_name": "이더리움",
                    "english_name": "Ethereum",
                    "market_warning": "CAUTION",
                    "market_event": {"trading_suspended": True},
                },
            ],
        )

    client = LiveUpbitClient(
        http_client=httpx.Client(
            base_url=LiveUpbitClient.BASE_URL,
            transport=httpx.MockTransport(handler),
        ),
        min_request_interval_seconds=0,
    )

    catalog = client.get_market_catalog()

    assert [item.market_code for item in catalog] == ["KRW-BTC", "BTC-ETH"]
    assert catalog[0].korean_name == "비트코인"
    assert catalog[1].market_warning == "CAUTION"
    assert not catalog[1].tradable
    assert requests[0].url.path == "/v1/market/all"
    assert requests[0].url.params["is_details"] == "true"


def test_market_catalog_rejects_missing_market_event_details() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "market": "KRW-BTC",
                    "korean_name": "비트코인",
                    "english_name": "Bitcoin",
                    "market_warning": "NONE",
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

    with pytest.raises(ValueError, match="market_event"):
        client.get_market_catalog()


def test_worker_runs_approved_backfill_job_and_records_progress() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=KST)
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


def test_worker_reports_backfill_progress_during_long_job() -> None:
    repository = SQLiteOperationsRepository()
    instruments = [
        repository.upsert_instrument("KRW-BTC", "비트코인"),
        repository.upsert_instrument("KRW-ETH", "이더리움"),
    ]
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=KST)
    client = BackfillOnlyClient(
        [
            _worker_candle(instrument.id, start_at, "100")
            for instrument in instruments
        ]
    )
    worker = UpbitCollectionWorker(repository, client)
    plan = repository.create_backfill_plan(
        "source_candle",
        start_at,
        end_at,
        [instrument.id for instrument in instruments],
    )
    repository.approve_backfill_job(plan.plan_id)
    progress_events: list[None] = []

    worker.run_backfill_once(on_progress=lambda: progress_events.append(None))

    assert len(progress_events) >= 4


def test_worker_marks_backfill_target_running_before_fetch() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=KST)
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    job = repository.approve_backfill_job(plan.plan_id)

    def assert_target_is_running() -> None:
        assert repository.backfill_job_targets(job.id)[0].status == "running"

    client = StoppingBackfillClient(
        {
            "KRW-BTC": [
                _worker_candle(instrument.id, start_at, "100"),
            ]
        },
        on_first_fetch=assert_target_is_running,
    )
    worker = UpbitCollectionWorker(repository, client)

    worker.run_backfill_once()

    assert repository.backfill_job_targets(job.id)[0].status == "succeeded"


def test_worker_starts_backfill_from_first_missing_candle_after_existing_start() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 4, tzinfo=KST)
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


def test_worker_requests_only_missing_candle_ranges_between_existing_rows() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 8, tzinfo=KST)
    repository.record_incremental_collection(
        [],
        [],
        [
            _worker_candle(instrument.id, start_at, "100"),
            _worker_candle(instrument.id, start_at + timedelta(minutes=1), "101"),
            _worker_candle(instrument.id, start_at + timedelta(minutes=4), "104"),
            _worker_candle(instrument.id, start_at + timedelta(minutes=5), "105"),
            _worker_candle(instrument.id, start_at + timedelta(minutes=7), "107"),
        ],
    )
    client = BackfillOnlyClient(
        [
            _worker_candle(instrument.id, start_at + timedelta(minutes=2), "102"),
            _worker_candle(instrument.id, start_at + timedelta(minutes=3), "103"),
            _worker_candle(instrument.id, start_at + timedelta(minutes=6), "106"),
        ]
    )
    worker = UpbitCollectionWorker(repository, client)
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    repository.approve_backfill_job(plan.plan_id)

    written = worker.run_backfill_once()

    assert written == 3
    assert client.requests == [
        ("KRW-BTC", start_at + timedelta(minutes=2), start_at + timedelta(minutes=4)),
        ("KRW-BTC", start_at + timedelta(minutes=6), start_at + timedelta(minutes=7)),
    ]
    assert len(repository.candles(instrument.id, "1m", start_at, end_at)) == 8


def test_worker_flushes_large_missing_range_in_configured_batches() -> None:
    repository = CountingBackfillRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 8, tzinfo=KST)
    client = BackfillOnlyClient(
        [
            _worker_candle(instrument.id, start_at + timedelta(minutes=minute), str(100 + minute))
            for minute in range(8)
        ]
    )
    worker = UpbitCollectionWorker(repository, client, backfill_batch_size=3)
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    repository.approve_backfill_job(plan.plan_id)

    written = worker.run_backfill_once()

    assert written == 8
    assert repository.backfill_batch_sizes == [3, 3, 2]
    target = repository.backfill_job_targets(repository.backfill_jobs()[0].id)[0]
    target_progress = repository.backfill_target_progress(target.job_id, target.instrument_id)
    assert target_progress["rows_written_count"] == 8
    assert target.last_completed_at == start_at + timedelta(minutes=7)


def test_worker_keeps_page_evidence_when_page_is_split_into_storage_batches() -> None:
    repository = EvidenceRecordingRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 3, tzinfo=KST)
    evidence = _test_fetch_evidence("page-1")
    rows = [
        _worker_candle_row(start_at + timedelta(minutes=offset), str(100 + offset))
        for offset in range(3)
    ]
    client = EvidencePageClient([FetchedCandlePage(rows=rows, evidence=evidence)])
    worker = UpbitCollectionWorker(repository, client, backfill_batch_size=2)
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    repository.approve_backfill_job(plan.plan_id)

    assert worker.run_backfill_once() == 3
    assert repository.recorded_pages == [(2, evidence), (1, evidence)]


def test_worker_forwards_failed_request_evidence_to_repository() -> None:
    repository = EvidenceRecordingRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 1, tzinfo=KST)
    evidence = _test_fetch_evidence("failed-page", response_status=429)
    error = UpbitApiError(429, "요청 수 제한")
    error.evidence = evidence
    worker = UpbitCollectionWorker(repository, FailedEvidenceClient(error))
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    repository.approve_backfill_job(plan.plan_id)

    assert worker.run_backfill_once() == 0
    assert repository.failed_evidence is evidence


def test_worker_uses_transport_error_type_in_failed_target_error_code() -> None:
    repository = EvidenceRecordingRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 1, tzinfo=KST)
    requested_at = datetime(2026, 1, 1, tzinfo=UTC)
    evidence = FetchEvidence(
        endpoint="/v1/candles/minutes/1",
        request_parameters={
            "market": "KRW-BTC",
            "to": "2025-12-31T15:01:00Z",
            "count": 200,
        },
        requested_at=requested_at,
        responded_at=requested_at + timedelta(milliseconds=1),
        response_status=None,
        response_payload=None,
        error_type="ReadTimeout",
        error_message="upstream timed out",
    )
    error = UpbitApiError(None, "upstream timed out", evidence=evidence)
    worker = UpbitCollectionWorker(repository, FailedEvidenceClient(error))
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    repository.approve_backfill_job(plan.plan_id)

    assert worker.run_backfill_once() == 0
    assert repository.failed_evidence is evidence
    assert repository.failed_error_code == "UPBIT_ReadTimeout"


def test_worker_records_heartbeat_after_fetch_before_batch_progress_changes() -> None:
    repository = CountingBackfillRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=KST)
    client = BackfillOnlyClient(
        [
            _worker_candle(instrument.id, start_at, "100"),
            _worker_candle(instrument.id, start_at + timedelta(minutes=1), "101"),
        ]
    )
    worker = UpbitCollectionWorker(repository, client, backfill_batch_size=10)
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    job = repository.approve_backfill_job(plan.plan_id)
    progress_snapshots: list[tuple[int, bool, int, datetime | None]] = []

    def record_progress() -> None:
        target = repository.backfill_job_targets(job.id)[0]
        target_progress = repository.backfill_target_progress(job.id, target.instrument_id)
        progress_snapshots.append(
            (
                len(client.requests),
                repository.upsert_in_progress,
                target_progress["rows_written_count"],
                target.last_completed_at,
            )
        )

    worker.run_backfill_once(on_progress=record_progress)

    assert (1, False, 0, None) in progress_snapshots
    assert (1, True, 0, None) not in progress_snapshots


def test_worker_records_progress_when_missing_range_returns_no_rows() -> None:
    repository = CountingBackfillRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=KST)
    client = NoYieldBackfillClient()
    worker = UpbitCollectionWorker(repository, client)
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    job = repository.approve_backfill_job(plan.plan_id)
    progress_snapshots: list[tuple[int, int, int]] = []

    def record_progress() -> None:
        target_progress = repository.backfill_target_progress(job.id, instrument.id)
        progress_snapshots.append(
            (
                len(client.requests),
                target_progress["processed_missing_range_count"],
                target_progress["estimated_missing_range_count"],
            )
        )

    written = worker.run_backfill_once(on_progress=record_progress)

    target = repository.backfill_job_targets(job.id)[0]
    target_progress = repository.backfill_target_progress(job.id, instrument.id)
    assert written == 0
    assert target.status == "succeeded"
    assert target.last_completed_at is None
    assert client.requests == [("KRW-BTC", start_at, end_at)]
    assert target_progress["rows_written_count"] == 0
    assert target_progress["processed_missing_range_count"] == 1
    assert target_progress["estimated_missing_range_count"] == 1
    assert (1, 1, 1) in progress_snapshots


def test_worker_does_not_advance_last_completed_at_when_fetch_returns_no_rows() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=KST)
    worker = UpbitCollectionWorker(repository, BackfillOnlyClient([]))
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    job = repository.approve_backfill_job(plan.plan_id)

    written = worker.run_backfill_once()

    target = repository.backfill_job_targets(job.id)[0]
    assert written == 0
    assert target.last_completed_at is None


@pytest.mark.parametrize("action, expected_status", [("stop", "stopped"), ("pause", "paused")])
def test_worker_honors_job_control_between_batches(
    action: str,
    expected_status: str,
) -> None:
    repository = ControllingBackfillRepository(action)
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 5, tzinfo=KST)
    client = BackfillOnlyClient(
        [
            _worker_candle(instrument.id, start_at + timedelta(minutes=minute), str(100 + minute))
            for minute in range(5)
        ]
    )
    worker = UpbitCollectionWorker(repository, client, backfill_batch_size=2)
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    repository.approve_backfill_job(plan.plan_id)

    written = worker.run_backfill_once()

    job = repository.backfill_jobs()[0]
    target = repository.backfill_job_targets(job.id)[0]
    target_progress = repository.backfill_target_progress(job.id, target.instrument_id)
    assert written == 2
    assert job.status == expected_status
    assert target_progress["rows_written_count"] == 2
    assert target.last_completed_at == start_at + timedelta(minutes=1)
    assert len(repository.candles(instrument.id, "1m", start_at, end_at)) == 2


def test_worker_records_failed_backfill_target_when_client_fails() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=KST)
    worker = UpbitCollectionWorker(repository, FailingBackfillClient())
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    repository.approve_backfill_job(plan.plan_id)

    written = worker.run_backfill_once()

    targets = repository.backfill_job_targets(repository.backfill_jobs()[0].id)
    assert written == 0
    assert repository.backfill_jobs()[0].status == "failed"
    assert targets[0].status == "failed"
    assert targets[0].error_code == "UPBIT_429"


def test_worker_resumes_failed_backfill_with_only_missing_rows() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 4, tzinfo=KST)
    repository.record_incremental_collection(
        [],
        [],
        [
            _worker_candle(instrument.id, start_at, "100"),
            _worker_candle(instrument.id, start_at + timedelta(minutes=2), "102"),
        ],
    )
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    job = repository.approve_backfill_job(plan.plan_id)
    repository.claim_next_backfill_job()
    repository.mark_backfill_target(
        job.id,
        instrument.id,
        "failed",
        start_at,
        "UpbitBackfillError",
        "백필 캔들 조회 실패",
    )
    repository.control_backfill_job(job.id, "resume")
    client = BackfillOnlyClient(
        [
            _worker_candle(instrument.id, start_at + timedelta(minutes=1), "101"),
            _worker_candle(instrument.id, start_at + timedelta(minutes=3), "103"),
        ]
    )
    worker = UpbitCollectionWorker(repository, client)

    written = worker.run_backfill_once()

    assert written == 2
    assert client.requests == [
        ("KRW-BTC", start_at + timedelta(minutes=1), start_at + timedelta(minutes=2)),
        ("KRW-BTC", start_at + timedelta(minutes=3), end_at),
    ]
    assert repository.backfill_jobs()[0].status == "succeeded"
    assert len(repository.candles(instrument.id, "1m", start_at, end_at)) == 4


def test_backfill_worker_emits_debuggable_progress_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=KST)
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    repository.approve_backfill_job(plan.plan_id)
    worker = UpbitCollectionWorker(
        repository,
        BackfillOnlyClient(
            [
                _worker_candle(instrument.id, start_at, "100"),
                _worker_candle(instrument.id, start_at + timedelta(minutes=1), "101"),
            ]
        ),
        backfill_batch_size=1,
    )
    caplog.set_level(logging.DEBUG, logger="goodmoneying_worker.collector")

    worker.run_backfill_once()

    messages = [record.getMessage() for record in caplog.records]
    assert any("backfill_job_claimed job_id=" in message for message in messages)
    assert any(
        "backfill_missing_ranges job_id=" in message
        and "market=KRW-BTC" in message
        and "range_count=1" in message
        for message in messages
    )
    assert any(
        "backfill_fetch_succeeded job_id=" in message
        and "market=KRW-BTC" in message
        and "row_count=2" in message
        for message in messages
    )
    assert any(
        "backfill_batch_upserted job_id=" in message
        and "rows_written=1" in message
        for message in messages
    )


def test_worker_stops_before_next_target_when_job_is_stopped() -> None:
    repository = SQLiteOperationsRepository()
    btc = repository.upsert_instrument("KRW-BTC", "비트코인")
    eth = repository.upsert_instrument("KRW-ETH", "이더리움")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=KST)
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


@pytest.mark.parametrize("action, expected_status", [("stop", "stopped"), ("pause", "paused")])
def test_worker_claims_next_pending_job_after_current_job_is_controlled(
    action: str,
    expected_status: str,
) -> None:
    repository = SQLiteOperationsRepository()
    btc = repository.upsert_instrument("KRW-BTC", "비트코인")
    eth = repository.upsert_instrument("KRW-ETH", "이더리움")
    xrp = repository.upsert_instrument("KRW-XRP", "리플")
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 2, tzinfo=KST)
    first_plan = repository.create_backfill_plan(
        "source_candle",
        start_at,
        end_at,
        [btc.id, eth.id],
    )
    first_job = repository.approve_backfill_job(first_plan.plan_id)
    second_plan = repository.create_backfill_plan("source_candle", start_at, end_at, [xrp.id])
    second_job = repository.approve_backfill_job(second_plan.plan_id)
    client = StoppingBackfillClient(
        {
            "KRW-BTC": [_worker_candle(btc.id, start_at, "100")],
            "KRW-ETH": [_worker_candle(eth.id, start_at, "200")],
            "KRW-XRP": [_worker_candle(xrp.id, start_at, "300")],
        },
        on_first_fetch=lambda: repository.control_backfill_job(first_job.id, action),
    )
    worker = UpbitCollectionWorker(repository, client)

    written = worker.run_backfill_once()

    jobs_by_id = {job.id: job for job in repository.backfill_jobs()}
    assert written == 2
    assert client.fetch_count == 2
    assert jobs_by_id[first_job.id].status == expected_status
    assert jobs_by_id[second_job.id].status == "succeeded"


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


def _worker_candle_row(candle_start_at: datetime, close: str) -> dict[str, str]:
    return {
        "market": "KRW-BTC",
        "candle_unit": "1m",
        "candle_start_at": candle_start_at.isoformat(),
        "open_price": close,
        "high_price": close,
        "low_price": close,
        "close_price": close,
        "trade_volume": "1",
        "trade_amount": close,
    }


def _test_fetch_evidence(key: str, *, response_status: int = 200) -> FetchEvidence:
    requested_at = datetime(2026, 1, 1, tzinfo=UTC)
    return FetchEvidence(
        endpoint="/v1/candles/minutes/1",
        request_parameters={"market": "KRW-BTC", "to": key, "count": 200},
        requested_at=requested_at,
        responded_at=requested_at + timedelta(milliseconds=1),
        response_status=response_status,
        response_payload=[] if response_status == 200 else {"error": key},
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

    def fetch_minute_candle_pages(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> list[list[dict[str, str]]]:
        return [self.fetch_minute_candles(market, start_at, end_at)]


class NoYieldBackfillClient(FixtureUpbitClient):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[tuple[str, datetime, datetime]] = []

    def fetch_minute_candle_pages(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> list[list[dict[str, str]]]:
        self.requests.append((market, start_at, end_at))
        return []


class EvidencePageClient(FixtureUpbitClient):
    def __init__(self, pages: list[FetchedCandlePage]) -> None:
        super().__init__(market_count=1)
        self._pages = pages

    def fetch_minute_candle_pages(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> list[FetchedCandlePage]:
        del market, start_at, end_at
        return self._pages


class FailedEvidenceClient(FixtureUpbitClient):
    def __init__(self, error: UpbitApiError) -> None:
        super().__init__(market_count=1)
        self._error = error

    def fetch_minute_candle_pages(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> list[list[dict[str, str]]]:
        del market, start_at, end_at
        raise self._error


class CountingBackfillRepository(SQLiteOperationsRepository):
    def __init__(self) -> None:
        super().__init__()
        self.backfill_batch_sizes: list[int] = []
        self.upsert_in_progress = False

    def record_backfill_candles(
        self,
        job_id: int,
        instrument_id: int,
        candles: list[SourceCandle],
        *,
        fetch_evidence: object | None = None,
    ) -> int:
        self.backfill_batch_sizes.append(len(candles))
        self.upsert_in_progress = True
        try:
            return super().record_backfill_candles(
                job_id, instrument_id, candles, fetch_evidence=fetch_evidence
            )
        finally:
            self.upsert_in_progress = False

    def backfill_target_progress(self, job_id: int, instrument_id: int) -> dict[str, int]:
        row = self._execute(
            """
            SELECT rows_written_count, processed_missing_range_count, estimated_missing_range_count
            FROM backfill_job_targets
            WHERE backfill_job_id = ? AND instrument_id = ?
            """,
            (job_id, instrument_id),
        ).fetchone()
        if row is None:
            raise AssertionError("백필 target progress를 찾을 수 없다.")
        return {
            "rows_written_count": int(row["rows_written_count"]),
            "processed_missing_range_count": int(row["processed_missing_range_count"]),
            "estimated_missing_range_count": int(row["estimated_missing_range_count"]),
        }


class EvidenceRecordingRepository(SQLiteOperationsRepository):
    def __init__(self) -> None:
        super().__init__()
        self.recorded_pages: list[tuple[int, object | None]] = []
        self.failed_evidence: object | None = None
        self.failed_error_code: str | None = None

    def record_backfill_candles(
        self,
        job_id: int,
        instrument_id: int,
        candles: list[SourceCandle],
        *,
        fetch_evidence: object | None = None,
    ) -> int:
        self.recorded_pages.append((len(candles), fetch_evidence))
        return super().record_backfill_candles(
            job_id, instrument_id, candles, fetch_evidence=fetch_evidence
        )

    def mark_backfill_target(
        self,
        job_id: int,
        instrument_id: int,
        status: str,
        last_completed_at: datetime | None,
        error_code: str | None = None,
        error_message: str | None = None,
        retry_after_seconds: float | None = None,
        *,
        fetch_evidence: object | None = None,
    ) -> None:
        if status == "failed":
            self.failed_evidence = fetch_evidence
            self.failed_error_code = error_code
        super().mark_backfill_target(
            job_id,
            instrument_id,
            status,
            last_completed_at,
            error_code,
            error_message,
            retry_after_seconds,
            fetch_evidence=fetch_evidence,
        )


class ControllingBackfillRepository(CountingBackfillRepository):
    def __init__(self, action_after_first_batch: str) -> None:
        super().__init__()
        self._action_after_first_batch = action_after_first_batch

    def record_backfill_candles(
        self,
        job_id: int,
        instrument_id: int,
        candles: list[SourceCandle],
        *,
        fetch_evidence: object | None = None,
    ) -> int:
        rows_written = super().record_backfill_candles(
            job_id, instrument_id, candles, fetch_evidence=fetch_evidence
        )
        if len(self.backfill_batch_sizes) == 1:
            self.control_backfill_job(job_id, self._action_after_first_batch)
        return rows_written


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

    def fetch_minute_candle_pages(
        self, market: str, start_at: datetime, end_at: datetime
    ) -> list[list[dict[str, str]]]:
        raise UpbitApiError(status_code=429, message="too many requests")
