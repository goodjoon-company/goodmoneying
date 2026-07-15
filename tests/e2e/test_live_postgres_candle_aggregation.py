from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal

import pytest

from goodmoneying_shared.models import SourceCandle
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.time import KST

pytestmark = pytest.mark.live


def test_live_postgres_집계_작업_혼합_상태의_건수와_진행률이_sqlite와_동일하다() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("GOODMONEYING_LIVE_POSTGRES_TEST=1에서만 실제 PostgreSQL을 검증한다")

    database_url = os.getenv("GOODMONEYING_DATABASE_URL")
    assert database_url, "실제 PostgreSQL 검증에는 GOODMONEYING_DATABASE_URL이 필요하다"
    repository = PostgresOperationsRepository(database_url)
    instrument = repository.refresh_candidate_universe(
        [("KRW-LIVEAGG", "실제 DB 집계 검증", "100")]
    )[0].instrument
    repository.update_active_targets([instrument.id], "실제 PostgreSQL 집계 E2E")
    started_at = datetime(2026, 7, 16, 9, 0, tzinfo=KST)
    repository.record_incremental_collection(
        [],
        [],
        [
            SourceCandle(
                instrument_id=instrument.id,
                candle_unit="1m",
                candle_start_at=started_at,
                open_price=Decimal("100"),
                high_price=Decimal("100"),
                low_price=Decimal("100"),
                close_price=Decimal("100"),
                trade_volume=Decimal("1"),
                trade_amount=Decimal("100"),
                collected_at=started_at,
            )
        ],
    )
    scheduled = repository.schedule_candle_aggregation()
    assert scheduled is not None
    job = repository.claim_next_candle_aggregation_job()
    assert job is not None
    targets = repository.candle_aggregation_job_targets(job.id)

    repository.mark_candle_aggregation_target(
        job.id, targets[0].instrument_id, targets[0].candle_unit, "succeeded", 1
    )
    repository.mark_candle_aggregation_target(
        job.id, targets[1].instrument_id, targets[1].candle_unit, "running", 0
    )
    repository.mark_candle_aggregation_target(
        job.id, targets[2].instrument_id, targets[2].candle_unit, "failed", 0
    )
    latest = repository.latest_candle_aggregation_job()

    assert latest is not None
    assert latest.total_target_count == 7
    assert latest.completed_target_count == 1
    assert latest.running_target_count == 1
    assert latest.pending_target_count == 4
    assert latest.failed_target_count == 1
    assert latest.total_target_count == (
        latest.completed_target_count
        + latest.running_target_count
        + latest.pending_target_count
        + latest.failed_target_count
    )
    assert latest.progress_percent == Decimal("100") / Decimal("7")
