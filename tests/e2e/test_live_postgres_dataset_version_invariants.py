from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, cast
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from goodmoneying_shared.dataset_version_store import PostgresDatasetVersionStore
from goodmoneying_shared.models import SourceCandle
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository

pytestmark = pytest.mark.live


@pytest.mark.parametrize(
    "table_name",
    (
        "dataset_version_series",
        "dataset_version_candles",
        "dataset_version_indicators",
        "dataset_version_market_statistics",
        "dataset_version_microstructures",
        "dataset_version_market_status_snapshots",
        "dataset_version_coverage_snapshots",
    ),
)
def test_게시된_dataset_version의_모든_child_INSERT는_DB가_거부한다(
    table_name: str,
) -> None:
    repository, store, instrument_id, start = _seed_market_and_candles(minutes=1)
    version_id, series_id = _publish_candle_version(
        store,
        instrument_id,
        start,
        start + timedelta(minutes=1),
        calculation_version="source-candle-v1",
    )

    with (
        pytest.raises(psycopg.errors.RaiseException, match="append-only|immutable|게시"),
        repository._connect() as connection,
    ):
        _insert_after_publication(
            connection,
            table_name=table_name,
            version_id=version_id,
            series_id=series_id,
            instrument_id=instrument_id,
            occurred_at=start + timedelta(hours=1),
        )


def test_정의가_다른_동일_상품_kind_unit_series를_한_version에_게시한다() -> None:
    repository, store, instrument_id, start = _seed_market_and_candles(minutes=0)
    end = start + timedelta(minutes=1)
    arguments = _build_arguments(
        instrument_id,
        start,
        end,
        series=(
            _series(
                instrument_id,
                "indicator",
                "1m",
                definition_set_hash="1" * 64,
                calculation_version=None,
            ),
            _series(
                instrument_id,
                "indicator",
                "1m",
                definition_set_hash="2" * 64,
                calculation_version=None,
            ),
        ),
        missing_policy="null",
    )

    accepted = store.create_build(**arguments)
    assert store.publish_next_build("dataset-two-series") == accepted["buildId"]
    completed = store.get_build(int(accepted["buildId"]))

    assert completed is not None
    assert completed["status"] == "succeeded"
    version = store.get_version(int(completed["datasetVersionId"]))
    assert version is not None
    assert [item["definitionSetHash"] for item in version["series"]] == ["1" * 64, "2" * 64]


def test_요청_calculationVersion과_exact_member가_다르면_안정적으로_거부한다() -> None:
    _repository, store, instrument_id, start = _seed_market_and_candles(minutes=1)
    end = start + timedelta(minutes=1)
    arguments = _build_arguments(
        instrument_id,
        start,
        end,
        series=(
            _series(
                instrument_id,
                "candle",
                "1m",
                definition_set_hash=None,
                calculation_version="존재하지-않는-계산-버전",
            ),
        ),
        missing_policy="null",
    )

    try:
        accepted = store.create_build(**arguments)
    except ValueError as exc:
        assert "calculation_version_mismatch" in str(exc)
        return

    store.publish_next_build("dataset-version-mismatch")
    completed = store.get_build(int(accepted["buildId"]))
    assert completed is not None
    assert completed["status"] == "failed"
    assert completed["errorCode"] == "calculation_version_mismatch"


@pytest.mark.parametrize(
    ("stored_definition_hash", "calculation_status"),
    (
        ("2" * 64, "ready"),
        ("1" * 64, "missing"),
    ),
)
def test_missing_fail은_요청_definition과_성공상태의_실제_stage_member만_완전성으로_인정한다(
    stored_definition_hash: str,
    calculation_status: str,
) -> None:
    repository, store, instrument_id, start = _seed_market_and_candles(minutes=1)
    with repository._connect() as connection:
        _seed_typed_source(
            connection,
            table_name="dataset_version_indicators",
            instrument_id=instrument_id,
            occurred_at=start,
            definition_set_hash=stored_definition_hash,
            calculation_status=calculation_status,
        )
    accepted = store.create_build(
        **_build_arguments(
            instrument_id,
            start,
            start + timedelta(minutes=1),
            series=(
                _series(
                    instrument_id,
                    "indicator",
                    "1m",
                    definition_set_hash="1" * 64,
                    calculation_version="indicator-v1",
                ),
            ),
            missing_policy="fail",
        )
    )

    assert store.publish_next_build("dataset-stage-completeness") == accepted["buildId"]
    completed = store.get_build(int(accepted["buildId"]))

    assert completed is not None
    assert completed["status"] == "failed"
    assert completed["errorCode"] == "coverage_incomplete"


