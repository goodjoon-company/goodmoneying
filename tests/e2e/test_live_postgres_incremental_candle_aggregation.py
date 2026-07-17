from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from goodmoneying_shared.coverage_transition import replace_coverage_with_classification
from goodmoneying_shared.data_foundation import ROLLUP_FRONTIER_ADVISORY_LOCK_NAMESPACE
from goodmoneying_shared.models import Instrument, SourceCandle
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository

pytestmark = pytest.mark.live


def test_live_postgres_과거_원천_수정은_영향_버킷만_증분_재계산한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    instrument = _refresh_instrument(repository, "KRW-LIVEINC", "실제 증분 집계")
    start = datetime(2026, 7, 17, tzinfo=UTC)
    source = [_candle(instrument.id, start, index) for index in range(6)]
    repository.record_incremental_collection([], [], source)
    repository.materialize_candle_rollups(instrument.id, "3m")
    untouched_before = repository.candle_rollups(
        instrument.id, "3m", start + timedelta(minutes=3), start + timedelta(minutes=6)
    )[0]
    historical = repository.candle_rollups(
        instrument.id, "3m", start, start + timedelta(minutes=3)
    )[0]
    historical_knowledge_at = historical.knowledge_at
    assert historical_knowledge_at is not None
    historical_ceiling = max(historical.input_revision_ids)
    changed = SourceCandle(
        **{
            **source[2].__dict__,
            "close_price": Decimal("777"),
            "collected_at": source[2].collected_at + timedelta(hours=1),
        }
    )

    repository.record_incremental_collection([], [], [changed])
    with repository._connect() as connection:
        row = connection.execute(
            """
            SELECT job.id FROM candle_rollup_recompute_jobs job
            JOIN candle_rollup_invalidations invalidation
              ON invalidation.id = job.invalidation_id
            WHERE invalidation.instrument_id = %s AND invalidation.candle_unit = '3m'
            ORDER BY job.id DESC LIMIT 1
            """,
            (instrument.id,),
        ).fetchone()
        assert row is not None
        job_id = int(row["id"])
    claimed_at = datetime.now(UTC) + timedelta(seconds=1)
    assert repository.claim_candle_rollup_recompute_job(
        job_id, "live-worker", now=claimed_at, lease_seconds=60
    ) is not None
    assert repository.run_candle_rollup_recompute_job(
        job_id, "live-worker", now=claimed_at + timedelta(seconds=1)
    ) == 1

    changed_rollup = repository.candle_rollups(
        instrument.id, "3m", start, start + timedelta(minutes=3)
    )[0]
    untouched_after = repository.candle_rollups(
        instrument.id, "3m", start + timedelta(minutes=3), start + timedelta(minutes=6)
    )[0]
    assert changed_rollup.close == Decimal("777")
    assert untouched_after.input_content_hash == untouched_before.input_content_hash
    assert untouched_after.source_as_of == untouched_before.source_as_of
    assert repository.candle_rollup_recompute_job(job_id).status == "succeeded"
    assert repository.candle_rollups(
        instrument.id,
        "3m",
        start,
        start + timedelta(minutes=3),
        knowledge_at=historical_knowledge_at,
    )[0].close == historical.close
    assert repository.candle_rollups(
        instrument.id,
        "3m",
        start,
        start + timedelta(minutes=3),
        source_revision_through_id=historical_ceiling,
    )[0].close == historical.close
    with repository._connect() as connection:
        revisions = connection.execute(
            """
            SELECT input_content_hash, coverage_snapshot_hash, result_content_hash
            FROM candle_rollups
            WHERE instrument_id = %s AND candle_unit = '3m' AND candle_start_at = %s
            ORDER BY id
            """,
            (instrument.id, start),
        ).fetchall()
        assert len(revisions) == 2
        assert revisions[0]["input_content_hash"] != revisions[1]["input_content_hash"]
        assert all(
            len(row[field]) == 64
            for row in revisions
            for field in (
                "input_content_hash",
                "coverage_snapshot_hash",
                "result_content_hash",
            )
        )


