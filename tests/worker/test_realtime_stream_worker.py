from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import timedelta
from decimal import Decimal

from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_shared.time import now_kst
from goodmoneying_worker.collector import seed_repository
from goodmoneying_worker.realtime_stream_worker import (
    RealtimeStreamBuffer,
    build_upbit_websocket_subscription,
    run_realtime_stream_collection,
)
from goodmoneying_worker.upbit_client import FixtureUpbitClient


def test_upbit_websocket_subscription_requests_trade_frequency_types_for_markets() -> None:
    request = build_upbit_websocket_subscription(["KRW-BTC", "KRW-ETH"])

    assert request[0]["ticket"]
    assert request[1:] == [
        {
            "type": "ticker",
            "codes": ["KRW-BTC", "KRW-ETH"],
            "is_only_realtime": False,
        },
        {
            "type": "trade",
            "codes": ["KRW-BTC", "KRW-ETH"],
            "is_only_realtime": False,
        },
        {
            "type": "orderbook",
            "codes": ["KRW-BTC", "KRW-ETH"],
            "is_only_realtime": False,
        },
        {
            "type": "candle.1m",
            "codes": ["KRW-BTC", "KRW-ETH"],
            "is_only_realtime": False,
        },
        {"format": "DEFAULT"},
    ]


def test_realtime_stream_buffer_flushes_websocket_messages_to_repository() -> None:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    target = repository.list_active_targets()[0]
    collected_at = now_kst().replace(minute=30, second=0, microsecond=0)
    buffer = RealtimeStreamBuffer({target.market_code: target}, now=lambda: collected_at)

    buffer.apply(
        {
            "type": "ticker",
            "code": target.market_code,
            "trade_price": 100,
            "acc_trade_price_24h": 1000000,
            "signed_change_rate": "0.0123",
        }
    )
    buffer.apply(
        {
            "type": "trade",
            "code": target.market_code,
            "trade_price": 101,
            "trade_volume": "2.5",
            "ask_bid": "BID",
            "trade_timestamp": int(collected_at.timestamp() * 1000),
            "sequential_id": 9001,
        }
    )
    buffer.apply(
        {
            "type": "orderbook",
            "code": target.market_code,
            "orderbook_units": [
                {"ask_price": 102, "bid_price": 100, "ask_size": 1, "bid_size": 2},
                {"ask_price": 103, "bid_price": 99, "ask_size": 3, "bid_size": 4},
            ],
        }
    )
    buffer.apply(
        {
            "type": "candle.1m",
            "code": target.market_code,
            "candle_date_time_kst": collected_at.replace(second=0, microsecond=0).isoformat(),
            "opening_price": 95,
            "high_price": 105,
            "low_price": 94,
            "trade_price": 101,
            "candle_acc_trade_volume": "12.5",
            "candle_acc_trade_price": "1262.5",
        }
    )

    assert buffer.flush(repository) == 4
    ticker = repository.latest_ticker(target.id)
    orderbook = repository.latest_orderbook(target.id)
    assert ticker is not None
    assert orderbook is not None
    assert ticker.trade_price == Decimal("100")
    assert orderbook.spread == Decimal("2")
    candle_start = collected_at.replace(second=0, microsecond=0)
    assert repository.candles(
        target.id,
        "1m",
        candle_start - timedelta(minutes=1),
        collected_at + timedelta(seconds=1),
    )
    heatmap = repository.dashboard_realtime_heatmap()[0]
    assert heatmap.hourly_buckets[-1].trade_count == 1
    assert heatmap.hourly_buckets[-1].trade_amount == Decimal("252.5")


def test_realtime_stream_collection_flushes_buffer_before_propagating_stream_error() -> None:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    target = repository.list_active_targets()[0]

    previous_run_count = len(repository.collection_runs(1000))

    def failing_messages() -> Iterator[Mapping[str, object]]:
        yield {
            "type": "ticker",
            "code": target.market_code,
            "trade_price": 123,
            "acc_trade_price_24h": 1000000,
            "signed_change_rate": "0.01",
        }
        raise RuntimeError("websocket disconnected")

    try:
        run_realtime_stream_collection(
            repository,
            failing_messages(),
            flush_interval_seconds=999,
            now_monotonic=lambda: 0,
        )
    except RuntimeError as exc:
        assert str(exc) == "websocket disconnected"
    else:
        raise AssertionError("웹소켓 스트림 오류가 호출자에게 전파되지 않았습니다.")

    runs = repository.collection_runs(1000)
    assert len(runs) == previous_run_count + 1
    assert runs[0].run_type == "incremental"


def test_realtime_stream_collection_records_running_heartbeat_after_successful_flush() -> None:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    target = repository.list_active_targets()[0]

    run_realtime_stream_collection(
        repository,
        [
            {
                "type": "ticker",
                "code": target.market_code,
                "trade_price": 123,
                "acc_trade_price_24h": 1000000,
                "signed_change_rate": "0.01",
            }
        ],
        flush_interval_seconds=0,
        now_monotonic=lambda: 1,
    )

    worker_status = repository.dashboard_worker_status().realtime
    assert worker_status.status == "running"
    assert worker_status.status_label == "동작 중"