def test_직접_1d와_1m_rollup_fallback이_섞인_series도_게시한다() -> None:
    repository, store, instrument_id, start = _seed_market_and_candles(minutes=0)
    _record_candle(repository, instrument_id, start, unit="1d", close="100")
    second_day = start + timedelta(days=1)
    _record_candle(repository, instrument_id, second_day, unit="1m", close="200")
    assert repository.materialize_candle_rollups(instrument_id, "1d") >= 1
    end = start + timedelta(days=2)

    accepted = store.create_build(
        **_build_arguments(
            instrument_id,
            start,
            end,
            series=(
                _series(
                    instrument_id,
                    "candle",
                    "1d",
                    definition_set_hash=None,
                    calculation_version=None,
                ),
            ),
            missing_policy="null",
        )
    )
    store.publish_next_build("dataset-daily-fallback")
    completed = store.get_build(int(accepted["buildId"]))

    assert completed is not None
    assert completed["status"] == "succeeded"
    version = store.get_version(int(completed["datasetVersionId"]))
    assert version is not None
    page = store.get_series(
        dataset_version_id=int(completed["datasetVersionId"]),
        series_id=int(version["series"][0]["seriesId"]),
        from_at=start,
        to_at=end,
        page_size=10,
        cursor=None,
    )
    assert page is not None
    assert [(item["occurredAt"], item["values"]["close"]) for item in page["items"]] == [
        (start, "100"),
        (second_day, "200"),
    ]


def test_coverage는_타_data_type_event를_격리하고_inactive_시장을_unavailable로_고정한다() -> None:
    repository, store, instrument_id, start = _seed_market_and_candles(minutes=2)
    middle = start + timedelta(minutes=1)
    end = start + timedelta(minutes=2)
    market_id = _market_id(repository, instrument_id)
    _replace_market_status(repository, market_id, middle, "inactive")
    source_spec_id, orderbook_spec_id = _quality_specs(repository, market_id, start)
    _quality_event(repository, source_spec_id, start, middle, "available")
    _quality_event(repository, orderbook_spec_id, start, middle, "missing")

    accepted = store.create_build(
        **_build_arguments(
            instrument_id,
            start,
            end,
            series=(
                _series(
                    instrument_id,
                    "candle",
                    "1m",
                    definition_set_hash=None,
                    calculation_version="source-candle-v1",
                ),
            ),
            missing_policy="null",
        )
    )
    store.publish_next_build("dataset-coverage-isolation")
    completed = store.get_build(int(accepted["buildId"]))
    assert completed is not None and completed["status"] == "succeeded"
    coverage = store.get_coverage(int(completed["datasetVersionId"]))

    assert coverage is not None
    assert [item["status"] for item in coverage["items"]] == ["available", "unavailable"]
    assert coverage["counts"] == {
        "available": 1,
        "no_trade": 0,
        "missing": 0,
        "unavailable": 1,
        "unverified": 0,
    }


