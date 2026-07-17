from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal, cast

from psycopg.types.json import Jsonb

from goodmoneying_shared.models import CandleView
from goodmoneying_shared.versioned_indicators import (
    INDICATOR_DEFINITION_VERSIONS,
    IndicatorPoint,
    _next_bucket,
    calculate_indicator_series,
)
from goodmoneying_shared.versioned_market_statistics import (
    MarketStatisticPoint,
    calculate_market_statistics,
)


@dataclass(frozen=True)
class StoredIndicatorPoint:
    materialization_id: int
    point: IndicatorPoint


@dataclass(frozen=True)
class StoredMarketStatistic:
    materialization_id: int
    point: MarketStatisticPoint


def run_next_indicator_invalidation(
    repository: object, worker_id: str, *, now: datetime | None = None
) -> int:
    connector = getattr(repository, "_connect", None)
    if not callable(connector):
        return 0
    with connector() as connection:
        seed = connection.execute(
            """
            SELECT seed.* FROM indicator_invalidations seed
            WHERE (
                (seed.status IN ('pending','retry_wait')
                 AND seed.next_retry_at <= clock_timestamp())
                OR (seed.status='running' AND seed.lease_expires_at <= clock_timestamp())
              )
              AND NOT EXISTS (
                SELECT 1 FROM indicator_invalidations active
                WHERE active.instrument_id=seed.instrument_id
                  AND active.candle_unit=seed.candle_unit
                  AND active.status='running'
                  AND active.lease_expires_at > clock_timestamp()
              )
              AND NOT EXISTS (
                SELECT 1 FROM indicator_invalidations prior
                WHERE prior.instrument_id=seed.instrument_id
                  AND prior.candle_unit=seed.candle_unit
                  AND prior.id < seed.id
                  AND prior.status IN ('pending','retry_wait','running')
                  AND ROW(
                    prior.knowledge_at,
                    prior.source_revision_through_id,
                    prior.quality_event_through_id
                  ) IS DISTINCT FROM ROW(
                    seed.knowledge_at,
                    seed.source_revision_through_id,
                    seed.quality_event_through_id
                  )
              )
            ORDER BY seed.id FOR UPDATE SKIP LOCKED LIMIT 1
            """,
        ).fetchone()
        if seed is None:
            return 0
        claim_lock = connection.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS acquired",
            (f"indicator-claim:{seed['instrument_id']}:{seed['candle_unit']}",),
        ).fetchone()
        if claim_lock is None or not claim_lock["acquired"]:
            return 0
        rows = connection.execute(
            """
            WITH candidates AS (
              SELECT id FROM indicator_invalidations
              WHERE instrument_id=%s AND candle_unit=%s
                AND knowledge_at=%s AND source_revision_through_id=%s
                AND quality_event_through_id IS NOT DISTINCT FROM %s
                AND ((status IN ('pending','retry_wait')
                      AND next_retry_at <= clock_timestamp())
                  OR (status='running' AND lease_expires_at <= clock_timestamp()))
                AND NOT EXISTS (
                  SELECT 1 FROM indicator_invalidations prior
                  WHERE prior.instrument_id=indicator_invalidations.instrument_id
                    AND prior.candle_unit=indicator_invalidations.candle_unit
                    AND prior.id < indicator_invalidations.id
                    AND prior.status IN ('pending','retry_wait','running')
                    AND ROW(
                      prior.knowledge_at,
                      prior.source_revision_through_id,
                      prior.quality_event_through_id
                    ) IS DISTINCT FROM ROW(
                      indicator_invalidations.knowledge_at,
                      indicator_invalidations.source_revision_through_id,
                      indicator_invalidations.quality_event_through_id
                    )
                )
              ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 512
            )
            UPDATE indicator_invalidations invalidation SET
              status='running', attempt_count=attempt_count+1,
              lease_owner=%s,
              lease_expires_at=clock_timestamp() + interval '120 seconds',
              lease_generation=lease_generation+1
            FROM candidates WHERE invalidation.id=candidates.id
            RETURNING invalidation.*
            """,
            (
                seed["instrument_id"],
                seed["candle_unit"],
                seed["knowledge_at"],
                seed["source_revision_through_id"],
                seed["quality_event_through_id"],
                worker_id,
            ),
        ).fetchall()
    if not rows:
        return 0
    instrument_id = int(rows[0]["instrument_id"])
    unit = str(rows[0]["candle_unit"])
    effective_start = min(row["progress_at"] or row["impact_start_at"] for row in rows)
    as_of = max(row["knowledge_at"] for row in rows)
    source_ceiling = max(int(row["source_revision_through_id"]) for row in rows)
    quality_ceiling = max(
        (
            int(row["quality_event_through_id"])
            for row in rows
            if row["quality_event_through_id"] is not None
        ),
        default=None,
    )
    fence_connection = connector()
    lock_key = f"indicator:{instrument_id}:{unit}"
    try:
        fence_connection.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (lock_key,))
        renewed = fence_connection.execute(
            """
            WITH expected(id, generation) AS (
              SELECT * FROM unnest(%s::bigint[], %s::integer[])
            )
            UPDATE indicator_invalidations invalidation SET
              lease_expires_at=clock_timestamp() + interval '120 seconds'
            FROM expected
            WHERE invalidation.id=expected.id
              AND invalidation.lease_generation=expected.generation
              AND invalidation.lease_owner=%s AND invalidation.status='running'
              AND invalidation.lease_expires_at > clock_timestamp()
            RETURNING invalidation.id
            """,
            (
                [int(row["id"]) for row in rows],
                [int(row["lease_generation"]) for row in rows],
                worker_id,
            ),
        ).fetchall()
        if len(renewed) != len(rows):
            fence_connection.rollback()
            return 0
        if _has_preceding_unfinished_frontier(
            fence_connection,
            rows,
            instrument_id,
            unit,
        ):
            _release_claimed_invalidations(
                fence_connection,
                rows,
                worker_id,
            )
            return 0
        definition_set_hash = hashlib.sha256(
            "|".join(
                sorted(item.definition_hash for item in INDICATOR_DEFINITION_VERSIONS.values())
            ).encode()
        ).hexdigest()
        indicator_checkpoint, statistic_checkpoint = _load_parent_checkpoints(
            connector,
            instrument_id,
            unit,
            effective_start,
            as_of,
            source_ceiling,
            quality_ceiling,
            definition_set_hash,
            _connection=fence_connection,
        )
        progress_rows = [row for row in rows if row["progress_at"] is not None]
        if progress_rows:
            transient_indicator = progress_rows[0]["indicator_checkpoint_state"]
            transient_statistic = progress_rows[0]["statistic_checkpoint_state"]
            if transient_indicator is None or transient_statistic is None:
                raise RuntimeError("지표 무효화 progress 체크포인트가 완전하지 않다.")
            indicator_checkpoint = MappingProxyType(dict(transient_indicator))
            statistic_checkpoint = MappingProxyType(dict(transient_statistic))
        indicator_checkpoint, statistic_checkpoint, beginning = _replay_start_from_checkpoints(
            indicator_checkpoint,
            statistic_checkpoint,
            effective_start,
            unit,
        )
        if unit == "1m":
            with connector() as connection:
                revision_rows = connection.execute(
                    """
                    SELECT * FROM (
                      SELECT revision.*,
                        COALESCE(quality.new_status, 'available') AS projected_quality,
                        ROW_NUMBER() OVER (
                          PARTITION BY instrument_id, candle_unit, candle_start_at
                          ORDER BY source_as_of DESC, revision_number DESC, id DESC
                        ) AS projection_rank
                      FROM source_candle_revisions revision
                      LEFT JOIN LATERAL (
                        SELECT event.new_status
                        FROM data_quality_events event
                        JOIN collection_target_specs specification
                          ON specification.id=event.target_spec_id
                        WHERE specification.market_id=revision.market_id
                          AND specification.data_type='source_candle'
                          AND specification.candle_unit='1m'
                          AND %s::bigint IS NOT NULL AND event.id <= %s
                          AND event.detected_at <= %s
                          AND tstzrange(event.range_start_at, event.range_end_at, '[)')
                              @> revision.candle_start_at
                        ORDER BY event.id DESC LIMIT 1
                      ) quality ON TRUE
                      WHERE instrument_id=%s AND candle_unit='1m'
                        AND candle_start_at >= %s
                        AND id <= %s AND knowledge_at <= %s
                    ) projection WHERE projection_rank=1 ORDER BY candle_start_at LIMIT 513
                    """,
                    (
                        quality_ceiling,
                        quality_ceiling,
                        as_of,
                        instrument_id,
                        beginning,
                        source_ceiling,
                        as_of,
                    ),
                ).fetchall()
            projection_source_as_of = max(
                (
                    *(cast(datetime, row["source_as_of"]) for row in revision_rows),
                    *(
                        value
                        for value in (
                            _checkpoint_source_as_of(indicator_checkpoint),
                            _checkpoint_source_as_of(statistic_checkpoint),
                        )
                        if value is not None
                    ),
                ),
                default=as_of,
            )
            candles = [
                _source_revision_candle(
                    row,
                    source_revision_through_id=source_ceiling,
                    quality_event_through_id=quality_ceiling,
                    knowledge_at=as_of,
                    source_as_of=projection_source_as_of,
                )
                for row in revision_rows
            ]
        else:
            first_rollup_at = _next_rollup_at(
                fence_connection,
                instrument_id,
                unit,
                beginning,
                as_of,
                source_ceiling,
                quality_ceiling,
            )
            bounded_end = (
                _advance_bucket(first_rollup_at, unit, 513)
                if first_rollup_at is not None
                else beginning
            )
            candles = getattr(repository, "candle_rollups")(  # noqa: B009
                instrument_id,
                unit,
                beginning,
                bounded_end,
                knowledge_at=as_of,
                source_revision_through_id=source_ceiling,
                quality_event_through_id=quality_ceiling,
            )
            projection_source_as_of = max(
                (
                    *(item.source_as_of for item in candles if item.source_as_of is not None),
                    *(
                        value
                        for value in (
                            _checkpoint_source_as_of(indicator_checkpoint),
                            _checkpoint_source_as_of(statistic_checkpoint),
                        )
                        if value is not None
                    ),
                ),
                default=as_of,
            )
            candles = [
                replace(
                    item,
                    source_revision_through_id=source_ceiling,
                    quality_event_through_id=quality_ceiling,
                    knowledge_at=as_of,
                    source_as_of=projection_source_as_of,
                )
                for item in candles
            ]
            future_rollup_at = _next_rollup_at(
                fence_connection,
                instrument_id,
                unit,
                bounded_end,
                as_of,
                source_ceiling,
                quality_ceiling,
            )
        next_progress_at = None
        if len(candles) > 512:
            next_progress_at = candles[512].started_at
            candles = candles[:512]
        elif unit != "1m":
            next_progress_at = future_rollup_at
        calculated_indicators = calculate_indicator_series(
            candles, unit=unit, initial_checkpoint=indicator_checkpoint
        )
        calculated_statistics = calculate_market_statistics(
            candles, unit, initial_checkpoint=statistic_checkpoint
        )
        indicator_points = calculated_indicators
        statistic_points = calculated_statistics
        next_indicator_checkpoint = (
            calculated_indicators[-1].checkpoint_state
            if next_progress_at is not None and calculated_indicators
            else None
        )
        next_statistic_checkpoint = (
            calculated_statistics[-1].checkpoint_state
            if next_progress_at is not None and calculated_statistics
            else None
        )
        if next_progress_at is not None and (
            next_indicator_checkpoint is None or next_statistic_checkpoint is None
        ):
            raise RuntimeError("지표 무효화 청크 진행 체크포인트를 만들 수 없다.")
        if not _materialize_with_lease_fence(
            repository,
            rows,
            worker_id,
            instrument_id,
            unit,
            indicator_points,
            statistic_points,
            candles,
            definition_set_hash,
            next_progress_at,
            next_indicator_checkpoint,
            next_statistic_checkpoint,
            fence_connection,
        ):
            return 0
    except Exception as exc:
        fence_connection.rollback()
        with connector() as connection:
            connection.execute(
                """
                WITH expected(id, generation) AS (
                  SELECT * FROM unnest(%s::bigint[], %s::integer[])
                )
                UPDATE indicator_invalidations invalidation SET
                  status=CASE WHEN attempt_count >= max_attempts
                    THEN 'dead_letter' ELSE 'retry_wait' END,
                  next_retry_at=clock_timestamp() + interval '5 seconds',
                  lease_owner=NULL, lease_expires_at=NULL,
                  progress_at=CASE WHEN attempt_count >= max_attempts
                    THEN NULL ELSE progress_at END,
                  indicator_checkpoint_state=CASE WHEN attempt_count >= max_attempts
                    THEN NULL ELSE indicator_checkpoint_state END,
                  statistic_checkpoint_state=CASE WHEN attempt_count >= max_attempts
                    THEN NULL ELSE statistic_checkpoint_state END,
                  last_error_code=%s
                FROM expected
                WHERE invalidation.id=expected.id
                  AND invalidation.lease_generation=expected.generation
                  AND invalidation.lease_owner=%s
                """,
                (
                    [int(row["id"]) for row in rows],
                    [int(row["lease_generation"]) for row in rows],
                    type(exc).__name__,
                    worker_id,
                ),
            )
        raise
    finally:
        try:
            fence_connection.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (lock_key,)
            )
            fence_connection.commit()
        finally:
            fence_connection.close()
    return max(1, len(indicator_points) + len(statistic_points))


