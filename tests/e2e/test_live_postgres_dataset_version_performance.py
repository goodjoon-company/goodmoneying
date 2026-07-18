from __future__ import annotations

import os
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest

from goodmoneying_shared.dataset_version_store import PostgresDatasetVersionStore
from goodmoneying_shared.dataset_versions import (
    DatasetCanonicalMember,
    DatasetCanonicalSpecification,
    DatasetSeriesRequest,
    canonical_dataset_hashes,
)
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository

pytestmark = pytest.mark.live

MEMBER_COUNT = 10_001


def test_live_postgres_10001행_발행은_streaming_hash와_set_insert_상한을_지킨다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresDatasetVersionStore(repository)
    market_code = f"KRW-DSETPERF-{uuid4().hex[:8].upper()}"
    instrument = repository.upsert_instrument(market_code, "P2 데이터셋 성능")
    start = datetime(2027, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=MEMBER_COUNT)
    as_of = end + timedelta(minutes=10)
    _seed_market_status_and_candles(repository, instrument.id, start, end)

    accepted = store.create_build(
        request_id=f"request-{uuid4().hex}",
        idempotency_key=f"dataset-performance-{uuid4().hex}",
        actor_id="operator:e2e",
        requested_at=as_of + timedelta(minutes=1),
        reason="10,001행 bounded-memory 발행 검증",
        selection={
            "asOf": as_of,
            "from": start,
            "to": end,
            "series": [
                {
                    "instrumentId": instrument.id,
                    "dataKind": "candle",
                    "unit": "1m",
                    "definitionSetHash": None,
                    "calculationVersion": "source-candle-v1",
                }
            ],
        },
        policies={
            "availabilityPolicy": "point_in_time_v1",
            "fillPolicy": "none",
            "missingPolicy": "fail",
        },
    )

    tracemalloc.start()
    started_at = time.perf_counter()
    published_build_id = store.publish_next_build("dataset-performance-worker")
    elapsed_seconds = time.perf_counter() - started_at
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert published_build_id == accepted["buildId"]
    completed = store.get_build(accepted["buildId"])
    assert completed is not None and completed["status"] == "succeeded"
    version = store.get_version(int(cast(int, completed["datasetVersionId"])))
    assert version is not None
    assert elapsed_seconds < 30
    assert peak_bytes < 64 * 1024 * 1024

    members = _reference_members(repository, instrument.id, market_code, start, end)
    specification = DatasetCanonicalSpecification(
        schema_version="dataset-v1",
        as_of=as_of,
        input_start_at=start,
        output_start_at=start,
        end_at=end,
        series=(
            DatasetSeriesRequest(
                instrument_id=instrument.id,
                exchange="UPBIT",
                market_code=market_code,
                data_kind="candle",
                unit="1m",
                calculation_version="source-candle-v1",
            ),
        ),
        fill_policy="none",
        missing_policy="fail",
        ordering_policy="market-kind-unit-time-v1",
    )
    reference_manifest = canonical_dataset_hashes(specification, members).manifest_hash

    with repository._connect() as connection:
        persisted = connection.execute(
            """
            SELECT version.manifest_hash, series.member_count, series.members_hash,
              (SELECT COUNT(*) FROM dataset_version_candles member
               WHERE member.dataset_version_id=version.id) AS count
            FROM dataset_versions version
            JOIN dataset_version_series series ON series.dataset_version_id=version.id
            WHERE version.id=%s
            """,
            (version["datasetVersionId"],),
        ).fetchone()
    assert persisted is not None
    assert persisted["count"] == MEMBER_COUNT
    assert persisted["member_count"] == MEMBER_COUNT
    assert persisted["manifest_hash"] == reference_manifest
    assert persisted["members_hash"] == reference_manifest


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]


def _seed_market_status_and_candles(
    repository: PostgresOperationsRepository,
    instrument_id: int,
    start: datetime,
    end: datetime,
) -> None:
    with repository._connect() as connection:
        market = connection.execute(
            "SELECT id FROM markets WHERE legacy_instrument_id=%s", (instrument_id,)
        ).fetchone()
        assert market is not None
        connection.execute(
            """
            INSERT INTO market_status_history (
              market_id, trading_status, market_warning, market_event,
              source_payload_checksum, valid_from, observed_at
            ) VALUES (%s,'active','NONE','{}'::jsonb,%s,%s,%s)
            """,
            (market["id"], "c" * 64, start - timedelta(hours=1), start - timedelta(hours=1)),
        )
        connection.execute("SET LOCAL session_replication_role='replica'")
        connection.execute(
            """
            INSERT INTO source_candles (
              market_id, instrument_id, source, candle_unit, candle_start_at,
              open_price, high_price, low_price, close_price, trade_volume,
              trade_amount, source_timestamp_at, collected_at, occurred_at,
              received_at, stored_at, knowledge_at
            )
            SELECT %s,%s,'UPBIT','1m', bucket_at,
              100,100,100,100,1,100,bucket_at,bucket_at + interval '1 second',
              bucket_at,bucket_at + interval '1 second',bucket_at + interval '2 seconds',
              bucket_at + interval '2 seconds'
            FROM generate_series(%s::timestamptz, %s::timestamptz - interval '1 minute',
                                 interval '1 minute') bucket_at
            """,
            (market["id"], instrument_id, start, end),
        )
        connection.execute(
            """
            INSERT INTO source_candle_revisions (
              source_candle_id, revision_number, market_id, instrument_id, source,
              candle_unit, candle_start_at, open_price, high_price, low_price,
              close_price, trade_volume, trade_amount, source_as_of, knowledge_at,
              input_content_hash
            )
            SELECT candle.id,1,candle.market_id,candle.instrument_id,candle.source,
              candle.candle_unit,candle.candle_start_at,candle.open_price,candle.high_price,
              candle.low_price,candle.close_price,candle.trade_volume,candle.trade_amount,
              candle.collected_at,candle.knowledge_at,
              source_candle_content_hash(
                candle.open_price,candle.high_price,candle.low_price,candle.close_price,
                candle.trade_volume,candle.trade_amount)
            FROM source_candles candle
            WHERE candle.instrument_id=%s AND candle.candle_start_at >= %s
              AND candle.candle_start_at < %s
            """,
            (instrument_id, start, end),
        )


def _reference_members(
    repository: PostgresOperationsRepository,
    instrument_id: int,
    market_code: str,
    start: datetime,
    end: datetime,
) -> tuple[DatasetCanonicalMember, ...]:
    with repository._connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM source_candle_revisions
            WHERE instrument_id=%s AND candle_start_at >= %s AND candle_start_at < %s
            ORDER BY candle_start_at
            """,
            (instrument_id, start, end),
        ).fetchall()
    return tuple(
        DatasetCanonicalMember(
            data_kind="candle",
            exchange="UPBIT",
            market_code=market_code,
            unit="1m",
            occurred_at=row["candle_start_at"],
            knowledge_at=row["knowledge_at"],
            source_as_of=row["source_as_of"],
            content_hash=row["input_content_hash"],
            quality="available",
            calculation_version="source-candle-v1",
            definition_hash=None,
            source_ref_id=int(row["id"]),
        )
        for row in cast(list[dict[str, Any]], rows)
    )
