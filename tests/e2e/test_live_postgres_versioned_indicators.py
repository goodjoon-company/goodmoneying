from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

import goodmoneying_shared.indicator_store as indicator_store
from goodmoneying_api.service import OperationsService
from goodmoneying_shared.coverage_transition import replace_coverage_with_classification
from goodmoneying_shared.indicator_store import run_next_indicator_invalidation
from goodmoneying_shared.models import Instrument, SourceCandle
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.versioned_indicators import calculate_indicator_series

pytestmark = pytest.mark.live


def test_live_postgres_지표_worker는_1m_frontier별_불변_물질화를_만들고_GET은_읽기만_한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    instrument = _instrument(repository, "KRW-P2IND", "P2 지표")
    start = datetime(2026, 7, 17, tzinfo=UTC)
    candles = [
        _candle(instrument.id, start + timedelta(minutes=index), Decimal(index + 1))
        for index in range(25)
    ]
    repository.record_incremental_collection([], [], candles)

    processed = 0
    while rows := run_next_indicator_invalidation(repository, "indicator-live-worker"):
        processed += rows
    assert processed > 0
    with repository._connect() as connection:
        before_row = connection.execute(
            "SELECT count(*) AS count FROM indicator_materializations"
        ).fetchone()
        statistic_row = connection.execute(
            "SELECT count(*) AS count FROM market_statistics WHERE instrument_id=%s",
            (instrument.id,),
        ).fetchone()
        assert before_row is not None and statistic_row is not None
        before = before_row["count"]
        statistic_count = statistic_row["count"]
    response = OperationsService(repository).indicator_points(
        instrument.id,
        "1m",
        start,
        start + timedelta(minutes=25),
        as_of=start + timedelta(hours=2),
        page_size=500,
        cursor=None,
        definition_version=None,
    )
    assert len(response.items) == 25
    assert response.items[19].values["ema20"] == "10.5"
    assert all(item.materializationId is not None for item in response.items)
    with repository._connect() as connection:
        after_row = connection.execute(
            "SELECT count(*) AS count FROM indicator_materializations"
        ).fetchone()
        assert after_row is not None
        after = after_row["count"]
    assert before == after
    assert statistic_count == 25
    with repository._connect() as connection:
        chains = connection.execute(
            """
            WITH RECURSIVE materialization_chain AS (
              SELECT latest.id, latest.parent_materialization_id, 1 AS depth
              FROM (
                SELECT id, parent_materialization_id
                FROM indicator_materializations
                WHERE instrument_id=%s ORDER BY occurred_at DESC, id DESC LIMIT 1
              ) latest
              UNION ALL
              SELECT parent.id, parent.parent_materialization_id, chain.depth+1
              FROM indicator_materializations parent
              JOIN materialization_chain chain ON parent.id=chain.parent_materialization_id
            ), value_chain AS (
              SELECT latest.id, latest.parent_value_id, 1 AS depth
              FROM (
                SELECT value.id, value.parent_value_id
                FROM indicator_values value
                JOIN indicator_materializations materialization
                  ON materialization.id=value.materialization_id
                WHERE materialization.instrument_id=%s AND value.value_name='ema20'
                ORDER BY materialization.occurred_at DESC, value.id DESC LIMIT 1
              ) latest
              UNION ALL
              SELECT parent.id, parent.parent_value_id, chain.depth+1
              FROM indicator_values parent
              JOIN value_chain chain ON parent.id=chain.parent_value_id
            ), statistic_chain AS (
              SELECT latest.id, latest.parent_statistic_id, 1 AS depth
              FROM (
                SELECT id, parent_statistic_id
                FROM market_statistics
                WHERE instrument_id=%s ORDER BY occurred_at DESC, id DESC LIMIT 1
              ) latest
              UNION ALL
              SELECT parent.id, parent.parent_statistic_id, chain.depth+1
              FROM market_statistics parent
              JOIN statistic_chain chain ON parent.id=chain.parent_statistic_id
            )
            SELECT (SELECT MAX(depth) FROM materialization_chain) AS materialization_depth,
                   (SELECT MAX(depth) FROM value_chain) AS value_depth,
                   (SELECT MAX(depth) FROM statistic_chain) AS statistic_depth
            """,
            (instrument.id, instrument.id, instrument.id),
        ).fetchone()
        trigger_names = {
            row["tgname"]
            for row in connection.execute(
                """
                SELECT tgname FROM pg_trigger
                WHERE tgname IN (
                  'indicator_definitions_append_only',
                  'indicator_value_rollups_append_only'
                ) AND NOT tgisinternal
                """
            ).fetchall()
        }
    assert chains is not None
    assert (
        chains["materialization_depth"],
        chains["value_depth"],
        chains["statistic_depth"],
    ) == (25, 25, 25)
    assert trigger_names == {
        "indicator_definitions_append_only",
        "indicator_value_rollups_append_only",
    }
    with (
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            UPDATE indicator_definitions SET display_name='변경 금지'
            WHERE indicator_key='sma20'
            """
        )


def test_live_postgres_A_B_A_개정과_asOf가_중간_B를_보존한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    instrument = _instrument(repository, "KRW-P2ABA", "P2 A B A")
    started_at = datetime(2026, 7, 18, tzinfo=UTC)
    repository.record_incremental_collection(
        [],
        [],
        [
            _candle(
                instrument.id,
                started_at + timedelta(minutes=index),
                Decimal(index + 1),
            )
            for index in range(19)
        ],
    )
    while run_next_indicator_invalidation(repository, "indicator-aba-warmup"):
        pass
    target_at = started_at + timedelta(minutes=19)
    as_of_values: list[datetime] = []
    expected = ("10.5", "11.5", "10.5")
    for offset, close in enumerate((Decimal("20"), Decimal("40"), Decimal("20"))):
        repository.record_incremental_collection(
            [], [], [_candle(instrument.id, target_at, close, hours=offset)]
        )
        as_of_values.append(target_at + timedelta(hours=offset, seconds=2))

    while run_next_indicator_invalidation(repository, "indicator-aba-queued"):
        pass
    with repository._connect() as connection:
        succeeded_revisions = connection.execute(
            """
            SELECT COUNT(*) AS count FROM indicator_invalidations
            WHERE instrument_id=%s AND candle_unit='1m'
              AND impact_start_at=%s AND changed_source_revision_id IS NOT NULL
              AND status='succeeded'
            """,
            (instrument.id, target_at),
        ).fetchone()
    assert succeeded_revisions == {"count": 3}

    service = OperationsService(repository)
    materialization_ids: list[int | None] = []
    for as_of, expected_value in zip(as_of_values, expected, strict=True):
        response = service.indicator_points(
            instrument.id,
            "1m",
            started_at,
            target_at + timedelta(minutes=1),
            as_of=as_of,
            page_size=500,
            cursor=None,
            definition_version=None,
        )
        assert response.items[-1].values["sma20"] == expected_value
        materialization_ids.append(response.items[-1].materializationId)
    assert len(set(materialization_ids)) == 3

    middle_statistics = service.market_statistics(
        instrument.id,
        "1m",
        started_at,
        target_at + timedelta(minutes=1),
        as_of=as_of_values[1],
        page_size=500,
        cursor=None,
        calculation_version=None,
    )
    assert middle_statistics.items[-1].tradeAmount == "40"


def test_live_postgres_cursor는_첫_페이지_ceiling_뒤의_늦은_물질화를_섞지_않는다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    instrument = _instrument(repository, "KRW-P2PAGE", "P2 페이지 ceiling")
    started_at = datetime(2026, 7, 20, tzinfo=UTC)
    repository.record_incremental_collection(
        [],
        [],
        [
            _candle(instrument.id, started_at + timedelta(minutes=index), Decimal(index + 1))
            for index in range(3)
        ],
    )
    while run_next_indicator_invalidation(repository, "indicator-page-initial"):
        pass
    as_of = started_at + timedelta(days=1)
    service = OperationsService(repository)
    first_page = service.indicator_points(
        instrument.id,
        "1m",
        started_at,
        started_at + timedelta(minutes=3),
        as_of=as_of,
        page_size=1,
        cursor=None,
        definition_version=None,
    )
    assert first_page.nextCursor is not None
    second_at = started_at + timedelta(minutes=1)
    with repository._connect() as connection:
        old_row = connection.execute(
            """
            SELECT id FROM indicator_materializations
            WHERE instrument_id=%s AND candle_unit='1m' AND occurred_at=%s
            ORDER BY id DESC LIMIT 1
            """,
            (instrument.id, second_at),
        ).fetchone()
    assert old_row is not None
    old_second_id = int(old_row["id"])

    repository.record_incremental_collection(
        [], [], [_candle(instrument.id, second_at, Decimal("200"), hours=12)]
    )
    while run_next_indicator_invalidation(repository, "indicator-page-late"):
        pass
    with repository._connect() as connection:
        new_row = connection.execute(
            """
            SELECT id, knowledge_at FROM indicator_materializations
            WHERE instrument_id=%s AND candle_unit='1m' AND occurred_at=%s
            ORDER BY id DESC LIMIT 1
            """,
            (instrument.id, second_at),
        ).fetchone()
    assert new_row is not None
    assert int(new_row["id"]) > old_second_id
    assert new_row["knowledge_at"] <= as_of

    second_page = service.indicator_points(
        instrument.id,
        "1m",
        started_at,
        started_at + timedelta(minutes=3),
        as_of=as_of,
        page_size=1,
        cursor=first_page.nextCursor,
        definition_version=None,
    )
    assert second_page.items[0].startedAt == second_at
    assert second_page.items[0].materializationId == old_second_id


def test_live_postgres_1m_품질_A_B_A는_중간_missing과_복구_계보를_보존한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    instrument = _instrument(repository, "KRW-P2QUALITY", "P2 품질 A B A")
    target_spec_id = _ensure_source_spec(repository, instrument.id)
    started_at = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(days=1)
    repository.record_incremental_collection(
        [],
        [],
        [
            _candle(instrument.id, started_at + timedelta(minutes=index), Decimal(index + 1))
            for index in range(25)
        ],
    )
    while run_next_indicator_invalidation(repository, "indicator-quality-initial"):
        pass
    target_at = started_at + timedelta(minutes=20)
    initial_as_of = started_at + timedelta(hours=2)

    with repository._connect() as connection:
        replace_coverage_with_classification(
            connection,
            target_spec_id=target_spec_id,
            range_start_at=target_at,
            range_end_at=target_at + timedelta(minutes=1),
            status="missing",
            reason_code="p2_indicator_quality_missing",
            manifest_id=None,
            evidence={"test": "quality-a-b-a"},
        )
    while run_next_indicator_invalidation(repository, "indicator-quality-missing"):
        pass
    with repository._connect() as connection:
        missing_event = connection.execute(
            """
            SELECT id, detected_at FROM data_quality_events
            WHERE target_spec_id=%s AND new_status='missing'
            ORDER BY id DESC LIMIT 1
            """,
            (target_spec_id,),
        ).fetchone()
    assert missing_event is not None

    with repository._connect() as connection:
        replace_coverage_with_classification(
            connection,
            target_spec_id=target_spec_id,
            range_start_at=target_at,
            range_end_at=target_at + timedelta(minutes=1),
            status="available",
            reason_code="p2_indicator_quality_available",
            manifest_id=None,
            evidence={"test": "quality-a-b-a"},
        )
    while run_next_indicator_invalidation(repository, "indicator-quality-available"):
        pass
    with repository._connect() as connection:
        available_event = connection.execute(
            """
            SELECT id, detected_at FROM data_quality_events
            WHERE target_spec_id=%s AND new_status='available'
            ORDER BY id DESC LIMIT 1
            """,
            (target_spec_id,),
        ).fetchone()
    assert available_event is not None

    service = OperationsService(repository)
    statuses: list[str] = []
    materialization_ids: list[int | None] = []
    for as_of in (
        initial_as_of,
        missing_event["detected_at"],
        available_event["detected_at"],
    ):
        response = service.indicator_points(
            instrument.id,
            "1m",
            target_at,
            target_at + timedelta(minutes=1),
            as_of=as_of,
            page_size=10,
            cursor=None,
            definition_version=None,
        )
        statuses.append(response.items[0].statuses["sma20"])
        materialization_ids.append(response.items[0].materializationId)
    assert statuses == ["ready", "missing", "ready"]
    assert len(set(materialization_ids)) == 3

    middle_statistics = service.market_statistics(
        instrument.id,
        "1m",
        target_at,
        target_at + timedelta(minutes=1),
        as_of=missing_event["detected_at"],
        page_size=10,
        cursor=None,
        calculation_version=None,
    )
    assert middle_statistics.items[0].tradeStatus == "missing"


def test_live_postgres_checkpoint_append와_과거정정_suffix를_재현한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    instrument = _instrument(repository, "KRW-P2CHECKPOINT", "P2 체크포인트")
    started_at = datetime(2026, 7, 21, tzinfo=UTC)
    repository.record_incremental_collection(
        [],
        [],
        [
            _candle(instrument.id, started_at + timedelta(minutes=index), Decimal(index + 1))
            for index in range(100)
        ],
    )
    while run_next_indicator_invalidation(repository, "indicator-checkpoint-initial"):
        pass
    service = OperationsService(repository)
    before_as_of = started_at + timedelta(hours=2)
    before = service.indicator_points(
        instrument.id,
        "1m",
        started_at + timedelta(minutes=99),
        started_at + timedelta(minutes=100),
        as_of=before_as_of,
        page_size=10,
        cursor=None,
        definition_version=None,
    ).items[0]

    appended_at = started_at + timedelta(minutes=100)
    repository.record_incremental_collection(
        [], [], [_candle(instrument.id, appended_at, Decimal("101"), hours=3)]
    )
    assert run_next_indicator_invalidation(repository, "indicator-checkpoint-append") == 2

    correction_at = started_at + timedelta(minutes=50)
    repository.record_incremental_collection(
        [], [], [_candle(instrument.id, correction_at, Decimal("500"), hours=4)]
    )
    processed = run_next_indicator_invalidation(repository, "indicator-checkpoint-correction")
    assert processed == 102
    after_as_of = correction_at + timedelta(hours=4, seconds=2)
    after = service.indicator_points(
        instrument.id,
        "1m",
        started_at + timedelta(minutes=99),
        started_at + timedelta(minutes=100),
        as_of=after_as_of,
        page_size=10,
        cursor=None,
        definition_version=None,
    ).items[0]
    before_again = service.indicator_points(
        instrument.id,
        "1m",
        started_at + timedelta(minutes=99),
        started_at + timedelta(minutes=100),
        as_of=before_as_of,
        page_size=10,
        cursor=None,
        definition_version=None,
    ).items[0]
    assert before_again.materializationId == before.materializationId
    assert after.materializationId != before.materializationId
    assert after.sourceRevisionThroughId > before.sourceRevisionThroughId
    assert after.knowledgeAt > before.knowledgeAt


def test_live_postgres_512개_checkpoint_청크는_정정_frontier를_끝까지_유지한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    instrument = _instrument(repository, "KRW-P2CHUNK", "P2 체크포인트 청크")
    started_at = datetime(2026, 7, 23, tzinfo=UTC)
    repository.record_incremental_collection(
        [],
        [],
        [
            _candle(instrument.id, started_at + timedelta(minutes=index), Decimal(index + 1))
            for index in range(520)
        ],
    )
    with repository._connect() as connection:
        latest = connection.execute(
            """
            SELECT MAX(id) AS id FROM indicator_invalidations
            WHERE instrument_id=%s AND candle_unit='1m'
            """,
            (instrument.id,),
        ).fetchone()
        assert latest is not None and latest["id"] is not None
        connection.execute(
            """
            UPDATE indicator_invalidations
            SET status='succeeded', finished_at=clock_timestamp()
            WHERE instrument_id=%s AND candle_unit='1m' AND id<>%s
            """,
            (instrument.id, latest["id"]),
        )
        connection.execute(
            """
            UPDATE indicator_invalidations SET impact_start_at=%s, progress_at=NULL
            WHERE id=%s
            """,
            (started_at, latest["id"]),
        )

    assert run_next_indicator_invalidation(repository, "indicator-chunk-initial-1") == 1024
    assert run_next_indicator_invalidation(repository, "indicator-chunk-initial-2") == 16

    correction_source_as_of = started_at + timedelta(hours=12, seconds=1)
    repository.record_incremental_collection(
        [], [], [_candle(instrument.id, started_at, Decimal("999"), hours=12)]
    )
    assert run_next_indicator_invalidation(repository, "indicator-chunk-correction-1") == 1024
    assert run_next_indicator_invalidation(repository, "indicator-chunk-correction-2") == 16
    with repository._connect() as connection:
        last = connection.execute(
            """
            SELECT source_as_of, source_revision_through_id
            FROM indicator_materializations
            WHERE instrument_id=%s AND candle_unit='1m'
              AND occurred_at=%s
            ORDER BY id DESC LIMIT 1
            """,
            (instrument.id, started_at + timedelta(minutes=519)),
        ).fetchone()
        invalidation = connection.execute(
            """
            SELECT status, progress_at FROM indicator_invalidations
            WHERE instrument_id=%s AND changed_source_revision_id IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """,
            (instrument.id,),
        ).fetchone()
    assert last is not None and invalidation is not None
    assert last["source_as_of"] == correction_source_as_of
    assert invalidation == {"status": "succeeded", "progress_at": None}


def test_live_postgres_선행_512청크_중에는_후속_frontier를_claim하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = PostgresOperationsRepository(_database_url())
    instrument = _instrument(repository, "KRW-P2SERIAL", "P2 프런티어 직렬화")
    started_at = datetime(2026, 7, 24, tzinfo=UTC)
    initial_candles = [
        _candle(instrument.id, started_at + timedelta(minutes=index), Decimal(index + 1))
        for index in range(520)
    ]
    repository.record_incremental_collection([], [], initial_candles)
    with repository._connect() as connection:
        latest = connection.execute(
            """
            SELECT MAX(id) AS id FROM indicator_invalidations
            WHERE instrument_id=%s AND candle_unit='1m'
            """,
            (instrument.id,),
        ).fetchone()
        assert latest is not None and latest["id"] is not None
        connection.execute(
            """
            UPDATE indicator_invalidations
            SET status='succeeded', finished_at=clock_timestamp()
            WHERE instrument_id=%s AND candle_unit='1m' AND id<>%s
            """,
            (instrument.id, latest["id"]),
        )
        connection.execute(
            """
            UPDATE indicator_invalidations SET impact_start_at=%s, progress_at=NULL
            WHERE id=%s
            """,
            (started_at, latest["id"]),
        )

    calculation_started = threading.Event()
    release_first_worker = threading.Event()
    original_calculate = calculate_indicator_series
    call_lock = threading.Lock()
    call_count = 0

    def block_first_calculation(*args: object, **kwargs: object) -> object:
        nonlocal call_count
        with call_lock:
            call_count += 1
            current_call = call_count
        if current_call == 1:
            calculation_started.set()
            assert release_first_worker.wait(10)
        return original_calculate(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(indicator_store, "calculate_indicator_series", block_first_calculation)
    results: dict[str, int] = {}
    errors: list[BaseException] = []
    finished = {"a": threading.Event(), "b": threading.Event()}

    def run_worker(name: str) -> None:
        try:
            results[name] = run_next_indicator_invalidation(repository, f"indicator-serial-{name}")
        except BaseException as exc:  # pragma: no cover - 스레드 실패 전달
            errors.append(exc)
        finally:
            finished[name].set()

    worker_a = threading.Thread(target=run_worker, args=("a",), daemon=True)
    worker_a.start()
    assert calculation_started.wait(10)

    appended = _candle(
        instrument.id,
        started_at + timedelta(minutes=520),
        Decimal("521"),
        hours=12,
    )
    repository.record_incremental_collection([], [], [appended])
    worker_b = threading.Thread(target=run_worker, args=("b",), daemon=True)
    worker_b.start()
    second_worker_rejected_before_release = finished["b"].wait(1)
    release_first_worker.set()
    worker_a.join(20)
    worker_b.join(20)

    assert not errors
    assert second_worker_rejected_before_release
    assert results == {"a": 1024, "b": 0}

    while run_next_indicator_invalidation(repository, "indicator-serial-finish"):
        pass
    expected_candles = repository.candles(
        instrument.id,
        "1m",
        started_at,
        appended.candle_start_at + timedelta(minutes=1),
    )
    expected = original_calculate(expected_candles, unit="1m")[-1]
    actual = (
        OperationsService(repository)
        .indicator_points(
            instrument.id,
            "1m",
            appended.candle_start_at,
            appended.candle_start_at + timedelta(minutes=1),
            as_of=appended.knowledge_at or appended.collected_at,
            page_size=10,
            cursor=None,
            definition_version=None,
        )
        .items[0]
    )
    assert actual.values["ema20"] == str(expected.values["ema20"])
    assert actual.values["rsi14"] == str(expected.values["rsi14"])


@pytest.mark.parametrize("with_checkpoint", [False, True], ids=["epoch", "stored-checkpoint"])
def test_live_postgres_먼_impact의_예열_checkpoint는_영향범위밖_물질화없이_전진한다(
    with_checkpoint: bool,
) -> None:
    repository = PostgresOperationsRepository(_database_url())
    suffix = "CHECKPOINT" if with_checkpoint else "EPOCH"
    instrument = _instrument(repository, f"KRW-P2WARM{suffix}", f"P2 먼 예열 {suffix}")
    started_at = datetime(2026, 7, 25, tzinfo=UTC)
    candles = [
        _candle(instrument.id, started_at + timedelta(minutes=index), Decimal(index + 1))
        for index in range(601)
    ]
    repository.record_incremental_collection([], [], candles)
    with repository._connect() as connection:
        bounds = connection.execute(
            """
            SELECT MIN(id) AS first_id, MAX(id) AS last_id,
                   (ARRAY_AGG(source_revision_through_id ORDER BY id DESC))[1]
                     AS source_ceiling
            FROM indicator_invalidations
            WHERE instrument_id=%s AND candle_unit='1m'
            """,
            (instrument.id,),
        ).fetchone()
        assert bounds is not None
        preserved_ids = [bounds["last_id"]]
        if with_checkpoint:
            preserved_ids.append(bounds["first_id"])
        connection.execute(
            """
            UPDATE indicator_invalidations
            SET status='succeeded', finished_at=clock_timestamp()
            WHERE instrument_id=%s AND candle_unit='1m' AND NOT (id=ANY(%s))
            """,
            (instrument.id, preserved_ids),
        )
    if with_checkpoint:
        assert run_next_indicator_invalidation(repository, "indicator-warmup-checkpoint") == 2

    first = run_next_indicator_invalidation(repository, "indicator-warmup-far-1")
    second = run_next_indicator_invalidation(repository, "indicator-warmup-far-2")
    expected_second = 176 if with_checkpoint else 178
    assert (first, second) == (1024, expected_second)
    with repository._connect() as connection:
        invalidation = connection.execute(
            """
            SELECT status, progress_at, indicator_checkpoint_state,
                   statistic_checkpoint_state
            FROM indicator_invalidations WHERE id=%s
            """,
            (bounds["last_id"],),
        ).fetchone()
        counts = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM indicator_materializations
               WHERE instrument_id=%s AND source_revision_through_id=%s) AS indicators,
              (SELECT COUNT(*) FROM market_statistics
               WHERE instrument_id=%s AND source_revision_through_id=%s) AS statistics
            """,
            (
                instrument.id,
                bounds["source_ceiling"],
                instrument.id,
                bounds["source_ceiling"],
            ),
        ).fetchone()
        lineage = connection.execute(
            """
            WITH RECURSIVE indicator_chain AS (
              SELECT latest.id, latest.parent_materialization_id,
                     latest.current_source_revision_id, 1 AS depth
              FROM (
                SELECT id, parent_materialization_id, current_source_revision_id
                FROM indicator_materializations
                WHERE instrument_id=%s ORDER BY occurred_at DESC, id DESC LIMIT 1
              ) latest
              UNION ALL
              SELECT parent.id, parent.parent_materialization_id,
                     parent.current_source_revision_id, chain.depth+1
              FROM indicator_materializations parent
              JOIN indicator_chain chain ON parent.id=chain.parent_materialization_id
            ), value_chain AS (
              SELECT latest.id, latest.parent_value_id, 1 AS depth
              FROM (
                SELECT value.id, value.parent_value_id
                FROM indicator_values value
                JOIN indicator_materializations materialization
                  ON materialization.id=value.materialization_id
                JOIN indicator_definition_versions version
                  ON version.id=value.definition_version_id
                JOIN indicator_definitions definition ON definition.id=version.definition_id
                WHERE materialization.instrument_id=%s
                  AND definition.indicator_key='ema20'
                ORDER BY materialization.occurred_at DESC, value.id DESC LIMIT 1
              ) latest
              UNION ALL
              SELECT parent.id, parent.parent_value_id, chain.depth+1
              FROM indicator_values parent
              JOIN value_chain chain ON parent.id=chain.parent_value_id
            ), statistic_chain AS (
              SELECT latest.id, latest.parent_statistic_id,
                     latest.current_source_revision_id, 1 AS depth
              FROM (
                SELECT id, parent_statistic_id, current_source_revision_id
                FROM market_statistics
                WHERE instrument_id=%s ORDER BY occurred_at DESC, id DESC LIMIT 1
              ) latest
              UNION ALL
              SELECT parent.id, parent.parent_statistic_id,
                     parent.current_source_revision_id, chain.depth+1
              FROM market_statistics parent
              JOIN statistic_chain chain ON parent.id=chain.parent_statistic_id
            )
            SELECT
              (SELECT MAX(depth) FROM indicator_chain) AS indicator_depth,
              (SELECT COUNT(DISTINCT current_source_revision_id)
               FROM indicator_chain) AS indicator_inputs,
              (SELECT MAX(depth) FROM value_chain) AS value_depth,
              (SELECT MAX(depth) FROM statistic_chain) AS statistic_depth,
              (SELECT COUNT(DISTINCT current_source_revision_id)
               FROM statistic_chain) AS statistic_inputs
            """,
            (instrument.id, instrument.id, instrument.id),
        ).fetchone()
    assert invalidation == {
        "status": "succeeded",
        "progress_at": None,
        "indicator_checkpoint_state": None,
        "statistic_checkpoint_state": None,
    }
    expected_recovery_rows = 600 if with_checkpoint else 601
    assert counts == {
        "indicators": expected_recovery_rows,
        "statistics": expected_recovery_rows,
    }
    assert lineage == {
        "indicator_depth": 601,
        "indicator_inputs": 601,
        "value_depth": 601,
        "statistic_depth": 601,
        "statistic_inputs": 601,
    }


