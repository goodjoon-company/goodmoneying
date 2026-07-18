from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from goodmoneying_shared.dataset_version_store import (
    DatasetIdempotencyConflictError,
    PostgresDatasetVersionStore,
)
from goodmoneying_shared.models import SourceCandle
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository

pytestmark = pytest.mark.live


def test_live_postgres_데이터셋은_수락_frontier와_exact_member를_불변_게시한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresDatasetVersionStore(repository)
    market_code = f"KRW-DATASET-{uuid4().hex[:8].upper()}"
    instrument = repository.upsert_instrument(market_code, "P2 데이터셋")
    start = datetime(2026, 9, 1, tzinfo=UTC)
    end = start + timedelta(minutes=2)
    as_of = end + timedelta(minutes=10)
    _record_market_status(repository, instrument.id, start - timedelta(hours=1), "active")
    repository.record_incremental_collection(
        [],
        [],
        [
            _candle(instrument.id, start, "100"),
            _candle(instrument.id, start + timedelta(minutes=1), "101"),
        ],
    )

    arguments = _build_arguments(
        instrument.id,
        key=f"dataset-{uuid4().hex}",
        request_id=f"request-{uuid4().hex}",
        start=start,
        end=end,
        as_of=as_of,
    )
    accepted = store.create_build(**arguments)
    replay = store.create_build(**arguments)

    assert accepted["buildId"] == replay["buildId"]
    with pytest.raises(DatasetIdempotencyConflictError):
        store.create_build(**{**arguments, "reason": "같은 키의 다른 명령"})

    # 수락 뒤 knowledge_at이 asOf 이전인 늦은 정정과 시장 상태 이력이 생겨도
    # 이미 고정한 ceiling과 상태 snapshot은 바뀌지 않는다.
    repository.record_incremental_collection(
        [],
        [],
        [_candle(instrument.id, start, "900", knowledge_delay_minutes=5)],
    )
    _replace_market_status(
        repository,
        instrument.id,
        observed_at=start + timedelta(minutes=1),
        status="inactive",
    )

    build_id = int(accepted["buildId"])
    assert store.publish_next_build("dataset-e2e-worker") == build_id
    completed = store.get_build(build_id)
    assert completed is not None and completed["status"] == "succeeded"
    version_id = int(completed["datasetVersionId"])
    version = store.get_version(version_id)
    assert version is not None
    series_id = int(version["series"][0]["seriesId"])

    page = store.get_series(
        dataset_version_id=version_id,
        series_id=series_id,
        from_at=start,
        to_at=end,
        page_size=1,
        cursor=None,
    )
    assert page is not None
    assert page["items"][0]["values"]["close"] == "100"
    assert page["nextCursor"] is not None
    second_page = store.get_series(
        dataset_version_id=version_id,
        series_id=series_id,
        from_at=start,
        to_at=end,
        page_size=1,
        cursor=page["nextCursor"],
    )
    assert second_page is not None
    assert second_page["items"][0]["values"]["close"] == "101"

    coverage = store.get_coverage(version_id)
    assert coverage is not None
    assert coverage["counts"] == {
        "available": 2,
        "no_trade": 0,
        "missing": 0,
        "unavailable": 0,
        "unverified": 0,
    }
    listed = store.list_versions(page_size=1, cursor=None)
    assert any(item["datasetVersionId"] == version_id for item in listed["items"])

    with repository._connect() as connection:
        frozen_status = connection.execute(
            """
            SELECT trading_status, valid_to
            FROM dataset_version_market_status_snapshots
            WHERE dataset_version_id=%s
            """,
            (version_id,),
        ).fetchone()
        assert frozen_status is not None
        assert frozen_status["trading_status"] == "active"
        assert frozen_status["valid_to"] == end
    with (
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
        repository._connect() as connection,
    ):
        connection.execute(
            "UPDATE dataset_versions SET ordering_policy='changed' WHERE id=%s",
            (version_id,),
        )


