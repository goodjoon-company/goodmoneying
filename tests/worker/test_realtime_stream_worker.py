from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from goodmoneying_shared import sqlite_repository as sqlite_repository_module
from goodmoneying_shared.data_foundation import CollectionSubscriptionDesire
from goodmoneying_shared.models import RealtimeSourceFrame
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_worker.collector import seed_repository
from goodmoneying_worker.realtime_stream_worker import (
    RealtimeStreamBuffer,
    SubscriptionPlan,
    build_upbit_websocket_subscription,
    load_subscription_plan,
    mark_subscription_plan_applied,
    run_realtime_stream_collection,
    run_realtime_subscription_loop,
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


def test_upbit_websocket_subscription_uses_exact_codes_for_each_selected_type() -> None:
    request = build_upbit_websocket_subscription(
        ["KRW-BTC", "KRW-ETH"],
        market_codes_by_type={
            "trade": ("KRW-BTC", "KRW-ETH"),
            "orderbook": ("KRW-BTC",),
        },
    )

    assert request[1:] == [
        {
            "type": "trade",
            "codes": ["KRW-BTC", "KRW-ETH"],
            "is_only_realtime": False,
        },
        {
            "type": "orderbook",
            "codes": ["KRW-BTC"],
            "is_only_realtime": False,
        },
        {"format": "DEFAULT"},
    ]


def test_realtime_stream_buffer_flushes_websocket_messages_to_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    target = repository.list_active_targets()[0]
    seeded_ticker = repository.latest_ticker(target.id)
    assert seeded_ticker is not None
    collected_at = seeded_ticker.bucket_at.replace(minute=59, second=0, microsecond=0)
    if collected_at <= seeded_ticker.bucket_at:
        collected_at = seeded_ticker.bucket_at + timedelta(minutes=1)
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
    monkeypatch.setattr(
        sqlite_repository_module,
        "now_kst",
        lambda: collected_at + timedelta(seconds=1),
    )
    heatmap = repository.dashboard_realtime_heatmap()[0]
    assert heatmap.hourly_buckets[-1].trade_count == 1
    assert heatmap.hourly_buckets[-1].trade_amount == Decimal("252.5")


def test_realtime_ticker_and_orderbook_use_exchange_timestamp_as_occurred_at() -> None:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    target = repository.list_active_targets()[0]
    seeded_ticker = repository.latest_ticker(target.id)
    assert seeded_ticker is not None
    ticker_occurred_at = (seeded_ticker.received_at + timedelta(minutes=1)).astimezone(UTC)
    ticker_occurred_at = ticker_occurred_at.replace(second=1, microsecond=123000)
    orderbook_occurred_at = ticker_occurred_at.replace(second=2, microsecond=456000)
    received_at = orderbook_occurred_at + timedelta(seconds=1)
    buffer = RealtimeStreamBuffer({target.market_code: target}, now=lambda: received_at)

    buffer.apply(
        {
            "type": "ticker",
            "code": target.market_code,
            "timestamp": int(ticker_occurred_at.timestamp() * 1000),
            "trade_timestamp": int((ticker_occurred_at - timedelta(seconds=1)).timestamp() * 1000),
            "trade_price": 100,
            "acc_trade_price_24h": 1000,
            "signed_change_rate": "0.01",
        }
    )
    buffer.apply(
        {
            "type": "orderbook",
            "code": target.market_code,
            "timestamp": int(orderbook_occurred_at.timestamp() * 1000),
            "orderbook_units": [
                {"ask_price": 101, "bid_price": 99, "ask_size": 1, "bid_size": 2}
            ],
        }
    )
    buffer.flush(repository)

    ticker = repository.latest_ticker(target.id)
    orderbook = repository.latest_orderbook(target.id)
    assert ticker is not None
    assert orderbook is not None
    assert ticker.occurred_at == ticker_occurred_at
    assert ticker.received_at == received_at
    assert ticker.collected_at == received_at
    assert orderbook.occurred_at == orderbook_occurred_at
    assert orderbook.received_at == received_at
    assert orderbook.collected_at == received_at


def test_realtime_stream_preserves_all_orderbook_levels_and_raw_delivery_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    target = repository.list_active_targets()[0]
    occurred_at = datetime(2026, 7, 17, 3, 4, 5, 678000, tzinfo=UTC)
    received_at = occurred_at + timedelta(milliseconds=250)
    captured: list[RealtimeSourceFrame] = []
    original = repository.record_realtime_source_frames

    def capture(frames: list[RealtimeSourceFrame]) -> int:
        captured.extend(frames)
        return original(frames)

    monkeypatch.setattr(repository, "record_realtime_source_frames", capture)
    units = [
        {
            "ask_price": str(1000 + index),
            "ask_size": str(index + 1),
            "bid_price": str(999 - index),
            "bid_size": str((index + 1) * 2),
        }
        for index in range(30)
    ]
    payload = {
        "type": "orderbook",
        "code": target.market_code,
        "timestamp": int(occurred_at.timestamp() * 1000),
        "total_ask_size": "465",
        "total_bid_size": "930",
        "level": 0,
        "stream_type": "REALTIME",
        "orderbook_units": units,
    }
    buffer = RealtimeStreamBuffer({target.market_code: target}, now=lambda: received_at)

    buffer.apply(payload, connection_id="connection-a", frame_sequence=1)
    assert buffer.flush(repository) == 1

    assert len(captured) == 1
    frame = captured[0]
    assert frame.receipt.connection_id == "connection-a"
    assert frame.receipt.frame_sequence == 1
    assert frame.receipt.occurred_at == occurred_at
    assert frame.receipt.received_at == received_at
    assert frame.receipt.raw_payload == payload
    assert len(frame.receipt.payload_checksum) == 64
    assert frame.snapshot is not None
    assert frame.snapshot.level_count == 30
    assert len(frame.snapshot.levels) == 30
    assert frame.snapshot.total_ask_size == Decimal("465")
    assert frame.snapshot.total_bid_size == Decimal("930")
    assert frame.snapshot.level == Decimal("0")
    assert frame.snapshot.stream_type == "REALTIME"
    assert frame.snapshot.levels[-1].level_index == 29
    assert frame.summary is not None
    assert frame.summary.ask_depth_10 == Decimal("55")
    assert frame.summary.bid_depth_10 == Decimal("110")


def test_realtime_collection_numbers_every_frame_and_runs_retention_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    target = repository.list_active_targets()[0]
    frames: list[RealtimeSourceFrame] = []
    cleanup_calls: list[bool] = []
    original = repository.record_realtime_source_frames

    def capture(source_frames: list[RealtimeSourceFrame]) -> int:
        frames.extend(source_frames)
        return original(source_frames)

    monkeypatch.setattr(repository, "record_realtime_source_frames", capture)
    def capture_cleanup(*, as_of: datetime | None = None) -> tuple[int, int]:
        cleanup_calls.append(as_of is None)
        return (0, 0)

    monkeypatch.setattr(repository, "purge_expired_source_evidence", capture_cleanup)
    messages = [
        {
            "type": "ticker",
            "code": target.market_code,
            "timestamp": 1784257200000,
            "trade_price": 100 + index,
            "acc_trade_price_24h": 1000,
            "signed_change_rate": "0.01",
        }
        for index in range(3)
    ]

    run_realtime_stream_collection(
        repository,
        messages,
        connection_id="connection-sequence",
        flush_interval_seconds=999,
    )

    assert [frame.receipt.frame_sequence for frame in frames] == [1, 2, 3]
    assert {frame.receipt.connection_id for frame in frames} == {"connection-sequence"}
    assert cleanup_calls == [True]


def test_realtime_stream_buffer_discards_market_type_pairs_outside_plan() -> None:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    target = repository.list_active_targets()[0]
    collected_at = repository.latest_ticker(target.id)
    assert collected_at is not None
    buffer = RealtimeStreamBuffer(
        {target.market_code: target},
        allowed_market_types={(target.market_code, "trade")},
        now=lambda: collected_at.bucket_at + timedelta(minutes=1),
    )

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
            "trade_timestamp": int(collected_at.bucket_at.timestamp() * 1000),
            "sequential_id": 99001,
        }
    )

    assert buffer.flush(repository) == 1


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