def test_live_postgres_커버리지_전이만으로_새_품질_집계_개정을_추가한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    instrument = _refresh_instrument(repository, "KRW-LIVEQUALITY", "실제 품질 집계")
    target_spec_id = _ensure_source_spec(repository, instrument.id)
    start = datetime(2026, 7, 17, 3, tzinfo=UTC)
    repository.record_incremental_collection(
        [], [], [_candle(instrument.id, start, offset) for offset in range(2)]
    )
    with repository._connect() as connection:
        replace_coverage_with_classification(
            connection,
            target_spec_id=target_spec_id,
            range_start_at=start,
            range_end_at=start + timedelta(minutes=1),
            status="available",
            reason_code="p2_quality_roundtrip_baseline_e2e",
            manifest_id=None,
            evidence={"classification": "quality-roundtrip-baseline"},
        )
    repository.materialize_candle_rollups(instrument.id, "3m")
    historical = repository.candle_rollups(
        instrument.id, "3m", start, start + timedelta(minutes=3)
    )[0]
    assert historical.knowledge_at is not None

    with repository._connect() as connection:
        replace_coverage_with_classification(
            connection,
            target_spec_id=target_spec_id,
            range_start_at=start,
            range_end_at=start + timedelta(minutes=1),
            status="no_trade",
            reason_code="p2_quality_only_e2e",
            manifest_id=None,
            evidence={"classification": "quality-only-e2e"},
        )
        row = connection.execute(
            """
            SELECT job.id FROM candle_rollup_recompute_jobs job
            JOIN candle_rollup_invalidations invalidation
              ON invalidation.id = job.invalidation_id
            WHERE invalidation.instrument_id = %s
              AND invalidation.candle_unit = '3m'
              AND invalidation.quality_event_through_id IS NOT NULL
            ORDER BY job.id DESC LIMIT 1
            """,
            (instrument.id,),
        ).fetchone()
        assert row is not None
        job_id = int(row["id"])
        quality_invalidation = connection.execute(
            """
            SELECT invalidation.quality_event_through_id, invalidation.knowledge_at
            FROM candle_rollup_recompute_jobs job
            JOIN candle_rollup_invalidations invalidation
              ON invalidation.id = job.invalidation_id
            WHERE job.id = %s
            """,
            (job_id,),
        ).fetchone()
        assert quality_invalidation is not None
    repository.materialize_candle_rollups(instrument.id, "3m")
    with repository._connect() as connection:
        materialized = connection.execute(
            """
            SELECT quality_event_through_id, knowledge_at FROM candle_rollups
            WHERE instrument_id = %s AND candle_unit = '3m' AND candle_start_at = %s
            ORDER BY id DESC LIMIT 1
            """,
            (instrument.id, start),
        ).fetchone()
        assert materialized is not None
        assert (
            materialized["quality_event_through_id"]
            == quality_invalidation["quality_event_through_id"]
        )
        assert materialized["knowledge_at"] == quality_invalidation["knowledge_at"]
    assert repository.candle_rollups(
        instrument.id,
        "3m",
        start,
        start + timedelta(minutes=3),
        knowledge_at=historical.knowledge_at,
    )[0].quality == historical.quality
    claimed_at = datetime.now(UTC) + timedelta(seconds=1)
    assert repository.claim_candle_rollup_recompute_job(
        job_id, "quality-worker", now=claimed_at, lease_seconds=60
    ) is not None
    assert repository.run_candle_rollup_recompute_job(
        job_id, "quality-worker", now=claimed_at + timedelta(seconds=1)
    ) == 0
    with repository._connect() as connection:
        revisions = connection.execute(
            """
            SELECT input_content_hash, coverage_snapshot_hash, result_content_hash,
                   quality, completeness
            FROM candle_rollups
            WHERE instrument_id = %s AND candle_unit = '3m' AND candle_start_at = %s
            ORDER BY id
            """,
            (instrument.id, start),
        ).fetchall()
    assert len(revisions) == 2
    assert revisions[0]["input_content_hash"] == revisions[1]["input_content_hash"]
    assert revisions[0]["coverage_snapshot_hash"] != revisions[1]["coverage_snapshot_hash"]
    assert len(revisions[1]["result_content_hash"]) == 64
    assert (revisions[1]["quality"], revisions[1]["completeness"]) == (
        "unverified",
        "partial",
    )
    with repository._connect() as connection:
        replace_coverage_with_classification(
            connection,
            target_spec_id=target_spec_id,
            range_start_at=start,
            range_end_at=start + timedelta(minutes=1),
            status="available",
            reason_code="p2_quality_roundtrip_e2e",
            manifest_id=None,
            evidence={"classification": "quality-roundtrip"},
        )
    assert _run_latest_rollup_job(repository, instrument.id, "quality-roundtrip-worker") == 1
    with repository._connect() as connection:
        roundtrip = connection.execute(
            """
            SELECT coverage_snapshot_hash, quality_event_through_id
            FROM candle_rollups
            WHERE instrument_id = %s AND candle_unit = '3m' AND candle_start_at = %s
            ORDER BY id
            """,
            (instrument.id, start),
        ).fetchall()
    assert len(roundtrip) == 3
    assert roundtrip[0]["coverage_snapshot_hash"] == roundtrip[2]["coverage_snapshot_hash"]
    assert len({row["quality_event_through_id"] for row in roundtrip}) == 3