def test_live_postgres_fence_transaction은_완료전_실패한_물질화를_전부_rollback한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = PostgresOperationsRepository(_database_url())
    instrument = _instrument(repository, "KRW-P2FENCE", "P2 fence")
    started_at = datetime(2026, 7, 22, tzinfo=UTC)
    repository.record_incremental_collection(
        [], [], [_candle(instrument.id, started_at, Decimal("1"))]
    )
    original = indicator_store.materialize_market_statistics

    def fail_after_statistic_write(*args: object, **kwargs: object) -> object:
        original(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("fence-rollback-probe")

    monkeypatch.setattr(
        indicator_store, "materialize_market_statistics", fail_after_statistic_write
    )
    with pytest.raises(RuntimeError, match="fence-rollback-probe"):
        run_next_indicator_invalidation(repository, "indicator-fence-probe")

    with repository._connect() as connection:
        counts = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM indicator_materializations
               WHERE instrument_id=%s) AS indicators,
              (SELECT COUNT(*) FROM market_statistics WHERE instrument_id=%s) AS statistics
            """,
            (instrument.id, instrument.id),
        ).fetchone()
        invalidation = connection.execute(
            """
            SELECT status, lease_owner, lease_expires_at
            FROM indicator_invalidations WHERE instrument_id=%s ORDER BY id DESC LIMIT 1
            """,
            (instrument.id,),
        ).fetchone()
    assert counts is not None and invalidation is not None
    assert (counts["indicators"], counts["statistics"]) == (0, 0)
    assert invalidation["status"] == "retry_wait"
    assert invalidation["lease_owner"] is None
    assert invalidation["lease_expires_at"] is None


def _database_url() -> str:
    value = os.environ.get("GOODMONEYING_DATABASE_URL")
    if not value:
        pytest.skip("GOODMONEYING_DATABASE_URL이 필요하다.")
    return value


def _instrument(
    repository: PostgresOperationsRepository, market_code: str, display_name: str
) -> Instrument:
    return repository.upsert_instrument(market_code, display_name)


def _ensure_source_spec(repository: PostgresOperationsRepository, instrument_id: int) -> int:
    with repository._connect() as connection:
        market = connection.execute(
            "SELECT id FROM markets WHERE legacy_instrument_id=%s", (instrument_id,)
        ).fetchone()
        assert market is not None
        policy = connection.execute(
            """
            INSERT INTO collection_policies (
              exchange, quote_currency, name, default_start_at, priority
            ) VALUES ('UPBIT','KRW','p2-indicator-quality','2024-01-01T00:00:00Z',100)
            ON CONFLICT (exchange, quote_currency, name) DO UPDATE SET updated_at=now()
            RETURNING id
            """
        ).fetchone()
        assert policy is not None
        specification = connection.execute(
            """
            INSERT INTO collection_target_specs (
              policy_id, market_id, data_type, candle_unit, range_start_at,
              priority, continuous, auto_managed, status
            ) VALUES (%s,%s,'source_candle','1m','2024-01-01T00:00:00Z',
                      100,true,true,'active')
            ON CONFLICT (policy_id, market_id, data_type, candle_unit) DO UPDATE
              SET status='active', updated_at=now()
            RETURNING id
            """,
            (policy["id"], market["id"]),
        ).fetchone()
        assert specification is not None
        return int(specification["id"])


def _candle(
    instrument_id: int, started_at: datetime, close: Decimal, *, hours: int = 0
) -> SourceCandle:
    return SourceCandle(
        instrument_id=instrument_id,
        candle_unit="1m",
        candle_start_at=started_at,
        open_price=close,
        high_price=close,
        low_price=close,
        close_price=close,
        trade_volume=Decimal("1"),
        trade_amount=close,
        collected_at=started_at + timedelta(hours=hours, seconds=1),
        knowledge_at=started_at + timedelta(hours=hours, seconds=2),
    )