def test_subscription_loop_reloads_targets_and_records_applied_generation() -> None:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    target_codes = [target.market_code for target in repository.list_active_targets()]
    plans = iter(
        [
            SubscriptionPlan(market_codes=(target_codes[0],), generation=1),
            SubscriptionPlan(market_codes=tuple(target_codes[:2]), generation=2),
        ]
    )
    opened_connections: list[tuple[str, ...]] = []
    applied: list[tuple[int, str]] = []

    def stream_factory(
        market_codes: list[str],
        *,
        market_codes_by_type: Mapping[str, tuple[str, ...]] | None,
        lifetime_seconds: float,
        on_connected: object,
    ) -> Iterator[Mapping[str, object]]:
        del market_codes_by_type, lifetime_seconds
        opened_connections.append(tuple(market_codes))
        assert callable(on_connected)
        on_connected()
        return iter(())

    run_realtime_subscription_loop(
        repository,
        load_plan=lambda _repository: next(plans),
        mark_applied=lambda _repository, plan, connection_id: applied.append(
            (plan.generation, connection_id)
        ),
        stream_factory=stream_factory,
        refresh_interval_seconds=300,
        max_connections=2,
    )

    assert opened_connections == [
        (target_codes[0],),
        tuple(target_codes[:2]),
    ]
    assert [generation for generation, _connection_id in applied] == [1, 2]
    assert len({connection_id for _generation, connection_id in applied}) == 2