def test_예상밖_publish_실패는_retry_wait후_시도예산에서_dead_letter된다() -> None:
    repository, store, instrument_id, start = _seed_market_and_candles(minutes=1)
    accepted = store.create_build(
        **_build_arguments(
            instrument_id,
            start,
            start + timedelta(minutes=1),
            series=(
                _series(
                    instrument_id,
                    "candle",
                    "1m",
                    definition_set_hash=None,
                    calculation_version="source-candle-v1",
                ),
            ),
            missing_policy="null",
        )
    )
    build_id = int(accepted["buildId"])
    suffix = uuid4().hex
    function_name = f"fail_dataset_publish_{suffix}"
    trigger_name = f"fail_dataset_publish_{suffix}"
    trigger_created = False
    try:
        with repository._connect() as connection:
            connection.execute(
                "UPDATE dataset_builds SET max_attempts=2 WHERE id=%s",
                (build_id,),
            )
            connection.execute(
                sql.SQL(
                    "CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS "
                    "'BEGIN RAISE EXCEPTION ''forced unexpected publication failure''; END'"
                ).format(sql.Identifier(function_name))
            )
            connection.execute(
                sql.SQL(
                    "CREATE TRIGGER {} BEFORE INSERT ON dataset_versions "
                    "FOR EACH ROW EXECUTE FUNCTION {}()"
                ).format(sql.Identifier(trigger_name), sql.Identifier(function_name))
            )
            trigger_created = True

        assert store.publish_next_build("dataset-retry-1") == build_id
        with repository._connect() as connection:
            first = connection.execute(
                """
                SELECT status, attempt_count, last_error_code, lease_owner, next_retry_at
                FROM dataset_builds WHERE id=%s
                """,
                (build_id,),
            ).fetchone()
            assert first is not None
            assert first["status"] == "retry_wait"
            assert first["attempt_count"] == 1
            assert first["last_error_code"] == "unexpected_publication_error"
            assert first["lease_owner"] is None
            connection.execute(
                """
                UPDATE dataset_builds
                SET next_retry_at=clock_timestamp()-interval '1 second'
                WHERE id=%s
                """,
                (build_id,),
            )

        assert store.publish_next_build("dataset-retry-2") == build_id
        with repository._connect() as connection:
            final = connection.execute(
                """
                SELECT status, attempt_count, last_error_code, dead_letter_reason, lease_owner
                FROM dataset_builds WHERE id=%s
                """,
                (build_id,),
            ).fetchone()
        assert final is not None
        assert final["status"] == "dead_letter"
        assert final["attempt_count"] == 2
        assert final["last_error_code"] == "unexpected_publication_error"
        assert final["dead_letter_reason"]
        assert final["lease_owner"] is None
    finally:
        if trigger_created:
            with repository._connect() as connection:
                connection.execute(
                    sql.SQL("DROP TRIGGER IF EXISTS {} ON dataset_versions").format(
                        sql.Identifier(trigger_name)
                    )
                )
                connection.execute(
                    sql.SQL("DROP FUNCTION IF EXISTS {}()").format(sql.Identifier(function_name))
                )


@pytest.mark.parametrize(
    ("source_kind", "ceiling_column"),
    (
        ("source_candle", "source_revision_through_id"),
        ("candle_rollup", "candle_rollup_through_id"),
        ("indicator", "indicator_materialization_through_id"),
        ("market_statistic", "market_statistic_through_id"),
        ("microstructure", "microstructure_materialization_through_id"),
    ),
)
@pytest.mark.parametrize("violation", ("above", "null", "knowledge_after_as_of"))
def test_typed_member는_고정된_원천_frontier와_asOf를_넘지_못한다(
    source_kind: str,
    ceiling_column: str,
    violation: str,
) -> None:
    repository, store, instrument_id, start = _seed_market_and_candles(minutes=6)
    source_ids = _seed_frontier_sources(
        repository,
        instrument_id=instrument_id,
        start=start,
        source_kind=source_kind,
    )
    selected_source_id = source_ids[1] if violation != "null" else source_ids[0]
    ceiling = None if violation == "null" else (
        source_ids[0] if violation == "above" else source_ids[1]
    )
    source_knowledge_at = _source_knowledge_at(
        repository,
        source_kind=source_kind,
        source_id=selected_source_id,
    )
    version_as_of = (
        source_knowledge_at - timedelta(microseconds=1)
        if violation == "knowledge_after_as_of"
        else start + timedelta(days=1)
    )

    with (
        pytest.raises(psycopg.errors.RaiseException, match="frontier|asOf"),
        repository._connect() as connection,
    ):
        version_id, series_id = _insert_unsealed_frontier_parent(
            connection,
            repository=repository,
            store=store,
            instrument_id=instrument_id,
            start=start,
            source_kind=source_kind,
            ceiling_column=ceiling_column,
            ceiling=ceiling,
            as_of=version_as_of,
        )
        _insert_matching_typed_member(
            connection,
            version_id=version_id,
            series_id=series_id,
            source_kind=source_kind,
            source_id=selected_source_id,
        )


@pytest.mark.parametrize(
    ("source_kind", "ceiling_column"),
    (
        ("source_candle", "source_revision_through_id"),
        ("candle_rollup", "candle_rollup_through_id"),
        ("indicator", "indicator_materialization_through_id"),
        ("market_statistic", "market_statistic_through_id"),
        ("microstructure", "microstructure_materialization_through_id"),
    ),
)
def test_typed_member는_원천_frontier이하이고_asOf시점에_알려졌으면_허용한다(
    source_kind: str,
    ceiling_column: str,
) -> None:
    repository, store, instrument_id, start = _seed_market_and_candles(minutes=6)
    source_ids = _seed_frontier_sources(
        repository,
        instrument_id=instrument_id,
        start=start,
        source_kind=source_kind,
    )
    source_id = source_ids[1]
    as_of = _source_knowledge_at(
        repository,
        source_kind=source_kind,
        source_id=source_id,
    )

    with repository._connect() as connection:
        version_id, series_id = _insert_unsealed_frontier_parent(
            connection,
            repository=repository,
            store=store,
            instrument_id=instrument_id,
            start=start,
            source_kind=source_kind,
            ceiling_column=ceiling_column,
            ceiling=source_id,
            as_of=as_of,
        )
        _insert_matching_typed_member(
            connection,
            version_id=version_id,
            series_id=series_id,
            source_kind=source_kind,
            source_id=source_id,
        )