def _has_preceding_unfinished_frontier(
    connection: Any,
    rows: list[dict[str, object]],
    instrument_id: int,
    unit: str,
) -> bool:
    first_id = min(int(str(row["id"])) for row in rows)
    frontier = rows[0]
    result = connection.execute(
        """
        SELECT EXISTS (
          SELECT 1 FROM indicator_invalidations prior
          WHERE prior.instrument_id=%s AND prior.candle_unit=%s
            AND prior.id < %s
            AND prior.status IN ('pending','retry_wait','running')
            AND ROW(
              prior.knowledge_at,
              prior.source_revision_through_id,
              prior.quality_event_through_id
            ) IS DISTINCT FROM ROW(%s::timestamptz, %s::bigint, %s::bigint)
        ) AS blocked
        """,
        (
            instrument_id,
            unit,
            first_id,
            frontier["knowledge_at"],
            frontier["source_revision_through_id"],
            frontier["quality_event_through_id"],
        ),
    ).fetchone()
    return bool(result and result["blocked"])


def _release_claimed_invalidations(
    connection: Any,
    rows: list[dict[str, object]],
    worker_id: str,
) -> None:
    connection.execute(
        """
        WITH expected(id, generation) AS (
          SELECT * FROM unnest(%s::bigint[], %s::integer[])
        )
        UPDATE indicator_invalidations invalidation SET
          status='pending', attempt_count=GREATEST(attempt_count-1, 0),
          next_retry_at=clock_timestamp(), lease_owner=NULL, lease_expires_at=NULL
        FROM expected
        WHERE invalidation.id=expected.id
          AND invalidation.lease_generation=expected.generation
          AND invalidation.lease_owner=%s AND invalidation.status='running'
        """,
        (
            [int(str(row["id"])) for row in rows],
            [int(str(row["lease_generation"])) for row in rows],
            worker_id,
        ),
    )
    connection.commit()


