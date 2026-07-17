from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from goodmoneying_shared.versioned_microstructure import (
    MicrostructureCandleInput,
    MicrostructureOrderbookInput,
    MicrostructureOrderbookLevel,
    MicrostructurePoint,
    MicrostructureTradeInput,
    SourceQuality,
    calculate_microstructure_bucket,
)


@dataclass(frozen=True)
class StoredMicrostructureStatistic:
    materialization_id: int
    point: MicrostructurePoint


@dataclass(frozen=True)
class ConnectionQualityEvidence:
    interval_id: int
    quality: SourceQuality
    source_as_of: datetime
    knowledge_at: datetime


def microstructure_projection_ceiling(
    repository: object, instrument_id: int, as_of: datetime
) -> int:
    connector = getattr(repository, "_connect", None)
    if not callable(connector):
        return 0
    with connector() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(id),0) AS ceiling_id
            FROM microstructure_materializations
            WHERE instrument_id=%s AND knowledge_at <= %s
            """,
            (instrument_id, as_of),
        ).fetchone()
    return int(row["ceiling_id"]) if row is not None else 0


def read_microstructure_statistics(
    repository: object,
    instrument_id: int,
    start_at: datetime,
    end_at: datetime,
    as_of: datetime,
    after_at: datetime | None = None,
    ceiling_id: int | None = None,
    limit: int = 500,
    calculation_version: str | None = None,
) -> list[StoredMicrostructureStatistic]:
    connector = getattr(repository, "_connect", None)
    if not callable(connector):
        return []
    resolved_ceiling = (
        microstructure_projection_ceiling(repository, instrument_id, as_of)
        if ceiling_id is None
        else ceiling_id
    )
    with connector() as connection:
        rows = connection.execute(
            """
            SELECT * FROM (
              SELECT materialization.id AS materialization_id,
                     materialization.bucket_start_at,
                     definition.calculation_version,
                     materialization.source_candle_revision_id,
                     materialization.orderbook_snapshot_through_id,
                     materialization.trade_event_through_id,
                     materialization.source_receipt_through_id,
                     materialization.quality_event_through_id,
                     materialization.connection_quality_through_id,
                     materialization.knowledge_at, materialization.source_as_of,
                     materialization.input_lineage_hash,
                     closing_snapshot.source_receipt_id
                       AS closing_orderbook_source_receipt_id,
                     statistic.closing_orderbook_snapshot_id,
                     statistic.spread, statistic.spread_bps,
                     statistic.bid_depth_10, statistic.ask_depth_10,
                     statistic.orderbook_imbalance_10,
                     statistic.trade_count, statistic.trade_intensity_per_minute,
                     statistic.volume_intensity_per_minute,
                     statistic.bid_count, statistic.ask_count,
                     statistic.bid_volume, statistic.ask_volume,
                     statistic.bid_ask_imbalance, statistic.execution_strength,
                     statistic.orderbook_status, statistic.orderbook_quality,
                     statistic.trade_status, statistic.trade_quality,
                     statistic.execution_strength_status, statistic.content_hash,
                     ROW_NUMBER() OVER (
                       PARTITION BY materialization.bucket_start_at
                       ORDER BY materialization.knowledge_at DESC,
                                materialization.id DESC
                     ) AS projection_rank
              FROM microstructure_materializations materialization
              JOIN microstructure_definition_versions definition
                ON definition.id=materialization.definition_version_id
              JOIN microstructure_statistics statistic
                ON statistic.materialization_id=materialization.id
              LEFT JOIN orderbook_snapshots closing_snapshot
                ON closing_snapshot.id=statistic.closing_orderbook_snapshot_id
              WHERE materialization.instrument_id=%s
                AND materialization.bucket_start_at >= %s
                AND materialization.bucket_start_at < %s
                AND (%s::timestamptz IS NULL OR materialization.bucket_start_at > %s)
                AND materialization.knowledge_at <= %s
                AND materialization.id <= %s
                AND (%s::text IS NULL OR definition.calculation_version=%s)
            ) projection
            WHERE projection_rank=1
            ORDER BY projection.bucket_start_at, projection.materialization_id
            LIMIT %s
            """,
            (
                instrument_id,
                start_at,
                end_at,
                after_at,
                after_at,
                as_of,
                resolved_ceiling,
                calculation_version,
                calculation_version,
                limit,
            ),
        ).fetchall()
    return [_stored(row) for row in rows]


def run_next_microstructure_invalidation(
    repository: object, worker_id: str, *, now: datetime | None = None
) -> int:
    del now  # 임대와 fencing은 PostgreSQL DB 시계만 사용한다.
    connector = getattr(repository, "_connect", None)
    if not callable(connector):
        return 0
    with connector() as connection:
        row = connection.execute(
            """
            WITH candidate AS (
              SELECT id, status AS previous_status
              FROM microstructure_invalidations invalidation
              WHERE (
                  (status IN ('pending','retry_wait') AND next_retry_at <= clock_timestamp())
                  OR (status='running' AND lease_expires_at <= clock_timestamp())
                )
                AND bucket_start_at + interval '1 minute' <= clock_timestamp()
                AND NOT EXISTS (
                  SELECT 1 FROM microstructure_invalidations predecessor
                  WHERE predecessor.instrument_id=invalidation.instrument_id
                    AND predecessor.bucket_start_at=invalidation.bucket_start_at
                    AND predecessor.id < invalidation.id
                    AND predecessor.status IN ('pending','retry_wait','running')
                )
                AND NOT EXISTS (
                  SELECT 1 FROM microstructure_invalidations active
                  WHERE active.instrument_id=invalidation.instrument_id
                    AND active.bucket_start_at=invalidation.bucket_start_at
                    AND active.status='running'
                    AND active.lease_expires_at > clock_timestamp()
                )
              ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1
            )
            UPDATE microstructure_invalidations invalidation SET
              status='running', attempt_count=attempt_count+1,
              lease_owner=%s,
              lease_expires_at=clock_timestamp() + interval '120 seconds',
              lease_generation=lease_generation+1,
              updated_at=clock_timestamp()
            FROM candidate WHERE invalidation.id=candidate.id
            RETURNING invalidation.*, candidate.previous_status
            """,
            (worker_id,),
        ).fetchone()
    if row is None:
        return 0

    invalidation_id = int(row["id"])
    generation = int(row["lease_generation"])
    instrument_id = int(row["instrument_id"])
    market_id = int(row["market_id"])
    started_at = cast(datetime, row["bucket_start_at"])
    ended_at = started_at + timedelta(minutes=1)
    lock_key = (
        f"microstructure-invalidations-active-bucket:"
        f"{instrument_id}:{started_at.astimezone(UTC).isoformat()}"
    )
    fence = connector()
    try:
        fence.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (lock_key,))
        live = fence.execute(
            """
            SELECT * FROM microstructure_invalidations
            WHERE id=%s AND status='running' AND lease_owner=%s
              AND lease_generation=%s AND lease_expires_at > clock_timestamp()
            FOR UPDATE
            """,
            (invalidation_id, worker_id, generation),
        ).fetchone()
        if live is None:
            fence.rollback()
            return 0
        prior = fence.execute(
            """
            SELECT 1 FROM microstructure_invalidations
            WHERE instrument_id=%s AND bucket_start_at=%s AND id < %s
              AND status IN ('pending','retry_wait','running')
            LIMIT 1
            """,
            (instrument_id, started_at, invalidation_id),
        ).fetchone()
        if prior is not None:
            _release_preceding_frontier(
                fence,
                invalidation_id,
                worker_id,
                generation,
                str(row["previous_status"]),
            )
            return 0

        as_of = cast(datetime, live["knowledge_at"])
        quality_ceiling = cast(int | None, live["quality_event_through_id"])
        connection_quality = _connection_qualities(
            fence,
            market_id,
            started_at,
            ended_at,
            as_of,
            int(live["connection_quality_through_id"]),
        )
        trade_quality = connection_quality.get("trade_event")
        if trade_quality is None:
            _release_for_retry(fence, invalidation_id, worker_id, generation, "trade_gap")
            return 0

        source_candle = _source_candle_input(
            fence,
            instrument_id,
            market_id,
            started_at,
            as_of,
            quality_ceiling,
            cast(int | None, live["source_candle_revision_id"]),
        )
        if source_candle is None:
            _release_for_retry(fence, invalidation_id, worker_id, generation, "candle_unverified")
            return 0
        orderbooks = _orderbook_inputs(fence, live, started_at, ended_at, as_of)
        trades = _trade_inputs(fence, live, started_at, ended_at, as_of)
        point = calculate_microstructure_bucket(
            started_at,
            ended_at,
            orderbooks,
            trades,
            orderbook_snapshot_through_id=int(live["orderbook_snapshot_through_id"]),
            trade_event_through_id=int(live["trade_event_through_id"]),
            source_receipt_through_id=int(live["source_receipt_through_id"]),
            source_candle=source_candle,
            orderbook_quality=(
                connection_quality["orderbook_snapshot"].quality
                if "orderbook_snapshot" in connection_quality
                else "unverified"
            ),
            trade_quality=trade_quality.quality,
            connection_quality_through_id=max(
                evidence.interval_id for evidence in connection_quality.values()
            ),
            connection_quality_source_as_of=max(
                evidence.source_as_of for evidence in connection_quality.values()
            ),
            connection_quality_knowledge_at=max(
                evidence.knowledge_at for evidence in connection_quality.values()
            ),
        )
        if point.source_as_of is None or point.knowledge_at is None:
            _release_for_retry(fence, invalidation_id, worker_id, generation, "missing_evidence")
            return 0
        materialization_id = _insert_materialization(
            fence, instrument_id, market_id, point, orderbooks, trades
        )
        completed = fence.execute(
            """
            UPDATE microstructure_invalidations SET
              status='succeeded', lease_owner=NULL, lease_expires_at=NULL,
              finished_at=clock_timestamp(), updated_at=clock_timestamp()
            WHERE id=%s AND status='running' AND lease_owner=%s
              AND lease_generation=%s AND lease_expires_at > clock_timestamp()
            RETURNING id
            """,
            (invalidation_id, worker_id, generation),
        ).fetchone()
        if completed is None:
            fence.rollback()
            return 0
        fence.commit()
        return materialization_id
    except Exception as exc:
        fence.rollback()
        with connector() as connection:
            connection.execute(
                """
                UPDATE microstructure_invalidations SET
                  status=CASE WHEN attempt_count >= max_attempts
                    THEN 'dead_letter' ELSE 'retry_wait' END,
                  next_retry_at=clock_timestamp() + interval '5 seconds',
                  lease_owner=NULL, lease_expires_at=NULL,
                  last_error_code=%s, updated_at=clock_timestamp()
                WHERE id=%s AND lease_owner=%s AND lease_generation=%s
                """,
                (type(exc).__name__, invalidation_id, worker_id, generation),
            )
        raise
    finally:
        try:
            fence.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (lock_key,))
        finally:
            fence.close()


def _connection_qualities(
    connection: Any,
    market_id: int,
    started_at: datetime,
    ended_at: datetime,
    as_of: datetime,
    connection_quality_ceiling: int,
) -> dict[str, ConnectionQualityEvidence]:
    rows = connection.execute(
        """
        SELECT DISTINCT ON (quality.data_type)
          quality.id, quality.data_type, quality.quality,
          quality.range_end_at, quality.detected_at
        FROM realtime_connection_quality_intervals quality
        JOIN realtime_connection_sessions session
          ON session.connection_id=quality.connection_id
        WHERE (
            quality.market_id=%s
            OR (
              quality.market_id IS NULL
              AND session.subscription_scope @> jsonb_build_object(
                'marketIds', jsonb_build_array(%s::bigint)
              )
              AND session.subscription_scope @> jsonb_build_object(
                'dataTypes', jsonb_build_array(quality.data_type)
              )
            )
          )
          AND quality.range_start_at <= %s AND quality.range_end_at >= %s
          AND quality.detected_at <= %s
          AND quality.id <= %s
          AND quality.data_type IN ('trade_event','orderbook_snapshot')
        ORDER BY quality.data_type, quality.detected_at DESC, quality.id DESC
        """,
        (
            market_id,
            market_id,
            started_at,
            ended_at,
            as_of,
            connection_quality_ceiling,
        ),
    ).fetchall()
    return {
        str(row["data_type"]): ConnectionQualityEvidence(
            interval_id=int(row["id"]),
            quality=cast(SourceQuality, row["quality"]),
            source_as_of=cast(datetime, row["range_end_at"]),
            knowledge_at=cast(datetime, row["detected_at"]),
        )
        for row in rows
    }


def _source_candle_input(
    connection: Any,
    instrument_id: int,
    market_id: int,
    started_at: datetime,
    as_of: datetime,
    quality_ceiling: int | None,
    source_candle_revision_ceiling: int | None,
) -> MicrostructureCandleInput | None:
    quality_row = connection.execute(
        """
        SELECT event.id, event.new_status, event.detected_at
        FROM data_quality_events event
        JOIN collection_target_specs specification ON specification.id=event.target_spec_id
        WHERE specification.market_id=%s
          AND specification.data_type='source_candle'
          AND specification.candle_unit='1m'
          AND event.detected_at <= %s
          AND (%s::bigint IS NULL OR event.id <= %s)
          AND tstzrange(event.range_start_at,event.range_end_at,'[)') @> %s
        ORDER BY event.id DESC LIMIT 1
        """,
        (market_id, as_of, quality_ceiling, quality_ceiling, started_at),
    ).fetchone()
    quality = str(quality_row["new_status"]) if quality_row is not None else "unverified"
    revision = None
    if source_candle_revision_ceiling is not None:
        revision = connection.execute(
            """
            SELECT id, trade_volume, trade_amount, knowledge_at, source_as_of
            FROM source_candle_revisions
            WHERE instrument_id=%s AND candle_unit='1m' AND candle_start_at=%s
              AND knowledge_at <= %s AND id <= %s
            ORDER BY source_as_of DESC, revision_number DESC, id DESC LIMIT 1
            """,
            (instrument_id, started_at, as_of, source_candle_revision_ceiling),
        ).fetchone()
    if quality == "no_trade":
        if quality_row is None:
            return None
        return MicrostructureCandleInput(
            source_candle_revision_id=None,
            started_at=started_at,
            knowledge_at=cast(datetime, quality_row["detected_at"]),
            source_as_of=cast(datetime, quality_row["detected_at"]),
            quality="no_trade",
            volume=Decimal(0),
            amount=Decimal(0),
            quality_event_through_id=int(quality_row["id"]),
        )
    if revision is None:
        if quality_row is None:
            return None
        return MicrostructureCandleInput(
            source_candle_revision_id=None,
            started_at=started_at,
            knowledge_at=cast(datetime, quality_row["detected_at"]),
            source_as_of=cast(datetime, quality_row["detected_at"]),
            quality=cast(SourceQuality, quality),
            volume=Decimal(0),
            amount=Decimal(0),
            quality_event_through_id=int(quality_row["id"]),
        )
    resolved_quality: SourceQuality = (
        "available" if quality == "unverified" else cast(SourceQuality, quality)
    )
    return MicrostructureCandleInput(
        source_candle_revision_id=int(revision["id"]),
        started_at=started_at,
        knowledge_at=cast(datetime, revision["knowledge_at"]),
        source_as_of=cast(datetime, revision["source_as_of"]),
        quality=resolved_quality,
        volume=Decimal(str(revision["trade_volume"])),
        amount=Decimal(str(revision["trade_amount"])),
        quality_event_through_id=(int(quality_row["id"]) if quality_row is not None else None),
    )


def _orderbook_inputs(
    connection: Any, invalidation: Any, started_at: datetime, ended_at: datetime, as_of: datetime
) -> list[MicrostructureOrderbookInput]:
    snapshots = connection.execute(
        """
        SELECT snapshot.* FROM orderbook_snapshots snapshot
        JOIN source_receipts receipt ON receipt.id=snapshot.source_receipt_id
        WHERE snapshot.instrument_id=%s AND snapshot.occurred_at >= %s
          AND snapshot.occurred_at < %s AND snapshot.id <= %s
          AND receipt.id <= %s AND snapshot.knowledge_at <= %s
        ORDER BY snapshot.occurred_at, snapshot.id
        """,
        (
            invalidation["instrument_id"],
            started_at,
            ended_at,
            invalidation["orderbook_snapshot_through_id"],
            invalidation["source_receipt_through_id"],
            as_of,
        ),
    ).fetchall()
    result: list[MicrostructureOrderbookInput] = []
    for snapshot in snapshots:
        levels = connection.execute(
            """
            SELECT * FROM orderbook_snapshot_levels
            WHERE snapshot_id=%s ORDER BY level_index
            """,
            (snapshot["id"],),
        ).fetchall()
        result.append(
            MicrostructureOrderbookInput(
                snapshot_id=int(snapshot["id"]),
                occurred_at=cast(datetime, snapshot["occurred_at"]),
                knowledge_at=cast(datetime, snapshot["knowledge_at"]),
                level=(Decimal(str(snapshot["level"])) if snapshot["level"] is not None else None),
                levels=tuple(
                    MicrostructureOrderbookLevel(
                        level_index=int(level["level_index"]),
                        ask_price=Decimal(str(level["ask_price"])),
                        ask_size=Decimal(str(level["ask_size"])),
                        bid_price=Decimal(str(level["bid_price"])),
                        bid_size=Decimal(str(level["bid_size"])),
                    )
                    for level in levels
                ),
                source_receipt_id=int(snapshot["source_receipt_id"]),
            )
        )
    return result


def _trade_inputs(
    connection: Any, invalidation: Any, started_at: datetime, ended_at: datetime, as_of: datetime
) -> list[MicrostructureTradeInput]:
    rows = connection.execute(
        """
        SELECT trade.* FROM trade_events trade
        JOIN source_receipts receipt ON receipt.id=trade.source_receipt_id
        WHERE trade.instrument_id=%s AND trade.occurred_at >= %s
          AND trade.occurred_at < %s AND trade.id <= %s
          AND receipt.id <= %s AND trade.knowledge_at <= %s
        ORDER BY trade.occurred_at, trade.id
        """,
        (
            invalidation["instrument_id"],
            started_at,
            ended_at,
            invalidation["trade_event_through_id"],
            invalidation["source_receipt_through_id"],
            as_of,
        ),
    ).fetchall()
    return [
        MicrostructureTradeInput(
            trade_event_id=int(row["id"]),
            occurred_at=cast(datetime, row["occurred_at"]),
            knowledge_at=cast(datetime, row["knowledge_at"]),
            direction=str(row["ask_bid"]),
            volume=Decimal(str(row["trade_volume"])),
            amount=Decimal(str(row["trade_amount"])),
            source_receipt_id=int(row["source_receipt_id"]),
        )
        for row in rows
    ]


def _insert_materialization(
    connection: Any,
    instrument_id: int,
    market_id: int,
    point: MicrostructurePoint,
    orderbooks: list[MicrostructureOrderbookInput],
    trades: list[MicrostructureTradeInput],
) -> int:
    definition = connection.execute(
        "SELECT id FROM microstructure_definition_versions WHERE calculation_version=%s",
        (point.calculation_version,),
    ).fetchone()
    parent = connection.execute(
        """
        SELECT id FROM microstructure_materializations
        WHERE instrument_id=%s AND bucket_start_at=%s AND definition_version_id=%s
          AND knowledge_at <= %s
          AND orderbook_snapshot_through_id <= %s
          AND trade_event_through_id <= %s
          AND source_receipt_through_id <= %s
          AND connection_quality_through_id <= %s
          AND COALESCE(quality_event_through_id,0) <= COALESCE(%s,0)
          AND (
            %s::bigint IS NULL OR source_candle_revision_id IS NULL
            OR source_candle_revision_id <= %s
          )
        ORDER BY knowledge_at DESC, id DESC LIMIT 1
        """,
        (
            instrument_id,
            point.started_at,
            definition["id"],
            point.knowledge_at,
            point.orderbook_snapshot_through_id,
            point.trade_event_through_id,
            point.source_receipt_through_id,
            point.connection_quality_through_id,
            point.quality_event_through_id,
            point.source_candle_revision_id,
            point.source_candle_revision_id,
        ),
    ).fetchone()
    inserted = connection.execute(
        """
        INSERT INTO microstructure_materializations (
          instrument_id, market_id, definition_version_id, bucket_start_at,
          parent_materialization_id, source_candle_revision_id,
          orderbook_snapshot_through_id, trade_event_through_id,
          source_receipt_through_id, quality_event_through_id,
          connection_quality_through_id,
          knowledge_at, source_as_of, input_lineage_hash, content_hash
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING RETURNING id
        """,
        (
            instrument_id,
            market_id,
            definition["id"],
            point.started_at,
            parent["id"] if parent is not None else None,
            point.source_candle_revision_id,
            point.orderbook_snapshot_through_id,
            point.trade_event_through_id,
            point.source_receipt_through_id,
            point.quality_event_through_id,
            point.connection_quality_through_id,
            point.knowledge_at,
            point.source_as_of,
            point.input_lineage_hash,
            point.content_hash,
        ),
    ).fetchone()
    if inserted is None:
        existing = connection.execute(
            """
            SELECT id FROM microstructure_materializations
            WHERE instrument_id=%s AND definition_version_id=%s AND bucket_start_at=%s
              AND orderbook_snapshot_through_id=%s AND trade_event_through_id=%s
              AND source_receipt_through_id=%s
              AND source_candle_revision_id IS NOT DISTINCT FROM %s
              AND quality_event_through_id IS NOT DISTINCT FROM %s
              AND connection_quality_through_id=%s
              AND content_hash=%s
            """,
            (
                instrument_id,
                definition["id"],
                point.started_at,
                point.orderbook_snapshot_through_id,
                point.trade_event_through_id,
                point.source_receipt_through_id,
                point.source_candle_revision_id,
                point.quality_event_through_id,
                point.connection_quality_through_id,
                point.content_hash,
            ),
        ).fetchone()
        return int(existing["id"])
    materialization_id = int(inserted["id"])
    parent_statistic = (
        connection.execute(
            "SELECT id FROM microstructure_statistics WHERE materialization_id=%s",
            (parent["id"],),
        ).fetchone()
        if parent is not None
        else None
    )
    connection.execute(
        """
        INSERT INTO microstructure_statistics (
          materialization_id, parent_statistic_id, closing_orderbook_snapshot_id,
          spread, spread_bps, bid_depth_10, ask_depth_10, orderbook_imbalance_10,
          trade_count, trade_intensity_per_minute, volume_intensity_per_minute,
          bid_count, ask_count, bid_volume, ask_volume, bid_ask_imbalance,
          execution_strength, orderbook_status, orderbook_quality, trade_status,
          trade_quality, execution_strength_status, content_hash
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            materialization_id,
            parent_statistic["id"] if parent_statistic is not None else None,
            point.closing_orderbook_snapshot_id,
            point.spread,
            point.spread_bps,
            point.bid_depth_10,
            point.ask_depth_10,
            point.orderbook_imbalance_10,
            point.trade_count,
            point.trade_intensity_per_minute,
            point.volume_intensity_per_minute,
            point.buy_count,
            point.sell_count,
            point.buy_volume,
            point.sell_volume,
            point.buy_sell_imbalance,
            point.execution_strength,
            point.orderbook_status,
            point.orderbook_quality,
            point.trade_status,
            point.trade_quality,
            point.execution_strength_status,
            point.content_hash,
        ),
    )
    for orderbook_item in orderbooks:
        connection.execute(
            """
            INSERT INTO microstructure_materialization_orderbooks
              (materialization_id,orderbook_snapshot_id,source_receipt_id)
            VALUES (%s,%s,%s)
            """,
            (
                materialization_id,
                orderbook_item.snapshot_id,
                orderbook_item.source_receipt_id,
            ),
        )
    for trade_item in trades:
        connection.execute(
            """
            INSERT INTO microstructure_materialization_trades
              (materialization_id,trade_event_id,source_receipt_id)
            VALUES (%s,%s,%s)
            """,
            (materialization_id, trade_item.trade_event_id, trade_item.source_receipt_id),
        )
    return materialization_id