def _seed_frontier_sources(
    repository: PostgresOperationsRepository,
    *,
    instrument_id: int,
    start: datetime,
    source_kind: str,
) -> tuple[int, int]:
    if source_kind == "candle_rollup":
        assert repository.materialize_candle_rollups(instrument_id, "3m") >= 2
        table = "candle_rollups"
        condition = "instrument_id=%s AND candle_unit='3m'"
    elif source_kind == "source_candle":
        table = "source_candle_revisions"
        condition = "instrument_id=%s"
    else:
        table_name = {
            "indicator": "dataset_version_indicators",
            "market_statistic": "dataset_version_market_statistics",
            "microstructure": "dataset_version_microstructures",
        }[source_kind]
        with repository._connect() as connection:
            first = _seed_typed_source(
                connection,
                table_name=table_name,
                instrument_id=instrument_id,
                occurred_at=start + timedelta(minutes=10),
            )
            second = _seed_typed_source(
                connection,
                table_name=table_name,
                instrument_id=instrument_id,
                occurred_at=start + timedelta(minutes=11),
            )
        return first, second
    with repository._connect() as connection:
        rows = connection.execute(
            f"SELECT id FROM {table} WHERE {condition} ORDER BY id LIMIT 2",
            (instrument_id,),
        ).fetchall()
    assert len(rows) == 2
    return int(rows[0]["id"]), int(rows[1]["id"])


def _source_knowledge_at(
    repository: PostgresOperationsRepository,
    *,
    source_kind: str,
    source_id: int,
) -> datetime:
    table = {
        "source_candle": "source_candle_revisions",
        "candle_rollup": "candle_rollups",
        "indicator": "indicator_materializations",
        "market_statistic": "market_statistics",
        "microstructure": "microstructure_materializations",
    }[source_kind]
    with repository._connect() as connection:
        row = connection.execute(
            sql.SQL("SELECT knowledge_at FROM {} WHERE id=%s").format(sql.Identifier(table)),
            (source_id,),
        ).fetchone()
    assert row is not None
    return cast(datetime, row["knowledge_at"])


def _insert_unsealed_frontier_parent(
    connection: psycopg.Connection[dict[str, object]],
    *,
    repository: PostgresOperationsRepository,
    store: PostgresDatasetVersionStore,
    instrument_id: int,
    start: datetime,
    source_kind: str,
    ceiling_column: str,
    ceiling: int | None,
    as_of: datetime,
) -> tuple[int, int]:
    accepted = store.create_build(
        **_build_arguments(
            instrument_id,
            start,
            start + timedelta(minutes=1),
            series=(
                _series(
                    instrument_id,
                    "candle",
                    "1m",
                    definition_set_hash=None,
                    calculation_version="source-candle-v1",
                ),
            ),
            missing_policy="null",
        )
    )
    with repository._connect() as build_connection:
        build_connection.execute(
            """
            UPDATE dataset_builds
            SET status='cancelled', finished_at=clock_timestamp()
            WHERE id=%s AND status='pending'
            """,
            (int(accepted["buildId"]),),
        )
    build_series = connection.execute(
        "SELECT id, market_id FROM dataset_build_series WHERE dataset_build_id=%s",
        (int(accepted["buildId"]),),
    ).fetchone()
    assert build_series is not None
    digest = uuid4().hex * 2
    version = connection.execute(
        """
        INSERT INTO dataset_versions (
          schema_version, as_of, input_start_at, output_start_at, end_at,
          fill_policy, missing_policy, ordering_policy, selection_hash,
          manifest_hash, market_status_hash, coverage_hash, content_hash
        ) VALUES (
          'p2-dataset-v1',%s,%s,%s,%s,'none','null','canonical-v1',
          %s,%s,%s,%s,%s
        ) RETURNING id
        """,
        (
            as_of,
            start - timedelta(minutes=2),
            start - timedelta(minutes=1),
            start,
            digest,
            digest,
            digest,
            digest,
            digest,
        ),
    ).fetchone()
    assert version is not None
    data_kind, unit, definition_hash, calculation_version = {
        "source_candle": ("candle", "1m", None, "source-candle-v1"),
        "candle_rollup": ("candle", "3m", None, "candle-rollup-v2"),
        "indicator": ("indicator", "1m", "1" * 64, "indicator-v1"),
        "market_statistic": (
            "market_statistic",
            "1m",
            None,
            "market-statistics-v1",
        ),
        "microstructure": (
            "microstructure",
            "1m",
            "7485c044b6945f406b67de71892ed362a97318261577c2c81cf02da33c674491",
            "microstructure-v1",
        ),
    }[source_kind]
    series = connection.execute(
        sql.SQL(
            """
            INSERT INTO dataset_version_series (
              dataset_version_id, source_build_series_id, market_id, instrument_id,
              data_kind, unit, definition_set_hash, calculation_version, {},
              member_count, members_hash
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s) RETURNING id
            """
        ).format(sql.Identifier(ceiling_column)),
        (
            version["id"],
            build_series["id"],
            build_series["market_id"],
            instrument_id,
            data_kind,
            unit,
            definition_hash,
            calculation_version,
            ceiling,
            digest,
        ),
    ).fetchone()
    assert series is not None
    return cast(int, version["id"]), cast(int, series["id"])