def _replay_start_from_checkpoints(
    indicator_checkpoint: Mapping[str, object] | None,
    statistic_checkpoint: Mapping[str, object] | None,
    impact_start: datetime,
    unit: str,
) -> tuple[Mapping[str, object] | None, Mapping[str, object] | None, datetime]:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    if indicator_checkpoint is None or statistic_checkpoint is None:
        return None, None, epoch
    indicator_previous = _checkpoint_previous_started_at(indicator_checkpoint)
    statistic_previous = _checkpoint_previous_started_at(statistic_checkpoint)
    if indicator_previous is None or indicator_previous != statistic_previous:
        return None, None, epoch
    replay_start = _next_bucket(indicator_previous, unit)
    if replay_start > impact_start:
        return None, None, epoch
    return indicator_checkpoint, statistic_checkpoint, replay_start


def _checkpoint_previous_started_at(
    checkpoint: Mapping[str, object],
) -> datetime | None:
    value = checkpoint.get("previousStartedAt")
    return datetime.fromisoformat(str(value)) if value else None


def _load_parent_checkpoints(
    connector: Any,
    instrument_id: int,
    unit: str,
    impact_start: datetime,
    as_of: datetime,
    source_ceiling: int,
    quality_ceiling: int | None,
    definition_set_hash: str,
    *,
    _connection: Any | None = None,
) -> tuple[Mapping[str, object] | None, Mapping[str, object] | None]:
    manager = connector() if _connection is None else nullcontext(_connection)
    with manager as connection:
        indicator = connection.execute(
            """
            SELECT checkpoint_state FROM indicator_materializations
            WHERE instrument_id=%s AND candle_unit=%s AND occurred_at < %s
              AND knowledge_at <= %s AND definition_set_hash=%s
              AND source_revision_through_id <= %s
              AND COALESCE(quality_event_through_id,0) <= COALESCE(%s,0)
            ORDER BY occurred_at DESC, source_revision_through_id DESC,
                     quality_event_through_id DESC NULLS LAST, id DESC LIMIT 1
            """,
            (
                instrument_id,
                unit,
                impact_start,
                as_of,
                definition_set_hash,
                source_ceiling,
                quality_ceiling,
            ),
        ).fetchone()
        statistic = connection.execute(
            """
            SELECT checkpoint_state FROM market_statistics
            WHERE instrument_id=%s AND interval=%s AND occurred_at < %s
              AND knowledge_at <= %s AND calculation_version='market-statistics-v1'
              AND source_revision_through_id <= %s
              AND COALESCE(quality_event_through_id,0) <= COALESCE(%s,0)
            ORDER BY occurred_at DESC, source_revision_through_id DESC,
                     quality_event_through_id DESC NULLS LAST, id DESC LIMIT 1
            """,
            (instrument_id, unit, impact_start, as_of, source_ceiling, quality_ceiling),
        ).fetchone()
    return (
        MappingProxyType(dict(indicator["checkpoint_state"])) if indicator else None,
        MappingProxyType(dict(statistic["checkpoint_state"])) if statistic else None,
    )