def test_live_postgres_A_B_A_정정은_서로_다른_dataset_content_hash를_만든다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresDatasetVersionStore(repository)
    market_code = f"KRW-DSETABA-{uuid4().hex[:8].upper()}"
    instrument = repository.upsert_instrument(market_code, "P2 데이터셋 ABA")
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    _record_market_status(repository, instrument.id, start - timedelta(hours=1), "active")

    hashes: list[str] = []
    for index, close in enumerate(("100", "200", "100"), start=1):
        repository.record_incremental_collection(
            [],
            [],
            [_candle(instrument.id, start, close, knowledge_delay_minutes=index)],
        )
        accepted = store.create_build(
            **_build_arguments(
                instrument.id,
                key=f"dataset-aba-{uuid4().hex}",
                request_id=f"request-aba-{uuid4().hex}",
                start=start,
                end=end,
                as_of=end + timedelta(minutes=10),
            )
        )
        store.publish_next_build(f"dataset-aba-{index}")
        completed = store.get_build(int(accepted["buildId"]))
        assert completed is not None
        version = store.get_version(int(completed["datasetVersionId"]))
        assert version is not None
        hashes.append(str(version["contentHash"]))

    assert len(set(hashes)) == 3


def test_live_postgres_no_trade는_fail_게시를_막지_않고_정책적용전_exact_member만_읽는다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresDatasetVersionStore(repository)
    market_code = f"KRW-DSETNT-{uuid4().hex[:8].upper()}"
    instrument = repository.upsert_instrument(market_code, "P2 데이터셋 무거래")
    start = datetime(2026, 11, 1, tzinfo=UTC)
    end = start + timedelta(minutes=2)
    as_of = end + timedelta(minutes=10)
    detected_at = start + timedelta(minutes=1, seconds=30)
    _record_market_status(repository, instrument.id, start - timedelta(hours=1), "active")
    repository.record_incremental_collection([], [], [_candle(instrument.id, start, "100")])
    target_spec_id = _ensure_source_candle_spec(repository, instrument.id)
    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO data_quality_events (
              target_spec_id, event_type, previous_status, new_status,
              range_start_at, range_end_at, fingerprint, evidence, detected_at
            ) VALUES (
              %s,'coverage_classified',NULL,'no_trade',%s,%s,%s,
              '{"reason":"dataset-no-trade-e2e"}'::jsonb,%s
            )
            """,
            (
                target_spec_id,
                start + timedelta(minutes=1),
                end,
                uuid4().hex,
                detected_at,
            ),
        )

    arguments = _build_arguments(
        instrument.id,
        key=f"dataset-no-trade-{uuid4().hex}",
        request_id=f"request-no-trade-{uuid4().hex}",
        start=start,
        end=end,
        as_of=as_of,
    )
    arguments["policies"] = {
        "availabilityPolicy": "point_in_time_v1",
        "fillPolicy": "no_trade_carry_forward_v1",
        "missingPolicy": "fail",
    }
    accepted = store.create_build(**arguments)

    assert store.publish_next_build("dataset-no-trade-worker") == int(accepted["buildId"])
    completed = store.get_build(int(accepted["buildId"]))
    assert completed is not None and completed["status"] == "succeeded"
    version = store.get_version(int(completed["datasetVersionId"]))
    assert version is not None
    assert version["fillPolicy"] == "no_trade_carry_forward_v1"
    series_id = int(version["series"][0]["seriesId"])
    series = store.get_series(
        dataset_version_id=int(version["datasetVersionId"]),
        series_id=series_id,
        from_at=start,
        to_at=end,
        page_size=10,
        cursor=None,
    )
    assert series is not None
    assert [item["occurredAt"] for item in series["items"]] == [start]

    coverage = store.get_coverage(int(version["datasetVersionId"]))
    assert coverage is not None
    assert [(item["status"], item["knowledgeAt"]) for item in coverage["items"]] == [
        ("available", accepted["frozenAt"]),
        ("no_trade", detected_at),
    ]


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]


def _build_arguments(
    instrument_id: int,
    *,
    key: str,
    request_id: str,
    start: datetime,
    end: datetime,
    as_of: datetime,
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "idempotency_key": key,
        "actor_id": "operator:e2e",
        "requested_at": as_of + timedelta(minutes=1),
        "reason": "불변 데이터셋 E2E",
        "selection": {
            "asOf": as_of,
            "from": start,
            "to": end,
            "series": [
                {
                    "instrumentId": instrument_id,
                    "dataKind": "candle",
                    "unit": "1m",
                    "definitionSetHash": None,
                    "calculationVersion": "source-candle-v1",
                }
            ],
        },
        "policies": {
            "availabilityPolicy": "point_in_time_v1",
            "fillPolicy": "none",
            "missingPolicy": "fail",
        },
    }


def _candle(
    instrument_id: int,
    started_at: datetime,
    close: str,
    *,
    knowledge_delay_minutes: int = 0,
) -> SourceCandle:
    price = Decimal(close)
    knowledge_at = started_at + timedelta(minutes=knowledge_delay_minutes, seconds=2)
    return SourceCandle(
        instrument_id=instrument_id,
        candle_unit="1m",
        candle_start_at=started_at,
        open_price=price,
        high_price=price,
        low_price=price,
        close_price=price,
        trade_volume=Decimal("1"),
        trade_amount=price,
        collected_at=knowledge_at - timedelta(seconds=1),
        knowledge_at=knowledge_at,
    )


def _record_market_status(
    repository: PostgresOperationsRepository,
    instrument_id: int,
    observed_at: datetime,
    status: str,
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
            ) VALUES (%s,%s,'NONE','{}'::jsonb,%s,%s,%s)
            """,
            (market["id"], status, "a" * 64, observed_at, observed_at),
        )