def _release_for_retry(
    connection: Any, invalidation_id: int, worker_id: str, generation: int, error: str
) -> None:
    connection.execute(
        """
        UPDATE microstructure_invalidations SET
          status=CASE WHEN attempt_count >= max_attempts
            THEN 'dead_letter' ELSE 'retry_wait' END,
          next_retry_at=clock_timestamp() + interval '5 seconds',
          lease_owner=NULL, lease_expires_at=NULL, last_error_code=%s,
          finished_at=CASE WHEN attempt_count >= max_attempts
            THEN clock_timestamp() ELSE NULL END,
          updated_at=clock_timestamp()
        WHERE id=%s AND lease_owner=%s AND lease_generation=%s
        """,
        (error, invalidation_id, worker_id, generation),
    )
    connection.commit()


def _release_preceding_frontier(
    connection: Any,
    invalidation_id: int,
    worker_id: str,
    generation: int,
    previous_status: str,
) -> None:
    restored_status = (
        previous_status if previous_status in {"pending", "retry_wait"} else "retry_wait"
    )
    connection.execute(
        """
        UPDATE microstructure_invalidations SET
          status=%s, attempt_count=GREATEST(attempt_count-1,0),
          lease_owner=NULL, lease_expires_at=NULL,
          last_error_code='preceding_frontier', updated_at=clock_timestamp()
        WHERE id=%s AND lease_owner=%s AND lease_generation=%s
        """,
        (restored_status, invalidation_id, worker_id, generation),
    )
    connection.commit()