def _advance_bucket(started_at: datetime, unit: str, count: int) -> datetime:
    result = started_at
    for _ in range(count):
        result = _next_bucket(result, unit)
    return result


def _checkpoint_source_as_of(
    checkpoint: Mapping[str, object] | None,
) -> datetime | None:
    value = checkpoint.get("sourceAsOf") if checkpoint is not None else None
    return datetime.fromisoformat(str(value)) if value else None


def _next_rollup_at(
    connection: Any,
    instrument_id: int,
    unit: str,
    start_at: datetime,
    as_of: datetime,
    source_ceiling: int,
    quality_ceiling: int | None,
) -> datetime | None:
    row = connection.execute(
        """
        SELECT MIN(candle_start_at) AS started_at FROM candle_rollups
        WHERE instrument_id=%s AND candle_unit=%s AND candle_start_at >= %s
          AND knowledge_at <= %s AND source_revision_through_id <= %s
          AND COALESCE(quality_event_through_id,0) <= COALESCE(%s,0)
        """,
        (instrument_id, unit, start_at, as_of, source_ceiling, quality_ceiling),
    ).fetchone()
    return cast(datetime | None, row["started_at"] if row else None)


def _materialize_with_lease_fence(
    repository: object,
    rows: list[dict[str, object]],
    worker_id: str,
    instrument_id: int,
    unit: str,
    indicator_points: tuple[IndicatorPoint, ...],
    statistic_points: tuple[MarketStatisticPoint, ...],
    candles: list[CandleView],
    definition_set_hash: str,
    next_progress_at: datetime | None,
    next_indicator_checkpoint: Mapping[str, object] | None,
    next_statistic_checkpoint: Mapping[str, object] | None,
    connection: Any,
) -> bool:
    ids = [int(str(row["id"])) for row in rows]
    generations = [int(str(row["lease_generation"])) for row in rows]
    materialize_indicator_points(
        repository,
        instrument_id,
        unit,
        indicator_points,
        candles,
        definition_set_hash,
        _connection=connection,
    )
    materialize_market_statistics(
        repository,
        instrument_id,
        unit,
        statistic_points,
        _connection=connection,
    )
    completed = connection.execute(
        """
        WITH expected(id, generation) AS (
          SELECT * FROM unnest(%s::bigint[], %s::integer[])
        )
        UPDATE indicator_invalidations invalidation SET
          status=CASE WHEN %s::timestamptz IS NULL THEN 'succeeded' ELSE 'pending' END,
          finished_at=CASE WHEN %s::timestamptz IS NULL THEN clock_timestamp() ELSE NULL END,
          progress_at=%s, attempt_count=0, next_retry_at=clock_timestamp(),
          indicator_checkpoint_state=CASE WHEN %s::timestamptz IS NULL
            THEN NULL ELSE %s::jsonb END,
          statistic_checkpoint_state=CASE WHEN %s::timestamptz IS NULL
            THEN NULL ELSE %s::jsonb END,
          lease_owner=NULL, lease_expires_at=NULL
        FROM expected
        WHERE invalidation.id=expected.id
          AND invalidation.lease_generation=expected.generation
          AND invalidation.lease_owner=%s AND invalidation.status='running'
          AND invalidation.lease_expires_at > clock_timestamp()
        RETURNING invalidation.id
        """,
        (
            ids,
            generations,
            next_progress_at,
            next_progress_at,
            next_progress_at,
            next_progress_at,
            Jsonb(dict(next_indicator_checkpoint))
            if next_indicator_checkpoint is not None
            else None,
            next_progress_at,
            Jsonb(dict(next_statistic_checkpoint))
            if next_statistic_checkpoint is not None
            else None,
            worker_id,
        ),
    ).fetchall()
    if len(completed) != len(ids):
        connection.rollback()
        raise RuntimeError("지표 물질화 lease fencing 완료 조건이 만료되었다.")
    connection.commit()
    return True