def test_live_postgres_완전_빈_품질_구간은_ledger만_남기고_가짜_집계를_만들지_않는다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    instrument = _refresh_instrument(
        repository, "KRW-LIVEEMPTYQUALITY", "실제 빈 품질 구간"
    )
    target_spec_id = _ensure_source_spec(repository, instrument.id)
    start = datetime(2026, 7, 18, tzinfo=UTC)
    with repository._connect() as connection:
        before_events_row = connection.execute(
            "SELECT COUNT(*) AS count FROM data_quality_events WHERE target_spec_id = %s",
            (target_spec_id,),
        ).fetchone()
        assert before_events_row is not None
        before_events = before_events_row["count"]
        before_jobs_row = connection.execute(
            """
            SELECT COUNT(*) AS count FROM candle_rollup_recompute_jobs job
            JOIN candle_rollup_invalidations invalidation
              ON invalidation.id = job.invalidation_id
            WHERE invalidation.instrument_id = %s
            """,
            (instrument.id,),
        ).fetchone()
        assert before_jobs_row is not None
        before_jobs = before_jobs_row["count"]
        before_rollups_row = connection.execute(
            "SELECT COUNT(*) AS count FROM candle_rollups WHERE instrument_id = %s",
            (instrument.id,),
        ).fetchone()
        assert before_rollups_row is not None
        before_rollups = before_rollups_row["count"]
        replace_coverage_with_classification(
            connection,
            target_spec_id=target_spec_id,
            range_start_at=start,
            range_end_at=start + timedelta(minutes=3),
            status="missing",
            reason_code="p2_fully_empty_quality_e2e",
            manifest_id=None,
            evidence={"classification": "fully-empty"},
        )
        after_events_row = connection.execute(
            "SELECT COUNT(*) AS count FROM data_quality_events WHERE target_spec_id = %s",
            (target_spec_id,),
        ).fetchone()
        assert after_events_row is not None
        after_events = after_events_row["count"]
        job_count_row = connection.execute(
            """
            SELECT COUNT(*) AS count FROM candle_rollup_recompute_jobs job
            JOIN candle_rollup_invalidations invalidation
              ON invalidation.id = job.invalidation_id
            WHERE invalidation.instrument_id = %s
            """,
            (instrument.id,),
        ).fetchone()
        assert job_count_row is not None
        job_count = job_count_row["count"]
        rollup_count_row = connection.execute(
            "SELECT COUNT(*) AS count FROM candle_rollups WHERE instrument_id = %s",
            (instrument.id,),
        ).fetchone()
        assert rollup_count_row is not None
        rollup_count = rollup_count_row["count"]

    assert after_events == before_events + 1
    assert job_count == before_jobs
    assert rollup_count == before_rollups


