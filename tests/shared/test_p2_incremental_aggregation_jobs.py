from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import goodmoneying_shared.sqlite_repository as sqlite_repository_module
from goodmoneying_shared.models import SourceCandle, SourceCandleRevisionCreated
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository


def test_신규_원천_개정은_같은_트랜잭션에서_정확한_파생_범위를_대기시킨다() -> None:
    repository, instrument_id = _repository()
    started_at = datetime(2026, 7, 17, 12, 37, tzinfo=UTC)

    repository.record_incremental_collection([], [], [_candle(instrument_id, started_at, "100")])

    invalidations = repository._execute(
        "SELECT * FROM candle_rollup_invalidations ORDER BY candle_unit"
    ).fetchall()
    assert len(invalidations) == 10
    three_minute = next(row for row in invalidations if row["candle_unit"] == "3m")
    assert datetime.fromisoformat(three_minute["range_start_at"]).astimezone(UTC) == datetime(
        2026, 7, 17, 12, 36, tzinfo=UTC
    )
    assert datetime.fromisoformat(three_minute["range_end_at"]).astimezone(UTC) == datetime(
        2026, 7, 17, 12, 39, tzinfo=UTC
    )
    jobs = repository._execute(
        "SELECT status, attempt_count, max_attempts FROM candle_rollup_recompute_jobs"
    ).fetchall()
    assert [(row["status"], row["attempt_count"], row["max_attempts"]) for row in jobs] == [
        ("pending", 0, 5)
    ] * 10


def test_동일_내용은_작업을_늘리지_않고_변경된_과거_내용만_새_작업을_만든다() -> None:
    repository, instrument_id = _repository()
    started_at = datetime(2026, 7, 17, 12, 37, tzinfo=UTC)
    first = _candle(instrument_id, started_at, "100")
    same = SourceCandle(
        **{**first.__dict__, "collected_at": first.collected_at + timedelta(seconds=1)}
    )
    changed = SourceCandle(
        **{
            **first.__dict__,
            "close_price": Decimal("101"),
            "collected_at": first.collected_at + timedelta(seconds=2),
        }
    )

    repository.record_incremental_collection([], [], [first])
    repository.record_incremental_collection([], [], [same])
    assert (
        repository._execute("SELECT COUNT(*) FROM candle_rollup_recompute_jobs").fetchone()[0]
        == 10
    )

    repository.record_incremental_collection([], [], [changed])

    assert repository._execute("SELECT COUNT(*) FROM source_candle_revisions").fetchone()[0] == 2
    assert (
        repository._execute("SELECT COUNT(*) FROM candle_rollup_recompute_jobs").fetchone()[0]
        == 20
    )


def test_무효화_생성이_실패하면_원천_개정도_롤백한다() -> None:
    class FailingRepository(SQLiteOperationsRepository):
        def _source_candle_revisions_created(
            self, created: list[SourceCandleRevisionCreated]
        ) -> None:
            raise RuntimeError("enqueue failed")

    repository = FailingRepository()
    instrument_id = repository.refresh_candidate_universe([("KRW-ATOMIC", "원자성", "100")])[
        0
    ].instrument.id

    with pytest.raises(RuntimeError, match="enqueue failed"):
        repository.record_incremental_collection(
            [], [], [_candle(instrument_id, datetime(2026, 7, 17, tzinfo=UTC), "100")]
        )

    assert repository._execute("SELECT COUNT(*) FROM source_candles").fetchone()[0] == 0
    assert repository._execute("SELECT COUNT(*) FROM source_candle_revisions").fetchone()[0] == 0