def _source_revision_candle(
    row: dict[str, object],
    *,
    source_revision_through_id: int | None = None,
    quality_event_through_id: int | None = None,
    knowledge_at: datetime | None = None,
    source_as_of: datetime | None = None,
) -> CandleView:
    revision_id = int(str(row["id"]))
    quality = str(row.get("projected_quality", "available"))
    return CandleView(
        started_at=cast(datetime, row["candle_start_at"]),
        open=Decimal(str(row["open_price"])),
        high=Decimal(str(row["high_price"])),
        low=Decimal(str(row["low_price"])),
        close=Decimal(str(row["close_price"])),
        volume=Decimal(str(row["trade_volume"])),
        trade_amount=Decimal(str(row["trade_amount"])),
        completeness=(
            "complete"
            if quality == "available"
            else "empty"
            if quality == "no_trade"
            else "partial"
        ),
        source_as_of=source_as_of or cast(datetime, row["source_as_of"]),
        knowledge_at=knowledge_at or cast(datetime, row["knowledge_at"]),
        input_content_hash=str(row["input_content_hash"]),
        input_revision_ids=(revision_id,),
        source_revision_through_id=source_revision_through_id or revision_id,
        quality_event_through_id=quality_event_through_id,
        quality=cast(
            Literal["available", "no_trade", "missing", "unavailable", "unverified"],
            quality,
        ),
    )


def materialize_indicator_points(
    repository: object,
    instrument_id: int,
    unit: str,
    points: tuple[IndicatorPoint, ...],
    candles: list[CandleView],
    definition_set_hash: str,
    *,
    _connection: Any | None = None,
) -> tuple[StoredIndicatorPoint, ...]:
    connector = getattr(repository, "_connect", None)
    if not callable(connector):
        return _materialize_memory(repository, instrument_id, unit, points)
    stored: list[StoredIndicatorPoint] = []
    manager = connector() if _connection is None else nullcontext(_connection)
    with manager as connection:
        market_row = connection.execute(
            "SELECT id FROM markets WHERE legacy_instrument_id = %s",
            (instrument_id,),
        ).fetchone()
        if market_row is None:
            return ()
        market_id = int(market_row["id"])
        definitions = {
            str(row["indicator_key"]): int(row["id"])
            for row in connection.execute(
                """
                SELECT definition.indicator_key, version.id
                FROM indicator_definition_versions version
                JOIN indicator_definitions definition ON definition.id = version.definition_id
                WHERE version.implementation_version = 'indicator-engine-v1'
                """
            ).fetchall()
        }
        parent_materialization_id: int | None = None
        parent_values: dict[tuple[str, str], int] = {}
        if points:
            parent_row = connection.execute(
                """
                SELECT id FROM indicator_materializations
                WHERE instrument_id=%s AND candle_unit=%s AND definition_set_hash=%s
                  AND occurred_at < %s AND knowledge_at <= %s
                  AND source_revision_through_id <= %s
                  AND COALESCE(quality_event_through_id,0) <= COALESCE(%s,0)
                ORDER BY occurred_at DESC, source_revision_through_id DESC,
                         quality_event_through_id DESC NULLS LAST, id DESC LIMIT 1
                """,
                (
                    instrument_id,
                    unit,
                    definition_set_hash,
                    points[0].started_at,
                    points[0].knowledge_at,
                    points[0].source_revision_through_id,
                    points[0].quality_event_through_id,
                ),
            ).fetchone()
            if parent_row is not None:
                parent_materialization_id = int(parent_row["id"])
                parent_values = {
                    (str(row["indicator_key"]), str(row["value_name"])): int(row["id"])
                    for row in connection.execute(
                        """
                        SELECT value.id, value.value_name, definition.indicator_key
                        FROM indicator_values value
                        JOIN indicator_definition_versions version
                          ON version.id=value.definition_version_id
                        JOIN indicator_definitions definition
                          ON definition.id=version.definition_id
                        WHERE value.materialization_id=%s
                        """,
                        (parent_materialization_id,),
                    ).fetchall()
                }
        for point in points:
            if (
                point.current_input_id is None
                or point.knowledge_at is None
                or point.source_as_of is None
            ):
                continue
            content_hash = _point_hash(point)
            lineage_hash = hashlib.sha256(
                f"{parent_materialization_id or 0}|{point.current_input_id}|{content_hash}".encode()
            ).hexdigest()
            current_rollup_id = point.current_input_id if point.current_input_is_rollup else None
            current_source_revision_id = (
                None if point.current_input_is_rollup else point.current_input_id
            )
            row = connection.execute(
                """
                INSERT INTO indicator_materializations (
                  instrument_id, market_id, candle_unit, occurred_at, definition_set_hash,
                  parent_materialization_id, current_rollup_id, current_source_revision_id,
                  lineage_hash, source_revision_through_id, quality_event_through_id,
                  knowledge_at, source_as_of, calculation_status, checkpoint_state, content_hash
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) ON CONFLICT DO NOTHING RETURNING id
                """,
                (
                    instrument_id,
                    market_id,
                    unit,
                    point.started_at,
                    definition_set_hash,
                    parent_materialization_id,
                    current_rollup_id,
                    current_source_revision_id,
                    lineage_hash,
                    point.source_revision_through_id,
                    point.quality_event_through_id,
                    point.knowledge_at,
                    point.source_as_of,
                    _overall_status(point),
                    Jsonb(dict(point.checkpoint_state)),
                    content_hash,
                ),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """
                    SELECT id FROM indicator_materializations
                    WHERE instrument_id = %s AND candle_unit = %s AND occurred_at = %s
                      AND definition_set_hash = %s AND content_hash = %s
                      AND source_revision_through_id = %s
                      AND quality_event_through_id IS NOT DISTINCT FROM %s
                    ORDER BY id DESC LIMIT 1
                    """,
                    (
                        instrument_id,
                        unit,
                        point.started_at,
                        definition_set_hash,
                        content_hash,
                        point.source_revision_through_id,
                        point.quality_event_through_id,
                    ),
                ).fetchone()
            assert row is not None
            materialization_id = int(row["id"])
            value_ids: dict[tuple[str, str], int] = {}
            for value_name, value in point.values.items():
                definition_key = _definition_key(value_name)
                value_row = connection.execute(
                    """
                    INSERT INTO indicator_values (
                      materialization_id, definition_version_id, value_name, value,
                      calculation_status, parent_value_id
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (materialization_id, definition_version_id, value_name)
                    DO NOTHING RETURNING id
                    """,
                    (
                        materialization_id,
                        definitions[definition_key],
                        value_name,
                        value,
                        point.statuses[definition_key],
                        parent_values.get((definition_key, value_name)),
                    ),
                ).fetchone()
                if value_row is None:
                    value_row = connection.execute(
                        """
                        SELECT id FROM indicator_values
                        WHERE materialization_id = %s AND definition_version_id = %s
                          AND value_name = %s
                        """,
                        (materialization_id, definitions[definition_key], value_name),
                    ).fetchone()
                assert value_row is not None
                value_id = int(value_row["id"])
                value_ids[(definition_key, value_name)] = value_id
                if current_rollup_id is not None:
                    connection.execute(
                        """
                        INSERT INTO indicator_value_rollups (indicator_value_id, candle_rollup_id)
                        VALUES (%s, %s) ON CONFLICT DO NOTHING
                        """,
                        (value_id, current_rollup_id),
                    )
            parent_materialization_id = materialization_id
            parent_values = value_ids
            stored.append(StoredIndicatorPoint(materialization_id, point))
    return tuple(stored)