def test_live_postgres_같은_source_as_of_정정은_현재_원천과_최신_revision을_일치시킨다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    instrument = _refresh_instrument(
        repository, "KRW-LIVETIESOURCE", "실제 동일 원천 시각 정정"
    )
    start = datetime(2026, 7, 19, tzinfo=UTC)
    initial = _candle(instrument.id, start, 0)
    corrected = SourceCandle(**{**initial.__dict__, "close_price": Decimal("777")})

    repository.record_incremental_collection([], [], [initial])
    repository.record_incremental_collection([], [], [corrected])
    repository.materialize_candle_rollups(instrument.id, "3m")

    with repository._connect() as connection:
        state = connection.execute(
            """
            SELECT candle.close_price,
                   (SELECT revision.close_price
                    FROM source_candle_revisions revision
                    WHERE revision.source_candle_id = candle.id
                    ORDER BY revision.source_as_of DESC,
                             revision.revision_number DESC, revision.id DESC
                    LIMIT 1) AS revision_close_price
            FROM source_candles candle
            WHERE candle.instrument_id = %s AND candle.candle_unit = '1m'
              AND candle.candle_start_at = %s
            """,
            (instrument.id, start),
        ).fetchone()
        assert state is not None
        assert state["close_price"] == state["revision_close_price"] == Decimal("777")
    assert repository.candle_rollups(
        instrument.id, "3m", start, start + timedelta(minutes=3)
    )[0].close == Decimal("777")


def test_live_postgres_최소권한_runtime은_품질_ledger_SELECT없이_full_materialize한다() -> None:
    admin_url = _database_url()
    repository = PostgresOperationsRepository(admin_url)
    instrument = _refresh_instrument(
        repository, "KRW-LIVERUNTIMEQUALITY", "실제 최소권한 품질 집계"
    )
    target_spec_id = _ensure_source_spec(repository, instrument.id)
    start = datetime(2026, 7, 20, tzinfo=UTC)
    repository.record_incremental_collection([], [], [_candle(instrument.id, start, 0)])
    with repository._connect() as connection:
        replace_coverage_with_classification(
            connection,
            target_spec_id=target_spec_id,
            range_start_at=start,
            range_end_at=start + timedelta(minutes=1),
            status="missing",
            reason_code="p2_runtime_quality_e2e",
            manifest_id=None,
            evidence={"classification": "runtime-no-select"},
        )
        connection.execute("DROP ROLE IF EXISTS p2_runtime_probe")
        connection.execute("CREATE ROLE p2_runtime_probe LOGIN PASSWORD 'p2-runtime-probe'")
        connection.execute("GRANT USAGE ON SCHEMA public TO p2_runtime_probe")
        connection.execute(
            """
            GRANT SELECT ON instruments, markets, source_candles, source_candle_revisions,
              coverage_intervals, collection_target_specs, candle_rollups,
              candle_rollup_invalidations, candle_rollup_recompute_jobs
            TO p2_runtime_probe
            """
        )
        connection.execute("GRANT INSERT ON candle_rollups TO p2_runtime_probe")
        connection.execute("GRANT USAGE, SELECT ON candle_rollups_id_seq TO p2_runtime_probe")
        connection.execute(
            """
            GRANT EXECUTE ON FUNCTION current_rollup_quality_ceiling(
              BIGINT, TIMESTAMPTZ, TIMESTAMPTZ
            ) TO p2_runtime_probe
            """
        )
        connection.execute("REVOKE ALL ON data_quality_events FROM p2_runtime_probe")

    runtime_url = make_conninfo(
        admin_url, user="p2_runtime_probe", password="p2-runtime-probe"
    )
    try:
        with psycopg.connect(runtime_url) as runtime_connection:
            privilege_row = runtime_connection.execute(
                "SELECT has_table_privilege(current_user, 'data_quality_events', 'SELECT')"
            ).fetchone()
            assert privilege_row is not None
            can_read_quality = privilege_row[0]
            assert can_read_quality is False
        runtime_repository = PostgresOperationsRepository(runtime_url)
        assert runtime_repository.materialize_candle_rollups(instrument.id, "3m") == 1
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin_connection:
            admin_connection.execute("DROP OWNED BY p2_runtime_probe")
            admin_connection.execute("DROP ROLE p2_runtime_probe")