def test_realtime_subscription_loop_does_not_mark_generation_before_connection_is_open() -> None:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    target = repository.list_active_targets()[0]
    applied: list[int] = []

    def disconnected_stream(
        market_codes: list[str],
        *,
        market_codes_by_type: Mapping[str, tuple[str, ...]] | None,
        lifetime_seconds: float,
        on_connected: object,
    ) -> Iterator[Mapping[str, object]]:
        del market_codes, market_codes_by_type, lifetime_seconds, on_connected
        raise RuntimeError("connection refused")

    try:
        run_realtime_subscription_loop(
            repository,
            load_plan=lambda _repository: SubscriptionPlan(
                market_codes=(target.market_code,), generation=7
            ),
            mark_applied=lambda _repository, plan, _connection_id: applied.append(plan.generation),
            stream_factory=disconnected_stream,
            max_connections=1,
        )
    except RuntimeError as exc:
        assert str(exc) == "connection refused"
    else:
        raise AssertionError("연결 실패가 호출자에게 전파되지 않았습니다.")

    assert applied == []


def test_postgres_subscription_plan_reads_desired_state_and_records_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = PostgresOperationsRepository("postgresql://unused")
    desires = [
        CollectionSubscriptionDesire(
            11, "KRW-BTC", "subscribed", 8, "active", "active", "trade_event", True
        ),
        CollectionSubscriptionDesire(
            12,
            "KRW-ETH",
            "subscribed",
            3,
            "active",
            "active",
            "orderbook_snapshot",
            True,
        ),
        CollectionSubscriptionDesire(
            13, "KRW-XRP", "unsubscribed", 4, "paused", "active", "ticker_snapshot", True
        ),
        CollectionSubscriptionDesire(
            14, "KRW-BTC", "subscribed", 5, "active", "active", "ticker_snapshot", False
        ),
        CollectionSubscriptionDesire(
            15, "KRW-BTC", "subscribed", 9, "active", "active", "source_candle", True
        ),
    ]
    applied: list[tuple[tuple[tuple[int, int], ...], str]] = []
    monkeypatch.setattr(repository, "load_collection_subscription_desires", lambda: desires)
    monkeypatch.setattr(
        repository,
        "mark_collection_subscription_desires_applied",
        lambda versions, *, connection_id: applied.append((versions, connection_id)),
    )

    plan = load_subscription_plan(repository)
    mark_subscription_plan_applied(repository, plan, "connection-8")

    assert plan == SubscriptionPlan(
        market_codes=("KRW-BTC", "KRW-ETH"),
        generation=9,
        target_versions=((11, 8), (12, 3), (13, 4), (14, 5), (15, 9)),
        market_codes_by_type=(
            ("trade", ("KRW-BTC",)),
            ("orderbook", ("KRW-ETH",)),
            ("candle.1m", ("KRW-BTC",)),
        ),
    )
    assert applied == [(((11, 8), (12, 3), (13, 4), (14, 5), (15, 9)), "connection-8")]