def read_indicator_points(
    repository: object,
    instrument_id: int,
    unit: str,
    start_at: datetime,
    end_at: datetime,
    as_of: datetime,
    definition_set_hash: str,
    *,
    after_at: datetime | None,
    ceiling_id: int,
    limit: int,
) -> tuple[StoredIndicatorPoint, ...]:
    connector = getattr(repository, "_connect", None)
    if not callable(connector):
        return ()
    result: list[StoredIndicatorPoint] = []
    with connector() as connection:
        rows = connection.execute(
            """
            SELECT * FROM (
              SELECT materialization.*,
                     ROW_NUMBER() OVER (
                       PARTITION BY instrument_id, candle_unit, occurred_at, definition_set_hash
                       ORDER BY source_revision_through_id DESC,
                                quality_event_through_id DESC NULLS LAST,
                                knowledge_at DESC, id DESC
                     ) AS projection_rank
              FROM indicator_materializations materialization
              WHERE instrument_id=%s AND candle_unit=%s
                AND occurred_at >= %s AND occurred_at < %s
                AND knowledge_at <= %s AND definition_set_hash=%s
                AND id <= %s AND (%s::timestamptz IS NULL OR occurred_at > %s)
            ) projection WHERE projection_rank=1 ORDER BY occurred_at
            LIMIT %s
            """,
            (
                instrument_id,
                unit,
                start_at,
                end_at,
                as_of,
                definition_set_hash,
                ceiling_id,
                after_at,
                after_at,
                limit,
            ),
        ).fetchall()
        materialization_ids = [int(row["id"]) for row in rows]
        all_value_rows = (
            connection.execute(
                """
                SELECT value.materialization_id, value.value_name, value.value,
                       value.calculation_status,
                       definition.indicator_key, version.definition_hash
                FROM indicator_values value
                JOIN indicator_definition_versions version
                  ON version.id=value.definition_version_id
                JOIN indicator_definitions definition ON definition.id=version.definition_id
                WHERE value.materialization_id = ANY(%s)
                """,
                (materialization_ids,),
            ).fetchall()
            if materialization_ids
            else []
        )
        values_by_materialization: dict[int, list[dict[str, object]]] = {}
        for value_row in all_value_rows:
            values_by_materialization.setdefault(int(value_row["materialization_id"]), []).append(
                value_row
            )
        for row in rows:
            value_rows = values_by_materialization.get(int(row["id"]), [])
            values = {
                str(item["value_name"]): cast(Decimal | None, item["value"]) for item in value_rows
            }
            statuses = {
                str(item["indicator_key"]): str(item["calculation_status"]) for item in value_rows
            }
            definitions = {
                str(item["indicator_key"]): str(item["definition_hash"]) for item in value_rows
            }
            current_rollup_id = row["current_rollup_id"]
            current_source_id = row["current_source_revision_id"]
            point = IndicatorPoint(
                started_at=row["occurred_at"],
                values=MappingProxyType(values),
                statuses=MappingProxyType(statuses),  # type: ignore[arg-type]
                definition_version_hashes=MappingProxyType(definitions),
                lineage_by_indicator=MappingProxyType({}),
                rollup_ids=((int(current_rollup_id),) if current_rollup_id is not None else ()),
                source_revision_through_id=int(row["source_revision_through_id"]),
                quality_event_through_id=(
                    int(row["quality_event_through_id"])
                    if row["quality_event_through_id"] is not None
                    else None
                ),
                source_as_of=row["source_as_of"],
                knowledge_at=row["knowledge_at"],
                current_input_id=int(current_rollup_id or current_source_id),
                current_input_is_rollup=current_rollup_id is not None,
                checkpoint_state=MappingProxyType(dict(row["checkpoint_state"])),
            )
            result.append(StoredIndicatorPoint(int(row["id"]), point))
    return tuple(result)