def _ensure_source_candle_spec(
    repository: PostgresOperationsRepository, instrument_id: int
) -> int:
    with repository._connect() as connection:
        market = connection.execute(
            "SELECT id FROM markets WHERE legacy_instrument_id=%s", (instrument_id,)
        ).fetchone()
        assert market is not None
        policy = connection.execute(
            """
            INSERT INTO collection_policies (
              exchange, quote_currency, name, default_start_at, priority
            ) VALUES ('UPBIT','KRW',%s,'2024-01-01T00:00:00Z',100)
            RETURNING id
            """,
            (f"p2-dataset-{uuid4().hex}",),
        ).fetchone()
        assert policy is not None
        specification = connection.execute(
            """
            INSERT INTO collection_target_specs (
              policy_id, market_id, data_type, candle_unit, range_start_at,
              priority, continuous, auto_managed, status
            ) VALUES (%s,%s,'source_candle','1m','2024-01-01T00:00:00Z',
                      100,true,true,'active')
            RETURNING id
            """,
            (policy["id"], market["id"]),
        ).fetchone()
        assert specification is not None
        return int(specification["id"])


def _replace_market_status(
    repository: PostgresOperationsRepository,
    instrument_id: int,
    *,
    observed_at: datetime,
    status: str,
) -> None:
    with repository._connect() as connection:
        current = connection.execute(
            """
            SELECT history.* FROM market_status_history history
            JOIN markets market ON market.id=history.market_id
            WHERE market.legacy_instrument_id=%s AND history.valid_to IS NULL
            ORDER BY history.id DESC LIMIT 1 FOR UPDATE
            """,
            (instrument_id,),
        ).fetchone()
        assert current is not None
        connection.execute(
            "UPDATE market_status_history SET valid_to=%s WHERE id=%s",
            (observed_at, current["id"]),
        )
        connection.execute(
            """
            INSERT INTO market_status_history (
              market_id, trading_status, market_warning, market_event,
              source_payload_checksum, valid_from, observed_at
            ) VALUES (%s,%s,'NONE','{}'::jsonb,%s,%s,%s)
            """,
            (current["market_id"], status, "b" * 64, observed_at, observed_at),
        )