def test_임대는_동시_claim을_막고_만료된_소유자의_늦은_쓰기를_거부한다() -> None:
    repository, instrument_id = _repository()
    started_at = datetime(2026, 7, 17, tzinfo=UTC)
    repository.record_incremental_collection([], [], [_candle(instrument_id, started_at, "100")])
    claimed_at = _eligible_at(repository, 1)

    first = repository.claim_next_candle_rollup_recompute_job(
        "worker-a", now=claimed_at, lease_seconds=60
    )
    assert first is not None
    assert first.status == "running"
    assert repository.claim_next_candle_rollup_recompute_job(
        "worker-b", now=claimed_at, lease_seconds=60
    ) is not None  # 다른 범위 작업은 병렬 claim 가능

    repository._execute(
        "UPDATE candle_rollup_recompute_jobs SET status = 'running', lease_owner = 'worker-a', "
        "lease_expires_at = ? WHERE id = ?",
        ((claimed_at + timedelta(seconds=60)).isoformat(), first.id),
    )
    reclaimed = repository.claim_candle_rollup_recompute_job(
        first.id, "worker-b", now=claimed_at + timedelta(seconds=61), lease_seconds=60
    )
    assert reclaimed is not None
    with pytest.raises(RuntimeError, match="임대"):
        repository.run_candle_rollup_recompute_job(
            first.id, "worker-a", now=claimed_at + timedelta(seconds=62)
        )


def test_범위_재계산은_영향_버킷만_바꾸고_성공_상태를_기록한다() -> None:
    repository, instrument_id = _repository()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    repository.record_incremental_collection(
        [],
        [],
        [
            _candle(instrument_id, start + timedelta(minutes=index), str(100 + index))
            for index in range(6)
        ],
    )
    repository.materialize_candle_rollups(instrument_id, "3m")
    untouched_before = repository._execute(
        "SELECT input_content_hash, materialized_at FROM candle_rollups "
        "WHERE candle_unit = '3m' AND candle_start_at = ?",
        ((start + timedelta(minutes=3)).astimezone().isoformat(),),
    ).fetchone()
    changed = _candle(instrument_id, start, "777")
    changed = SourceCandle(
        **{**changed.__dict__, "collected_at": changed.collected_at + timedelta(minutes=10)}
    )
    repository.record_incremental_collection([], [], [changed])
    job_id = repository._execute(
        """
        SELECT job.id FROM candle_rollup_recompute_jobs job
        JOIN candle_rollup_invalidations invalidation ON invalidation.id = job.invalidation_id
        WHERE invalidation.candle_unit = '3m' ORDER BY job.id DESC LIMIT 1
        """
    ).fetchone()[0]
    now = _eligible_at(repository, job_id)
    assert repository.claim_candle_rollup_recompute_job(
        job_id, "worker", now=now, lease_seconds=60
    ) is not None

    assert repository.run_candle_rollup_recompute_job(
        job_id, "worker", now=now + timedelta(seconds=1)
    ) == 1

    job = repository.candle_rollup_recompute_job(job_id)
    assert job.status == "succeeded"
    assert job.rows_written == 1
    untouched_after = repository._execute(
        "SELECT input_content_hash, materialized_at FROM candle_rollups "
        "WHERE candle_unit = '3m' AND candle_start_at = ?",
        ((start + timedelta(minutes=3)).astimezone().isoformat(),),
    ).fetchone()
    assert tuple(untouched_after) == tuple(untouched_before)