def _stored(row: Any) -> StoredMicrostructureStatistic:
    return StoredMicrostructureStatistic(
        materialization_id=int(row["materialization_id"]),
        point=MicrostructurePoint(
            started_at=cast(datetime, row["bucket_start_at"]),
            calculation_version=str(row["calculation_version"]),
            closing_orderbook_snapshot_id=cast(int | None, row["closing_orderbook_snapshot_id"]),
            closing_orderbook_source_receipt_id=cast(
                int | None, row["closing_orderbook_source_receipt_id"]
            ),
            spread=_decimal(row["spread"]),
            spread_bps=_decimal(row["spread_bps"]),
            bid_depth_10=_decimal(row["bid_depth_10"]),
            ask_depth_10=_decimal(row["ask_depth_10"]),
            orderbook_imbalance_10=_decimal(row["orderbook_imbalance_10"]),
            trade_count=cast(int | None, row["trade_count"]),
            trade_intensity_per_minute=_decimal(row["trade_intensity_per_minute"]),
            volume_intensity_per_minute=_decimal(row["volume_intensity_per_minute"]),
            buy_count=cast(int | None, row["bid_count"]),
            sell_count=cast(int | None, row["ask_count"]),
            buy_volume=_decimal(row["bid_volume"]),
            sell_volume=_decimal(row["ask_volume"]),
            buy_sell_imbalance=_decimal(row["bid_ask_imbalance"]),
            execution_strength=_decimal(row["execution_strength"]),
            orderbook_status=cast(Any, row["orderbook_status"]),
            orderbook_quality=cast(Any, row["orderbook_quality"]),
            trade_status=cast(Any, row["trade_status"]),
            trade_quality=cast(Any, row["trade_quality"]),
            execution_strength_status=cast(Any, row["execution_strength_status"]),
            orderbook_snapshot_through_id=int(row["orderbook_snapshot_through_id"]),
            trade_event_through_id=int(row["trade_event_through_id"]),
            source_receipt_through_id=int(row["source_receipt_through_id"]),
            source_candle_revision_id=cast(int | None, row["source_candle_revision_id"]),
            quality_event_through_id=cast(int | None, row["quality_event_through_id"]),
            connection_quality_through_id=int(row["connection_quality_through_id"]),
            source_as_of=cast(datetime, row["source_as_of"]),
            knowledge_at=cast(datetime, row["knowledge_at"]),
            input_lineage_hash=str(row["input_lineage_hash"]),
            content_hash=str(row["content_hash"]),
        ),
    )


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))