def _insert_matching_typed_member(
    connection: psycopg.Connection[dict[str, object]],
    *,
    version_id: int,
    series_id: int,
    source_kind: str,
    source_id: int,
) -> None:
    statement = {
        "source_candle": """
          INSERT INTO dataset_version_candles (
            dataset_version_id, dataset_version_series_id, instrument_id, unit,
            occurred_at, source_candle_revision_id, candle_rollup_id, quality,
            content_hash, knowledge_at, source_as_of
          ) SELECT %s,%s,instrument_id,candle_unit,candle_start_at,id,NULL,
            'available',input_content_hash,knowledge_at,source_as_of
          FROM source_candle_revisions WHERE id=%s
        """,
        "candle_rollup": """
          INSERT INTO dataset_version_candles (
            dataset_version_id, dataset_version_series_id, instrument_id, unit,
            occurred_at, source_candle_revision_id, candle_rollup_id, quality,
            content_hash, knowledge_at, source_as_of
          ) SELECT %s,%s,instrument_id,candle_unit,candle_start_at,NULL,id,
            'available',result_content_hash,knowledge_at,source_as_of
          FROM candle_rollups WHERE id=%s
        """,
        "indicator": """
          INSERT INTO dataset_version_indicators (
            dataset_version_id, dataset_version_series_id,
            indicator_materialization_id, instrument_id, unit, occurred_at,
            quality, content_hash, knowledge_at, source_as_of
          ) SELECT %s,%s,id,instrument_id,candle_unit,occurred_at,
            'available',content_hash,knowledge_at,source_as_of
          FROM indicator_materializations WHERE id=%s
        """,
        "market_statistic": """
          INSERT INTO dataset_version_market_statistics (
            dataset_version_id, dataset_version_series_id, market_statistic_id,
            instrument_id, unit, occurred_at, quality, content_hash,
            knowledge_at, source_as_of
          ) SELECT %s,%s,id,instrument_id,interval,occurred_at,
            'available',content_hash,knowledge_at,source_as_of
          FROM market_statistics WHERE id=%s
        """,
        "microstructure": """
          INSERT INTO dataset_version_microstructures (
            dataset_version_id, dataset_version_series_id,
            microstructure_materialization_id, instrument_id, unit, occurred_at,
            quality, content_hash, knowledge_at, source_as_of
          ) SELECT %s,%s,materialization.id,materialization.instrument_id,'1m',
            materialization.bucket_start_at,'available',statistic.content_hash,
            materialization.knowledge_at,materialization.source_as_of
          FROM microstructure_materializations materialization
          JOIN microstructure_statistics statistic
            ON statistic.materialization_id=materialization.id
          WHERE materialization.id=%s
        """,
    }[source_kind]
    connection.execute(statement, (version_id, series_id, source_id))


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]


def _seed_market_and_candles(
    *, minutes: int
) -> tuple[
    PostgresOperationsRepository,
    PostgresDatasetVersionStore,
    int,
    datetime,
]:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresDatasetVersionStore(repository)
    market_code = f"KRW-DSET-INV-{uuid4().hex[:10].upper()}"
    instrument = repository.upsert_instrument(market_code, "데이터셋 불변조건")
    start = datetime(2026, 11, 1, tzinfo=UTC) + timedelta(days=uuid4().int % 300)
    _record_market_status(repository, _market_id(repository, instrument.id), start, "active")
    for offset in range(minutes):
        _record_candle(
            repository,
            instrument.id,
            start + timedelta(minutes=offset),
            unit="1m",
            close=str(100 + offset),
        )
    return repository, store, instrument.id, start