def test_집계_개정은_과거를_보존하고_현재와_시점_기준_투영을_선택한다() -> None:
    repository, instrument_id = _repository()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    source = [
        _candle(instrument_id, start + timedelta(minutes=index), str(100 + index))
        for index in range(3)
    ]
    repository.record_incremental_collection([], [], source)
    repository.materialize_candle_rollups(instrument_id, "3m")
    historical = repository.candle_rollups(
        instrument_id, "3m", start, start + timedelta(minutes=3)
    )[0]
    historical_ceiling = max(historical.input_revision_ids)
    historical_knowledge_at = historical.knowledge_at
    assert historical_knowledge_at is not None

    changed = SourceCandle(
        **{
            **source[2].__dict__,
            "close_price": Decimal("777"),
            "collected_at": source[2].collected_at + timedelta(hours=1),
        }
    )
    repository.record_incremental_collection([], [], [changed])
    job_id = repository._execute(
        """
        SELECT job.id FROM candle_rollup_recompute_jobs job
        JOIN candle_rollup_invalidations invalidation ON invalidation.id = job.invalidation_id
        WHERE invalidation.candle_unit = '3m' ORDER BY job.id DESC LIMIT 1
        """
    ).fetchone()[0]
    now = _eligible_at(repository, job_id)
    repository.claim_candle_rollup_recompute_job(job_id, "worker", now=now, lease_seconds=60)
    repository.run_candle_rollup_recompute_job(
        job_id, "worker", now=now + timedelta(seconds=1)
    )

    revisions = repository._execute(
        """
        SELECT id, close_price, input_content_hash
        FROM candle_rollups
        WHERE instrument_id = ? AND candle_unit = '3m' AND candle_start_at = ?
        ORDER BY id
        """,
        (instrument_id, start.astimezone().isoformat()),
    ).fetchall()
    assert len(revisions) == 2
    assert revisions[0][1] == "102"
    assert revisions[1][1] == "777"
    assert revisions[0][2] != revisions[1][2]
    assert repository.candle_rollups(
        instrument_id, "3m", start, start + timedelta(minutes=3)
    )[0].close == Decimal("777")
    assert repository.candle_rollups(
        instrument_id,
        "3m",
        start,
        start + timedelta(minutes=3),
        knowledge_at=historical_knowledge_at,
    )[0].close == Decimal("102")
    assert repository.candle_rollups(
        instrument_id,
        "3m",
        start,
        start + timedelta(minutes=3),
        source_revision_through_id=historical_ceiling,
    )[0].close == Decimal("102")

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        repository._execute("UPDATE candle_rollups SET close_price = '0'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        repository._execute("DELETE FROM candle_rollups")


def test_동일_집계_재실행은_개정을_중복_추가하지_않는다() -> None:
    repository, instrument_id = _repository()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    repository.record_incremental_collection(
        [], [], [_candle(instrument_id, start, "100")]
    )

    repository.materialize_candle_rollups(instrument_id, "3m")
    repository.materialize_candle_rollups(instrument_id, "3m")

    assert repository._execute(
        "SELECT COUNT(*) FROM candle_rollups WHERE instrument_id = ? AND candle_unit = '3m'",
        (instrument_id,),
    ).fetchone()[0] == 1


def test_늦게_도착한_과거_source_as_of는_원장에_남되_현재_집계를_회귀시키지_않는다() -> None:
    repository, instrument_id = _repository()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    initial = _candle(instrument_id, start, "100")
    repository.record_incremental_collection([], [], [initial])
    repository.materialize_candle_rollups(instrument_id, "3m")

    current = SourceCandle(
        **{
            **initial.__dict__,
            "close_price": Decimal("300"),
            "collected_at": start + timedelta(hours=3),
            "knowledge_at": start + timedelta(hours=3),
        }
    )
    repository.record_incremental_collection([], [], [current])
    _run_latest_three_minute_job(repository)
    assert repository.candle_rollups(
        instrument_id, "3m", start, start + timedelta(minutes=3)
    )[0].close == Decimal("300")

    late = SourceCandle(
        **{
            **initial.__dict__,
            "close_price": Decimal("200"),
            "collected_at": start + timedelta(hours=2),
            "knowledge_at": start + timedelta(hours=4),
        }
    )
    repository.record_incremental_collection([], [], [late])
    _run_latest_three_minute_job(repository)

    source_revisions = repository._execute(
        "SELECT close_price, source_as_of FROM source_candle_revisions ORDER BY revision_number"
    ).fetchall()
    assert [row["close_price"] for row in source_revisions] == ["100", "300", "200"]
    assert repository.candle_rollups(
        instrument_id, "3m", start, start + timedelta(minutes=3)
    )[0].close == Decimal("300")
    assert repository._execute(
        "SELECT COUNT(*) FROM candle_rollups WHERE instrument_id = ? AND candle_unit = '3m'",
        (instrument_id,),
    ).fetchone()[0] == 3


def test_원천_해시가_같아도_커버리지_전이는_새_품질_집계_개정을_추가한다() -> None:
    repository, instrument_id = _repository()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    repository.record_incremental_collection(
        [],
        [],
        [
            _candle(instrument_id, start + timedelta(minutes=index), str(100 + index))
            for index in range(2)
        ],
    )
    repository.materialize_candle_rollups(instrument_id, "3m")
    initial = repository.candle_rollups(
        instrument_id, "3m", start, start + timedelta(minutes=3)
    )[0]

    repository.replace_candle_coverage_classification(
        instrument_id, start, start + timedelta(minutes=3), "no_trade"
    )
    _run_latest_three_minute_job(repository)

    revisions = repository._execute(
        """
        SELECT input_content_hash, coverage_snapshot_hash, result_content_hash,
               quality, completeness
        FROM candle_rollups
        WHERE instrument_id = ? AND candle_unit = '3m' AND candle_start_at = ?
        ORDER BY id
        """,
        (instrument_id, start.astimezone().isoformat()),
    ).fetchall()
    assert len(revisions) == 2
    assert revisions[0]["input_content_hash"] == revisions[1]["input_content_hash"]
    assert revisions[0]["coverage_snapshot_hash"] != revisions[1]["coverage_snapshot_hash"]
    assert revisions[0]["result_content_hash"] != revisions[1]["result_content_hash"]
    assert revisions[1]["quality"] == "available"
    assert revisions[1]["completeness"] == "complete"
    current = repository.candle_rollups(
        instrument_id, "3m", start, start + timedelta(minutes=3)
    )[0]
    assert current.input_content_hash == initial.input_content_hash
    assert current.quality == "available"


def test_영향_범위_밖_커버리지는_무효화_입력_해시를_바꾸지_않는다() -> None:
    repository, instrument_id = _repository()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    initial = _candle(instrument_id, start, "100")
    repository.record_incremental_collection([], [], [initial])
    first_hash = repository._execute(
        """
        SELECT coverage_snapshot_hash FROM candle_rollup_invalidations
        WHERE instrument_id = ? AND candle_unit = '3m' ORDER BY id LIMIT 1
        """,
        (instrument_id,),
    ).fetchone()[0]
    outside_start = start - timedelta(days=30)
    repository._execute(
        """
        INSERT INTO coverage_intervals (
          instrument_id, candle_unit, range_start_at, range_end_at, status
        ) VALUES (?, '1m', ?, ?, 'missing')
        """,
        (
            instrument_id,
            outside_start.astimezone().isoformat(),
            (outside_start + timedelta(minutes=1)).astimezone().isoformat(),
        ),
    )
    changed = SourceCandle(
        **{
            **initial.__dict__,
            "close_price": Decimal("101"),
            "collected_at": initial.collected_at + timedelta(hours=1),
        }
    )
    repository.record_incremental_collection([], [], [changed])
    last_hash = repository._execute(
        """
        SELECT coverage_snapshot_hash FROM candle_rollup_invalidations
        WHERE instrument_id = ? AND candle_unit = '3m' ORDER BY id DESC LIMIT 1
        """,
        (instrument_id,),
    ).fetchone()[0]

    assert last_hash == first_hash


def test_실패는_재시도_후_예산을_소진하면_dead_letter이고_safe_restart만_초기화한다() -> None:
    repository, instrument_id = _repository()
    repository.record_incremental_collection(
        [], [], [_candle(instrument_id, datetime(2026, 7, 17, tzinfo=UTC), "100")]
    )
    job_id = repository._execute("SELECT MIN(id) FROM candle_rollup_recompute_jobs").fetchone()[0]
    repository._execute(
        "UPDATE candle_rollup_recompute_jobs SET max_attempts = 2 WHERE id = ?", (job_id,)
    )
    now = _eligible_at(repository, job_id)

    repository.claim_candle_rollup_recompute_job(job_id, "worker", now=now, lease_seconds=60)
    first = repository.fail_candle_rollup_recompute_job(
        job_id, "worker", "CALCULATION_FAILED", now=now + timedelta(seconds=1)
    )
    assert first.status == "retry_wait"
    repository.claim_candle_rollup_recompute_job(
        job_id, "worker", now=first.next_retry_at, lease_seconds=60
    )
    second = repository.fail_candle_rollup_recompute_job(
        job_id, "worker", "CALCULATION_FAILED", now=first.next_retry_at + timedelta(seconds=1)
    )
    assert second.status == "dead_letter"

    restarted = repository.safe_restart_candle_rollup_recompute_job(job_id)
    assert restarted.status == "pending"
    assert restarted.attempt_count == 0
    assert restarted.dead_letter_reason is None


def test_claim과_만료_reclaim은_시도_예산을_소진하고_다음_claim에서_dead_letter가_된다() -> None:
    repository, instrument_id = _repository()
    repository.record_incremental_collection(
        [], [], [_candle(instrument_id, datetime(2026, 7, 17, tzinfo=UTC), "100")]
    )
    job_id = repository._execute("SELECT MIN(id) FROM candle_rollup_recompute_jobs").fetchone()[0]
    repository._execute(
        "UPDATE candle_rollup_recompute_jobs SET max_attempts = 2 WHERE id = ?", (job_id,)
    )
    first_at = _eligible_at(repository, job_id)

    first = repository.claim_candle_rollup_recompute_job(
        job_id, "worker-a", now=first_at, lease_seconds=1
    )
    assert first is not None and first.attempt_count == 1
    second = repository.claim_candle_rollup_recompute_job(
        job_id, "worker-b", now=first_at + timedelta(seconds=2), lease_seconds=1
    )
    assert second is not None and second.attempt_count == 2

    assert repository.claim_candle_rollup_recompute_job(
        job_id, "worker-c", now=first_at + timedelta(seconds=4), lease_seconds=1
    ) is None
    exhausted = repository.candle_rollup_recompute_job(job_id)
    assert exhausted.status == "dead_letter"
    assert exhausted.dead_letter_reason == "LEASE_EXPIRED"
    with pytest.raises(RuntimeError, match="임대"):
        repository.run_candle_rollup_recompute_job(
            job_id, "worker-b", now=first_at + timedelta(seconds=4)
        )


def test_계산_도중_임대가_끝나면_종료_시각_fencing이_늦은_쓰기를_롤백한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, instrument_id = _repository()
    repository.record_incremental_collection(
        [], [], [_candle(instrument_id, datetime(2026, 7, 17, tzinfo=UTC), "100")]
    )
    job_id = repository._execute("SELECT MIN(id) FROM candle_rollup_recompute_jobs").fetchone()[0]
    claimed_at = _eligible_at(repository, job_id)
    repository.claim_candle_rollup_recompute_job(
        job_id, "slow-worker", now=claimed_at, lease_seconds=1
    )
    monkeypatch.setattr(
        sqlite_repository_module,
        "now_kst",
        lambda: claimed_at + timedelta(seconds=2),
    )

    with pytest.raises(RuntimeError, match="fencing"):
        repository.run_candle_rollup_recompute_job(
            job_id, "slow-worker", now=claimed_at + timedelta(milliseconds=500)
        )
    assert repository.candle_rollup_recompute_job(job_id).status == "running"


def test_실패_전이도_종료_시점에_임대가_끝났으면_거부한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, instrument_id = _repository()
    repository.record_incremental_collection(
        [], [], [_candle(instrument_id, datetime(2026, 7, 17, tzinfo=UTC), "100")]
    )
    job_id = repository._execute("SELECT MIN(id) FROM candle_rollup_recompute_jobs").fetchone()[0]
    claimed_at = _eligible_at(repository, job_id)
    repository.claim_candle_rollup_recompute_job(
        job_id, "slow-worker", now=claimed_at, lease_seconds=1
    )
    monkeypatch.setattr(
        sqlite_repository_module,
        "now_kst",
        lambda: claimed_at + timedelta(seconds=2),
    )

    with pytest.raises(RuntimeError, match="임대"):
        repository.fail_candle_rollup_recompute_job(
            job_id,
            "slow-worker",
            "CALCULATION_FAILED",
            now=claimed_at + timedelta(milliseconds=500),
        )
    assert repository.candle_rollup_recompute_job(job_id).status == "running"


def test_부분_커버리지_전이는_잔여_구간과_전체_영향_버킷_스냅샷을_보존한다() -> None:
    repository, instrument_id = _repository()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    repository.record_incremental_collection(
        [], [], [_candle(instrument_id, start, "100")]
    )
    repository._execute("DELETE FROM coverage_intervals WHERE instrument_id = ?", (instrument_id,))
    repository._execute(
        """
        INSERT INTO coverage_intervals (
          instrument_id, candle_unit, range_start_at, range_end_at, status
        ) VALUES (?, '1m', ?, ?, 'available')
        """,
        (
            instrument_id,
            start.astimezone().isoformat(),
            (start + timedelta(minutes=3)).astimezone().isoformat(),
        ),
    )
    repository._conn.commit()

    repository.replace_candle_coverage_classification(
        instrument_id,
        start + timedelta(minutes=1),
        start + timedelta(minutes=2),
        "missing",
    )

    intervals = repository._execute(
        "SELECT range_start_at, range_end_at, status FROM coverage_intervals "
        "WHERE instrument_id = ? ORDER BY range_start_at",
        (instrument_id,),
    ).fetchall()
    assert [row["status"] for row in intervals] == ["available", "missing", "available"]
    row = repository._execute(
        """
        SELECT coverage_snapshot FROM candle_rollup_invalidations
        WHERE instrument_id = ? AND candle_unit = '3m'
          AND quality_event_through_id IS NOT NULL
        ORDER BY id DESC LIMIT 1
        """,
        (instrument_id,),
    ).fetchone()
    snapshot = json.loads(row["coverage_snapshot"])
    assert [item["status"] for item in snapshot] == ["available", "missing", "available"]
    assert snapshot[0]["startAt"] == start.isoformat()
    assert snapshot[-1]["endAt"] == (start + timedelta(minutes=3)).isoformat()


def test_full_materialize도_현재_품질_ceiling과_지식_시각을_고정한다() -> None:
    repository, instrument_id = _repository()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    repository.record_incremental_collection([], [], [_candle(instrument_id, start, "100")])
    repository.materialize_candle_rollups(instrument_id, "3m")
    historical = repository.candle_rollups(
        instrument_id, "3m", start, start + timedelta(minutes=3)
    )[0]
    assert historical.knowledge_at is not None

    repository.replace_candle_coverage_classification(
        instrument_id, start, start + timedelta(minutes=1), "missing"
    )
    quality_invalidation = repository._execute(
        """
        SELECT quality_event_through_id, knowledge_at
        FROM candle_rollup_invalidations
        WHERE instrument_id = ? AND candle_unit = '3m'
          AND quality_event_through_id IS NOT NULL
        ORDER BY id DESC LIMIT 1
        """,
        (instrument_id,),
    ).fetchone()
    repository.materialize_candle_rollups(instrument_id, "3m")

    latest = repository._execute(
        """
        SELECT quality_event_through_id, knowledge_at FROM candle_rollups
        WHERE instrument_id = ? AND candle_unit = '3m'
        ORDER BY id DESC LIMIT 1
        """,
        (instrument_id,),
    ).fetchone()
    assert latest["quality_event_through_id"] == quality_invalidation["quality_event_through_id"]
    assert latest["knowledge_at"] == quality_invalidation["knowledge_at"]
    assert repository.candle_rollups(
        instrument_id,
        "3m",
        start,
        start + timedelta(minutes=3),
        knowledge_at=historical.knowledge_at,
    )[0].quality == historical.quality
    assert repository.candle_rollups(
        instrument_id,
        "3m",
        start,
        start + timedelta(minutes=3),
        quality_event_through_id=0,
    )[0].quality == historical.quality


def test_직접_1d_원천_수정은_1d_1w_1M에만_전파된다() -> None:
    repository, instrument_id = _repository()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    daily = SourceCandle(
        **{**_candle(instrument_id, start, "100").__dict__, "candle_unit": "1d"}
    )

    repository.record_incremental_collection([], [], [daily])

    units = {
        row["candle_unit"]
        for row in repository._execute(
            "SELECT candle_unit FROM candle_rollup_invalidations"
        ).fetchall()
    }
    assert units == {"1d", "1w", "1M"}
    job_id = repository._execute(
        """
        SELECT job.id FROM candle_rollup_recompute_jobs job
        JOIN candle_rollup_invalidations invalidation ON invalidation.id = job.invalidation_id
        WHERE invalidation.candle_unit = '1d' ORDER BY job.id DESC LIMIT 1
        """
    ).fetchone()[0]
    claimed_at = _eligible_at(repository, job_id)
    repository.claim_candle_rollup_recompute_job(
        job_id, "daily-worker", now=claimed_at, lease_seconds=60
    )
    assert repository.run_candle_rollup_recompute_job(
        job_id, "daily-worker", now=claimed_at + timedelta(seconds=1)
    ) == 1
    assert repository.candle_rollups(
        instrument_id, "1d", start, start + timedelta(days=1)
    )[0].close == Decimal("100")


def test_현재_페이지는_같은_버킷의_여러_개정에서_최신_하나만_반환한다() -> None:
    repository, instrument_id = _repository()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    initial = _candle(instrument_id, start, "100")
    repository.record_incremental_collection([], [], [initial])
    repository.materialize_candle_rollups(instrument_id, "3m")
    changed = SourceCandle(
        **{
            **initial.__dict__,
            "close_price": Decimal("777"),
            "collected_at": initial.collected_at + timedelta(hours=1),
        }
    )
    repository.record_incremental_collection([], [], [changed])
    _run_latest_three_minute_job(repository)

    page, cursor = repository.candle_page(
        instrument_id, "3m", start, start + timedelta(minutes=3), 2, None
    )

    assert [item.close for item in page] == [Decimal("777")]
    assert cursor is None


def test_같은_source_as_of의_새_내용은_최신_revision과_현재_원천을_함께_갱신한다() -> None:
    repository, instrument_id = _repository()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    initial = _candle(instrument_id, start, "100")
    corrected = SourceCandle(**{**initial.__dict__, "close_price": Decimal("777")})

    repository.record_incremental_collection([], [], [initial])
    repository.record_incremental_collection([], [], [corrected])
    repository.materialize_candle_rollups(instrument_id, "3m")

    current = repository._execute(
        "SELECT close_price FROM source_candles WHERE instrument_id = ?",
        (instrument_id,),
    ).fetchone()
    assert current["close_price"] == "777"
    assert repository._execute(
        "SELECT COUNT(*) FROM source_candle_revisions WHERE instrument_id = ?",
        (instrument_id,),
    ).fetchone()[0] == 2
    assert repository.candle_rollups(
        instrument_id, "3m", start, start + timedelta(minutes=3)
    )[0].close == Decimal("777")


def test_SQLite_legacy_집계_승격은_입력_revision_최댓값을_ceiling으로_backfill한다(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE candle_rollups (
          instrument_id INTEGER NOT NULL,
          candle_unit TEXT NOT NULL,
          candle_start_at TEXT NOT NULL,
          open_price TEXT NOT NULL,
          high_price TEXT NOT NULL,
          low_price TEXT NOT NULL,
          close_price TEXT NOT NULL,
          trade_volume TEXT NOT NULL,
          trade_amount TEXT NOT NULL,
          completeness TEXT NOT NULL,
          calculation_version TEXT NOT NULL,
          source_as_of TEXT,
          knowledge_at TEXT,
          input_content_hash TEXT NOT NULL,
          input_revision_ids TEXT NOT NULL,
          quality TEXT NOT NULL,
          materialized_at TEXT NOT NULL,
          PRIMARY KEY (instrument_id, candle_unit, candle_start_at, calculation_version)
        );
        INSERT INTO candle_rollups VALUES (
          1, '3m', '2026-07-17T09:00:00+09:00',
          '1', '2', '0', '1', '3', '4', 'complete', 'candle-rollup-v2',
          '2026-07-17T09:00:05+09:00', '2026-07-17T09:00:05+09:00',
          'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
          '3,7,5', 'available', '2026-07-17T09:00:06+09:00'
        );
        """
    )
    connection.close()

    upgraded = SQLiteOperationsRepository(str(database))

    row = upgraded._execute(
        "SELECT source_revision_through_id FROM candle_rollups"
    ).fetchone()
    assert row["source_revision_through_id"] == 7


def test_원천_A_B_A_왕복은_같은_내용도_새_frontier_개정으로_보존한다() -> None:
    repository, instrument_id = _repository()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    first = _candle(instrument_id, start, "100")
    repository.record_incremental_collection([], [], [first])
    repository.materialize_candle_rollups(instrument_id, "3m")
    second = SourceCandle(
        **{
            **first.__dict__,
            "close_price": Decimal("200"),
            "collected_at": first.collected_at + timedelta(hours=1),
        }
    )
    repository.record_incremental_collection([], [], [second])
    _run_latest_three_minute_job(repository)
    reverted = SourceCandle(
        **{
            **first.__dict__,
            "collected_at": first.collected_at + timedelta(hours=2),
        }
    )
    repository.record_incremental_collection([], [], [reverted])
    _run_latest_three_minute_job(repository)

    rows = repository._execute(
        """
        SELECT close_price, source_revision_through_id FROM candle_rollups
        WHERE instrument_id = ? AND candle_unit = '3m' ORDER BY id
        """,
        (instrument_id,),
    ).fetchall()
    assert [(row["close_price"], row["source_revision_through_id"]) for row in rows] == [
        ("100", 1),
        ("200", 2),
        ("100", 3),
    ]
    assert repository.candle_rollups(
        instrument_id, "3m", start, start + timedelta(minutes=3)
    )[0].close == Decimal("100")


def test_품질_available_missing_available_왕복도_새_event_frontier를_보존한다() -> None:
    repository, instrument_id = _repository()
    start = datetime(2026, 7, 17, tzinfo=UTC)
    repository.record_incremental_collection([], [], [_candle(instrument_id, start, "100")])
    repository._execute(
        """
        INSERT INTO coverage_intervals (
          instrument_id, candle_unit, range_start_at, range_end_at, status
        ) VALUES (?, '1m', ?, ?, 'available')
        """,
        (
            instrument_id,
            start.astimezone().isoformat(),
            (start + timedelta(minutes=3)).astimezone().isoformat(),
        ),
    )
    repository._conn.commit()
    repository.materialize_candle_rollups(instrument_id, "3m")

    repository.replace_candle_coverage_classification(
        instrument_id, start, start + timedelta(minutes=3), "missing"
    )
    _run_latest_three_minute_job(repository)
    repository.replace_candle_coverage_classification(
        instrument_id, start, start + timedelta(minutes=3), "available"
    )
    _run_latest_three_minute_job(repository)

    rows = repository._execute(
        """
        SELECT coverage_snapshot_hash, quality_event_through_id FROM candle_rollups
        WHERE instrument_id = ? AND candle_unit = '3m' ORDER BY id
        """,
        (instrument_id,),
    ).fetchall()
    assert len(rows) == 3
    assert rows[0]["coverage_snapshot_hash"] == rows[2]["coverage_snapshot_hash"]
    assert [row["quality_event_through_id"] for row in rows] == [0, 1, 2]


def _repository() -> tuple[SQLiteOperationsRepository, int]:
    repository = SQLiteOperationsRepository()
    instrument = repository.refresh_candidate_universe([("KRW-P2INC", "증분", "100")])[0].instrument
    return repository, instrument.id


def _eligible_at(repository: SQLiteOperationsRepository, job_id: int) -> datetime:
    value = repository._execute(
        "SELECT next_retry_at FROM candle_rollup_recompute_jobs WHERE id = ?", (job_id,)
    ).fetchone()[0]
    return datetime.fromisoformat(value).astimezone(UTC) + timedelta(microseconds=1)


def _run_latest_three_minute_job(repository: SQLiteOperationsRepository) -> None:
    job_id = repository._execute(
        """
        SELECT job.id FROM candle_rollup_recompute_jobs job
        JOIN candle_rollup_invalidations invalidation ON invalidation.id = job.invalidation_id
        WHERE invalidation.candle_unit = '3m' ORDER BY job.id DESC LIMIT 1
        """
    ).fetchone()[0]
    now = _eligible_at(repository, job_id)
    assert repository.claim_candle_rollup_recompute_job(
        job_id, "worker", now=now, lease_seconds=60
    ) is not None
    repository.run_candle_rollup_recompute_job(
        job_id, "worker", now=now + timedelta(seconds=1)
    )


def _candle(instrument_id: int, started_at: datetime, close: str) -> SourceCandle:
    return SourceCandle(
        instrument_id=instrument_id,
        candle_unit="1m",
        candle_start_at=started_at,
        open_price=Decimal("100"),
        high_price=Decimal("102"),
        low_price=Decimal("99"),
        close_price=Decimal(close),
        trade_volume=Decimal("1"),
        trade_amount=Decimal("100"),
        collected_at=started_at + timedelta(seconds=5),
    )