def test_live_postgres_동시_source와_quality_tx는_최신_결합_frontier를_남긴다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    instrument = _refresh_instrument(
        repository, "KRW-LIVECONCURRENTFRONTIER", "실제 동시 결합 프런티어"
    )
    one_minute_spec_id = _ensure_source_spec(repository, instrument.id, candle_unit="1m")
    _ensure_source_spec(repository, instrument.id, candle_unit="1d")
    start = datetime(2026, 7, 21, tzinfo=UTC)
    minute = _candle(instrument.id, start, 0)
    daily = SourceCandle(**{**minute.__dict__, "candle_unit": "1d"})
    repository.record_incremental_collection([], [], [minute, daily])
    corrected_daily = SourceCandle(
        **{
            **daily.__dict__,
            "close_price": Decimal("777"),
            "collected_at": daily.collected_at + timedelta(hours=1),
        }
    )
    with repository._connect() as connection:
        market = connection.execute(
            "SELECT id FROM markets WHERE legacy_instrument_id = %s", (instrument.id,)
        ).fetchone()
        assert market is not None
        market_id = int(market["id"])

    failures: list[BaseException] = []

    def write_source() -> None:
        try:
            repository.record_incremental_collection([], [], [corrected_daily])
        except BaseException as exc:  # pragma: no cover - assertion reports worker failure
            failures.append(exc)

    def write_quality() -> None:
        try:
            with repository._connect() as connection:
                replace_coverage_with_classification(
                    connection,
                    target_spec_id=one_minute_spec_id,
                    range_start_at=start,
                    range_end_at=start + timedelta(minutes=1),
                    status="missing",
                    reason_code="p2_concurrent_frontier_e2e",
                    manifest_id=None,
                    evidence={"classification": "concurrent-frontier"},
                )
        except BaseException as exc:  # pragma: no cover - assertion reports worker failure
            failures.append(exc)

    with repository._connect() as blocker:
        blocker.execute(
            "SELECT pg_advisory_lock(%s, %s)",
            (ROLLUP_FRONTIER_ADVISORY_LOCK_NAMESPACE, market_id),
        )
        source_thread = threading.Thread(target=write_source)
        quality_thread = threading.Thread(target=write_quality)
        source_thread.start()
        quality_thread.start()
        blocker.execute("SELECT pg_sleep(0.2)")
        blocker.execute(
            "SELECT pg_advisory_unlock(%s, %s)",
            (ROLLUP_FRONTIER_ADVISORY_LOCK_NAMESPACE, market_id),
        )
        blocker.commit()
    source_thread.join(timeout=10)
    quality_thread.join(timeout=10)
    assert not source_thread.is_alive() and not quality_thread.is_alive()
    assert failures == []

    with repository._connect() as connection:
        frontier = connection.execute(
            """
            WITH ceilings AS (
              SELECT
                (SELECT MAX(id) FROM source_candle_revisions
                 WHERE instrument_id = %s) AS source_id,
                (SELECT MAX(event.id) FROM data_quality_events event
                 JOIN collection_target_specs specification
                   ON specification.id = event.target_spec_id
                 WHERE specification.market_id = %s
                   AND specification.data_type = 'source_candle') AS quality_id
            )
            SELECT 1 FROM candle_rollup_invalidations invalidation, ceilings
            WHERE invalidation.instrument_id = %s
              AND invalidation.candle_unit = '1w'
              AND invalidation.source_revision_through_id = ceilings.source_id
              AND invalidation.quality_event_through_id = ceilings.quality_id
            LIMIT 1
            """,
            (instrument.id, market_id, instrument.id),
        ).fetchone()
    assert frontier is not None