def _market_id(repository: PostgresOperationsRepository, instrument_id: int) -> int:
    with repository._connect() as connection:
        row = connection.execute(
            "SELECT id FROM markets WHERE legacy_instrument_id=%s",
            (instrument_id,),
        ).fetchone()
    assert row is not None
    return int(row["id"])


def _record_market_status(
    repository: PostgresOperationsRepository,
    market_id: int,
    start: datetime,
    status: str,
) -> None:
    observed_at = start - timedelta(hours=1)
    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO market_status_history (
              market_id, trading_status, market_warning, market_event,
              source_payload_checksum, valid_from, observed_at
            ) VALUES (%s,%s,'NONE','{}'::jsonb,%s,%s,%s)
            """,
            (market_id, status, "a" * 64, observed_at, observed_at),
        )


def _replace_market_status(
    repository: PostgresOperationsRepository,
    market_id: int,
    observed_at: datetime,
    status: str,
) -> None:
    with repository._connect() as connection:
        current = connection.execute(
            """
            SELECT id FROM market_status_history
            WHERE market_id=%s AND valid_to IS NULL
            ORDER BY id DESC LIMIT 1 FOR UPDATE
            """,
            (market_id,),
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
            (market_id, status, "b" * 64, observed_at, observed_at),
        )


def _record_candle(
    repository: PostgresOperationsRepository,
    instrument_id: int,
    occurred_at: datetime,
    *,
    unit: Literal["1m", "1d"],
    close: str,
) -> None:
    price = Decimal(close)
    knowledge_at = occurred_at + timedelta(seconds=2)
    repository.record_incremental_collection(
        [],
        [],
        [
            SourceCandle(
                instrument_id=instrument_id,
                candle_unit=unit,
                candle_start_at=occurred_at,
                open_price=price,
                high_price=price,
                low_price=price,
                close_price=price,
                trade_volume=Decimal("1"),
                trade_amount=price,
                collected_at=knowledge_at - timedelta(seconds=1),
                knowledge_at=knowledge_at,
            )
        ],
    )


def _series(
    instrument_id: int,
    data_kind: str,
    unit: str,
    *,
    definition_set_hash: str | None,
    calculation_version: str | None,
) -> dict[str, object]:
    return {
        "instrumentId": instrument_id,
        "dataKind": data_kind,
        "unit": unit,
        "definitionSetHash": definition_set_hash,
        "calculationVersion": calculation_version,
    }


def _build_arguments(
    instrument_id: int,
    start: datetime,
    end: datetime,
    *,
    series: tuple[dict[str, object], ...],
    missing_policy: str,
) -> dict[str, object]:
    as_of = end + timedelta(hours=1)
    return {
        "request_id": f"request-{uuid4().hex}",
        "idempotency_key": f"dataset-{uuid4().hex}",
        "actor_id": "operator:e2e",
        "requested_at": as_of + timedelta(minutes=1),
        "reason": "P2-5 불변조건 RED E2E",
        "selection": {
            "asOf": as_of,
            "from": start,
            "to": end,
            "series": list(series),
        },
        "policies": {
            "availabilityPolicy": "point_in_time_v1",
            "fillPolicy": "none",
            "missingPolicy": missing_policy,
        },
    }


def _publish_candle_version(
    store: PostgresDatasetVersionStore,
    instrument_id: int,
    start: datetime,
    end: datetime,
    *,
    calculation_version: str,
) -> tuple[int, int]:
    accepted = store.create_build(
        **_build_arguments(
            instrument_id,
            start,
            end,
            series=(
                _series(
                    instrument_id,
                    "candle",
                    "1m",
                    definition_set_hash=None,
                    calculation_version=calculation_version,
                ),
            ),
            missing_policy="fail",
        )
    )
    store.publish_next_build("dataset-invariant-worker")
    completed = store.get_build(int(accepted["buildId"]))
    assert completed is not None and completed["status"] == "succeeded"
    version_id = int(completed["datasetVersionId"])
    version = store.get_version(version_id)
    assert version is not None
    return version_id, int(version["series"][0]["seriesId"])


def _quality_specs(
    repository: PostgresOperationsRepository,
    market_id: int,
    start: datetime,
) -> tuple[int, int]:
    with repository._connect() as connection:
        policy = connection.execute(
            """
            INSERT INTO collection_policies (
              exchange, quote_currency, name, default_start_at, priority
            ) VALUES ('UPBIT','KRW',%s,%s,100) RETURNING id
            """,
            (f"dataset-invariant-{uuid4().hex}", start),
        ).fetchone()
        assert policy is not None
        rows = connection.execute(
            """
            INSERT INTO collection_target_specs (
              policy_id, market_id, data_type, candle_unit,
              range_start_at, priority, continuous, status
            ) VALUES
              (%s,%s,'source_candle','1m',%s,100,true,'active'),
              (%s,%s,'orderbook_snapshot',NULL,%s,100,true,'active')
            RETURNING id, data_type
            """,
            (policy["id"], market_id, start, policy["id"], market_id, start),
        ).fetchall()
    by_type = {str(row["data_type"]): int(row["id"]) for row in rows}
    return by_type["source_candle"], by_type["orderbook_snapshot"]


def _quality_event(
    repository: PostgresOperationsRepository,
    target_spec_id: int,
    start: datetime,
    end: datetime,
    status: str,
) -> None:
    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO data_quality_events (
              target_spec_id, event_type, previous_status, new_status,
              range_start_at, range_end_at, fingerprint, evidence, detected_at
            ) VALUES (%s,'dataset-invariant',NULL,%s,%s,%s,%s,'{}'::jsonb,%s)
            """,
            (target_spec_id, status, start, end, uuid4().hex, end),
        )