def indicator_projection_ceiling(
    repository: object,
    instrument_id: int,
    unit: str,
    as_of: datetime,
    definition_set_hash: str,
) -> int:
    connector = getattr(repository, "_connect", None)
    if not callable(connector):
        cache = repository.__dict__.get("_indicator_materialization_cache", {})
        return max(cache.values(), default=0)
    with connector() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(id),0) AS ceiling FROM indicator_materializations
            WHERE instrument_id=%s AND candle_unit=%s AND knowledge_at<=%s
              AND definition_set_hash=%s
            """,
            (instrument_id, unit, as_of, definition_set_hash),
        ).fetchone()
    return int(row["ceiling"]) if row is not None else 0


def materialize_market_statistics(
    repository: object,
    instrument_id: int,
    unit: str,
    points: tuple[MarketStatisticPoint, ...],
    *,
    _connection: Any | None = None,
) -> tuple[StoredMarketStatistic, ...]:
    connector = getattr(repository, "_connect", None)
    if not callable(connector):
        cache: dict[tuple[int, str, datetime, str], int] = repository.__dict__.setdefault(
            "_market_statistic_cache", {}
        )
        return tuple(
            StoredMarketStatistic(
                cache.setdefault(
                    (instrument_id, unit, point.started_at, point.content_hash), len(cache) + 1
                ),
                point,
            )
            for point in points
        )
    result: list[StoredMarketStatistic] = []
    manager = connector() if _connection is None else nullcontext(_connection)
    with manager as connection:
        market_row = connection.execute(
            "SELECT id FROM markets WHERE legacy_instrument_id = %s", (instrument_id,)
        ).fetchone()
        if market_row is None:
            return ()
        market_id = int(market_row["id"])
        parent_id: int | None = None
        if points:
            parent_row = connection.execute(
                """
                SELECT id FROM market_statistics
                WHERE instrument_id=%s AND interval=%s
                  AND calculation_version='market-statistics-v1'
                  AND occurred_at < %s AND knowledge_at <= %s
                  AND source_revision_through_id <= %s
                  AND COALESCE(quality_event_through_id,0) <= COALESCE(%s,0)
                ORDER BY occurred_at DESC, source_revision_through_id DESC,
                         quality_event_through_id DESC NULLS LAST, id DESC LIMIT 1
                """,
                (
                    instrument_id,
                    unit,
                    points[0].started_at,
                    points[0].knowledge_at,
                    points[0].source_revision_through_id,
                    points[0].quality_event_through_id,
                ),
            ).fetchone()
            if parent_row is not None:
                parent_id = int(parent_row["id"])
        for point in points:
            if (
                point.current_input_id is None
                or point.source_as_of is None
                or point.knowledge_at is None
            ):
                continue
            current_rollup_id = point.current_input_id if point.current_input_is_rollup else None
            current_source_id = None if point.current_input_is_rollup else point.current_input_id
            lineage_hash = hashlib.sha256(
                f"{parent_id or 0}|{point.current_input_id}|{point.content_hash}".encode()
            ).hexdigest()
            row = connection.execute(
                """
                INSERT INTO market_statistics (
                  market_id, instrument_id, interval, occurred_at, calculation_version,
                  close_return_1, realized_volatility_20, trade_volume, trade_amount,
                  volatility_sample_count, input_completeness_ratio,
                  return_status, volatility_status, trade_status, parent_statistic_id,
                  current_rollup_id, current_source_revision_id,
                  source_revision_through_id, quality_event_through_id,
                  source_as_of, knowledge_at, lineage_hash, checkpoint_state, content_hash
                ) VALUES (
                  %s,%s,%s,%s,'market-statistics-v1',%s,%s,%s,%s,%s,%s,%s,%s,%s,
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                ) ON CONFLICT DO NOTHING RETURNING id
                """,
                (
                    market_id,
                    instrument_id,
                    unit,
                    point.started_at,
                    point.close_return_1,
                    point.realized_volatility_20,
                    point.trade_volume,
                    point.trade_amount,
                    point.volatility_sample_count,
                    point.input_completeness_ratio,
                    point.return_status,
                    point.volatility_status,
                    point.trade_status,
                    parent_id,
                    current_rollup_id,
                    current_source_id,
                    point.source_revision_through_id,
                    point.quality_event_through_id,
                    point.source_as_of,
                    point.knowledge_at,
                    lineage_hash,
                    Jsonb(dict(point.checkpoint_state)),
                    point.content_hash,
                ),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """
                    SELECT id FROM market_statistics
                    WHERE market_id=%s AND interval=%s AND occurred_at=%s
                      AND calculation_version='market-statistics-v1'
                      AND content_hash=%s AND source_revision_through_id=%s
                      AND quality_event_through_id IS NOT DISTINCT FROM %s
                    ORDER BY id DESC LIMIT 1
                    """,
                    (
                        market_id,
                        unit,
                        point.started_at,
                        point.content_hash,
                        point.source_revision_through_id,
                        point.quality_event_through_id,
                    ),
                ).fetchone()
            assert row is not None
            parent_id = int(row["id"])
            result.append(StoredMarketStatistic(parent_id, point))
    return tuple(result)


def read_market_statistics(
    repository: object,
    instrument_id: int,
    unit: str,
    start_at: datetime,
    end_at: datetime,
    as_of: datetime,
    *,
    after_at: datetime | None,
    ceiling_id: int,
    limit: int,
) -> tuple[StoredMarketStatistic, ...]:
    connector = getattr(repository, "_connect", None)
    if not callable(connector):
        return ()
    with connector() as connection:
        rows = connection.execute(
            """
            SELECT * FROM (
              SELECT statistic.*,
                     ROW_NUMBER() OVER (
                       PARTITION BY market_id, interval, occurred_at, calculation_version
                       ORDER BY source_revision_through_id DESC,
                                quality_event_through_id DESC NULLS LAST,
                                knowledge_at DESC, id DESC
                     ) AS projection_rank
              FROM market_statistics statistic
              WHERE instrument_id=%s AND interval=%s
                AND occurred_at >= %s AND occurred_at < %s
                AND knowledge_at <= %s AND calculation_version='market-statistics-v1'
                AND id <= %s AND (%s::timestamptz IS NULL OR occurred_at > %s)
            ) projection WHERE projection_rank=1 ORDER BY occurred_at LIMIT %s
            """,
            (instrument_id, unit, start_at, end_at, as_of, ceiling_id, after_at, after_at, limit),
        ).fetchall()
    return tuple(
        StoredMarketStatistic(
            int(row["id"]),
            MarketStatisticPoint(
                started_at=row["occurred_at"],
                close_return_1=row["close_return_1"],
                realized_volatility_20=row["realized_volatility_20"],
                trade_volume=row["trade_volume"],
                trade_amount=row["trade_amount"],
                volatility_sample_count=int(row["volatility_sample_count"]),
                input_completeness_ratio=row["input_completeness_ratio"],
                return_status=row["return_status"],
                volatility_status=row["volatility_status"],
                trade_status=row["trade_status"],
                current_input_id=int(row["current_rollup_id"] or row["current_source_revision_id"]),
                current_input_is_rollup=row["current_rollup_id"] is not None,
                source_revision_through_id=int(row["source_revision_through_id"]),
                quality_event_through_id=(
                    int(row["quality_event_through_id"])
                    if row["quality_event_through_id"] is not None
                    else None
                ),
                source_as_of=row["source_as_of"],
                knowledge_at=row["knowledge_at"],
                content_hash=str(row["content_hash"]),
                checkpoint_state=MappingProxyType(dict(row["checkpoint_state"])),
            ),
        )
        for row in rows
    )


def market_statistic_projection_ceiling(
    repository: object, instrument_id: int, unit: str, as_of: datetime
) -> int:
    connector = getattr(repository, "_connect", None)
    if not callable(connector):
        cache = repository.__dict__.get("_market_statistic_cache", {})
        return max(cache.values(), default=0)
    with connector() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(id),0) AS ceiling FROM market_statistics
            WHERE instrument_id=%s AND interval=%s AND knowledge_at<=%s
              AND calculation_version='market-statistics-v1'
            """,
            (instrument_id, unit, as_of),
        ).fetchone()
    return int(row["ceiling"]) if row is not None else 0