def test_live_postgres_원천_A_B_A는_세_불변_revision과_최종_A를_보존한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    instrument = _refresh_instrument(repository, "KRW-LIVEABA", "실제 원천 A B A")
    start = datetime(2026, 7, 22, tzinfo=UTC)
    first = _candle(instrument.id, start, 0)
    repository.record_incremental_collection([], [], [first])
    repository.materialize_candle_rollups(instrument.id, "3m")
    second = SourceCandle(
        **{
            **first.__dict__,
            "close_price": Decimal("200"),
            "collected_at": first.collected_at + timedelta(hours=1),
        }
    )
    repository.record_incremental_collection([], [], [second])
    assert _run_latest_rollup_job(repository, instrument.id, "aba-b-worker") == 1
    reverted = SourceCandle(
        **{
            **first.__dict__,
            "collected_at": first.collected_at + timedelta(hours=2),
        }
    )
    repository.record_incremental_collection([], [], [reverted])
    assert _run_latest_rollup_job(repository, instrument.id, "aba-a-worker") == 1

    with repository._connect() as connection:
        revisions = connection.execute(
            """
            SELECT close_price, source_revision_through_id FROM candle_rollups
            WHERE instrument_id = %s AND candle_unit = '3m' AND candle_start_at = %s
            ORDER BY id
            """,
            (instrument.id, start),
        ).fetchall()
    assert [row["close_price"] for row in revisions] == [
        Decimal("100"), Decimal("200"), Decimal("100")
    ]
    ceilings = [row["source_revision_through_id"] for row in revisions]
    assert ceilings == sorted(ceilings)
    assert len(set(ceilings)) == 3
    assert repository.candle_rollups(
        instrument.id, "3m", start, start + timedelta(minutes=3)
    )[0].close == Decimal("100")


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("GOODMONEYING_LIVE_POSTGRES_TEST=1에서만 실제 PostgreSQL을 검증한다")
    value = os.getenv("GOODMONEYING_DATABASE_URL")
    assert value
    return value


def _refresh_instrument(
    repository: PostgresOperationsRepository, market_code: str, display_name: str
) -> Instrument:
    return repository.upsert_instrument(market_code, display_name)


def _run_latest_rollup_job(
    repository: PostgresOperationsRepository, instrument_id: int, worker_id: str
) -> int:
    with repository._connect() as connection:
        row = connection.execute(
            """
            SELECT job.id FROM candle_rollup_recompute_jobs job
            JOIN candle_rollup_invalidations invalidation
              ON invalidation.id = job.invalidation_id
            WHERE invalidation.instrument_id = %s AND invalidation.candle_unit = '3m'
            ORDER BY job.id DESC LIMIT 1
            """,
            (instrument_id,),
        ).fetchone()
        assert row is not None
        job_id = int(row["id"])
    claimed_at = datetime.now(UTC) + timedelta(seconds=1)
    assert repository.claim_candle_rollup_recompute_job(
        job_id, worker_id, now=claimed_at, lease_seconds=60
    ) is not None
    return repository.run_candle_rollup_recompute_job(
        job_id, worker_id, now=claimed_at + timedelta(seconds=1)
    )


def _ensure_source_spec(
    repository: PostgresOperationsRepository,
    instrument_id: int,
    *,
    candle_unit: str = "1m",
) -> int:
    with repository._connect() as connection:
        context = connection.execute(
            "SELECT id FROM markets WHERE legacy_instrument_id = %s", (instrument_id,)
        ).fetchone()
        assert context is not None
        policy = connection.execute(
            """
            INSERT INTO collection_policies (
              exchange, quote_currency, name, default_start_at, priority
            ) VALUES ('UPBIT', 'KRW', 'p2-quality-e2e', '2024-01-01T00:00:00Z', 100)
            ON CONFLICT (exchange, quote_currency, name) DO UPDATE
              SET updated_at = now()
            RETURNING id
            """
        ).fetchone()
        assert policy is not None
        specification = connection.execute(
            """
            INSERT INTO collection_target_specs (
              policy_id, market_id, data_type, candle_unit, range_start_at,
              priority, continuous, auto_managed, status
            ) VALUES (%s, %s, 'source_candle', %s, '2024-01-01T00:00:00Z',
                      100, true, true, 'active')
            ON CONFLICT (policy_id, market_id, data_type, candle_unit) DO UPDATE
              SET status = 'active', updated_at = now()
            RETURNING id
            """,
            (policy["id"], context["id"], candle_unit),
        ).fetchone()
        assert specification is not None
        return int(specification["id"])


def _candle(instrument_id: int, start: datetime, offset: int) -> SourceCandle:
    value = Decimal(100 + offset)
    return SourceCandle(
        instrument_id=instrument_id,
        candle_unit="1m",
        candle_start_at=start + timedelta(minutes=offset),
        open_price=value,
        high_price=value + 1,
        low_price=value - 1,
        close_price=value,
        trade_volume=Decimal("1"),
        trade_amount=value,
        collected_at=start + timedelta(minutes=offset, seconds=5),
    )