def _insert_after_publication(
    connection: psycopg.Connection[dict[str, object]],
    *,
    table_name: str,
    version_id: int,
    series_id: int,
    instrument_id: int,
    occurred_at: datetime,
) -> None:
    clone_sql = {
        "dataset_version_series": """
            INSERT INTO dataset_version_series (
              dataset_version_id, source_build_series_id, market_id, instrument_id,
              data_kind, unit, definition_set_hash, calculation_version,
              source_revision_through_id, candle_rollup_through_id,
              quality_event_through_id, indicator_materialization_through_id,
              market_statistic_through_id, microstructure_materialization_through_id,
              market_status_history_through_id, orderbook_snapshot_through_id,
              trade_event_through_id, source_receipt_through_id,
              connection_quality_through_id, member_count, members_hash
            ) SELECT dataset_version_id, source_build_series_id, market_id, instrument_id,
              data_kind, unit, definition_set_hash, calculation_version,
              source_revision_through_id, candle_rollup_through_id,
              quality_event_through_id, indicator_materialization_through_id,
              market_statistic_through_id, microstructure_materialization_through_id,
              market_status_history_through_id, orderbook_snapshot_through_id,
              trade_event_through_id, source_receipt_through_id,
              connection_quality_through_id, member_count, members_hash
            FROM dataset_version_series WHERE id=%s
        """,
        "dataset_version_candles": """
            INSERT INTO dataset_version_candles
            SELECT * FROM dataset_version_candles
            WHERE dataset_version_id=%s LIMIT 1
        """,
        "dataset_version_market_status_snapshots": """
            INSERT INTO dataset_version_market_status_snapshots
            SELECT * FROM dataset_version_market_status_snapshots
            WHERE dataset_version_id=%s LIMIT 1
        """,
        "dataset_version_coverage_snapshots": """
            INSERT INTO dataset_version_coverage_snapshots
            SELECT * FROM dataset_version_coverage_snapshots
            WHERE dataset_version_id=%s LIMIT 1
        """,
    }
    if table_name in clone_sql:
        key = series_id if table_name == "dataset_version_series" else version_id
        connection.execute(clone_sql[table_name], (key,))
        return
    typed = {
        "dataset_version_indicators": "indicator_materialization_id",
        "dataset_version_market_statistics": "market_statistic_id",
        "dataset_version_microstructures": "microstructure_materialization_id",
    }
    source_ref_id = _seed_typed_source(
        connection,
        table_name=table_name,
        instrument_id=instrument_id,
        occurred_at=occurred_at,
    )
    connection.execute(
        sql.SQL(
            "INSERT INTO {} (dataset_version_id, dataset_version_series_id, {}, "
            "instrument_id, unit, occurred_at, quality, content_hash, knowledge_at, source_as_of) "
            "VALUES (%s,%s,%s,%s,'1m',%s,'available',%s,%s,%s)"
        ).format(sql.Identifier(table_name), sql.Identifier(typed[table_name])),
        (
            version_id,
            series_id,
            source_ref_id,
            instrument_id,
            occurred_at,
            "f" * 64,
            occurred_at,
            occurred_at,
        ),
    )