def _materialize_memory(
    repository: object, instrument_id: int, unit: str, points: tuple[IndicatorPoint, ...]
) -> tuple[StoredIndicatorPoint, ...]:
    cache: dict[tuple[int, str, datetime, str], int] = getattr(
        repository, "_indicator_materialization_cache", {}
    )
    result = []
    for point in points:
        key = (instrument_id, unit, point.started_at, _point_hash(point))
        identifier = cache.setdefault(key, len(cache) + 1)
        result.append(StoredIndicatorPoint(identifier, point))
    repository.__dict__["_indicator_materialization_cache"] = cache
    return tuple(result)


def _definition_key(value_name: str) -> str:
    if value_name.startswith("bollinger"):
        return "bollinger20"
    return value_name


def _point_hash(point: IndicatorPoint) -> str:
    payload = {
        "at": point.started_at.isoformat(),
        "definitions": dict(point.definition_version_hashes),
        "frontier": [point.source_revision_through_id, point.quality_event_through_id],
        "checkpoint": dict(point.checkpoint_state),
        "statuses": dict(point.statuses),
        "values": {
            key: str(value) if value is not None else None for key, value in point.values.items()
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _overall_status(point: IndicatorPoint) -> Literal["warming_up", "ready", "missing"]:
    statuses = set(point.statuses.values())
    if "missing" in statuses:
        return "missing"
    if "warming_up" in statuses:
        return "warming_up"
    return "ready"