def _seed_typed_source(
    connection: psycopg.Connection[dict[str, object]],
    *,
    table_name: str,
    instrument_id: int,
    occurred_at: datetime,
    definition_set_hash: str = "1" * 64,
    calculation_status: str = "ready",
) -> int:
    source = connection.execute(
        """
        SELECT revision.*, market.id AS resolved_market_id
        FROM source_candle_revisions revision
        JOIN markets market ON market.id=revision.market_id
        WHERE revision.instrument_id=%s
        ORDER BY revision.id DESC LIMIT 1
        """,
        (instrument_id,),
    ).fetchone()
    assert source is not None
    if table_name == "dataset_version_indicators":
        inserted = connection.execute(
            """
            INSERT INTO indicator_materializations (
              instrument_id, market_id, candle_unit, occurred_at,
              definition_set_hash, current_source_revision_id, lineage_hash,
              source_revision_through_id, knowledge_at, source_as_of,
              calculation_status, checkpoint_state, content_hash
            ) VALUES (%s,%s,'1m',%s,%s,%s,%s,%s,%s,%s,%s,'{}'::jsonb,%s)
            RETURNING id
            """,
            (
                instrument_id,
                source["resolved_market_id"],
                occurred_at,
                definition_set_hash,
                source["id"],
                "2" * 64,
                source["id"],
                source["knowledge_at"],
                source["source_as_of"],
                calculation_status,
                "3" * 64,
            ),
        ).fetchone()
    elif table_name == "dataset_version_market_statistics":
        inserted = connection.execute(
            """
            INSERT INTO market_statistics (
              market_id, instrument_id, interval, occurred_at, calculation_version,
              volatility_sample_count, input_completeness_ratio,
              return_status, volatility_status, trade_status,
              current_source_revision_id, source_revision_through_id,
              source_as_of, knowledge_at, lineage_hash, checkpoint_state, content_hash
            ) VALUES (
              %s,%s,'1m',%s,'market-statistics-v1',0,1,
              'ready','ready','ready',%s,%s,%s,%s,%s,'{}'::jsonb,%s
            ) RETURNING id
            """,
            (
                source["resolved_market_id"],
                instrument_id,
                occurred_at,
                source["id"],
                source["id"],
                source["source_as_of"],
                source["knowledge_at"],
                "4" * 64,
                "5" * 64,
            ),
        ).fetchone()
    else:
        connection_id = uuid4()
        connection.execute(
            """
            INSERT INTO realtime_connection_sessions (connection_id, connected_at)
            VALUES (%s,%s)
            """,
            (connection_id, occurred_at - timedelta(minutes=1)),
        )
        quality = connection.execute(
            """
            INSERT INTO realtime_connection_quality_intervals (
              connection_id, market_id, data_type, range_start_at, range_end_at,
              quality, reason_code, fingerprint, detected_at
            ) VALUES (%s,%s,'source_candle',%s,%s,'available','e2e',%s,%s)
            RETURNING id
            """,
            (
                connection_id,
                source["resolved_market_id"],
                occurred_at,
                occurred_at + timedelta(minutes=1),
                "6" * 64,
                occurred_at,
            ),
        ).fetchone()
        definition = connection.execute(
            """
            SELECT id FROM microstructure_definition_versions
            WHERE calculation_version='microstructure-v1'
            """
        ).fetchone()
        assert quality is not None and definition is not None
        inserted = connection.execute(
            """
            INSERT INTO microstructure_materializations (
              instrument_id, market_id, definition_version_id, bucket_start_at,
              source_candle_revision_id, orderbook_snapshot_through_id,
              trade_event_through_id, source_receipt_through_id,
              connection_quality_through_id, knowledge_at, source_as_of,
              input_lineage_hash, content_hash
            ) VALUES (%s,%s,%s,%s,%s,0,0,0,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                instrument_id,
                source["resolved_market_id"],
                definition["id"],
                occurred_at,
                source["id"],
                quality["id"],
                source["knowledge_at"],
                source["source_as_of"],
                "7" * 64,
                "8" * 64,
            ),
        ).fetchone()
        assert inserted is not None
        connection.execute(
            """
            INSERT INTO microstructure_statistics (
              materialization_id, orderbook_status, orderbook_quality,
              trade_status, trade_quality, execution_strength_status, content_hash
            ) VALUES (
              %s,'missing','missing','missing','missing','undefined',%s
            )
            """,
            (inserted["id"], "9" * 64),
        )
    assert inserted is not None
    return cast(int, inserted["id"])
