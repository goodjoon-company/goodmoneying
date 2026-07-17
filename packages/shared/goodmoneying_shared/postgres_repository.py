from __future__ import annotations

import json
import os
import uuid
from calendar import monthrange
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from math import ceil
from typing import Any, Literal, cast

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from goodmoneying_shared.aggregation import (
    AGGREGATION_UNITS,
    SOURCE_FETCH_BATCH_SIZE,
    aggregate_candles,
    rollup_bucket_start,
)
from goodmoneying_shared.coverage_transition import replace_coverage_with_classification
from goodmoneying_shared.data_foundation import (
    INSTRUMENT_ADVISORY_LOCK_NAMESPACE,
    CollectionSubscriptionDesire,
    CoverageEvidence,
    classify_coverage,
    internal_minute_candle_gaps,
)
from goodmoneying_shared.models import (
    AuditLogSummary,
    BackfillJob,
    BackfillJobDetail,
    BackfillJobTarget,
    BackfillPlan,
    BackfillWorkerStatus,
    CandidateUniverseEntry,
    CandleAggregationJob,
    CandleAggregationJobTarget,
    CandleView,
    CollectionActivityBucket,
    CollectionDashboardTarget,
    CollectionDataStatus,
    CollectionPlan,
    CollectionRun,
    CollectionWorkerDiagnostic,
    CollectionWorkerError,
    CollectionWorkerHeartbeatStatus,
    CollectionWorkerRuntimeStatus,
    CollectionWorkerStatus,
    CollectionWorkerStatusSummary,
    CollectionWorkerType,
    CoverageSegment,
    CoverageStatus,
    DashboardSummary,
    FetchEvidence,
    HealthCheck,
    Instrument,
    MarketListRow,
    MissingRangeSummary,
    NotificationEvent,
    OperationsTrendPoint,
    OrderbookSummary,
    RealtimeCollectionHeatmapBucket,
    RealtimeCollectionHeatmapRow,
    RealtimeSourceFrame,
    RealtimeWorkerStatus,
    SourceCandle,
    StorageBreakdownItem,
    TickerSnapshot,
    TradeEvent,
    TradeFrequencyStatus,
    TradeSummary,
)
from goodmoneying_shared.runtime_readiness import assert_p1_runtime_ready
from goodmoneying_shared.time import KST, isoformat_kst, minute_bucket, now_kst

Row = dict[str, Any]
BACKFILL_LEASE_SECONDS = 120
BACKFILL_RETRY_BASE_SECONDS = 5
BACKFILL_RETRY_MAX_SECONDS = 300
SOURCE_EVIDENCE_SCHEMA_VERSION = "20260717000300"
SOURCE_EVIDENCE_COLLECTOR_VERSION = "postgres-repository-v2"


class BackfillTargetNotWritableError(RuntimeError):
    """정책 전이로 백필 대상이 더 이상 기록 가능한 상태가 아님."""


def _is_fixture_candidate_entry(market_code: str, display_name: str) -> bool:
    return (
        market_code.startswith("KRW-GM") and market_code.removeprefix("KRW-GM").isdigit()
    ) or display_name.startswith("굿머니코인")


def _reject_fixture_candidate_entries(entries: list[tuple[str, str, str]]) -> None:
    fixture_codes = [
        market_code
        for market_code, display_name, _acc_trade_price_24h in entries
        if _is_fixture_candidate_entry(market_code, display_name)
    ]
    if fixture_codes:
        sample = ", ".join(fixture_codes[:5])
        raise ValueError(
            f"PostgreSQL 수집 후보군에는 fixture 데이터를 저장할 수 없다. fixture 후보={sample}"
        )


def _format_storage_bytes(value: int) -> str:
    if value >= 1024**3:
        return f"{value / 1024**3:.1f}GB"
    if value > 0:
        return f"{value / 1024**2:.1f}MB"
    return f"{value}B"


class PostgresOperationsRepository:
    """PostgreSQL 계약 기반 런타임 저장소."""

    def __init__(
        self,
        database_url: str,
        *,
        connect_and_statement_timeout_seconds: float | None = None,
        enforce_backfill_safety_gate: bool | None = None,
        release_sha: str | None = None,
    ) -> None:
        self._database_url = database_url
        self._backfill_worker_id = f"backfill:{uuid.uuid4()}"
        self._connect_and_statement_timeout_seconds = connect_and_statement_timeout_seconds
        self._enforce_backfill_safety_gate = (
            os.getenv("GOODMONEYING_RUNTIME_MODE") == "production"
            if enforce_backfill_safety_gate is None
            else enforce_backfill_safety_gate
        )
        self._release_sha = release_sha or os.getenv("GOODMONEYING_RELEASE_SHA", "")

    def _connect(self) -> psycopg.Connection[Any]:
        options = "-c timezone=UTC"
        connect_timeout: int | None = None
        if self._connect_and_statement_timeout_seconds is not None:
            statement_timeout_ms = max(
                1,
                round(self._connect_and_statement_timeout_seconds * 1_000),
            )
            options += f" -c statement_timeout={statement_timeout_ms}"
            connect_timeout = max(
                1,
                ceil(self._connect_and_statement_timeout_seconds),
            )
        if connect_timeout is not None:
            return psycopg.connect(
                self._database_url,
                row_factory=dict_row,
                options=options,
                connect_timeout=connect_timeout,
            )
        return psycopg.connect(
            self._database_url,
            row_factory=dict_row,
            options=options,
        )

    def assert_runtime_ready(self) -> None:
        try:
            with self._connect() as connection:
                assert_p1_runtime_ready(connection)
        except Exception as exc:
            raise RuntimeError("P1 PostgreSQL 런타임 계약을 초기화할 수 없다.") from exc

    def upsert_instrument(self, market_code: str, display_name: str) -> Instrument:
        quote_currency, base_asset = market_code.split("-", maxsplit=1)
        with self._connect() as conn:
            row = _expect_row(
                conn.execute(
                    """
                    INSERT INTO instruments (
                      exchange, market_code, quote_currency, base_asset, display_name
                    )
                    VALUES ('UPBIT', %s, %s, %s, %s)
                    ON CONFLICT (exchange, market_code) DO UPDATE SET
                      display_name = excluded.display_name,
                      updated_at = now()
                    RETURNING *
                    """,
                    (market_code, quote_currency, base_asset, display_name),
                ).fetchone()
            )
        return _instrument(row)

    def refresh_candidate_universe(
        self, entries: list[tuple[str, str, str]]
    ) -> list[CandidateUniverseEntry]:
        _reject_fixture_candidate_entries(entries)
        started_at = now_kst()
        with self._connect() as conn:
            run_id = int(
                _expect_row(
                    conn.execute(
                        """
                        INSERT INTO collection_runs (
                          run_type, data_type, status, trigger_type, started_at
                        )
                        VALUES (
                          'candidate_refresh', 'candidate_universe', 'running', 'schedule', %s
                        )
                        RETURNING id
                        """,
                        (started_at,),
                    ).fetchone()
                )["id"]
            )
            snapshot_id = _expect_row(
                conn.execute(
                    """
                    INSERT INTO candidate_universe_snapshots (
                      source, exchange, quote_currency, ranked_at
                    )
                    VALUES ('UPBIT', 'UPBIT', 'KRW', %s)
                    RETURNING id
                    """,
                    (now_kst(),),
                ).fetchone()
            )["id"]
            for rank, (market_code, display_name, acc_trade_price_24h) in enumerate(
                entries[:100], start=1
            ):
                instrument = self.upsert_instrument(market_code, display_name)
                conn.execute(
                    """
                    INSERT INTO candidate_universe_entries (
                      snapshot_id, instrument_id, rank, acc_trade_price_24h,
                      is_default_selected
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (snapshot_id, instrument.id, rank, Decimal(acc_trade_price_24h), rank <= 50),
                )
            conn.execute(
                """
                UPDATE collection_targets
                SET candidate_status = CASE
                  WHEN instrument_id IN (
                    SELECT instrument_id FROM candidate_universe_entries
                    WHERE snapshot_id = %s
                  )
                  THEN 'in_universe'
                  ELSE 'out_of_universe'
                END
                """,
                (snapshot_id,),
            )
            finished_at = now_kst()
            conn.execute(
                "UPDATE collection_runs SET status = 'succeeded', finished_at = %s WHERE id = %s",
                (finished_at, run_id),
            )
        return self.list_candidate_universe()[1]

    def ensure_default_active_targets(self, limit: int = 50) -> list[Instrument]:
        with self._connect() as conn:
            active_count = _expect_row(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM collection_targets WHERE status = 'active'"
                ).fetchone()
            )["count"]
            if active_count == 0:
                snapshot_id = _latest_snapshot_id(conn)
                rows = conn.execute(
                    """
                    SELECT instrument_id
                    FROM candidate_universe_entries
                    WHERE snapshot_id = %s
                    ORDER BY rank
                    LIMIT %s
                    """,
                    (snapshot_id, limit),
                ).fetchall()
                for target_order, row in enumerate(rows, start=1):
                    self._activate_target(
                        conn, int(row["instrument_id"]), "system", "default_top_50", target_order
                    )
        return self.list_active_targets()

    def update_active_targets(
        self, instrument_ids: list[int], reason: str | None
    ) -> list[Instrument]:
        if len(instrument_ids) > 50:
            raise ValueError("활성 수집 대상은 최대 50개까지 선택할 수 있다.")
        if len(set(instrument_ids)) != len(instrument_ids):
            raise ValueError("활성 수집 대상은 중복될 수 없다.")
        with self._connect() as conn:
            snapshot_id = _latest_snapshot_id(conn)
            candidate_ids = {
                int(row["instrument_id"])
                for row in conn.execute(
                    "SELECT instrument_id FROM candidate_universe_entries WHERE snapshot_id = %s",
                    (snapshot_id,),
                ).fetchall()
            }
            current_ids = {
                int(row["instrument_id"])
                for row in conn.execute(
                    "SELECT instrument_id FROM collection_targets WHERE status = 'active'"
                ).fetchall()
            }
            next_ids = set(instrument_ids)
            newly_selected_ids = next_ids - current_ids
            if not newly_selected_ids.issubset(candidate_ids):
                raise ValueError("활성 수집 대상은 수집 후보군 안에서만 선택할 수 있다.")
            for instrument_id in sorted(current_ids - next_ids):
                self._deactivate_target(conn, instrument_id, "local_user", reason)
            for target_order, instrument_id in enumerate(instrument_ids, start=1):
                self._activate_target(conn, instrument_id, "local_user", reason, target_order)
        return self.list_active_targets()

    def list_candidate_universe(self) -> tuple[datetime, list[CandidateUniverseEntry]]:
        with self._connect() as conn:
            snapshot_id = _latest_snapshot_id(conn)
            rows = conn.execute(
                """
                SELECT
                  cue.rank,
                  cue.acc_trade_price_24h,
                  cus.ranked_at,
                  i.*,
                  COALESCE(ct.status, 'inactive') AS target_status,
                  ct.target_order AS favorite_order,
                  COALESCE(ct.candidate_status, 'in_universe') AS candidate_status
                FROM candidate_universe_entries cue
                JOIN candidate_universe_snapshots cus ON cus.id = cue.snapshot_id
                JOIN instruments i ON i.id = cue.instrument_id
                LEFT JOIN collection_targets ct ON ct.instrument_id = i.id
                WHERE cue.snapshot_id = %s
                ORDER BY cue.rank
                """,
                (snapshot_id,),
            ).fetchall()
        ranked_at = cast(datetime, rows[0]["ranked_at"]) if rows else now_kst()
        return ranked_at, [
            CandidateUniverseEntry(
                instrument=_instrument(row),
                rank=int(row["rank"]),
                acc_trade_price_24h=Decimal(row["acc_trade_price_24h"]),
                selected=row["target_status"] == "active",
                candidate_status=cast(
                    Literal["in_universe", "out_of_universe"],
                    row["candidate_status"],
                ),
                favorite_order=(
                    int(row["favorite_order"]) if row["favorite_order"] is not None else None
                ),
            )
            for row in rows
        ]

    def list_active_targets(self) -> list[Instrument]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT i.*
                FROM collection_targets ct
                JOIN instruments i ON i.id = ct.instrument_id
                WHERE ct.status = 'active'
                ORDER BY ct.target_order, i.market_code
                """
            ).fetchall()
        return [_instrument(row) for row in rows]

    def load_collection_subscription_desires(
        self,
    ) -> list[CollectionSubscriptionDesire]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  desire.target_spec_id,
                  market.market_code,
                  desire.desired_state,
                  desire.generation,
                  target.status AS target_status,
                  COALESCE(history.trading_status, 'unknown') AS trading_status,
                  target.data_type,
                  target.continuous
                FROM collection_subscription_desires desire
                JOIN collection_target_specs target ON target.id = desire.target_spec_id
                JOIN markets market ON market.id = target.market_id
                LEFT JOIN market_status_history history
                  ON history.market_id = market.id AND history.valid_to IS NULL
                ORDER BY desire.target_spec_id
                """
            ).fetchall()
        return [
            CollectionSubscriptionDesire(
                target_spec_id=int(row["target_spec_id"]),
                market_code=str(row["market_code"]),
                desired_state=cast(Any, row["desired_state"]),
                generation=int(row["generation"]),
                target_status=cast(Any, row["target_status"]),
                trading_status=cast(Any, row["trading_status"]),
                data_type=cast(Any, row["data_type"]),
                continuous=bool(row["continuous"]),
            )
            for row in rows
        ]

    def mark_collection_subscription_desires_applied(
        self,
        versions: tuple[tuple[int, int], ...],
        *,
        connection_id: str,
    ) -> None:
        if not versions:
            return
        with self._connect() as conn:
            for target_spec_id, generation in versions:
                conn.execute(
                    """
                    UPDATE collection_subscription_desires
                    SET applied_generation = generation,
                        connection_id = %s,
                        last_applied_at = clock_timestamp()
                    WHERE target_spec_id = %s AND generation = %s
                    """,
                    (connection_id, target_spec_id, generation),
                )

    def record_incremental_collection(
        self,
        tickers: list[TickerSnapshot],
        orderbooks: list[OrderbookSummary],
        candles: list[SourceCandle],
    ) -> CollectionRun:
        started_at = now_kst()
        with self._connect() as conn:
            run_id = int(
                _expect_row(
                    conn.execute(
                        """
                        INSERT INTO collection_runs (
                          run_type, data_type, status, trigger_type, started_at
                        )
                        VALUES ('incremental', 'ticker_snapshot', 'running', 'schedule', %s)
                        RETURNING id
                        """,
                        (started_at,),
                    ).fetchone()
                )["id"]
            )
            ticker_rows = self._upsert_tickers(conn, run_id, tickers, requested_at=started_at)
            orderbook_rows = self._upsert_orderbooks(
                conn, run_id, orderbooks, requested_at=started_at
            )
            candle_rows = self._upsert_candles(conn, run_id, candles, requested_at=started_at)
            all_ids = sorted(
                {item.instrument_id for item in tickers}
                | {item.instrument_id for item in orderbooks}
                | {item.instrument_id for item in candles}
            )
            for instrument_id in all_ids:
                conn.execute(
                    """
                    INSERT INTO target_collection_results (
                      collection_run_id, instrument_id, data_type, status,
                      latency_ms, rows_written
                    )
                    VALUES (%s, %s, 'ticker_snapshot', 'succeeded', 0, %s)
                    """,
                    (
                        run_id,
                        instrument_id,
                        ticker_rows.get(instrument_id, 0)
                        + orderbook_rows.get(instrument_id, 0)
                        + candle_rows.get(instrument_id, 0),
                    ),
                )
            finished_at = now_kst()
            conn.execute(
                "UPDATE collection_runs SET status = 'succeeded', finished_at = %s WHERE id = %s",
                (finished_at, run_id),
            )
        return CollectionRun(
            id=run_id,
            run_type="incremental",
            data_type="ticker_snapshot",
            status="succeeded",
            started_at=started_at,
            finished_at=finished_at,
        )

    def record_trade_events(self, trades: list[TradeEvent]) -> int:
        if not trades:
            return 0
        started_at = now_kst()
        with self._connect() as conn:
            run_id = int(
                _expect_row(
                    conn.execute(
                        """
                        INSERT INTO collection_runs (
                          run_type, data_type, status, trigger_type, started_at
                        )
                        VALUES ('incremental', 'trade_event', 'running', 'schedule', %s)
                        RETURNING id
                        """,
                        (started_at,),
                    ).fetchone()
                )["id"]
            )
            inserted_by_instrument: dict[int, int] = {}
            manifests = self._source_manifests_by_instrument(
                conn,
                run_id=run_id,
                data_type="trade_event",
                candle_unit=None,
                endpoint="/websocket/v1/trade",
                items=trades,
                requested_at=started_at,
            )
            for trade in trades:
                market_id, target_spec_id, manifest_id = manifests[trade.instrument_id]
                row = conn.execute(
                    """
                    INSERT INTO trade_events (
                      instrument_id, source, sequential_id, trade_timestamp_at,
                      trade_price, trade_volume, trade_amount, ask_bid,
                      collected_at, collection_run_id, market_id, occurred_at,
                      received_at, stored_at, knowledge_at, fetch_manifest_id
                    )
                    VALUES (
                      %s, 'UPBIT', %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, clock_timestamp(), %s, %s
                    )
                    ON CONFLICT (instrument_id, source, sequential_id) DO NOTHING
                    RETURNING instrument_id
                    """,
                    (
                        trade.instrument_id,
                        trade.sequential_id,
                        trade.trade_timestamp_at,
                        trade.trade_price,
                        trade.trade_volume,
                        trade.trade_amount,
                        trade.ask_bid,
                        trade.collected_at,
                        run_id,
                        market_id,
                        trade.trade_timestamp_at,
                        trade.collected_at,
                        trade.collected_at,
                        manifest_id,
                    ),
                ).fetchone()
                conn.execute(
                    """
                    UPDATE trade_events
                    SET market_id = COALESCE(market_id, %s),
                        occurred_at = COALESCE(occurred_at, %s),
                        received_at = COALESCE(received_at, %s),
                        stored_at = COALESCE(stored_at, clock_timestamp()),
                        knowledge_at = COALESCE(knowledge_at, %s),
                        fetch_manifest_id = COALESCE(fetch_manifest_id, %s)
                    WHERE instrument_id = %s AND source = 'UPBIT' AND sequential_id = %s
                    """,
                    (
                        market_id,
                        trade.trade_timestamp_at,
                        trade.collected_at,
                        trade.collected_at,
                        manifest_id,
                        trade.instrument_id,
                        trade.sequential_id,
                    ),
                )
                if row is not None:
                    inserted_by_instrument[trade.instrument_id] = (
                        inserted_by_instrument.get(trade.instrument_id, 0) + 1
                    )
                exists = conn.execute(
                    """
                    SELECT 1 FROM trade_events
                    WHERE instrument_id = %s AND source = 'UPBIT' AND sequential_id = %s
                    """,
                    (trade.instrument_id, trade.sequential_id),
                ).fetchone()
                if exists is not None and target_spec_id is not None:
                    self._replace_coverage_with_observed(
                        conn,
                        target_spec_id=target_spec_id,
                        range_start_at=trade.trade_timestamp_at,
                        range_end_at=trade.trade_timestamp_at + timedelta(microseconds=1),
                        manifest_id=manifest_id,
                        natural_key={"sequentialId": trade.sequential_id},
                    )
            for instrument_id, rows_written in inserted_by_instrument.items():
                conn.execute(
                    """
                    INSERT INTO target_collection_results (
                      collection_run_id, instrument_id, data_type, status,
                      latency_ms, rows_written
                    )
                    VALUES (%s, %s, 'trade_event', 'succeeded', 0, %s)
                    """,
                    (run_id, instrument_id, rows_written),
                )
            finished_at = now_kst()
            conn.execute(
                "UPDATE collection_runs SET status = 'succeeded', finished_at = %s WHERE id = %s",
                (finished_at, run_id),
            )
        return sum(inserted_by_instrument.values())

    def record_realtime_source_frames(self, frames: list[RealtimeSourceFrame]) -> int:
        if not frames:
            return 0
        started_at = now_kst()
        summaries: list[OrderbookSummary] = []
        inserted_receipts = 0
        with self._connect() as conn:
            for frame in frames:
                receipt = frame.receipt
                market_row = _expect_row(
                    conn.execute(
                        """
                        SELECT id
                        FROM markets
                        WHERE legacy_instrument_id = %s
                        """,
                        (receipt.instrument_id,),
                    ).fetchone()
                )
                market_id = int(market_row["id"])
                receipt_row = conn.execute(
                    """
                    INSERT INTO source_receipts (
                      data_type, market_id, instrument_id, connection_id,
                      frame_sequence, occurred_at, received_at, payload_checksum,
                      raw_payload, fetch_manifest_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (connection_id, frame_sequence) DO NOTHING
                    RETURNING id
                    """,
                    (
                        receipt.data_type,
                        market_id,
                        receipt.instrument_id,
                        receipt.connection_id,
                        receipt.frame_sequence,
                        receipt.occurred_at,
                        receipt.received_at,
                        receipt.payload_checksum,
                        Jsonb(receipt.raw_payload),
                        receipt.fetch_manifest_id,
                    ),
                ).fetchone()
                if receipt_row is None:
                    existing_receipt = _expect_row(
                        conn.execute(
                            """
                            SELECT data_type, instrument_id, payload_checksum, raw_payload
                            FROM source_receipts
                            WHERE connection_id = %s AND frame_sequence = %s
                            """,
                            (receipt.connection_id, receipt.frame_sequence),
                        ).fetchone()
                    )
                    if (
                        existing_receipt["data_type"] != receipt.data_type
                        or int(existing_receipt["instrument_id"]) != receipt.instrument_id
                        or existing_receipt["payload_checksum"] != receipt.payload_checksum
                        or existing_receipt["raw_payload"] != receipt.raw_payload
                    ):
                        raise ValueError(
                            "source receipt connection_id/frame_sequence payload 불일치"
                        )
                    continue
                inserted_receipts += 1
                if frame.summary is not None:
                    summaries.append(frame.summary)
                snapshot = frame.snapshot
                if snapshot is None:
                    continue
                snapshot_row = conn.execute(
                    """
                    INSERT INTO orderbook_snapshots (
                      market_id, instrument_id, source, occurred_at, received_at,
                      stored_at, knowledge_at, total_ask_size, total_bid_size,
                      level_count, level, stream_type, payload_checksum, fetch_manifest_id
                    )
                    VALUES (
                      %s, %s, %s, %s, %s, clock_timestamp(), %s, %s, %s, %s,
                      %s, %s, %s, %s
                    )
                    ON CONFLICT (
                      instrument_id, source, occurred_at, payload_checksum
                    ) DO NOTHING
                    RETURNING id
                    """,
                    (
                        market_id,
                        snapshot.instrument_id,
                        snapshot.source,
                        snapshot.occurred_at,
                        snapshot.received_at,
                        snapshot.received_at,
                        snapshot.total_ask_size,
                        snapshot.total_bid_size,
                        snapshot.level_count,
                        snapshot.level,
                        snapshot.stream_type,
                        snapshot.payload_checksum,
                        snapshot.fetch_manifest_id,
                    ),
                ).fetchone()
                if snapshot_row is None:
                    continue
                snapshot_id = int(snapshot_row["id"])
                for level in snapshot.levels:
                    conn.execute(
                        """
                        INSERT INTO orderbook_snapshot_levels (
                          snapshot_id, level_index, ask_price, ask_size,
                          bid_price, bid_size
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            snapshot_id,
                            level.level_index,
                            level.ask_price,
                            level.ask_size,
                            level.bid_price,
                            level.bid_size,
                        ),
                    )
            if summaries:
                run_id = int(
                    _expect_row(
                        conn.execute(
                            """
                            INSERT INTO collection_runs (
                              run_type, data_type, status, trigger_type, started_at
                            )
                            VALUES (
                              'incremental', 'orderbook_summary', 'running', 'schedule', %s
                            )
                            RETURNING id
                            """,
                            (started_at,),
                        ).fetchone()
                    )["id"]
                )
                counts = self._upsert_orderbooks(conn, run_id, summaries, requested_at=started_at)
                for instrument_id, rows_written in counts.items():
                    conn.execute(
                        """
                        INSERT INTO target_collection_results (
                          collection_run_id, instrument_id, data_type, status,
                          latency_ms, rows_written
                        )
                        VALUES (%s, %s, 'orderbook_summary', 'succeeded', 0, %s)
                        """,
                        (run_id, instrument_id, rows_written),
                    )
                conn.execute(
                    """
                    UPDATE collection_runs
                    SET status = 'succeeded', finished_at = clock_timestamp()
                    WHERE id = %s
                    """,
                    (run_id,),
                )
        return inserted_receipts

    def purge_expired_source_evidence(self, *, as_of: datetime | None = None) -> tuple[int, int]:
        retention_as_of = as_of or now_kst()
        with self._connect() as conn:
            receipt_result = conn.execute(
                """
                WITH effective_retention AS (
                  SELECT
                    market_id,
                    data_type,
                    CASE
                      WHEN bool_or(retention_days IS NULL) THEN NULL
                      ELSE max(retention_days)
                    END AS retention_days
                  FROM collection_target_specs
                  GROUP BY market_id, data_type
                )
                DELETE FROM source_receipts receipt
                USING effective_retention retention
                WHERE retention.market_id = receipt.market_id
                  AND retention.data_type = receipt.data_type
                  AND receipt.data_type = 'orderbook_snapshot'
                  AND retention.retention_days IS NOT NULL
                  AND receipt.occurred_at
                      < %s - make_interval(days => retention.retention_days)
                """,
                (retention_as_of,),
            )
            snapshot_result = conn.execute(
                """
                WITH effective_retention AS (
                  SELECT
                    market_id,
                    CASE
                      WHEN bool_or(retention_days IS NULL) THEN NULL
                      ELSE max(retention_days)
                    END AS retention_days
                  FROM collection_target_specs
                  WHERE data_type = 'orderbook_snapshot'
                  GROUP BY market_id
                )
                DELETE FROM orderbook_snapshots snapshot
                USING effective_retention retention
                WHERE retention.market_id = snapshot.market_id
                  AND retention.retention_days IS NOT NULL
                  AND snapshot.occurred_at
                      < %s - make_interval(days => retention.retention_days)
                """,
                (retention_as_of,),
            )
        return (receipt_result.rowcount, snapshot_result.rowcount)

    def dashboard_summary(self) -> DashboardSummary:
        targets = self.collection_dashboard_targets()
        coverage = self._dashboard_coverage_from_targets(targets)
        normal_targets = sum(
            1 for target in targets if target.overall_status == "latest_collecting"
        )
        warning_targets = sum(1 for target in targets if target.overall_status == "warning")
        incident_targets = sum(1 for target in targets if target.overall_status == "incident")
        delayed_targets = sum(1 for status in coverage if status.status != "normal")
        missing_ranges_open = sum(
            status.missing_segment_count
            for status in coverage
            if status.data_type == "source_candle"
        )
        failed_runs_24h = self._failed_runs_24h()
        recent_runs = self._recent_run_count()
        failure_rate_24h = (
            Decimal(failed_runs_24h) / Decimal(recent_runs) if recent_runs > 0 else Decimal("0")
        )
        storage_bytes_today = self._storage_bytes_today_estimate()
        storage_rows_today = self._storage_rows_today()
        alerts = self.notification_events()
        if any(
            alert.severity in {"error", "critical"} and alert.status == "open" for alert in alerts
        ):
            summary_status: Literal["normal", "warning", "incident"] = "incident"
        elif delayed_targets > 0 or failed_runs_24h > 0:
            summary_status = "warning"
        else:
            summary_status = "normal"
        return DashboardSummary(
            status=summary_status,
            active_targets=len(targets),
            active_target_limit=50,
            normal_targets=normal_targets,
            warning_targets=warning_targets,
            incident_targets=incident_targets,
            failed_runs_24h=failed_runs_24h,
            failure_rate_24h=failure_rate_24h,
            delayed_targets=delayed_targets,
            missing_ranges_open=missing_ranges_open,
            storage_bytes_today=storage_bytes_today,
            storage_bytes_today_display=_format_storage_bytes(storage_bytes_today),
            storage_rows_today=storage_rows_today,
            realtime_rows_last_minute=self._collection_rows_last_minute("incremental"),
            backfill_rows_last_minute=self._collection_rows_last_minute("backfill"),
            recent_request_count=self._recent_collection_result_count(),
            coverage=coverage,
            targets=targets,
            alerts=alerts,
            health_checks=self._health_checks(coverage, alerts),
            collection_activity=self.dashboard_collection_activity(),
            realtime_collection_heatmap=self.dashboard_realtime_heatmap(),
            storage_breakdown=self._storage_breakdown_today(storage_bytes_today),
            operations_trend=self._operations_trend(
                coverage, storage_bytes_today, warning_targets, incident_targets
            ),
            missing_range_top=self._missing_range_top(targets),
            audit_log_summary=self.dashboard_audit_log_summary(),
            worker_status=self.dashboard_worker_status(),
            refreshed_at=now_kst(),
        )

    def dashboard_coverage(self) -> list[CoverageStatus]:
        return self._dashboard_coverage_from_targets(self.collection_dashboard_targets())

    def dashboard_collection_activity(self) -> list[CollectionActivityBucket]:
        return self._collection_activity_buckets()

    def dashboard_realtime_heatmap(self) -> list[RealtimeCollectionHeatmapRow]:
        return self._realtime_collection_heatmap()

    def dashboard_storage_breakdown(self) -> list[StorageBreakdownItem]:
        return self._storage_breakdown_today(self._storage_bytes_today_estimate())

    def dashboard_operations_trend(self) -> list[OperationsTrendPoint]:
        targets = self.collection_dashboard_targets()
        coverage = self._dashboard_coverage_from_targets(targets)
        warning_targets = sum(1 for target in targets if target.overall_status == "warning")
        incident_targets = sum(1 for target in targets if target.overall_status == "incident")
        return self._operations_trend(
            coverage,
            self._storage_bytes_today_estimate(),
            warning_targets,
            incident_targets,
        )

    def dashboard_missing_ranges(self) -> list[MissingRangeSummary]:
        return self._missing_range_top(self.collection_dashboard_targets())

    def dashboard_audit_log_summary(self) -> AuditLogSummary:
        return self._audit_log_summary()

    def dashboard_worker_status(self) -> CollectionWorkerStatusSummary:
        return CollectionWorkerStatusSummary(
            realtime=self._realtime_worker_status(),
            backfill=self._backfill_worker_status(),
        )

    def _dashboard_coverage_from_targets(
        self, targets: list[CollectionDashboardTarget]
    ) -> list[CoverageStatus]:
        return [
            CoverageStatus(
                instrument_id=target.instrument.id,
                data_type=status.data_type,
                status=status.status,
                progress_percent=status.progress_percent,
                last_successful_at=status.last_successful_at,
                missing_segment_count=status.missing_segment_count,
            )
            for target in targets
            for status in target.data_statuses
        ]

    def collection_dashboard_targets(
        self, include_segments: bool = False
    ) -> list[CollectionDashboardTarget]:
        targets: list[CollectionDashboardTarget] = []
        active_targets = self.list_active_targets()
        instrument_ids = [instrument.id for instrument in active_targets]
        latest_tickers = self._latest_tickers_by_instrument(instrument_ids)
        latest_orderbooks = self._latest_orderbooks_by_instrument(instrument_ids)
        source_candle_counts = self._table_counts_by_instrument(
            "source_candles",
            instrument_ids,
        )
        storage_bytes_by_instrument, storage_rows_by_instrument = (
            self._instrument_storage_totals_by_instrument(
                instrument_ids,
                source_candle_counts=source_candle_counts,
            )
        )
        source_candle_ranges = self._source_candle_ranges_by_instrument(instrument_ids)
        plans_by_instrument = self._collection_plans_by_instrument(instrument_ids)
        for instrument in active_targets:
            ticker = latest_tickers.get(instrument.id)
            plan = plans_by_instrument[instrument.id]
            coverage = sorted(
                self._dashboard_target_coverage(
                    instrument.id,
                    plan,
                    source_candle_counts.get(instrument.id, 0),
                    source_candle_ranges.get(instrument.id, (None, None)),
                    ticker,
                    latest_orderbooks.get(instrument.id),
                ),
                key=lambda item: {
                    "source_candle": 0,
                    "ticker_snapshot": 1,
                    "orderbook_summary": 2,
                }[item.data_type],
            )
            data_statuses = [
                self._collection_data_status(item, source_candle_counts) for item in coverage
            ]
            candle_status = next(
                item for item in data_statuses if item.data_type == "source_candle"
            )
            overall_status: Literal["latest_collecting", "warning", "incident"]
            if any(item.status == "incident" for item in data_statuses):
                overall_status = "incident"
            elif all(item.status == "normal" for item in data_statuses):
                overall_status = "latest_collecting"
            else:
                overall_status = "warning"
            targets.append(
                CollectionDashboardTarget(
                    instrument=instrument,
                    overall_status=overall_status,
                    overall_status_label="최신수집중"
                    if overall_status == "latest_collecting"
                    else "장애"
                    if overall_status == "incident"
                    else "주의",
                    plan=plan,
                    data_statuses=data_statuses,
                    coverage_segments=[
                        segment
                        for data_status in data_statuses
                        for segment in self._coverage_segments_for(
                            instrument.id, data_status.data_type
                        )
                    ]
                    if include_segments
                    else [],
                    change_rate=ticker.change_rate if ticker else Decimal("0"),
                    acc_trade_price_24h_display=(
                        f"₩{int(ticker.acc_trade_price_24h):,}" if ticker else "₩0"
                    ),
                    ticker_collected_at=ticker.collected_at if ticker else now_kst(),
                    coverage_percent=candle_status.progress_percent,
                    storage_row_count=storage_rows_by_instrument.get(instrument.id, 0),
                    storage_bytes_display=_format_storage_bytes(
                        storage_bytes_by_instrument.get(instrument.id, 0)
                    ),
                    collected_start_at=source_candle_ranges.get(instrument.id, (None, None))[0],
                    collected_end_at=source_candle_ranges.get(instrument.id, (None, None))[1],
                )
            )
        return targets

    def _source_candle_ranges_by_instrument(
        self, instrument_ids: list[int]
    ) -> dict[int, tuple[datetime | None, datetime | None]]:
        if not instrument_ids:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT instrument_id,
                       min(candle_start_at) AS collected_start_at,
                       max(candle_start_at) AS collected_end_at
                FROM source_candles
                WHERE candle_unit = '1m'
                  AND instrument_id = ANY(%s)
                GROUP BY instrument_id
                """,
                (instrument_ids,),
            ).fetchall()
        return {
            int(row["instrument_id"]): (
                cast(datetime | None, row["collected_start_at"]),
                cast(datetime | None, row["collected_end_at"]),
            )
            for row in rows
        }

    def coverage_segments_for(self, instrument_id: int) -> list[CoverageSegment]:
        return [
            segment
            for status in self.coverage_for(instrument_id)
            for segment in self._coverage_segments_for(instrument_id, status.data_type)
        ]

    def market_list(self) -> list[MarketListRow]:
        rows: list[MarketListRow] = []
        _, candidate_entries = self.list_candidate_universe()
        instrument_ids = [entry.instrument.id for entry in candidate_entries]
        latest_tickers = self._latest_tickers_by_instrument(instrument_ids)
        latest_orderbooks = self._latest_orderbooks_by_instrument(instrument_ids)
        source_candle_counts = self._table_counts_by_instrument(
            "source_candles",
            instrument_ids,
        )
        source_candle_ranges = self._source_candle_ranges_by_instrument(instrument_ids)
        storage_bytes_by_instrument, _ = self._instrument_storage_totals_by_instrument(
            instrument_ids
        )
        collection_plans = self._collection_plans_by_instrument(instrument_ids)
        current_at = now_kst()
        for entry in candidate_entries:
            instrument = entry.instrument
            ticker = latest_tickers.get(instrument.id)
            orderbook = latest_orderbooks.get(instrument.id)
            storage_bytes = storage_bytes_by_instrument.get(instrument.id, 0)
            one_minute_candle_count = source_candle_counts.get(instrument.id, 0)
            stored_candle_start_at, candle_coverage_end_at = source_candle_ranges.get(
                instrument.id,
                (None, None),
            )
            plan = collection_plans[instrument.id]
            candle_coverage = self._source_candle_coverage_status_from_summary(
                instrument.id,
                plan,
                one_minute_candle_count,
                (stored_candle_start_at, candle_coverage_end_at),
            )
            acc_trade_price_24h = (
                ticker.acc_trade_price_24h if ticker else entry.acc_trade_price_24h
            )
            rows.append(
                MarketListRow(
                    instrument=instrument,
                    asset_type="coin",
                    is_favorite=entry.selected,
                    favorite_order=entry.favorite_order,
                    trade_price=ticker.trade_price if ticker else None,
                    price_currency=instrument.quote_currency,
                    acc_trade_price_24h=acc_trade_price_24h,
                    acc_trade_price_24h_display=f"₩{int(acc_trade_price_24h):,}",
                    trade_amount_currency=instrument.quote_currency,
                    change_rate=ticker.change_rate if ticker else None,
                    change_rate_basis="전일 종가 대비",
                    ticker_collected_at=ticker.collected_at if ticker else None,
                    orderbook_collected_at=orderbook.collected_at if orderbook else None,
                    quality_status=self._quality_status_from_coverage([candle_coverage]),
                    coverage_percent=candle_coverage.progress_percent,
                    candle_coverage_start_at=plan.range_start_at,
                    candle_coverage_end_at=candle_coverage_end_at,
                    candle_coverage_current_at=current_at,
                    one_minute_candle_count=one_minute_candle_count,
                    storage_bytes=storage_bytes,
                    storage_row_count=one_minute_candle_count,
                    storage_bytes_display=_format_storage_bytes(storage_bytes),
                )
            )
        return sorted(
            rows,
            key=lambda row: (
                0 if row.is_favorite else 1,
                row.favorite_order if row.favorite_order is not None else row.instrument.id,
                row.instrument.id,
            ),
        )

    def get_instrument(self, instrument_id: int) -> Instrument | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM instruments WHERE id = %s", (instrument_id,)
            ).fetchone()
        return _instrument(row) if row else None

    def latest_ticker(self, instrument_id: int) -> TickerSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM ticker_snapshots
                WHERE instrument_id = %s
                ORDER BY bucket_at DESC
                LIMIT 1
                """,
                (instrument_id,),
            ).fetchone()
        return _ticker(row) if row else None

    def latest_orderbook(self, instrument_id: int) -> OrderbookSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM orderbook_summaries
                WHERE instrument_id = %s
                ORDER BY bucket_at DESC
                LIMIT 1
                """,
                (instrument_id,),
            ).fetchone()
        return _orderbook(row) if row else None

    def _latest_tickers_by_instrument(self, instrument_ids: list[int]) -> dict[int, TickerSnapshot]:
        if not instrument_ids:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT latest.*
                FROM unnest(%s::bigint[]) AS target(instrument_id)
                CROSS JOIN LATERAL (
                  SELECT *
                  FROM ticker_snapshots
                  WHERE instrument_id = target.instrument_id
                  ORDER BY bucket_at DESC
                  LIMIT 1
                ) latest
                """,
                (instrument_ids,),
            ).fetchall()
        return {int(row["instrument_id"]): _ticker(row) for row in rows}

    def _latest_orderbooks_by_instrument(
        self, instrument_ids: list[int]
    ) -> dict[int, OrderbookSummary]:
        if not instrument_ids:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT latest.*
                FROM unnest(%s::bigint[]) AS target(instrument_id)
                CROSS JOIN LATERAL (
                  SELECT *
                  FROM orderbook_summaries
                  WHERE instrument_id = target.instrument_id
                  ORDER BY bucket_at DESC
                  LIMIT 1
                ) latest
                """,
                (instrument_ids,),
            ).fetchall()
        return {int(row["instrument_id"]): _orderbook(row) for row in rows}

    def coverage_for(self, instrument_id: int) -> list[CoverageStatus]:
        latest_ticker = self.latest_ticker(instrument_id)
        latest_orderbook = self.latest_orderbook(instrument_id)
        return self._coverage_for_with_latest(instrument_id, latest_ticker, latest_orderbook)

    def _coverage_for_with_latest(
        self,
        instrument_id: int,
        latest_ticker: TickerSnapshot | None,
        latest_orderbook: OrderbookSummary | None,
    ) -> list[CoverageStatus]:
        return [
            self._source_candle_coverage_status(instrument_id),
            self._freshness_coverage_status(
                instrument_id,
                "ticker_snapshot",
                latest_ticker.collected_at if latest_ticker else None,
            ),
            self._freshness_coverage_status(
                instrument_id,
                "orderbook_summary",
                latest_orderbook.collected_at if latest_orderbook else None,
            ),
        ]

    def _dashboard_target_coverage(
        self,
        instrument_id: int,
        plan: CollectionPlan,
        source_candle_count: int,
        source_candle_range: tuple[datetime | None, datetime | None],
        latest_ticker: TickerSnapshot | None,
        latest_orderbook: OrderbookSummary | None,
    ) -> list[CoverageStatus]:
        return [
            self._source_candle_coverage_status_from_summary(
                instrument_id, plan, source_candle_count, source_candle_range
            ),
            self._freshness_coverage_status(
                instrument_id,
                "ticker_snapshot",
                latest_ticker.collected_at if latest_ticker else None,
            ),
            self._freshness_coverage_status(
                instrument_id,
                "orderbook_summary",
                latest_orderbook.collected_at if latest_orderbook else None,
            ),
        ]

    def candles(
        self, instrument_id: int, unit: str, start_at: datetime, end_at: datetime
    ) -> list[CandleView]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM source_candles
                WHERE instrument_id = %s AND candle_start_at >= %s AND candle_start_at <= %s
                ORDER BY candle_start_at
                """,
                (instrument_id, start_at, end_at),
            ).fetchall()
        source = [_candle(row) for row in rows]
        if unit == "1m":
            return [_candle_view(item) for item in source if item.candle_unit == unit]
        if unit in AGGREGATION_UNITS:
            rollups = self.candle_rollups(instrument_id, unit, start_at, end_at)
            if rollups:
                return rollups
        return _derive_candles(unit, source)

    def materialize_candle_rollups(self, instrument_id: int, unit: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM source_candles WHERE instrument_id = %s
                ORDER BY candle_start_at
                """,
                (instrument_id,),
            )
            source: list[SourceCandle] = []
            while rows := cursor.fetchmany(SOURCE_FETCH_BATCH_SIZE):
                source.extend(_candle(row) for row in rows)
            rollups = aggregate_candles(unit, source)
            for item in rollups:
                conn.execute(
                    """
                    INSERT INTO candle_rollups (
                      instrument_id, candle_unit, candle_start_at, open_price, high_price,
                      low_price,
                      close_price, trade_volume, trade_amount, completeness, materialized_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (instrument_id, candle_unit, candle_start_at) DO UPDATE SET
                      open_price = excluded.open_price, high_price = excluded.high_price,
                      low_price = excluded.low_price, close_price = excluded.close_price,
                      trade_volume = excluded.trade_volume, trade_amount = excluded.trade_amount,
                      completeness = excluded.completeness, materialized_at = now()
                    """,
                    (
                        instrument_id,
                        unit,
                        item.started_at,
                        item.open,
                        item.high,
                        item.low,
                        item.close,
                        item.volume,
                        item.trade_amount,
                        item.completeness,
                    ),
                )
        return len(rollups)

    def candle_rollups(
        self, instrument_id: int, unit: str, start_at: datetime, end_at: datetime
    ) -> list[CandleView]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM candle_rollups
                WHERE instrument_id = %s AND candle_unit = %s
                  AND candle_start_at >= %s AND candle_start_at < %s
                ORDER BY candle_start_at
                """,
                (instrument_id, unit, start_at, end_at),
            ).fetchall()
        return [_rollup_candle(row) for row in rows]

    def schedule_candle_aggregation(self) -> CandleAggregationJob | None:
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM candle_aggregation_jobs
                WHERE status IN ('pending', 'running') ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            if existing is not None:
                return self._candle_aggregation_job(int(existing["id"]))
            targets: list[tuple[int, str]] = []
            for instrument in self.list_active_targets():
                source_row = _expect_row(
                    conn.execute(
                        "SELECT MAX(candle_start_at) AS candle_start_at "
                        "FROM source_candles WHERE instrument_id = %s",
                        (instrument.id,),
                    ).fetchone()
                )
                source_latest = source_row["candle_start_at"]
                if source_latest is None:
                    continue
                for unit in AGGREGATION_UNITS:
                    rollup_row = _expect_row(
                        conn.execute(
                            """
                        SELECT MAX(candle_start_at) AS candle_start_at FROM candle_rollups
                        WHERE instrument_id = %s AND candle_unit = %s
                        """,
                            (instrument.id, unit),
                        ).fetchone()
                    )
                    rollup_latest = rollup_row["candle_start_at"]
                    if rollup_latest is None or rollup_latest < rollup_bucket_start(
                        unit, source_latest
                    ):
                        targets.append((instrument.id, unit))
            if not targets:
                return None
            job_id = int(
                _expect_row(
                    conn.execute(
                        "INSERT INTO candle_aggregation_jobs (status) "
                        "VALUES ('pending') RETURNING id"
                    ).fetchone()
                )["id"]
            )
            for instrument_id, unit in targets:
                conn.execute(
                    """
                    INSERT INTO candle_aggregation_job_targets (
                      job_id, instrument_id, candle_unit, status
                    )
                    VALUES (%s, %s, %s, 'pending')
                    """,
                    (job_id, instrument_id, unit),
                )
        return self._candle_aggregation_job(job_id)

    def claim_next_candle_aggregation_job(self) -> CandleAggregationJob | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM candle_aggregation_jobs WHERE status = 'pending'
                ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            job_id = int(row["id"])
            conn.execute(
                "UPDATE candle_aggregation_jobs SET status = 'running', "
                "started_at = now() WHERE id = %s",
                (job_id,),
            )
        return self._candle_aggregation_job(job_id)

    def candle_aggregation_job_targets(self, job_id: int) -> list[CandleAggregationJobTarget]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM candle_aggregation_job_targets
                WHERE job_id = %s ORDER BY instrument_id, candle_unit
                """,
                (job_id,),
            ).fetchall()
        return [_candle_aggregation_target(row) for row in rows]

    def mark_candle_aggregation_target(
        self, job_id: int, instrument_id: int, unit: str, status: str, rows_written: int
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE candle_aggregation_job_targets
                SET status = %s, rows_written = %s, updated_at = now()
                WHERE job_id = %s AND instrument_id = %s AND candle_unit = %s
                """,
                (status, rows_written, job_id, instrument_id, unit),
            )
            remaining_row = _expect_row(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count FROM candle_aggregation_job_targets
                    WHERE job_id = %s AND status NOT IN ('succeeded', 'failed')
                    """,
                    (job_id,),
                ).fetchone()
            )
            remaining = int(remaining_row["count"])
            if remaining == 0:
                failed_row = _expect_row(
                    conn.execute(
                        """
                        SELECT COUNT(*) AS count FROM candle_aggregation_job_targets
                        WHERE job_id = %s AND status = 'failed'
                        """,
                        (job_id,),
                    ).fetchone()
                )
                failed = int(failed_row["count"])
                conn.execute(
                    """
                    UPDATE candle_aggregation_jobs SET status = %s, finished_at = now()
                    WHERE id = %s
                    """,
                    ("failed" if failed else "succeeded", job_id),
                )

    def latest_candle_aggregation_job(self) -> CandleAggregationJob | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM candle_aggregation_jobs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return self._candle_aggregation_job(int(row["id"])) if row else None

    def _candle_aggregation_job(self, job_id: int) -> CandleAggregationJob:
        with self._connect() as conn:
            row = _expect_row(
                conn.execute(
                    """
                    SELECT j.id, j.status, j.created_at, COUNT(t.candle_unit) AS total,
                      COUNT(*) FILTER (WHERE t.status = 'succeeded') AS completed,
                      COUNT(*) FILTER (WHERE t.status = 'running') AS running,
                      COUNT(*) FILTER (WHERE t.status = 'pending') AS pending,
                      COUNT(*) FILTER (WHERE t.status = 'failed') AS failed
                    FROM candle_aggregation_jobs j
                    LEFT JOIN candle_aggregation_job_targets t ON t.job_id = j.id
                    WHERE j.id = %s GROUP BY j.id
                    """,
                    (job_id,),
                ).fetchone()
            )
        total = int(row["total"])
        completed = int(row["completed"])
        return CandleAggregationJob(
            id=int(row["id"]),
            status=cast(Literal["pending", "running", "succeeded", "failed"], row["status"]),
            progress_percent=Decimal(completed * 100) / Decimal(total) if total else Decimal("0"),
            total_target_count=total,
            completed_target_count=completed,
            running_target_count=int(row["running"]),
            pending_target_count=int(row["pending"]),
            failed_target_count=int(row["failed"]),
            created_at=cast(datetime, row["created_at"]),
        )

    def ticker_snapshots(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> list[TickerSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM ticker_snapshots
                WHERE instrument_id = %s AND bucket_at >= %s AND bucket_at <= %s
                ORDER BY bucket_at
                """,
                (instrument_id, start_at, end_at),
            ).fetchall()
        return [_ticker(row) for row in rows]

    def orderbook_summaries(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> list[OrderbookSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM orderbook_summaries
                WHERE instrument_id = %s AND bucket_at >= %s AND bucket_at <= %s
                ORDER BY bucket_at
                """,
                (instrument_id, start_at, end_at),
            ).fetchall()
        return [_orderbook(row) for row in rows]

    def trade_summary(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> TradeSummary:
        with self._connect() as conn:
            row = cast(
                Row,
                conn.execute(
                    """
                    SELECT COUNT(*) AS trade_count,
                           COALESCE(SUM(CASE WHEN ask_bid = 'BID' THEN trade_volume ELSE 0 END), 0)
                             AS buy_volume,
                           COALESCE(SUM(CASE WHEN ask_bid = 'ASK' THEN trade_volume ELSE 0 END), 0)
                             AS sell_volume,
                           MAX(trade_timestamp_at) AS last_trade_at
                    FROM trade_events
                    WHERE instrument_id = %s
                      AND trade_timestamp_at >= %s
                      AND trade_timestamp_at <= %s
                    """,
                    (instrument_id, start_at, end_at),
                ).fetchone(),
            )
        return TradeSummary(
            trade_count=int(row["trade_count"]),
            buy_volume=Decimal(str(row["buy_volume"])),
            sell_volume=Decimal(str(row["sell_volume"])),
            last_trade_at=cast(datetime | None, row["last_trade_at"]),
        )

    def collection_runs(self, limit: int) -> list[CollectionRun]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM collection_runs ORDER BY started_at DESC, id DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [_collection_run(row) for row in rows]

    def record_collection_worker_heartbeat(
        self,
        worker_type: CollectionWorkerType,
        status: CollectionWorkerHeartbeatStatus,
        error_message: str | None = None,
    ) -> None:
        if worker_type not in {
            "realtime_collection",
            "backfill_collection",
            "candle_aggregation",
        }:
            raise ValueError("지원하지 않는 수집 워커 유형이다.")
        if status not in {"running", "gated", "failed"}:
            raise ValueError("지원하지 않는 수집 워커 상태다.")
        timestamp = now_kst()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO collection_worker_heartbeats (
                  worker_type, status, last_heartbeat_at, last_started_at,
                  last_successful_at, last_error_at, last_error_message
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (worker_type) DO UPDATE SET
                  status = excluded.status,
                  last_heartbeat_at = excluded.last_heartbeat_at,
                  last_started_at = CASE
                    WHEN excluded.status = 'running'
                    THEN excluded.last_started_at
                    ELSE collection_worker_heartbeats.last_started_at
                  END,
                  last_successful_at = CASE
                    WHEN excluded.status = 'running'
                    THEN excluded.last_successful_at
                    ELSE collection_worker_heartbeats.last_successful_at
                  END,
                  last_error_at = CASE
                    WHEN excluded.status = 'failed'
                    THEN excluded.last_error_at
                    ELSE collection_worker_heartbeats.last_error_at
                  END,
                  last_error_message = CASE
                    WHEN excluded.status IN ('failed', 'gated')
                    THEN excluded.last_error_message
                    ELSE collection_worker_heartbeats.last_error_message
                  END,
                  updated_at = now()
                """,
                (
                    worker_type,
                    status,
                    timestamp,
                    timestamp if status == "running" else None,
                    timestamp if status == "running" else None,
                    timestamp if status == "failed" else None,
                    error_message,
                ),
            )

    def record_collection_run_failure(
        self,
        run_type: str,
        data_type: str,
        started_at: datetime,
        error_code: str,
        error_message: str,
    ) -> CollectionRun:
        finished_at = now_kst()
        with self._connect() as conn:
            row = _expect_row(
                conn.execute(
                    """
                    INSERT INTO collection_runs (
                      run_type, data_type, status, trigger_type, started_at,
                      finished_at, error_code, error_message
                    )
                    VALUES (%s, %s, 'failed', 'system', %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        run_type,
                        data_type,
                        started_at,
                        finished_at,
                        error_code,
                        error_message,
                    ),
                ).fetchone()
            )
        return _collection_run(row)

    def create_backfill_plan(
        self,
        data_type: str,
        target_start_at: datetime,
        target_end_at: datetime,
        instrument_ids: list[int],
    ) -> BackfillPlan:
        if data_type != "source_candle":
            raise ValueError("M1 백필 계획은 source_candle만 지원한다.")
        if target_start_at >= target_end_at:
            raise ValueError("백필 종료 시각은 시작 시각보다 뒤여야 한다.")
        duration_minutes = max(1, int((target_end_at - target_start_at).total_seconds() // 60))
        estimated_request_count = len(instrument_ids) * max(1, duration_minutes // 200 + 1)
        plan = BackfillPlan(
            plan_id=str(uuid.uuid4()),
            data_type="source_candle",
            target_start_at=target_start_at,
            target_end_at=target_end_at,
            estimated_request_count=estimated_request_count,
            estimated_row_count=len(instrument_ids) * duration_minutes,
            estimated_storage_bytes=len(instrument_ids) * duration_minutes * 256,
            targets=instrument_ids,
        )
        with self._connect() as conn:
            for instrument_id in sorted(set(instrument_ids)):
                conn.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (INSTRUMENT_ADVISORY_LOCK_NAMESPACE, instrument_id),
                )
                conflict = conn.execute(
                    """
                    SELECT 1
                    FROM backfill_jobs job
                    WHERE job.status IN (
                      'planned', 'pending', 'leased', 'running', 'retry_wait', 'paused'
                    )
                      AND (
                        job.plan -> 'targets' @> %s
                        OR EXISTS (
                          SELECT 1 FROM backfill_job_targets target
                          WHERE target.backfill_job_id = job.id
                            AND target.instrument_id = %s
                        )
                      )
                    LIMIT 1
                    """,
                    (Jsonb([instrument_id]), instrument_id),
                ).fetchone()
                if conflict is not None:
                    raise ValueError("같은 상품에 이미 활성 백필 작업이 있다.")
            conn.execute(
                """
                INSERT INTO backfill_jobs (
                  status, data_type, plan, target_start_at, target_end_at,
                  estimated_request_count, estimated_row_count, estimated_storage_bytes,
                  restart_mode, created_by, idempotency_key
                )
                VALUES (
                  'planned', %s, %s, %s, %s, %s, %s, %s,
                  'safe_restart', 'local_user', %s
                )
                """,
                (
                    plan.data_type,
                    Jsonb({"planId": plan.plan_id, "targets": plan.targets}),
                    plan.target_start_at,
                    plan.target_end_at,
                    plan.estimated_request_count,
                    plan.estimated_row_count,
                    plan.estimated_storage_bytes,
                    f"manual:{plan.plan_id}",
                ),
            )
        return plan

    def approve_backfill_job(self, plan_id: str) -> BackfillJob:
        with self._connect() as conn:
            planned = conn.execute(
                """
                SELECT *
                FROM backfill_jobs
                WHERE status = 'planned' AND plan ->> 'planId' = %s
                ORDER BY created_at DESC
                LIMIT 1
                FOR UPDATE
                """,
                (plan_id,),
            ).fetchone()
            if planned is None:
                raise ValueError("존재하지 않는 백필 계획이다.")
            targets = [
                int(item) for item in cast(dict[str, Any], planned["plan"]).get("targets", [])
            ]
            for instrument_id in sorted(set(targets)):
                conn.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (INSTRUMENT_ADVISORY_LOCK_NAMESPACE, instrument_id),
                )
                conflict = conn.execute(
                    """
                    SELECT 1
                    FROM backfill_jobs job
                    WHERE job.id <> %s
                      AND job.status IN (
                        'planned', 'pending', 'leased', 'running', 'retry_wait', 'paused'
                      )
                      AND (
                        job.plan -> 'targets' @> %s
                        OR EXISTS (
                          SELECT 1 FROM backfill_job_targets target
                          WHERE target.backfill_job_id = job.id
                            AND target.instrument_id = %s
                        )
                      )
                    LIMIT 1
                    """,
                    (planned["id"], Jsonb([instrument_id]), instrument_id),
                ).fetchone()
                if conflict is not None:
                    raise ValueError("같은 상품에 이미 활성 백필 작업이 있다.")
                active_market = conn.execute(
                    """
                    SELECT 1
                    FROM collection_targets target
                    JOIN instruments instrument ON instrument.id = target.instrument_id
                    WHERE target.instrument_id = %s
                      AND target.status = 'active'
                      AND instrument.status = 'active'
                    LIMIT 1
                    """,
                    (instrument_id,),
                ).fetchone()
                if active_market is None:
                    raise ValueError("비활성 수집 시장의 수동 백필 계획은 승인할 수 없다.")
            row = _expect_row(
                conn.execute(
                    """
                    UPDATE backfill_jobs
                    SET status = 'pending',
                        approved_by = 'local_user',
                        approved_at = %s,
                        updated_at = now()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (now_kst(), planned["id"]),
                ).fetchone()
            )
            for instrument_id in targets:
                conn.execute(
                    """
                    INSERT INTO backfill_job_targets (backfill_job_id, instrument_id, status)
                    VALUES (%s, %s, 'pending')
                    ON CONFLICT (backfill_job_id, instrument_id) DO NOTHING
                    """,
                    (row["id"], instrument_id),
                )
            return self._backfill_job_by_id(conn, int(row["id"]))

    def claim_next_backfill_job(self) -> BackfillJobDetail | None:
        claimed_at = datetime.now(UTC)
        lease_expires_at = claimed_at + timedelta(seconds=BACKFILL_LEASE_SECONDS)
        with self._connect() as conn:
            if (
                self._enforce_backfill_safety_gate
                and self._backfill_gate_reason(conn, claimed_at) is not None
            ):
                return None
            exhausted_rows = conn.execute(
                """
                SELECT id, last_error_code
                FROM backfill_jobs
                WHERE attempt_count >= max_attempts
                  AND (
                    status IN ('pending', 'retry_wait')
                    OR (
                      status = 'running'
                      AND (lease_expires_at IS NULL OR lease_expires_at <= %s)
                    )
                  )
                FOR UPDATE SKIP LOCKED
                """,
                (claimed_at,),
            ).fetchall()
            for exhausted in exhausted_rows:
                self._move_backfill_job_to_dead_letter(
                    conn,
                    int(exhausted["id"]),
                    error_code=cast(str | None, exhausted["last_error_code"]),
                    reason="backfill attempt budget exhausted before claim",
                )
            row = conn.execute(
                """
                SELECT *
                FROM backfill_jobs
                WHERE attempt_count < max_attempts
                  AND (
                    status = 'pending'
                    OR (
                      status = 'retry_wait'
                      AND COALESCE(next_retry_at, '-infinity'::timestamptz) <= %s
                    )
                    OR (
                      status = 'running'
                      AND (
                        lease_expires_at IS NULL
                        OR lease_expires_at <= %s
                      )
                    )
                  )
                ORDER BY priority DESC, created_at, id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                (claimed_at, claimed_at),
            ).fetchone()
            if row is None:
                return None
            row = _expect_row(
                conn.execute(
                    """
                    UPDATE backfill_jobs
                    SET status = 'running',
                        started_at = COALESCE(started_at, %s),
                        lease_owner = %s,
                        lease_expires_at = %s,
                        next_retry_at = NULL,
                        attempt_count = attempt_count + %s,
                        updated_at = %s
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        claimed_at,
                        self._backfill_worker_id,
                        lease_expires_at,
                        1,
                        claimed_at,
                        row["id"],
                    ),
                ).fetchone()
            )
        return _backfill_job_detail(row)

    def backfill_claim_gate_reason(self) -> str | None:
        if not self._enforce_backfill_safety_gate:
            return None
        with self._connect() as conn:
            return self._backfill_gate_reason(conn, datetime.now(UTC))

    def _backfill_gate_reason(
        self,
        conn: psycopg.Connection[Any],
        checked_at: datetime,
    ) -> str | None:
        if not self._release_sha:
            return "실행 릴리스 SHA가 없어 백필 안전 게이트를 열 수 없습니다."
        row = conn.execute(
            """
            SELECT enabled, backup_verified_at, free_capacity_bytes,
                   required_capacity_bytes, approved_sha
            FROM backfill_safety_gate
            WHERE singleton
            """,
        ).fetchone()
        if row is None or not row["enabled"]:
            return "백필 안전 게이트가 운영 승인 전 기본 닫힘 상태입니다."
        backup_verified_at = cast(datetime | None, row["backup_verified_at"])
        if (
            backup_verified_at is None
            or backup_verified_at < checked_at - timedelta(hours=24)
            or backup_verified_at > checked_at
        ):
            return "최근 24시간 이내 백업 검증 증적이 없습니다."
        if int(row["required_capacity_bytes"]) <= 0:
            return "백필 필요 용량이 양수 측정값으로 승인되지 않았습니다."
        if int(row["free_capacity_bytes"]) < int(row["required_capacity_bytes"]):
            return "백필 실행에 필요한 저장소 여유 용량이 부족합니다."
        if row["approved_sha"] != self._release_sha:
            return "운영 승인 SHA와 실행 릴리스 SHA가 일치하지 않습니다."
        return None

    def backfill_job_targets(self, job_id: int) -> list[BackfillJobTarget]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM backfill_job_targets
                WHERE backfill_job_id = %s
                ORDER BY instrument_id
                """,
                (job_id,),
            ).fetchall()
        return [_backfill_target(row) for row in rows]

    def record_backfill_candles(
        self,
        job_id: int,
        instrument_id: int,
        candles: list[SourceCandle],
        *,
        fetch_evidence: FetchEvidence | None = None,
    ) -> int:
        if any(item.instrument_id != instrument_id for item in candles):
            raise ValueError("백필 캔들 대상 instrument_id가 작업 대상과 다르다.")
        started_at = now_kst()
        with self._connect() as conn:
            self._assert_backfill_write_lease(conn, job_id, instrument_id)
            run_id = int(
                _expect_row(
                    conn.execute(
                        """
                        INSERT INTO collection_runs (
                          run_type, data_type, status, trigger_type, started_at
                        )
                        VALUES ('backfill', 'source_candle', 'running', 'backfill_job', %s)
                        RETURNING id
                        """,
                        (started_at,),
                    ).fetchone()
                )["id"]
            )
            manifest_overrides: dict[tuple[int, str], tuple[int | None, int | None, int]] = {}
            target_spec_id: int | None = None
            manifest_id: int | None = None
            if fetch_evidence is not None:
                context = conn.execute(
                    """
                    SELECT market.id AS market_id, spec.id AS target_spec_id
                    FROM instruments instrument
                    LEFT JOIN markets market ON market.legacy_instrument_id = instrument.id
                    LEFT JOIN collection_target_specs spec
                      ON spec.market_id = market.id
                     AND spec.data_type = 'source_candle'
                     AND spec.candle_unit = '1m'
                    WHERE instrument.id = %s
                    ORDER BY (spec.status = 'active') DESC NULLS LAST, spec.id
                    LIMIT 1
                    """,
                    (instrument_id,),
                ).fetchone()
                market_id = cast(int | None, context["market_id"]) if context else None
                target_spec_id = cast(int | None, context["target_spec_id"]) if context else None
                manifest_id = self._insert_fetch_manifest(
                    conn,
                    target_spec_id=target_spec_id,
                    collection_run_id=run_id,
                    endpoint=fetch_evidence.endpoint,
                    request_parameters=fetch_evidence.request_parameters,
                    payload=fetch_evidence.response_payload,
                    requested_at=fetch_evidence.requested_at,
                    responded_at=fetch_evidence.responded_at,
                    response_status=fetch_evidence.response_status,
                    outcome="succeeded",
                    error_code=None,
                    preserve_payload=True,
                    share_across_runs=True,
                )
                manifest_overrides[(instrument_id, "1m")] = (
                    market_id,
                    target_spec_id,
                    manifest_id,
                )
            counts = self._upsert_candles(
                conn,
                run_id,
                candles,
                requested_at=started_at,
                expected_instrument_ids=(instrument_id,),
                request_context={"backfillJobId": job_id},
                manifest_overrides=manifest_overrides,
            )
            if (
                fetch_evidence is not None
                and target_spec_id is not None
                and manifest_id is not None
            ):
                self._record_confirmed_no_trade_gaps(
                    conn,
                    target_spec_id=target_spec_id,
                    manifest_id=manifest_id,
                    fetch_evidence=fetch_evidence,
                )
            rows_written = counts.get(instrument_id, 0)
            conn.execute(
                """
                INSERT INTO target_collection_results (
                  collection_run_id, instrument_id, data_type, status, rows_written
                )
                VALUES (%s, %s, 'source_candle', 'succeeded', %s)
                """,
                (run_id, instrument_id, rows_written),
            )
            finished_at = now_kst()
            conn.execute(
                """
                UPDATE collection_runs
                SET status = 'succeeded', finished_at = %s
                WHERE id = %s
                """,
                (finished_at, run_id),
            )
            conn.execute(
                """
                UPDATE backfill_job_targets
                SET status = 'running', updated_at = now()
                WHERE backfill_job_id = %s AND instrument_id = %s AND status = 'pending'
                """,
                (job_id, instrument_id),
            )
            self._renew_backfill_lease(conn, job_id)
        return rows_written

    def record_backfill_target_progress(
        self,
        job_id: int,
        instrument_id: int,
        processed_missing_range_count: int,
        estimated_missing_range_count: int,
        rows_written_count: int,
        last_completed_at: datetime | None,
    ) -> None:
        with self._connect() as conn:
            self._assert_backfill_write_lease(conn, job_id, instrument_id)
            conn.execute(
                """
                UPDATE backfill_job_targets
                SET processed_missing_range_count = %s,
                    estimated_missing_range_count = %s,
                    rows_written_count = %s,
                    last_completed_at = %s,
                    updated_at = now()
                WHERE backfill_job_id = %s AND instrument_id = %s
                """,
                (
                    max(0, processed_missing_range_count),
                    max(0, estimated_missing_range_count),
                    max(0, rows_written_count),
                    last_completed_at,
                    job_id,
                    instrument_id,
                ),
            )
            self._renew_backfill_lease(conn, job_id)

    def mark_backfill_target(
        self,
        job_id: int,
        instrument_id: int,
        status: str,
        last_completed_at: datetime | None,
        error_code: str | None = None,
        error_message: str | None = None,
        retry_after_seconds: float | None = None,
        *,
        fetch_evidence: FetchEvidence | None = None,
    ) -> None:
        if status not in {"pending", "running", "paused", "stopped", "succeeded", "failed"}:
            raise ValueError("지원하지 않는 백필 대상 상태다.")
        with self._connect() as conn:
            self._assert_backfill_write_lease(conn, job_id, instrument_id)
            conn.execute(
                """
                UPDATE backfill_job_targets
                SET status = %s,
                    last_completed_at = GREATEST(
                      last_completed_at, %s::timestamptz
                    ),
                    error_code = %s,
                    error_message = %s, updated_at = now()
                WHERE backfill_job_id = %s AND instrument_id = %s
                """,
                (
                    status,
                    last_completed_at,
                    error_code,
                    error_message,
                    job_id,
                    instrument_id,
                ),
            )
            if status == "failed":
                target = conn.execute(
                    """
                    SELECT target.target_spec_id, job.target_start_at, job.target_end_at
                    FROM backfill_job_targets target
                    JOIN backfill_jobs job ON job.id = target.backfill_job_id
                    WHERE target.backfill_job_id = %s AND target.instrument_id = %s
                    """,
                    (job_id, instrument_id),
                ).fetchone()
                if target is not None:
                    requested_at = (
                        fetch_evidence.requested_at
                        if fetch_evidence is not None
                        else datetime.now(UTC)
                    )
                    manifest_id = self._insert_fetch_manifest(
                        conn,
                        target_spec_id=cast(int | None, target["target_spec_id"]),
                        collection_run_id=None,
                        endpoint=(
                            fetch_evidence.endpoint
                            if fetch_evidence is not None
                            else "/v1/candles/minutes/1"
                        ),
                        request_parameters=(
                            fetch_evidence.request_parameters
                            if fetch_evidence is not None
                            else {
                                "backfillJobId": job_id,
                                "instrumentId": instrument_id,
                                "rangeStartAt": target["target_start_at"],
                                "rangeEndAt": target["target_end_at"],
                            }
                        ),
                        payload=(
                            fetch_evidence.response_payload if fetch_evidence is not None else None
                        ),
                        requested_at=requested_at,
                        responded_at=(
                            fetch_evidence.responded_at
                            if fetch_evidence is not None
                            else requested_at
                        ),
                        response_status=(
                            fetch_evidence.response_status
                            if fetch_evidence is not None
                            else _response_status_from_error_code(error_code)
                        ),
                        outcome=_manifest_outcome_from_error_code(error_code),
                        error_code=error_code,
                        error_message=(
                            fetch_evidence.error_message or error_message
                            if fetch_evidence is not None
                            else error_message
                        ),
                        preserve_payload=fetch_evidence is not None,
                        share_across_runs=fetch_evidence is not None,
                    )
                    conn.execute(
                        """
                        UPDATE backfill_job_targets
                        SET last_fetch_manifest_id = %s, updated_at = now()
                        WHERE backfill_job_id = %s AND instrument_id = %s
                        """,
                        (manifest_id, job_id, instrument_id),
                    )
            self._refresh_backfill_job_progress(
                conn,
                job_id,
                retry_after_seconds=retry_after_seconds,
            )

    def _renew_backfill_lease(
        self,
        conn: psycopg.Connection[Any],
        job_id: int,
    ) -> None:
        renewed_at = datetime.now(UTC)
        conn.execute(
            """
            UPDATE backfill_jobs
            SET lease_expires_at = %s, updated_at = %s
            WHERE id = %s
              AND status = 'running'
              AND lease_owner = %s
            """,
            (
                renewed_at + timedelta(seconds=BACKFILL_LEASE_SECONDS),
                renewed_at,
                job_id,
                self._backfill_worker_id,
            ),
        )

    def _assert_backfill_write_lease(
        self,
        conn: psycopg.Connection[Any],
        job_id: int,
        instrument_id: int,
    ) -> None:
        checked_at = datetime.now(UTC)
        job = conn.execute(
            """
            SELECT job.status, job.lease_owner, job.lease_expires_at,
                   target.status AS target_status
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            WHERE job.id = %s AND target.instrument_id = %s
            FOR UPDATE OF job, target
            """,
            (job_id, instrument_id),
        ).fetchone()
        if job is None:
            raise ValueError("존재하지 않는 백필 작업 대상이다.")
        if job["target_status"] not in {"pending", "running"}:
            raise BackfillTargetNotWritableError(
                "백필 대상이 중지되었거나 기록 가능한 상태가 아니다."
            )
        if (
            job["status"] != "running"
            or job["lease_owner"] != self._backfill_worker_id
            or job["lease_expires_at"] is None
            or cast(datetime, job["lease_expires_at"]) <= checked_at
        ):
            raise RuntimeError("백필 쓰기 임대가 없거나 만료되어 결과를 기록할 수 없다.")

    def _move_backfill_job_to_dead_letter(
        self,
        conn: psycopg.Connection[Any],
        job_id: int,
        *,
        error_code: str | None,
        reason: str,
    ) -> None:
        detected_at = datetime.now(UTC)
        conn.execute(
            """
            UPDATE backfill_jobs
            SET status = 'dead_letter',
                next_retry_at = NULL,
                lease_owner = NULL,
                lease_expires_at = NULL,
                last_error_code = COALESCE(%s, last_error_code),
                dead_letter_reason = %s,
                finished_at = COALESCE(finished_at, %s),
                updated_at = %s
            WHERE id = %s
            """,
            (error_code, reason, detected_at, detected_at, job_id),
        )
        targets = conn.execute(
            """
            SELECT target.target_spec_id, target.last_fetch_manifest_id,
                   job.target_start_at, job.target_end_at
            FROM backfill_job_targets target
            JOIN backfill_jobs job ON job.id = target.backfill_job_id
            WHERE target.backfill_job_id = %s
              AND target.target_spec_id IS NOT NULL
              AND target.status NOT IN ('succeeded', 'stopped', 'paused')
            ORDER BY target.target_spec_id
            FOR UPDATE OF target
            """,
            (job_id,),
        ).fetchall()
        evidence = {
            "backfillJobId": job_id,
            "errorCode": error_code,
            "reason": reason,
        }
        missing_status = classify_coverage(
            CoverageEvidence(attempted=True, retry_budget_exhausted=True)
        )
        for target in targets:
            target_spec_id = int(target["target_spec_id"])
            range_start_at = cast(datetime, target["target_start_at"])
            range_end_at = cast(datetime, target["target_end_at"])
            overlaps = conn.execute(
                """
                SELECT range_start_at, range_end_at, status
                FROM coverage_intervals
                WHERE target_spec_id = %s
                  AND tstzrange(range_start_at, range_end_at, '[)')
                      && tstzrange(%s, %s, '[)')
                ORDER BY range_start_at
                """,
                (target_spec_id, range_start_at, range_end_at),
            ).fetchall()
            cursor = range_start_at
            change_ranges: list[tuple[datetime, datetime]] = []
            for overlap in overlaps:
                overlap_start = max(range_start_at, cast(datetime, overlap["range_start_at"]))
                overlap_end = min(range_end_at, cast(datetime, overlap["range_end_at"]))
                if cursor < overlap_start:
                    change_ranges.append((cursor, overlap_start))
                if overlap["status"] == "unverified" and overlap_start < overlap_end:
                    change_ranges.append((overlap_start, overlap_end))
                cursor = max(cursor, overlap_end)
            if cursor < range_end_at:
                change_ranges.append((cursor, range_end_at))
            for change_start, change_end in change_ranges:
                replace_coverage_with_classification(
                    conn,
                    target_spec_id=target_spec_id,
                    range_start_at=change_start,
                    range_end_at=change_end,
                    status=missing_status,
                    reason_code="backfill_attempts_exhausted",
                    manifest_id=cast(int | None, target["last_fetch_manifest_id"]),
                    evidence=evidence,
                )
        conn.execute(
            """
            UPDATE backfill_job_targets
            SET status = 'failed', updated_at = %s
            WHERE backfill_job_id = %s
              AND status NOT IN ('succeeded', 'stopped', 'paused')
            """,
            (detected_at, job_id),
        )

    def control_backfill_job(self, job_id: int, action: str) -> BackfillJob:
        if action not in {"pause", "stop", "resume", "safe-restart"}:
            raise ValueError("지원하지 않는 백필 제어 명령이다.")
        with self._connect() as conn:
            current = conn.execute(
                "SELECT * FROM backfill_jobs WHERE id = %s", (job_id,)
            ).fetchone()
            if current is None:
                raise ValueError("존재하지 않는 백필 작업이다.")
            current_status = str(current["status"])
            if current_status == "succeeded":
                raise ValueError("완료 또는 중지된 백필 작업은 해당 명령을 수행할 수 없다.")
            if action == "pause" and current_status not in {
                "pending",
                "leased",
                "running",
                "retry_wait",
            }:
                raise ValueError("현재 상태의 백필 작업은 일시정지할 수 없다.")
            if action == "resume" and current_status not in {"paused", "failed"}:
                raise ValueError("현재 상태의 백필 작업은 재개할 수 없다.")
            if action == "safe-restart" and current_status not in {
                "paused",
                "stopped",
                "failed",
                "dead_letter",
                "cancelled",
            }:
                raise ValueError("현재 상태의 백필 작업은 안전 재시작할 수 없다.")
            next_status = {
                "pause": "paused",
                "stop": "stopped",
                "resume": "pending",
                "safe-restart": "pending",
            }[action]
            row = _expect_row(
                conn.execute(
                    """
                    UPDATE backfill_jobs
                    SET status = %s,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        next_retry_at = NULL,
                        attempt_count = CASE WHEN %s = 'safe-restart' THEN 0 ELSE attempt_count END,
                        last_error_code = CASE
                          WHEN %s = 'safe-restart' THEN NULL ELSE last_error_code
                        END,
                        dead_letter_reason = CASE
                          WHEN %s = 'safe-restart' THEN NULL ELSE dead_letter_reason
                        END,
                        finished_at = CASE
                          WHEN %s IN ('resume', 'safe-restart') THEN NULL ELSE finished_at
                        END,
                        updated_at = now()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (next_status, action, action, action, action, job_id),
                ).fetchone()
            )
            if action in {"resume", "safe-restart"}:
                conn.execute(
                    """
                    UPDATE backfill_job_targets
                    SET status = 'pending', updated_at = now()
                    WHERE backfill_job_id = %s AND status <> 'succeeded'
                    """,
                    (job_id,),
                )
            elif action == "pause":
                conn.execute(
                    """
                    UPDATE backfill_job_targets
                    SET status = 'paused', updated_at = now()
                    WHERE backfill_job_id = %s AND status IN ('pending', 'running')
                    """,
                    (job_id,),
                )
            elif action == "stop":
                conn.execute(
                    """
                    UPDATE backfill_job_targets
                    SET status = 'stopped', updated_at = now()
                    WHERE backfill_job_id = %s AND status NOT IN ('succeeded', 'failed')
                    """,
                    (job_id,),
                )
            return self._backfill_job_by_id(conn, int(row["id"]))

    def delete_backfill_job(self, job_id: int) -> None:
        with self._connect() as conn:
            current = conn.execute(
                "SELECT status FROM backfill_jobs WHERE id = %s FOR UPDATE", (job_id,)
            ).fetchone()
            if current is None:
                raise ValueError("존재하지 않는 백필 작업이다.")
            if current["status"] in {"leased", "running"}:
                raise ValueError("실행 중인 백필 작업은 먼저 중지해야 한다.")
            conn.execute("DELETE FROM backfill_jobs WHERE id = %s", (job_id,))

    def _backfill_job_by_id(self, conn: psycopg.Connection[Any], job_id: int) -> BackfillJob:
        row = _expect_row(
            conn.execute(
                """
                SELECT
                  bj.*,
                  running.instrument_id AS current_target_id,
                  running.processed_missing_range_count,
                  running.estimated_missing_range_count,
                  running.rows_written_count AS current_target_backfill_row_count,
                  COUNT(bjt.instrument_id) AS total_target_count,
                  COUNT(bjt.instrument_id) FILTER (
                    WHERE bjt.status IN ('succeeded', 'stopped')
                  )
                    AS completed_target_count,
                  CASE
                    WHEN running.instrument_id IS NULL THEN NULL
                    ELSE (
                      SELECT COUNT(*)
                      FROM backfill_job_targets bjt_index
                      WHERE bjt_index.backfill_job_id = bj.id
                        AND bjt_index.instrument_id <= running.instrument_id
                    )
                  END AS running_target_index,
                  COALESCE(
                    ROUND(
                      100.0 * SUM(
                        CASE
                          WHEN bjt.status IN ('succeeded', 'stopped') THEN 1.0
                          WHEN bjt.estimated_missing_range_count > 0 THEN
                            LEAST(
                              1.0,
                              bjt.processed_missing_range_count::numeric
                                / bjt.estimated_missing_range_count
                            )
                          ELSE 0
                        END
                      ) / NULLIF(COUNT(bjt.instrument_id), 0),
                      2
                    ),
                    0
                  ) AS progress_percent
                FROM backfill_jobs bj
                LEFT JOIN backfill_job_targets bjt ON bjt.backfill_job_id = bj.id
                LEFT JOIN LATERAL (
                  SELECT *
                  FROM backfill_job_targets running_target
                  WHERE running_target.backfill_job_id = bj.id
                    AND running_target.status = 'running'
                  ORDER BY running_target.instrument_id
                  LIMIT 1
                ) running ON true
                WHERE bj.id = %s
                GROUP BY bj.id
                       , running.instrument_id
                       , running.processed_missing_range_count
                       , running.estimated_missing_range_count
                       , running.rows_written_count
                """,
                (job_id,),
            ).fetchone()
        )
        return _backfill_job(
            row,
            self._backfill_job_target_instruments(conn, job_id),
            _instrument_by_id(conn, int(row["current_target_id"]))
            if row["current_target_id"] is not None
            else None,
        )

    def backfill_jobs(self) -> list[BackfillJob]:
        stopped_since = now_kst() - timedelta(days=30)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  bj.*,
                  running.instrument_id AS current_target_id,
                  running.processed_missing_range_count,
                  running.estimated_missing_range_count,
                  running.rows_written_count AS current_target_backfill_row_count,
                  COUNT(bjt.instrument_id) AS total_target_count,
                  COUNT(bjt.instrument_id) FILTER (
                    WHERE bjt.status IN ('succeeded', 'stopped')
                  )
                    AS completed_target_count,
                  CASE
                    WHEN running.instrument_id IS NULL THEN NULL
                    ELSE (
                      SELECT COUNT(*)
                      FROM backfill_job_targets bjt_index
                      WHERE bjt_index.backfill_job_id = bj.id
                        AND bjt_index.instrument_id <= running.instrument_id
                    )
                  END AS running_target_index,
                  COALESCE(
                    ROUND(
                      100.0 * SUM(
                        CASE
                          WHEN bjt.status IN ('succeeded', 'stopped') THEN 1.0
                          WHEN bjt.estimated_missing_range_count > 0 THEN
                            LEAST(
                              1.0,
                              bjt.processed_missing_range_count::numeric
                                / bjt.estimated_missing_range_count
                            )
                          ELSE 0
                        END
                      ) / NULLIF(COUNT(bjt.instrument_id), 0),
                      2
                    ),
                    0
                  ) AS progress_percent
                FROM backfill_jobs bj
                LEFT JOIN backfill_job_targets bjt ON bjt.backfill_job_id = bj.id
                LEFT JOIN LATERAL (
                  SELECT *
                  FROM backfill_job_targets running_target
                  WHERE running_target.backfill_job_id = bj.id
                    AND running_target.status = 'running'
                  ORDER BY running_target.instrument_id
                  LIMIT 1
                ) running ON true
                WHERE bj.status != 'stopped' OR bj.created_at >= %s
                GROUP BY bj.id
                       , running.instrument_id
                       , running.processed_missing_range_count
                       , running.estimated_missing_range_count
                       , running.rows_written_count
                ORDER BY bj.created_at DESC
                """,
                (stopped_since,),
            ).fetchall()
            return [
                _backfill_job(
                    row,
                    self._backfill_job_target_instruments(conn, int(row["id"])),
                    _instrument_by_id(conn, int(row["current_target_id"]))
                    if row["current_target_id"] is not None
                    else None,
                )
                for row in rows
            ]

    def notification_events(self) -> list[NotificationEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notification_events ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        return [_notification(row) for row in rows]

    def add_notification(
        self, severity: str, event_type: str, title: str, message: str
    ) -> NotificationEvent:
        with self._connect() as conn:
            row = _expect_row(
                conn.execute(
                    """
                    INSERT INTO notification_events (
                      severity, event_type, title, message, status
                    )
                    VALUES (%s, %s, %s, %s, 'open')
                    RETURNING *
                    """,
                    (severity, event_type, title, message),
                ).fetchone()
            )
        return _notification(row)

    def _recent_run_count(self) -> int:
        with self._connect() as conn:
            row = _expect_row(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM collection_runs WHERE started_at >= %s",
                    (now_kst() - timedelta(hours=24),),
                ).fetchone()
            )
        return int(row["count"])

    def _recent_collection_result_count(self) -> int:
        with self._connect() as conn:
            row = _expect_row(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM target_collection_results
                    WHERE created_at >= %s
                    """,
                    (now_kst() - timedelta(hours=24),),
                ).fetchone()
            )
        return int(row["count"])

    def _collection_rows_last_minute(self, run_type: str) -> int:
        with self._connect() as conn:
            row = _expect_row(
                conn.execute(
                    """
                    SELECT COALESCE(SUM(tcr.rows_written), 0) AS count
                    FROM target_collection_results tcr
                    JOIN collection_runs cr ON cr.id = tcr.collection_run_id
                    WHERE cr.run_type = %s AND tcr.created_at >= %s
                    """,
                    (run_type, now_kst() - timedelta(minutes=1)),
                ).fetchone()
            )
        return int(row["count"])

    def _storage_bytes_estimate(self) -> int:
        return sum(
            self._table_count(table) * row_size
            for table, row_size in (
                ("source_candles", 256),
                ("ticker_snapshots", 160),
                ("orderbook_summaries", 224),
            )
        )

    def _storage_bytes_today_estimate(self) -> int:
        day_start = now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            self._table_count_since("source_candles", "collected_at", day_start) * 256
            + self._table_count_since("ticker_snapshots", "collected_at", day_start) * 160
            + self._table_count_since("orderbook_summaries", "collected_at", day_start) * 224
        )

    def _storage_rows_today(self) -> int:
        day_start = now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            self._table_count_since("source_candles", "collected_at", day_start)
            + self._table_count_since("ticker_snapshots", "collected_at", day_start)
            + self._table_count_since("orderbook_summaries", "collected_at", day_start)
        )

    def _realtime_collection_heatmap(self) -> list[RealtimeCollectionHeatmapRow]:
        active_targets = self.list_active_targets()[:50]
        if not active_targets:
            return []
        now = now_kst()
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        first_hour = current_hour - timedelta(hours=23)
        target_ids = [target.id for target in active_targets]
        placeholders = ", ".join(["%s"] * len(target_ids))
        aggregates: dict[tuple[int, datetime], dict[str, Decimal | int]] = {}

        with self._connect() as conn:
            for row in conn.execute(
                f"""
                SELECT
                  instrument_id,
                  date_trunc('hour', trade_timestamp_at) AS bucket_start,
                  COUNT(*) AS trade_count,
                  COALESCE(
                    SUM(CASE WHEN ask_bid = 'BID' THEN trade_volume ELSE 0 END), 0
                  ) AS bid_volume,
                  COALESCE(
                    SUM(CASE WHEN ask_bid = 'ASK' THEN trade_volume ELSE 0 END), 0
                  ) AS ask_volume,
                  COALESCE(SUM(trade_volume), 0) AS trade_volume,
                  COALESCE(SUM(trade_amount), 0) AS trade_amount
                FROM trade_events
                WHERE trade_timestamp_at >= %s
                  AND instrument_id IN ({placeholders})
                GROUP BY instrument_id, bucket_start
                """,
                (first_hour, *target_ids),
            ).fetchall():
                bucket = row["bucket_start"].replace(minute=0, second=0, microsecond=0)
                aggregates[(int(row["instrument_id"]), bucket)] = {
                    "trade_count": int(row["trade_count"]),
                    "bid_volume": Decimal(row["bid_volume"]),
                    "ask_volume": Decimal(row["ask_volume"]),
                    "trade_volume": Decimal(row["trade_volume"]),
                    "trade_amount": Decimal(row["trade_amount"]),
                }

        heatmap: list[RealtimeCollectionHeatmapRow] = []
        for target in active_targets:
            hourly_buckets: list[RealtimeCollectionHeatmapBucket] = []
            for offset in range(24):
                bucket_start = first_hour + timedelta(hours=offset)
                aggregate = aggregates.get(
                    (target.id, bucket_start),
                    {
                        "trade_count": 0,
                        "bid_volume": Decimal("0"),
                        "ask_volume": Decimal("0"),
                        "trade_volume": Decimal("0"),
                        "trade_amount": Decimal("0"),
                    },
                )
                trade_count = int(aggregate["trade_count"])
                average_trades_per_minute = Decimal(trade_count) / Decimal("60")
                ask_volume = cast(Decimal, aggregate["ask_volume"])
                trade_strength = (
                    cast(Decimal, aggregate["bid_volume"]) / ask_volume * Decimal("100")
                    if ask_volume > 0
                    else Decimal("0")
                )
                hourly_buckets.append(
                    RealtimeCollectionHeatmapBucket(
                        bucket_start_at=bucket_start,
                        trade_count=trade_count,
                        average_trades_per_minute=average_trades_per_minute,
                        trade_strength=trade_strength,
                        trade_volume=cast(Decimal, aggregate["trade_volume"]),
                        trade_amount=cast(Decimal, aggregate["trade_amount"]),
                        status=self._realtime_collection_heatmap_status(average_trades_per_minute),
                    )
                )
            heatmap.append(
                RealtimeCollectionHeatmapRow(
                    instrument=target,
                    instrument_display_name=target.display_name,
                    hourly_buckets=hourly_buckets,
                )
            )
        return heatmap

    def _collection_activity_buckets(self) -> list[CollectionActivityBucket]:
        current_hour = now_kst().replace(minute=0, second=0, microsecond=0)
        first_hour = current_hour - timedelta(hours=(7 * 24) - 1)
        with self._connect() as conn:
            run_rows = conn.execute(
                """
                SELECT date_trunc('hour', started_at) AS bucket_start_at,
                       COUNT(*) AS run_count
                FROM collection_runs
                WHERE started_at >= %s
                GROUP BY bucket_start_at
                """,
                (first_hour,),
            ).fetchall()
            result_rows = conn.execute(
                """
                SELECT date_trunc('hour', created_at) AS bucket_start_at,
                       COUNT(*) AS result_count
                FROM target_collection_results
                WHERE created_at >= %s
                GROUP BY bucket_start_at
                """,
                (first_hour,),
            ).fetchall()
        run_counts = {
            row["bucket_start_at"].replace(minute=0, second=0, microsecond=0): int(row["run_count"])
            for row in run_rows
        }
        result_counts = {
            row["bucket_start_at"].replace(minute=0, second=0, microsecond=0): int(
                row["result_count"]
            )
            for row in result_rows
        }
        return [
            CollectionActivityBucket(
                bucket_start_at=bucket_start,
                run_count=run_counts.get(bucket_start, 0),
                result_count=result_counts.get(bucket_start, 0),
                status=self._activity_status(
                    run_counts.get(bucket_start, 0),
                    result_counts.get(bucket_start, 0),
                ),
            )
            for bucket_start in (first_hour + timedelta(hours=offset) for offset in range(7 * 24))
        ]

    def _storage_breakdown_today(self, total_bytes: int) -> list[StorageBreakdownItem]:
        day_start = now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = [
            (
                "source_candle",
                "캔들",
                self._table_count_since("source_candles", "collected_at", day_start),
                256,
            ),
            (
                "ticker_snapshot",
                "현재가",
                self._table_count_since("ticker_snapshots", "collected_at", day_start),
                160,
            ),
            (
                "orderbook_summary",
                "호가",
                self._table_count_since("orderbook_summaries", "collected_at", day_start),
                224,
            ),
        ]
        return [
            StorageBreakdownItem(
                data_type=cast(
                    Literal["source_candle", "ticker_snapshot", "orderbook_summary"],
                    data_type,
                ),
                label=label,
                row_count=row_count,
                bytes=row_count * row_size,
                bytes_display=_format_storage_bytes(row_count * row_size),
                share_percent=self._storage_share_percent(row_count * row_size, total_bytes),
            )
            for data_type, label, row_count, row_size in rows
        ]

    def _operations_trend(
        self,
        coverage: list[CoverageStatus],
        storage_bytes_today: int,
        warning_targets: int,
        incident_targets: int,
    ) -> list[OperationsTrendPoint]:
        today = now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
        coverage_percent = self._average_coverage_percent(coverage)
        storage_bytes_by_day = self._storage_bytes_by_day(today - timedelta(days=6), today)
        points = []
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            points.append(
                OperationsTrendPoint(
                    bucket_date=day,
                    coverage_percent=coverage_percent if offset == 0 else Decimal("0"),
                    storage_bytes=(
                        storage_bytes_today if offset == 0 else storage_bytes_by_day.get(day, 0)
                    ),
                    warning_targets=warning_targets if offset == 0 else 0,
                    incident_targets=incident_targets if offset == 0 else 0,
                )
            )
        return points

    def _missing_range_top(
        self, targets: list[CollectionDashboardTarget]
    ) -> list[MissingRangeSummary]:
        summaries = []
        for target in targets:
            candle_status = next(
                status for status in target.data_statuses if status.data_type == "source_candle"
            )
            summaries.append(
                MissingRangeSummary(
                    instrument=target.instrument,
                    missing_segment_count=candle_status.missing_segment_count,
                    coverage_percent=candle_status.progress_percent,
                    last_successful_at=candle_status.last_successful_at,
                )
            )
        return sorted(
            summaries,
            key=lambda item: (item.missing_segment_count, Decimal("100") - item.coverage_percent),
            reverse=True,
        )[:5]

    def _audit_log_summary(self) -> AuditLogSummary:
        since = now_kst() - timedelta(hours=24)
        with self._connect() as conn:
            target_count_row = _expect_row(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM collection_target_changes
                    WHERE changed_at >= %s
                    """,
                    (since,),
                ).fetchone()
            )
            backfill_count_row = _expect_row(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM backfill_jobs
                    WHERE created_at >= %s
                    """,
                    (since,),
                ).fetchone()
            )
            latest_target = conn.execute(
                """
                SELECT changed_at, '대상 변경' AS label
                FROM collection_target_changes
                ORDER BY changed_at DESC
                LIMIT 1
                """
            ).fetchone()
            latest_backfill = conn.execute(
                """
                SELECT created_at AS changed_at, '백필 시작' AS label
                FROM backfill_jobs
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        latest_rows = [row for row in [latest_target, latest_backfill] if row is not None]
        latest_row = max(
            latest_rows,
            key=lambda row: cast(datetime, row["changed_at"]),
            default=None,
        )
        return AuditLogSummary(
            target_change_count_24h=int(target_count_row["count"]),
            backfill_change_count_24h=int(backfill_count_row["count"]),
            latest_change_at=cast(datetime, latest_row["changed_at"]) if latest_row else None,
            latest_change_label=str(latest_row["label"]) if latest_row else "기록 없음",
        )

    def _realtime_worker_status(self) -> RealtimeWorkerStatus:
        runtime = self.collection_worker_runtime_status("realtime_collection")
        status = runtime.status
        status_label = runtime.status_label
        status_detail = runtime.status_detail
        last_heartbeat_at = runtime.last_heartbeat_at
        error_count_24h = self._realtime_error_count_24h()
        run_count_24h = self._realtime_run_count_24h()
        collected_row_count_24h = self._realtime_collected_row_count_24h()
        failure_rate_24h = (
            Decimal(error_count_24h) / Decimal(run_count_24h) * Decimal("100")
            if run_count_24h > 0
            else Decimal("0")
        )
        last_collected_at = self._latest_collection_finished_at("incremental")
        return RealtimeWorkerStatus(
            status=status,
            status_label=status_label,
            status_detail=status_detail,
            last_heartbeat_at=last_heartbeat_at,
            last_collected_at=last_collected_at,
            collected_row_count_24h=collected_row_count_24h,
            error_count_24h=error_count_24h,
            failure_rate_24h=failure_rate_24h,
            diagnostics=self._realtime_worker_diagnostics(
                status_detail,
                last_heartbeat_at,
                last_collected_at,
                collected_row_count_24h,
                error_count_24h,
                failure_rate_24h,
            ),
            recent_errors=self._recent_realtime_errors(),
        )

    def _backfill_worker_status(self) -> BackfillWorkerStatus:
        runtime = self.collection_worker_runtime_status("backfill_collection")
        status = runtime.status
        status_label = runtime.status_label
        status_detail = runtime.status_detail
        last_heartbeat_at = runtime.last_heartbeat_at
        total_error_count = self._backfill_error_count_all()
        total_target_count_all = self._backfill_target_count_all()
        failure_rate_all = (
            Decimal(total_error_count) / Decimal(total_target_count_all) * Decimal("100")
            if total_target_count_all > 0
            else Decimal("0")
        )
        (
            running_target_count,
            total_target_count,
            queued_job_count,
            queued_target_count,
        ) = self._active_backfill_target_summary()
        last_collected_at = self._latest_collection_finished_at("backfill")
        return BackfillWorkerStatus(
            status=status,
            status_label=status_label,
            status_detail=status_detail,
            last_heartbeat_at=last_heartbeat_at,
            last_collected_at=last_collected_at,
            total_error_count=total_error_count,
            failure_rate_all=failure_rate_all,
            running_target_count=running_target_count,
            total_target_count=total_target_count,
            queued_job_count=queued_job_count,
            queued_target_count=queued_target_count,
            diagnostics=self._backfill_worker_diagnostics(
                status_detail,
                last_heartbeat_at,
                last_collected_at,
                total_error_count,
                failure_rate_all,
                running_target_count,
                total_target_count,
                queued_job_count,
                queued_target_count,
            ),
            recent_errors=self._recent_backfill_errors(),
        )

    def _realtime_worker_diagnostics(
        self,
        status_detail: str,
        last_heartbeat_at: datetime | None,
        last_collected_at: datetime | None,
        collected_row_count: int,
        error_count: int,
        failure_rate: Decimal,
    ) -> list[CollectionWorkerDiagnostic]:
        return [
            CollectionWorkerDiagnostic(
                "마지막 heartbeat",
                _diagnostic_datetime(last_heartbeat_at),
                status_detail,
            ),
            CollectionWorkerDiagnostic(
                "마지막 저장 성공",
                _diagnostic_datetime(last_collected_at),
                "최근 성공 또는 부분 성공한 실시간 저장 시각",
            ),
            CollectionWorkerDiagnostic(
                "24시간 수집 row",
                f"{collected_row_count:,} rows",
                "최근 24시간 실시간 수집이 저장한 ticker/orderbook/candle row 합계",
            ),
            CollectionWorkerDiagnostic(
                "24시간 오류",
                f"{error_count:,}건",
                f"24시간 실패율 {failure_rate:.2f}%",
            ),
        ]

    def _backfill_worker_diagnostics(
        self,
        status_detail: str,
        last_heartbeat_at: datetime | None,
        last_collected_at: datetime | None,
        error_count: int,
        failure_rate: Decimal,
        running_target_count: int,
        total_target_count: int,
        queued_job_count: int,
        queued_target_count: int,
    ) -> list[CollectionWorkerDiagnostic]:
        return [
            CollectionWorkerDiagnostic(
                "마지막 heartbeat",
                _diagnostic_datetime(last_heartbeat_at),
                status_detail,
            ),
            CollectionWorkerDiagnostic(
                "마지막 저장 성공",
                _diagnostic_datetime(last_collected_at),
                "최근 성공 또는 부분 성공한 백필 저장 시각",
            ),
            CollectionWorkerDiagnostic(
                "전체 오류",
                f"{error_count:,}건",
                f"전체 실패율 {failure_rate:.2f}%",
            ),
            CollectionWorkerDiagnostic(
                "동작중 코인",
                f"{running_target_count:,}/{total_target_count:,}개",
                "현재 실행 중인 백필 계획의 running 대상 수",
            ),
            CollectionWorkerDiagnostic(
                "대기 백필",
                f"{queued_job_count:,}건 / {queued_target_count:,}개",
                "현재 계획 이후 대기 중인 백필 job/target",
            ),
        ]

    def _worker_runtime_status(
        self,
        worker_type: CollectionWorkerType,
        stale_after: timedelta,
    ) -> tuple[CollectionWorkerStatus, str, str, datetime | None]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM collection_worker_heartbeats
                WHERE worker_type = %s
                """,
                (worker_type,),
            ).fetchone()
        if row is None:
            return "stale", "중지 추정", "worker heartbeat 기록이 없습니다.", None
        last_heartbeat_at = cast(datetime, row["last_heartbeat_at"])
        if row["status"] == "failed":
            return (
                "failed",
                "오류",
                str(row["last_error_message"] or "마지막 heartbeat가 실패 상태입니다."),
                last_heartbeat_at,
            )
        if row["status"] == "gated":
            return (
                "gated",
                "승인 대기",
                str(row["last_error_message"] or "백필 안전 게이트가 닫혀 있습니다."),
                last_heartbeat_at,
            )
        if now_kst() - last_heartbeat_at > stale_after:
            return (
                "stale",
                "지연",
                "마지막 heartbeat가 허용 지연 시간을 넘었습니다.",
                last_heartbeat_at,
            )
        return "running", "동작 중", "최근 heartbeat 정상", last_heartbeat_at

    def collection_worker_runtime_status(
        self, worker_type: CollectionWorkerType
    ) -> CollectionWorkerRuntimeStatus:
        stale_after = {
            "realtime_collection": timedelta(minutes=2),
            "backfill_collection": timedelta(seconds=30),
            "candle_aggregation": timedelta(seconds=30),
        }[worker_type]
        return CollectionWorkerRuntimeStatus(
            *self._worker_runtime_status(worker_type, stale_after=stale_after)
        )

    def _latest_collection_finished_at(self, run_type: str) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT finished_at, started_at FROM collection_runs
                WHERE run_type = %s AND status IN ('succeeded', 'partial')
                ORDER BY COALESCE(finished_at, started_at) DESC
                LIMIT 1
                """,
                (run_type,),
            ).fetchone()
        if row is None:
            return None
        return cast(datetime, row["finished_at"] or row["started_at"])

    def _realtime_error_count_24h(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM collection_runs
                WHERE run_type = 'incremental' AND status = 'failed'
                  AND started_at >= %s
                """,
                (now_kst() - timedelta(hours=24),),
            ).fetchone()
        return int(_expect_row(row)["count"])

    def _realtime_run_count_24h(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM collection_runs
                WHERE run_type = 'incremental' AND started_at >= %s
                """,
                (now_kst() - timedelta(hours=24),),
            ).fetchone()
        return int(_expect_row(row)["count"])

    def _realtime_collected_row_count_24h(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(tcr.rows_written), 0) AS count
                FROM target_collection_results tcr
                JOIN collection_runs cr ON cr.id = tcr.collection_run_id
                WHERE cr.run_type = 'incremental' AND tcr.created_at >= %s
                """,
                (now_kst() - timedelta(hours=24),),
            ).fetchone()
        return int(_expect_row(row)["count"])

    def _recent_realtime_errors(self) -> list[CollectionWorkerError]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT started_at, error_code, error_message
                FROM collection_runs
                WHERE run_type = 'incremental' AND status = 'failed'
                  AND started_at >= %s
                ORDER BY started_at DESC
                LIMIT 10
                """,
                (now_kst() - timedelta(hours=24),),
            ).fetchall()
        return [
            CollectionWorkerError(
                occurred_at=cast(datetime, row["started_at"]),
                code=str(row["error_code"] or "CollectionRunFailed"),
                message=str(row["error_message"] or "실시간 수집 실행이 실패했습니다."),
            )
            for row in rows
        ]

    def _backfill_error_count_all(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM backfill_job_targets
                WHERE error_code IS NOT NULL
                """
            ).fetchone()
        return int(_expect_row(row)["count"])

    def _backfill_target_count_all(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM backfill_job_targets
                """
            ).fetchone()
        return int(_expect_row(row)["count"])

    def _active_backfill_target_summary(self) -> tuple[int, int, int, int]:
        with self._connect() as conn:
            active_job = conn.execute(
                """
                SELECT id
                FROM backfill_jobs
                WHERE status IN ('leased', 'running', 'retry_wait', 'pending')
                ORDER BY CASE
                  WHEN status IN ('leased', 'running') THEN 0
                  WHEN status = 'retry_wait' THEN 1
                  ELSE 2
                END, created_at
                LIMIT 1
                """
            ).fetchone()
            if active_job is None:
                return 0, 0, 0, 0
            active_job_id = int(active_job["id"])
            active_counts = _expect_row(
                conn.execute(
                    """
                    SELECT
                      COUNT(*) FILTER (WHERE status = 'running') AS running_count,
                      COUNT(*) AS total_count
                    FROM backfill_job_targets
                    WHERE backfill_job_id = %s
                    """,
                    (active_job_id,),
                ).fetchone()
            )
            queued_counts = _expect_row(
                conn.execute(
                    """
                    SELECT
                      COUNT(DISTINCT bj.id) AS queued_job_count,
                      COUNT(bjt.instrument_id) AS queued_target_count
                    FROM backfill_jobs bj
                    LEFT JOIN backfill_job_targets bjt ON bjt.backfill_job_id = bj.id
                    WHERE bj.status IN ('pending', 'retry_wait') AND bj.id <> %s
                    """,
                    (active_job_id,),
                ).fetchone()
            )
        return (
            int(active_counts["running_count"] or 0),
            int(active_counts["total_count"] or 0),
            int(queued_counts["queued_job_count"] or 0),
            int(queued_counts["queued_target_count"] or 0),
        )

    def _backfill_job_target_instruments(
        self, conn: psycopg.Connection[Any], job_id: int
    ) -> list[Instrument]:
        rows = conn.execute(
            """
            SELECT i.*
            FROM backfill_job_targets bjt
            JOIN instruments i ON i.id = bjt.instrument_id
            WHERE bjt.backfill_job_id = %s
            ORDER BY i.market_code
            """,
            (job_id,),
        ).fetchall()
        return [_instrument(row) for row in rows]

    def _active_backfill_target_counts(self) -> tuple[int, int]:
        running_count, total_count, _, _ = self._active_backfill_target_summary()
        return running_count, total_count

    def _recent_backfill_errors(self) -> list[CollectionWorkerError]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT bjt.updated_at, bjt.error_code, bjt.error_message, i.market_code
                FROM backfill_job_targets bjt
                JOIN instruments i ON i.id = bjt.instrument_id
                WHERE bjt.status = 'failed'
                ORDER BY bjt.updated_at DESC
                LIMIT 10
                """
            ).fetchall()
        return [
            CollectionWorkerError(
                occurred_at=cast(datetime, row["updated_at"]),
                code=str(row["error_code"] or "BackfillTargetFailed"),
                message=f"{row['market_code']}: {row['error_message'] or '백필 대상 수집 실패'}",
            )
            for row in rows
        ]

    def _storage_bytes_for_range(self, start_at: datetime, end_at: datetime) -> int:
        return (
            self._table_count_between("source_candles", "collected_at", start_at, end_at) * 256
            + self._table_count_between("ticker_snapshots", "collected_at", start_at, end_at) * 160
            + self._table_count_between("orderbook_summaries", "collected_at", start_at, end_at)
            * 224
        )

    def _storage_bytes_by_day(self, start_at: datetime, end_at: datetime) -> dict[datetime, int]:
        rows_by_day: dict[datetime, int] = {}
        range_end = end_at + timedelta(days=1)
        for table, row_size in (
            ("source_candles", 256),
            ("ticker_snapshots", 160),
            ("orderbook_summaries", 224),
        ):
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT date_trunc('day', collected_at) AS bucket_date,
                           COUNT(*) AS row_count
                    FROM {table}
                    WHERE collected_at >= %s AND collected_at < %s
                    GROUP BY bucket_date
                    """,
                    (start_at, range_end),
                ).fetchall()
            for row in rows:
                bucket_date = cast(datetime, row["bucket_date"]).astimezone(KST)
                bucket_date = bucket_date.replace(hour=0, minute=0, second=0, microsecond=0)
                rows_by_day[bucket_date] = rows_by_day.get(bucket_date, 0) + (
                    int(row["row_count"]) * row_size
                )
        return rows_by_day

    def _table_count_between(
        self, table: str, time_column: str, start_at: datetime, end_at: datetime
    ) -> int:
        with self._connect() as conn:
            row = _expect_row(
                conn.execute(
                    f"""
                    SELECT COUNT(*) AS count FROM {table}
                    WHERE {time_column} >= %s AND {time_column} < %s
                    """,
                    (start_at, end_at),
                ).fetchone()
            )
        return int(row["count"])

    @staticmethod
    def _activity_status(
        run_count: int,
        result_count: int,
    ) -> Literal["none", "low", "collecting", "high"]:
        if run_count == 0 and result_count == 0:
            return "none"
        if result_count >= 50:
            return "high"
        if run_count > 0:
            return "collecting"
        return "low"

    @staticmethod
    def _realtime_collection_heatmap_status(
        average_trades_per_minute: Decimal,
    ) -> TradeFrequencyStatus:
        if average_trades_per_minute < Decimal("10"):
            return "red"
        if average_trades_per_minute < Decimal("50"):
            return "orange"
        if average_trades_per_minute < Decimal("100"):
            return "yellow"
        if average_trades_per_minute < Decimal("200"):
            return "blue"
        return "green"

    @staticmethod
    def _average_coverage_percent(coverage: list[CoverageStatus]) -> Decimal:
        source = [item.progress_percent for item in coverage if item.data_type == "source_candle"]
        if not source:
            return Decimal("0")
        return (sum(source) / Decimal(len(source))).quantize(Decimal("0.01"))

    @staticmethod
    def _storage_share_percent(bytes_value: int, total_bytes: int) -> Decimal:
        if total_bytes <= 0:
            return Decimal("0")
        return (Decimal(bytes_value) / Decimal(total_bytes) * Decimal("100")).quantize(
            Decimal("0.01")
        )

    def _instrument_storage_bytes(self, instrument_id: int) -> int:
        return (
            self._table_count("source_candles", instrument_id) * 256
            + self._table_count("ticker_snapshots", instrument_id) * 160
            + self._table_count("orderbook_summaries", instrument_id) * 224
        )

    def _instrument_storage_row_count(self, instrument_id: int) -> int:
        return (
            self._table_count("source_candles", instrument_id)
            + self._table_count("ticker_snapshots", instrument_id)
            + self._table_count("orderbook_summaries", instrument_id)
        )

    def _instrument_storage_bytes_by_instrument(self, instrument_ids: list[int]) -> dict[int, int]:
        return self._instrument_storage_totals_by_instrument(instrument_ids)[0]

    def _instrument_storage_row_counts_by_instrument(
        self, instrument_ids: list[int]
    ) -> dict[int, int]:
        return self._instrument_storage_totals_by_instrument(instrument_ids)[1]

    def _instrument_storage_totals_by_instrument(
        self,
        instrument_ids: list[int],
        source_candle_counts: dict[int, int] | None = None,
    ) -> tuple[dict[int, int], dict[int, int]]:
        source_counts = (
            source_candle_counts
            if source_candle_counts is not None
            else self._table_counts_by_instrument("source_candles", instrument_ids)
        )
        ticker_counts = self._table_counts_by_instrument("ticker_snapshots", instrument_ids)
        orderbook_counts = self._table_counts_by_instrument("orderbook_summaries", instrument_ids)
        storage_bytes_by_instrument = {
            instrument_id: source_counts.get(instrument_id, 0) * 256
            + ticker_counts.get(instrument_id, 0) * 160
            + orderbook_counts.get(instrument_id, 0) * 224
            for instrument_id in instrument_ids
        }
        storage_rows_by_instrument = {
            instrument_id: source_counts.get(instrument_id, 0)
            + ticker_counts.get(instrument_id, 0)
            + orderbook_counts.get(instrument_id, 0)
            for instrument_id in instrument_ids
        }
        return storage_bytes_by_instrument, storage_rows_by_instrument

    def _table_count(self, table: str, instrument_id: int | None = None) -> int:
        with self._connect() as conn:
            if instrument_id is None:
                row = _expect_row(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone())
            else:
                row = _expect_row(
                    conn.execute(
                        f"SELECT COUNT(*) AS count FROM {table} WHERE instrument_id = %s",
                        (instrument_id,),
                    ).fetchone()
                )
        return int(row["count"])

    def _table_count_since(self, table: str, time_column: str, since: datetime) -> int:
        with self._connect() as conn:
            row = _expect_row(
                conn.execute(
                    f"SELECT COUNT(*) AS count FROM {table} WHERE {time_column} >= %s",
                    (since,),
                ).fetchone()
            )
        return int(row["count"])

    def _table_counts_by_instrument(self, table: str, instrument_ids: list[int]) -> dict[int, int]:
        if not instrument_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(instrument_ids))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT instrument_id, COUNT(*) AS count
                FROM {table}
                WHERE instrument_id IN ({placeholders})
                GROUP BY instrument_id
                """,
                tuple(instrument_ids),
            ).fetchall()
        return {int(row["instrument_id"]): int(row["count"]) for row in rows}

    def _market_coverage_percent(self, instrument_id: int) -> Decimal:
        coverage = self.coverage_for(instrument_id)
        return self._market_coverage_percent_from_statuses(coverage)

    def _market_coverage_percent_from_statuses(self, coverage: list[CoverageStatus]) -> Decimal:
        if not coverage:
            return Decimal("0")
        return sum((item.progress_percent for item in coverage), Decimal("0")) / Decimal(
            len(coverage)
        )

    def _quality_status_from_coverage(
        self, coverage: list[CoverageStatus]
    ) -> Literal["normal", "warning", "incident"]:
        if any(item.status == "incident" for item in coverage):
            return "incident"
        if any(item.status != "normal" for item in coverage):
            return "warning"
        return "normal"

    def _health_checks(
        self, coverage: list[CoverageStatus], alerts: list[NotificationEvent]
    ) -> list[HealthCheck]:
        ticker_warnings = sum(
            1
            for item in coverage
            if item.data_type == "ticker_snapshot" and item.status != "normal"
        )
        candle_warnings = sum(
            1 for item in coverage if item.data_type == "source_candle" and item.status != "normal"
        )
        orderbook_warnings = sum(
            1
            for item in coverage
            if item.data_type == "orderbook_summary" and item.status != "normal"
        )
        open_alerts = [alert for alert in alerts if alert.status == "open"]
        return [
            HealthCheck(
                title="현재가·거래대금",
                status="normal" if ticker_warnings == 0 else "warning",
                status_label="정상" if ticker_warnings == 0 else "주의",
                detail="최근 1-3분 정상" if ticker_warnings == 0 else f"지연 {ticker_warnings}구간",
            ),
            HealthCheck(
                title="캔들 상태",
                status="normal" if candle_warnings == 0 else "warning",
                status_label="정상" if candle_warnings == 0 else "주의",
                detail="직전 완성 1분봉 저장"
                if candle_warnings == 0
                else f"결측 {candle_warnings}구간",
            ),
            HealthCheck(
                title="호가 상태",
                status="normal" if orderbook_warnings == 0 else "warning",
                status_label="정상" if orderbook_warnings == 0 else "주의",
                detail="매수 잔량 우세"
                if orderbook_warnings == 0
                else f"지연 {orderbook_warnings}구간",
            ),
            HealthCheck(
                title="완전성 검사",
                status="normal" if not open_alerts else "warning",
                status_label="정상" if not open_alerts else "주의",
                detail="결측 0구간" if not open_alerts else f"알림 {len(open_alerts)}건",
            ),
        ]

    def _activate_target(
        self,
        conn: psycopg.Connection[Any],
        instrument_id: int,
        actor: str,
        reason: str | None,
        target_order: int,
    ) -> None:
        previous = conn.execute(
            "SELECT status FROM collection_targets WHERE instrument_id = %s",
            (instrument_id,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO collection_targets (
              instrument_id, status, activated_at, deactivated_at, target_order, candidate_status
            )
            VALUES (%s, 'active', %s, NULL, %s, 'in_universe')
            ON CONFLICT (instrument_id) DO UPDATE SET
              status = 'active',
              activated_at = COALESCE(collection_targets.activated_at, excluded.activated_at),
              deactivated_at = NULL,
              target_order = excluded.target_order,
              candidate_status = 'in_universe',
              updated_at = now()
            """,
            (instrument_id, now_kst(), target_order),
        )
        self._ensure_collection_plan(conn, instrument_id)
        self._record_target_change(
            conn, instrument_id, previous["status"] if previous else None, "active", actor, reason
        )

    def _deactivate_target(
        self,
        conn: psycopg.Connection[Any],
        instrument_id: int,
        actor: str,
        reason: str | None,
    ) -> None:
        previous = conn.execute(
            "SELECT status FROM collection_targets WHERE instrument_id = %s",
            (instrument_id,),
        ).fetchone()
        conn.execute(
            """
            UPDATE collection_targets
            SET status = 'inactive', target_order = NULL, deactivated_at = %s, updated_at = now()
            WHERE instrument_id = %s
            """,
            (now_kst(), instrument_id),
        )
        self._record_target_change(
            conn, instrument_id, previous["status"] if previous else None, "inactive", actor, reason
        )

    def _record_target_change(
        self,
        conn: psycopg.Connection[Any],
        instrument_id: int,
        previous_status: str | None,
        new_status: str,
        actor: str,
        reason: str | None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO collection_target_changes (
              instrument_id, previous_status, new_status, actor, reason
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (instrument_id, previous_status, new_status, actor, reason),
        )

    def _ensure_collection_plan(self, conn: psycopg.Connection[Any], instrument_id: int) -> None:
        plan_start = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
        conn.execute(
            """
            INSERT INTO collection_plans (
              instrument_id, preset, range_start_at, range_end_at,
              is_continuous, method, status
            )
            VALUES (
              %s, '2026년 1월 1분봉', %s, NULL,
              true, 'safe_restart', 'latest_collecting'
            )
            ON CONFLICT (instrument_id) DO NOTHING
            """,
            (instrument_id, plan_start),
        )

    def _collection_plan_for(self, instrument_id: int) -> CollectionPlan:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM collection_plans WHERE instrument_id = %s",
                (instrument_id,),
            ).fetchone()
            if row is None:
                self._ensure_collection_plan(conn, instrument_id)
                row = conn.execute(
                    "SELECT * FROM collection_plans WHERE instrument_id = %s",
                    (instrument_id,),
                ).fetchone()
        expected = _expect_row(row)
        return self._collection_plan_from_row(instrument_id, expected)

    def _collection_plans_by_instrument(
        self, instrument_ids: list[int]
    ) -> dict[int, CollectionPlan]:
        if not instrument_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(instrument_ids))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM collection_plans
                WHERE instrument_id IN ({placeholders})
                """,
                tuple(instrument_ids),
            ).fetchall()
            plans = {
                int(row["instrument_id"]): self._collection_plan_from_row(
                    int(row["instrument_id"]), row
                )
                for row in rows
            }
            missing_ids = [
                instrument_id for instrument_id in instrument_ids if instrument_id not in plans
            ]
            for instrument_id in missing_ids:
                self._ensure_collection_plan(conn, instrument_id)
            if missing_ids:
                missing_placeholders = ", ".join(["%s"] * len(missing_ids))
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM collection_plans
                    WHERE instrument_id IN ({missing_placeholders})
                    """,
                    tuple(missing_ids),
                ).fetchall()
                plans.update(
                    {
                        int(row["instrument_id"]): self._collection_plan_from_row(
                            int(row["instrument_id"]), row
                        )
                        for row in rows
                    }
                )
        return plans

    def _collection_plan_from_row(self, instrument_id: int, row: Row) -> CollectionPlan:
        return CollectionPlan(
            instrument_id=instrument_id,
            preset=str(row["preset"]),
            range_start_at=cast(datetime, row["range_start_at"]),
            range_end_at=cast(datetime | None, row["range_end_at"]),
            is_continuous=bool(row["is_continuous"]),
            method=str(row["method"]),
            display_range="2026-01-01 00:00 KST ~ NOW"
            if bool(row["is_continuous"])
            else "2026-01-01 00:00 KST ~ 2026-02-01 00:00 KST",
            range_time_zone="KST",
            progress_basis="현재(지속)은 KST 전일 23:59:59까지 기준",
        )

    def _collection_data_status(
        self,
        item: CoverageStatus,
        source_candle_counts: dict[int, int] | None = None,
    ) -> CollectionDataStatus:
        labels = {
            "source_candle": "캔들",
            "ticker_snapshot": "현재가",
            "orderbook_summary": "호가 요약",
        }
        stored_row_count = 0
        if item.data_type == "source_candle":
            stored_row_count = (
                source_candle_counts.get(item.instrument_id)
                if source_candle_counts is not None
                else self._table_count("source_candles", item.instrument_id)
            ) or 0
        return CollectionDataStatus(
            data_type=item.data_type,
            label=labels[item.data_type],
            status=item.status,
            status_label="정상"
            if item.status == "normal"
            else "장애"
            if item.status == "incident"
            else "주의",
            last_successful_at=item.last_successful_at,
            progress_percent=item.progress_percent,
            missing_segment_count=item.missing_segment_count,
            stored_row_count=stored_row_count,
        )

    def _coverage_segments_for(
        self,
        instrument_id: int,
        data_type: Literal["source_candle", "ticker_snapshot", "orderbook_summary"],
    ) -> list[CoverageSegment]:
        plan = self._collection_plan_for(instrument_id)
        if data_type == "source_candle":
            return self._source_candle_coverage_segments(instrument_id, plan)
        segment_end = self._coverage_range_end(plan)
        return [
            CoverageSegment(
                data_type=data_type,
                status="collected",
                offset_percent=Decimal("0"),
                width_percent=Decimal("100"),
                segment_start_at=plan.range_start_at,
                segment_end_at=segment_end,
                label="수집 완료",
            )
        ]

    def _freshness_coverage_status(
        self,
        instrument_id: int,
        data_type: Literal["ticker_snapshot", "orderbook_summary"],
        latest_at: datetime | None,
    ) -> CoverageStatus:
        if latest_at is None:
            return CoverageStatus(
                instrument_id=instrument_id,
                data_type=data_type,
                status="incident",
                progress_percent=Decimal("0"),
                last_successful_at=now_kst() - timedelta(days=365),
            )
        return CoverageStatus(
            instrument_id=instrument_id,
            data_type=data_type,
            status="normal" if now_kst() - latest_at <= timedelta(minutes=3) else "warning",
            progress_percent=Decimal("100"),
            last_successful_at=latest_at,
        )

    def _source_candle_coverage_status(self, instrument_id: int) -> CoverageStatus:
        plan = self._collection_plan_for(instrument_id)
        range_end = self._coverage_range_end(plan)
        expected_minutes = self._expected_minutes(plan.range_start_at, range_end)
        stored_count, missing_segments, latest_at = self._source_candle_coverage_summary(
            instrument_id, plan.range_start_at, range_end
        )
        progress = (Decimal(stored_count) * Decimal("100") / Decimal(expected_minutes)).quantize(
            Decimal("0.01")
        )
        if latest_at is None:
            status: Literal["normal", "warning", "incident"] = "incident"
            last_successful_at = now_kst() - timedelta(days=365)
        elif missing_segments == 0 and progress == Decimal("100.00"):
            status = "normal"
            last_successful_at = latest_at
        else:
            status = "warning"
            last_successful_at = latest_at
        return CoverageStatus(
            instrument_id=instrument_id,
            data_type="source_candle",
            status=status,
            progress_percent=progress.normalize(),
            last_successful_at=last_successful_at,
            missing_segment_count=missing_segments,
        )

    def _source_candle_coverage_status_from_summary(
        self,
        instrument_id: int,
        plan: CollectionPlan,
        stored_count: int,
        source_candle_range: tuple[datetime | None, datetime | None],
    ) -> CoverageStatus:
        range_end = self._coverage_range_end(plan)
        expected_minutes = self._expected_minutes(plan.range_start_at, range_end)
        first_start_at, latest_start_at = source_candle_range
        progress = (Decimal(stored_count) * Decimal("100") / Decimal(expected_minutes)).quantize(
            Decimal("0.01")
        )
        if latest_start_at is None:
            return CoverageStatus(
                instrument_id=instrument_id,
                data_type="source_candle",
                status="incident",
                progress_percent=Decimal("0"),
                last_successful_at=now_kst() - timedelta(days=365),
                missing_segment_count=1,
            )
        missing_segments = 0
        if first_start_at is not None and first_start_at > plan.range_start_at:
            missing_segments += 1
        if latest_start_at + timedelta(minutes=1) < range_end:
            missing_segments += 1
        if stored_count < expected_minutes and missing_segments == 0:
            missing_segments = 1
        status: Literal["normal", "warning", "incident"] = (
            "normal" if missing_segments == 0 and progress == Decimal("100.00") else "warning"
        )
        return CoverageStatus(
            instrument_id=instrument_id,
            data_type="source_candle",
            status=status,
            progress_percent=progress.normalize(),
            last_successful_at=latest_start_at,
            missing_segment_count=missing_segments,
        )

    def _source_candle_coverage_summary(
        self,
        instrument_id: int,
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[int, int, datetime | None]:
        with self._connect() as conn:
            row = conn.execute(
                """
                WITH ordered AS (
                  SELECT
                    candle_start_at,
                    lag(candle_start_at) OVER (ORDER BY candle_start_at) AS previous_start_at
                  FROM source_candles
                  WHERE instrument_id = %s
                    AND candle_unit = '1m'
                    AND candle_start_at >= %s
                    AND candle_start_at < %s
                )
                SELECT
                  count(*) AS stored_count,
                  min(candle_start_at) AS first_start_at,
                  max(candle_start_at) AS latest_start_at,
                  coalesce(
                    sum(
                      CASE
                        WHEN previous_start_at IS NOT NULL
                         AND candle_start_at > previous_start_at + interval '1 minute'
                        THEN 1
                        ELSE 0
                      END
                    ),
                    0
                  ) AS gap_count
                FROM ordered
                """,
                (instrument_id, start_at, end_at),
            ).fetchone()
        if row is None or row["stored_count"] == 0:
            return 0, 1, None
        stored_count = int(row["stored_count"])
        first_start_at = cast(datetime, row["first_start_at"]).astimezone(KST)
        latest_start_at = cast(datetime, row["latest_start_at"]).astimezone(KST)
        missing_segments = int(row["gap_count"])
        if first_start_at > start_at:
            missing_segments += 1
        if latest_start_at + timedelta(minutes=1) < end_at:
            missing_segments += 1
        return stored_count, missing_segments, latest_start_at

    def _source_candle_coverage_segments(
        self,
        instrument_id: int,
        plan: CollectionPlan,
    ) -> list[CoverageSegment]:
        range_start = plan.range_start_at
        range_end = self._coverage_range_end(plan)
        expected_minutes = self._expected_minutes(range_start, range_end)
        stored_starts = sorted(self._source_candle_starts(instrument_id, range_start, range_end))
        segments: list[CoverageSegment] = []
        cursor = range_start
        collected_start: datetime | None = None
        collected_end: datetime | None = None
        for bucket in stored_starts:
            bucket_end = min(bucket + timedelta(minutes=1), range_end)
            if collected_start is None:
                if cursor < bucket:
                    segments.append(
                        self._coverage_segment(
                            "source_candle",
                            "missing",
                            cursor,
                            bucket,
                            range_start,
                            expected_minutes,
                        )
                    )
                collected_start = bucket
                collected_end = bucket_end
                cursor = bucket_end
                continue
            if collected_end is not None and bucket == collected_end:
                collected_end = bucket_end
                cursor = bucket_end
                continue
            segments.append(
                self._coverage_segment(
                    "source_candle",
                    "collected",
                    collected_start,
                    collected_end or bucket,
                    range_start,
                    expected_minutes,
                )
            )
            if collected_end is not None and collected_end < bucket:
                segments.append(
                    self._coverage_segment(
                        "source_candle",
                        "missing",
                        collected_end,
                        bucket,
                        range_start,
                        expected_minutes,
                    )
                )
            collected_start = bucket
            collected_end = bucket_end
            cursor = bucket_end
        if collected_start is not None:
            segments.append(
                self._coverage_segment(
                    "source_candle",
                    "collected",
                    collected_start,
                    collected_end or range_end,
                    range_start,
                    expected_minutes,
                )
            )
        if cursor < range_end:
            segments.append(
                self._coverage_segment(
                    "source_candle",
                    "missing",
                    cursor,
                    range_end,
                    range_start,
                    expected_minutes,
                )
            )
        return segments

    def _coverage_segment(
        self,
        data_type: Literal["source_candle", "ticker_snapshot", "orderbook_summary"],
        status: Literal["collected", "missing"],
        segment_start_at: datetime,
        segment_end_at: datetime,
        range_start_at: datetime,
        expected_minutes: int,
    ) -> CoverageSegment:
        offset_minutes = int((segment_start_at - range_start_at).total_seconds() // 60)
        width_minutes = max(1, int((segment_end_at - segment_start_at).total_seconds() // 60))
        return CoverageSegment(
            data_type=data_type,
            status=status,
            offset_percent=(Decimal(offset_minutes) * Decimal("100") / Decimal(expected_minutes))
            .quantize(Decimal("0.01"))
            .normalize(),
            width_percent=(Decimal(width_minutes) * Decimal("100") / Decimal(expected_minutes))
            .quantize(Decimal("0.01"))
            .normalize(),
            segment_start_at=segment_start_at,
            segment_end_at=segment_end_at,
            label="수집 완료" if status == "collected" else "결측",
        )

    def _source_candle_starts(
        self, instrument_id: int, start_at: datetime, end_at: datetime
    ) -> set[datetime]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT candle_start_at
                FROM source_candles
                WHERE instrument_id = %s
                  AND candle_unit = '1m'
                  AND candle_start_at >= %s
                  AND candle_start_at < %s
                ORDER BY candle_start_at
                """,
                (instrument_id, start_at, end_at),
            ).fetchall()
        return {cast(datetime, row["candle_start_at"]).astimezone(KST) for row in rows}

    def _coverage_range_end(self, plan: CollectionPlan) -> datetime:
        if plan.range_end_at is not None:
            return plan.range_end_at
        return minute_bucket(now_kst())

    def _expected_minutes(self, start_at: datetime, end_at: datetime) -> int:
        return max(1, int((end_at - start_at).total_seconds() // 60))

    def _source_manifests_by_instrument(
        self,
        conn: psycopg.Connection[Any],
        *,
        run_id: int,
        data_type: str,
        candle_unit: str | None,
        endpoint: str,
        items: list[Any],
        requested_at: datetime,
        expected_instrument_ids: tuple[int, ...] = (),
        request_context: dict[str, Any] | None = None,
    ) -> dict[int, tuple[int | None, int | None, int]]:
        grouped: dict[int, list[Any]] = {}
        for item in items:
            grouped.setdefault(int(item.instrument_id), []).append(item)
        for instrument_id in expected_instrument_ids:
            grouped.setdefault(instrument_id, [])

        manifests: dict[int, tuple[int | None, int | None, int]] = {}
        for instrument_id, instrument_items in grouped.items():
            context = conn.execute(
                """
                SELECT market.id AS market_id, spec.id AS target_spec_id
                FROM instruments instrument
                LEFT JOIN markets market
                  ON market.legacy_instrument_id = instrument.id
                LEFT JOIN collection_target_specs spec
                  ON spec.market_id = market.id
                 AND spec.data_type = %s
                 AND spec.candle_unit IS NOT DISTINCT FROM %s
                WHERE instrument.id = %s
                ORDER BY (spec.status = 'active') DESC NULLS LAST, spec.id
                LIMIT 1
                """,
                (data_type, candle_unit, instrument_id),
            ).fetchone()
            market_id = cast(int | None, context["market_id"]) if context is not None else None
            target_spec_id = (
                cast(int | None, context["target_spec_id"]) if context is not None else None
            )
            parameters: dict[str, Any] = {
                "dataType": data_type,
                "candleUnit": candle_unit,
                "instrumentId": instrument_id,
                "itemCount": len(instrument_items),
            }
            if request_context is not None:
                parameters.update(request_context)
            manifest_id = self._insert_fetch_manifest(
                conn,
                target_spec_id=target_spec_id,
                collection_run_id=run_id,
                endpoint=endpoint,
                request_parameters=parameters,
                payload=[asdict(item) for item in instrument_items],
                requested_at=requested_at,
                responded_at=datetime.now(UTC),
                response_status=200,
                outcome="succeeded",
                error_code=None,
            )
            manifests[instrument_id] = (market_id, target_spec_id, manifest_id)
        return manifests

    def _insert_fetch_manifest(
        self,
        conn: psycopg.Connection[Any],
        *,
        target_spec_id: int | None,
        collection_run_id: int | None,
        endpoint: str,
        request_parameters: dict[str, Any],
        payload: object | None,
        requested_at: datetime,
        responded_at: datetime | None,
        response_status: int | None,
        outcome: str,
        error_code: str | None,
        error_message: str | None = None,
        preserve_payload: bool = False,
        share_across_runs: bool = False,
    ) -> int:
        normalized_parameters = _jsonable(request_parameters)
        fingerprint_payload: dict[str, Any] = {
            "endpoint": endpoint,
            "parameters": normalized_parameters,
        }
        if not share_across_runs:
            fingerprint_payload["collectionRunId"] = collection_run_id
        request_fingerprint = _checksum(fingerprint_payload)
        response_checksum = _checksum(payload) if payload is not None else None
        row = _expect_row(
            conn.execute(
                """
                INSERT INTO fetch_manifests (
                  target_spec_id, collection_run_id, source, endpoint,
                  request_parameters, request_fingerprint, requested_at,
                  responded_at, response_status, response_checksum,
                  response_payload, collector_version, schema_version, outcome, error_code,
                  error_message
                )
                VALUES (
                  %s, %s, 'UPBIT', %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (source, request_fingerprint, requested_at)
                DO UPDATE SET
                  responded_at = excluded.responded_at,
                  response_status = excluded.response_status,
                  response_checksum = excluded.response_checksum,
                  response_payload = excluded.response_payload,
                  outcome = excluded.outcome,
                  error_code = excluded.error_code,
                  error_message = excluded.error_message
                RETURNING id
                """,
                (
                    target_spec_id,
                    collection_run_id,
                    endpoint,
                    Jsonb(normalized_parameters),
                    request_fingerprint,
                    requested_at,
                    responded_at,
                    response_status,
                    response_checksum,
                    (
                        Jsonb(_jsonable(payload))
                        if preserve_payload and payload is not None
                        else None
                    ),
                    SOURCE_EVIDENCE_COLLECTOR_VERSION,
                    SOURCE_EVIDENCE_SCHEMA_VERSION,
                    outcome,
                    error_code,
                    error_message,
                ),
            ).fetchone()
        )
        return int(row["id"])

    def _replace_coverage_with_observed(
        self,
        conn: psycopg.Connection[Any],
        *,
        target_spec_id: int,
        range_start_at: datetime,
        range_end_at: datetime,
        manifest_id: int,
        natural_key: dict[str, Any],
    ) -> None:
        status = classify_coverage(
            CoverageEvidence(source_row_count=1, manifest_checksum=str(manifest_id))
        )
        replace_coverage_with_classification(
            conn,
            target_spec_id=target_spec_id,
            range_start_at=range_start_at,
            range_end_at=range_end_at,
            status=status,
            reason_code="source_row_observed",
            manifest_id=manifest_id,
            evidence={
                "classification": "source_natural_key_with_checksum_manifest",
                "naturalKey": natural_key,
                "responseChecksumVerified": True,
            },
        )

    def _record_confirmed_no_trade_gaps(
        self,
        conn: psycopg.Connection[Any],
        *,
        target_spec_id: int,
        manifest_id: int,
        fetch_evidence: FetchEvidence,
    ) -> None:
        if (
            fetch_evidence.response_status != 200
            or fetch_evidence.requested_range_start_at is None
            or fetch_evidence.requested_range_end_at is None
            or not isinstance(fetch_evidence.response_payload, list)
        ):
            return
        candle_starts: list[datetime] = []
        for item in fetch_evidence.response_payload:
            if not isinstance(item, dict):
                continue
            raw_started_at = item.get("candle_date_time_utc") or item.get("candle_start_at")
            if not isinstance(raw_started_at, str):
                continue
            started_at = datetime.fromisoformat(raw_started_at.replace("Z", "+00:00"))
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
            candle_starts.append(started_at.astimezone(UTC))
        no_trade_status = classify_coverage(
            CoverageEvidence(request_succeeded=True, no_trade_corroborated=True)
        )
        for gap_start, gap_end in internal_minute_candle_gaps(
            requested_start_at=fetch_evidence.requested_range_start_at,
            requested_end_at=fetch_evidence.requested_range_end_at,
            candle_starts=tuple(candle_starts),
        ):
            replace_coverage_with_classification(
                conn,
                target_spec_id=target_spec_id,
                range_start_at=gap_start,
                range_end_at=gap_end,
                status=no_trade_status,
                reason_code="upbit_minute_candle_internal_gap",
                manifest_id=manifest_id,
                evidence={
                    "classification": "fully_bounded_successful_upbit_minute_page_gap",
                    "requestedRangeStartAt": fetch_evidence.requested_range_start_at,
                    "requestedRangeEndAt": fetch_evidence.requested_range_end_at,
                },
            )

    def _upsert_tickers(
        self,
        conn: psycopg.Connection[Any],
        run_id: int,
        tickers: list[TickerSnapshot],
        *,
        requested_at: datetime,
    ) -> dict[int, int]:
        counts: dict[int, int] = {}
        manifests = self._source_manifests_by_instrument(
            conn,
            run_id=run_id,
            data_type="ticker_snapshot",
            candle_unit=None,
            endpoint="/websocket/v1/ticker",
            items=tickers,
            requested_at=requested_at,
        )
        for item in tickers:
            market_id, target_spec_id, manifest_id = manifests[item.instrument_id]
            conn.execute(
                """
                INSERT INTO ticker_snapshots (
                  instrument_id, source, bucket_at, trade_price,
                  acc_trade_price_24h, change_rate, signed_change_rate,
                  collected_at, collection_run_id, market_id, occurred_at,
                  received_at, stored_at, knowledge_at, fetch_manifest_id
                )
                VALUES (
                  %s, 'UPBIT', %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, clock_timestamp(), %s, %s
                )
                ON CONFLICT (instrument_id, source, bucket_at) DO UPDATE SET
                  trade_price = excluded.trade_price,
                  acc_trade_price_24h = excluded.acc_trade_price_24h,
                  change_rate = excluded.change_rate,
                  signed_change_rate = excluded.signed_change_rate,
                  collected_at = excluded.collected_at,
                  collection_run_id = excluded.collection_run_id,
                  market_id = excluded.market_id,
                  occurred_at = excluded.occurred_at,
                  received_at = excluded.received_at,
                  stored_at = excluded.stored_at,
                  knowledge_at = excluded.knowledge_at,
                  fetch_manifest_id = excluded.fetch_manifest_id,
                  updated_at = now()
                WHERE excluded.collected_at > ticker_snapshots.collected_at
                """,
                (
                    item.instrument_id,
                    minute_bucket(item.bucket_at),
                    item.trade_price,
                    item.acc_trade_price_24h,
                    item.change_rate,
                    item.change_rate,
                    item.received_at,
                    run_id,
                    market_id,
                    item.occurred_at,
                    item.received_at,
                    item.received_at,
                    manifest_id,
                ),
            )
            conn.execute(
                """
                UPDATE ticker_snapshots
                SET market_id = COALESCE(market_id, %s),
                    occurred_at = COALESCE(occurred_at, %s),
                    received_at = COALESCE(received_at, %s),
                    stored_at = COALESCE(stored_at, clock_timestamp()),
                    knowledge_at = COALESCE(knowledge_at, %s),
                    fetch_manifest_id = COALESCE(fetch_manifest_id, %s)
                WHERE instrument_id = %s AND source = 'UPBIT' AND bucket_at = %s
                """,
                (
                    market_id,
                    item.occurred_at,
                    item.received_at,
                    item.received_at,
                    manifest_id,
                    item.instrument_id,
                    minute_bucket(item.bucket_at),
                ),
            )
            counts[item.instrument_id] = counts.get(item.instrument_id, 0) + 1
            if target_spec_id is not None:
                self._replace_coverage_with_observed(
                    conn,
                    target_spec_id=target_spec_id,
                    range_start_at=minute_bucket(item.bucket_at),
                    range_end_at=minute_bucket(item.bucket_at) + timedelta(minutes=1),
                    manifest_id=manifest_id,
                    natural_key={"bucketAt": minute_bucket(item.bucket_at)},
                )
        return counts

    def _upsert_orderbooks(
        self,
        conn: psycopg.Connection[Any],
        run_id: int,
        orderbooks: list[OrderbookSummary],
        *,
        requested_at: datetime,
    ) -> dict[int, int]:
        counts: dict[int, int] = {}
        manifests = self._source_manifests_by_instrument(
            conn,
            run_id=run_id,
            data_type="orderbook_snapshot",
            candle_unit=None,
            endpoint="/websocket/v1/orderbook",
            items=orderbooks,
            requested_at=requested_at,
        )
        for item in orderbooks:
            market_id, target_spec_id, manifest_id = manifests[item.instrument_id]
            conn.execute(
                """
                INSERT INTO orderbook_summaries (
                  instrument_id, source, bucket_at, best_bid_price, best_bid_size,
                  best_ask_price, best_ask_size, spread, bid_depth_10, ask_depth_10,
                  imbalance_10, collected_at, collection_run_id, market_id,
                  occurred_at, received_at, stored_at, knowledge_at, fetch_manifest_id
                )
                VALUES (
                  %s, 'UPBIT', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, clock_timestamp(), %s, %s
                )
                ON CONFLICT (instrument_id, source, bucket_at) DO UPDATE SET
                  best_bid_price = excluded.best_bid_price,
                  best_bid_size = excluded.best_bid_size,
                  best_ask_price = excluded.best_ask_price,
                  best_ask_size = excluded.best_ask_size,
                  spread = excluded.spread,
                  bid_depth_10 = excluded.bid_depth_10,
                  ask_depth_10 = excluded.ask_depth_10,
                  imbalance_10 = excluded.imbalance_10,
                  collected_at = excluded.collected_at,
                  collection_run_id = excluded.collection_run_id,
                  market_id = excluded.market_id,
                  occurred_at = excluded.occurred_at,
                  received_at = excluded.received_at,
                  stored_at = excluded.stored_at,
                  knowledge_at = excluded.knowledge_at,
                  fetch_manifest_id = excluded.fetch_manifest_id,
                  updated_at = now()
                WHERE excluded.collected_at > orderbook_summaries.collected_at
                """,
                (
                    item.instrument_id,
                    minute_bucket(item.bucket_at),
                    item.best_bid_price,
                    item.best_bid_size,
                    item.best_ask_price,
                    item.best_ask_size,
                    item.spread,
                    item.bid_depth_10,
                    item.ask_depth_10,
                    item.imbalance_10,
                    item.received_at,
                    run_id,
                    market_id,
                    item.occurred_at,
                    item.received_at,
                    item.received_at,
                    manifest_id,
                ),
            )
            conn.execute(
                """
                UPDATE orderbook_summaries
                SET market_id = COALESCE(market_id, %s),
                    occurred_at = COALESCE(occurred_at, %s),
                    received_at = COALESCE(received_at, %s),
                    stored_at = COALESCE(stored_at, clock_timestamp()),
                    knowledge_at = COALESCE(knowledge_at, %s),
                    fetch_manifest_id = COALESCE(fetch_manifest_id, %s)
                WHERE instrument_id = %s AND source = 'UPBIT' AND bucket_at = %s
                """,
                (
                    market_id,
                    item.occurred_at,
                    item.received_at,
                    item.received_at,
                    manifest_id,
                    item.instrument_id,
                    minute_bucket(item.bucket_at),
                ),
            )
            counts[item.instrument_id] = counts.get(item.instrument_id, 0) + 1
            if target_spec_id is not None:
                self._replace_coverage_with_observed(
                    conn,
                    target_spec_id=target_spec_id,
                    range_start_at=minute_bucket(item.bucket_at),
                    range_end_at=minute_bucket(item.bucket_at) + timedelta(minutes=1),
                    manifest_id=manifest_id,
                    natural_key={"bucketAt": minute_bucket(item.bucket_at)},
                )
        return counts

    def _upsert_candles(
        self,
        conn: psycopg.Connection[Any],
        run_id: int,
        candles: list[SourceCandle],
        *,
        requested_at: datetime,
        expected_instrument_ids: tuple[int, ...] = (),
        request_context: dict[str, Any] | None = None,
        manifest_overrides: dict[tuple[int, str], tuple[int | None, int | None, int]] | None = None,
    ) -> dict[int, int]:
        counts: dict[int, int] = {}
        units = sorted({item.candle_unit for item in candles}) or ["1m"]
        manifests = dict(manifest_overrides or {})
        for candle_unit in units:
            unit_items = [item for item in candles if item.candle_unit == candle_unit]
            instrument_ids = {item.instrument_id for item in unit_items} or set(
                expected_instrument_ids
            )
            if instrument_ids and all(
                (instrument_id, candle_unit) in manifests for instrument_id in instrument_ids
            ):
                continue
            endpoint = "/v1/candles/minutes/1" if candle_unit == "1m" else "/v1/candles/days"
            unit_manifests = self._source_manifests_by_instrument(
                conn,
                run_id=run_id,
                data_type="source_candle",
                candle_unit=candle_unit,
                endpoint=endpoint,
                items=unit_items,
                requested_at=requested_at,
                expected_instrument_ids=(expected_instrument_ids if not unit_items else ()),
                request_context=request_context,
            )
            manifests.update(
                {
                    (instrument_id, candle_unit): manifest
                    for instrument_id, manifest in unit_manifests.items()
                }
            )
        if not candles:
            return counts
        sql = """
            INSERT INTO source_candles (
              instrument_id, source, candle_unit, candle_start_at,
              open_price, high_price, low_price, close_price,
              trade_volume, trade_amount, collected_at, collection_run_id,
              market_id, occurred_at, received_at, stored_at, knowledge_at,
              fetch_manifest_id
            )
            VALUES (
              %s, 'UPBIT', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, clock_timestamp(), %s, %s
            )
            ON CONFLICT (instrument_id, source, candle_unit, candle_start_at)
            DO UPDATE SET
              open_price = excluded.open_price,
              high_price = excluded.high_price,
              low_price = excluded.low_price,
              close_price = excluded.close_price,
              trade_volume = excluded.trade_volume,
              trade_amount = excluded.trade_amount,
              collected_at = excluded.collected_at,
              collection_run_id = excluded.collection_run_id,
              market_id = excluded.market_id,
              occurred_at = excluded.occurred_at,
              received_at = excluded.received_at,
              stored_at = excluded.stored_at,
              knowledge_at = excluded.knowledge_at,
              fetch_manifest_id = excluded.fetch_manifest_id,
              updated_at = now()
            WHERE excluded.collected_at > source_candles.collected_at
            """
        params = []
        for item in candles:
            market_id, _target_spec_id, manifest_id = manifests[
                (item.instrument_id, item.candle_unit)
            ]
            params.append(
                (
                    item.instrument_id,
                    item.candle_unit,
                    item.candle_start_at,
                    item.open_price,
                    item.high_price,
                    item.low_price,
                    item.close_price,
                    item.trade_volume,
                    item.trade_amount,
                    item.collected_at,
                    run_id,
                    market_id,
                    item.candle_start_at,
                    item.collected_at,
                    item.collected_at,
                    manifest_id,
                ),
            )
            counts[item.instrument_id] = counts.get(item.instrument_id, 0) + 1
        with conn.cursor() as cursor:
            cursor.executemany(sql, params)
        observed_starts: dict[tuple[int, int, str], list[datetime]] = {}
        for item in candles:
            _market_id, target_spec_id, manifest_id = manifests[
                (item.instrument_id, item.candle_unit)
            ]
            conn.execute(
                """
                UPDATE source_candles
                SET market_id = COALESCE(market_id, %s),
                    occurred_at = COALESCE(occurred_at, %s),
                    received_at = COALESCE(received_at, %s),
                    stored_at = COALESCE(stored_at, clock_timestamp()),
                    knowledge_at = COALESCE(knowledge_at, %s),
                    fetch_manifest_id = COALESCE(fetch_manifest_id, %s)
                WHERE instrument_id = %s AND source = 'UPBIT'
                  AND candle_unit = %s AND candle_start_at = %s
                """,
                (
                    _market_id,
                    item.candle_start_at,
                    item.collected_at,
                    item.collected_at,
                    manifest_id,
                    item.instrument_id,
                    item.candle_unit,
                    item.candle_start_at,
                ),
            )
            exists = conn.execute(
                """
                SELECT 1 FROM source_candles
                WHERE instrument_id = %s AND source = 'UPBIT'
                  AND candle_unit = %s AND candle_start_at = %s
                """,
                (item.instrument_id, item.candle_unit, item.candle_start_at),
            ).fetchone()
            if exists is not None and target_spec_id is not None:
                observed_starts.setdefault(
                    (target_spec_id, manifest_id, item.candle_unit), []
                ).append(item.candle_start_at)
        for (
            target_spec_id,
            manifest_id,
            coverage_candle_unit,
        ), starts in observed_starts.items():
            for range_start_at, range_end_at, row_count in _contiguous_candle_ranges(
                starts, coverage_candle_unit
            ):
                self._replace_coverage_with_observed(
                    conn,
                    target_spec_id=target_spec_id,
                    range_start_at=range_start_at,
                    range_end_at=range_end_at,
                    manifest_id=manifest_id,
                    natural_key={
                        "candleUnit": coverage_candle_unit,
                        "firstCandleStartAt": range_start_at,
                        "lastCandleStartAt": range_end_at - _candle_interval(coverage_candle_unit),
                        "rowCount": row_count,
                    },
                )
        return counts

    def _latest_candle_time(self, instrument_id: int) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT candle_start_at FROM source_candles
                WHERE instrument_id = %s
                ORDER BY candle_start_at DESC
                LIMIT 1
                """,
                (instrument_id,),
            ).fetchone()
        return cast(datetime, row["candle_start_at"]) if row else None

    def _failed_runs_24h(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count FROM collection_runs
                WHERE status = 'failed' AND started_at >= %s
                """,
                (now_kst() - timedelta(hours=24),),
            ).fetchone()
        return int(_expect_row(row)["count"])

    def _refresh_backfill_job_progress(
        self,
        conn: psycopg.Connection[Any],
        job_id: int,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        job = _expect_row(
            conn.execute(
                """
                SELECT status, attempt_count, max_attempts
                FROM backfill_jobs
                WHERE id = %s
                FOR UPDATE
                """,
                (job_id,),
            ).fetchone()
        )
        current = str(job["status"])
        if current in {
            "retry_wait",
            "paused",
            "stopped",
            "succeeded",
            "failed",
            "dead_letter",
            "cancelled",
        }:
            conn.execute(
                "UPDATE backfill_jobs SET updated_at = now() WHERE id = %s",
                (job_id,),
            )
            return
        rows = conn.execute(
            """
            SELECT status, error_code, error_message
            FROM backfill_job_targets
            WHERE backfill_job_id = %s
            ORDER BY instrument_id
            """,
            (job_id,),
        ).fetchall()
        total = len(rows)
        succeeded = sum(1 for row in rows if row["status"] == "succeeded")
        stopped = sum(1 for row in rows if row["status"] == "stopped")
        paused = sum(1 for row in rows if row["status"] == "paused")
        active = sum(1 for row in rows if row["status"] in {"pending", "running"})
        failed_rows = [row for row in rows if row["status"] == "failed"]
        if total == 0:
            self._move_backfill_job_to_dead_letter(
                conn,
                job_id,
                error_code="BackfillTargetsMissing",
                reason="backfill job has no targets",
            )
            return
        if failed_rows:
            latest_failure = failed_rows[-1]
            error_code = cast(str | None, latest_failure["error_code"])
            error_message = cast(str | None, latest_failure["error_message"])
            if int(job["attempt_count"]) < int(job["max_attempts"]):
                exponential_delay = min(
                    BACKFILL_RETRY_MAX_SECONDS,
                    BACKFILL_RETRY_BASE_SECONDS * (2 ** max(0, int(job["attempt_count"]) - 1)),
                )
                if retry_after_seconds is not None:
                    delay_seconds = max(exponential_delay, retry_after_seconds)
                elif _response_status_from_error_code(error_code) == 418:
                    delay_seconds = max(exponential_delay, BACKFILL_RETRY_MAX_SECONDS)
                elif _response_status_from_error_code(error_code) == 429:
                    delay_seconds = max(exponential_delay, 1)
                else:
                    delay_seconds = exponential_delay
                conn.execute(
                    """
                    UPDATE backfill_job_targets
                    SET status = 'pending', updated_at = now()
                    WHERE backfill_job_id = %s AND status = 'failed'
                    """,
                    (job_id,),
                )
                conn.execute(
                    """
                    UPDATE backfill_jobs
                    SET status = 'retry_wait',
                        next_retry_at = now() + make_interval(secs => %s),
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_error_code = %s,
                        dead_letter_reason = NULL,
                        finished_at = NULL,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (delay_seconds, error_code, job_id),
                )
                return
            self._move_backfill_job_to_dead_letter(
                conn,
                job_id,
                error_code=error_code,
                reason=error_message or error_code or "backfill attempt budget exhausted",
            )
            return
        if active:
            status = "running"
        elif paused:
            status = "paused"
        else:
            status = "succeeded" if succeeded + stopped == total else "running"
        conn.execute(
            """
            UPDATE backfill_jobs
            SET status = %s,
                finished_at = CASE
                  WHEN %s = 'succeeded' THEN now()
                  ELSE finished_at
                END,
                next_retry_at = NULL,
                lease_owner = CASE
                  WHEN %s IN ('succeeded', 'paused') THEN NULL ELSE lease_owner
                END,
                lease_expires_at = CASE
                  WHEN %s IN ('succeeded', 'paused') THEN NULL ELSE lease_expires_at
                END,
                updated_at = now()
            WHERE id = %s
            """,
            (status, status, status, status, job_id),
        )


def _jsonable(value: object) -> Any:
    return json.loads(
        json.dumps(value, default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _checksum(value: object) -> str:
    canonical = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _response_status_from_error_code(error_code: str | None) -> int | None:
    if error_code is None:
        return None
    for status in (418, 429):
        if str(status) in error_code:
            return status
    return None


def _manifest_outcome_from_error_code(error_code: str | None) -> str:
    response_status = _response_status_from_error_code(error_code)
    if response_status == 429:
        return "rate_limited"
    if response_status == 418:
        return "blocked"
    return "failed"


def _candle_interval(candle_unit: str) -> timedelta:
    return timedelta(minutes=1) if candle_unit == "1m" else timedelta(days=1)


def _contiguous_candle_ranges(
    starts: list[datetime], candle_unit: str
) -> list[tuple[datetime, datetime, int]]:
    interval = _candle_interval(candle_unit)
    ordered = sorted(set(starts))
    if not ordered:
        return []
    ranges: list[tuple[datetime, datetime, int]] = []
    range_start = ordered[0]
    previous = ordered[0]
    row_count = 1
    for current in ordered[1:]:
        if current == previous + interval:
            previous = current
            row_count += 1
            continue
        ranges.append((range_start, previous + interval, row_count))
        range_start = current
        previous = current
        row_count = 1
    ranges.append((range_start, previous + interval, row_count))
    return ranges


def _latest_snapshot_id(conn: psycopg.Connection[Any]) -> int:
    row = conn.execute(
        "SELECT id FROM candidate_universe_snapshots ORDER BY ranked_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise ValueError("수집 후보군 스냅샷이 없다.")
    return int(row["id"])


def _expect_row(row: Row | None) -> Row:
    if row is None:
        raise RuntimeError("PostgreSQL query did not return a required row.")
    return row


def _instrument(row: Row) -> Instrument:
    return Instrument(
        id=int(row["id"]),
        exchange="UPBIT",
        market_code=str(row["market_code"]),
        quote_currency=str(row["quote_currency"]),
        base_asset=str(row["base_asset"]),
        display_name=str(row["display_name"]),
    )


def _instrument_by_id(conn: psycopg.Connection[Any], instrument_id: int) -> Instrument | None:
    row = conn.execute("SELECT * FROM instruments WHERE id = %s", (instrument_id,)).fetchone()
    return _instrument(row) if row is not None else None


def _progress_decimal(value: object) -> Decimal:
    return Decimal(str(value or "0")).normalize()


def _ticker(row: dict[str, Any]) -> TickerSnapshot:
    return TickerSnapshot(
        instrument_id=int(row["instrument_id"]),
        bucket_at=cast(datetime, row["bucket_at"]),
        trade_price=Decimal(row["trade_price"]),
        acc_trade_price_24h=Decimal(row["acc_trade_price_24h"]),
        change_rate=Decimal(row["change_rate"] or "0"),
        occurred_at=cast(datetime, row.get("occurred_at") or row["bucket_at"]),
        received_at=cast(datetime, row.get("received_at") or row["collected_at"]),
    )


def _orderbook(row: dict[str, Any]) -> OrderbookSummary:
    return OrderbookSummary(
        instrument_id=int(row["instrument_id"]),
        bucket_at=cast(datetime, row["bucket_at"]),
        best_bid_price=Decimal(row["best_bid_price"]),
        best_bid_size=Decimal(row["best_bid_size"]),
        best_ask_price=Decimal(row["best_ask_price"]),
        best_ask_size=Decimal(row["best_ask_size"]),
        spread=Decimal(row["spread"]),
        bid_depth_10=Decimal(row["bid_depth_10"]),
        ask_depth_10=Decimal(row["ask_depth_10"]),
        imbalance_10=Decimal(row["imbalance_10"]),
        occurred_at=cast(datetime, row.get("occurred_at") or row["bucket_at"]),
        received_at=cast(datetime, row.get("received_at") or row["collected_at"]),
    )


def _candle(row: dict[str, Any]) -> SourceCandle:
    return SourceCandle(
        instrument_id=int(row["instrument_id"]),
        candle_unit=cast(Literal["1m", "1d"], row["candle_unit"]),
        candle_start_at=cast(datetime, row["candle_start_at"]),
        open_price=Decimal(row["open_price"]),
        high_price=Decimal(row["high_price"]),
        low_price=Decimal(row["low_price"]),
        close_price=Decimal(row["close_price"]),
        trade_volume=Decimal(row["trade_volume"]),
        trade_amount=Decimal(row["trade_amount"]),
        collected_at=cast(datetime, row["collected_at"]),
    )


def _candle_view(item: SourceCandle) -> CandleView:
    return CandleView(
        started_at=item.candle_start_at,
        open=item.open_price,
        high=item.high_price,
        low=item.low_price,
        close=item.close_price,
        volume=item.trade_volume,
        trade_amount=item.trade_amount,
        completeness="complete",
    )


def _derive_candles(unit: str, source: list[SourceCandle]) -> list[CandleView]:
    minute_units = {"3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "60m": 60, "240m": 240}
    bucket_size = minute_units.get(unit)
    source_1m = [item for item in source if item.candle_unit == "1m"]
    if unit == "1d":
        direct_daily = [item for item in source if item.candle_unit == "1d"]
        if direct_daily:
            return [_candle_view(item) for item in direct_daily]
        grouped_daily: dict[datetime, list[SourceCandle]] = {}
        for item in source_1m:
            bucket = item.candle_start_at.replace(hour=0, minute=0, second=0, microsecond=0)
            grouped_daily.setdefault(bucket, []).append(item)
        return _aggregate_candle_groups(grouped_daily, 24 * 60)
    if unit in {"1w", "1M"}:
        daily_groups: dict[datetime, list[SourceCandle]] = {}
        for item in [item for item in source if item.candle_unit == "1d"]:
            if unit == "1w":
                week_start = item.candle_start_at - timedelta(days=item.candle_start_at.weekday())
                bucket = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                bucket = item.candle_start_at.replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )
            daily_groups.setdefault(bucket, []).append(item)
        return _aggregate_candle_groups(daily_groups, 7 if unit == "1w" else 0)
    if bucket_size is None:
        return [_candle_view(item) for item in source]
    grouped: dict[datetime, list[SourceCandle]] = {}
    for item in source_1m:
        minute = item.candle_start_at.minute - (item.candle_start_at.minute % bucket_size)
        bucket = item.candle_start_at.replace(minute=minute, second=0, microsecond=0)
        grouped.setdefault(bucket, []).append(item)
    return _aggregate_candle_groups(grouped, bucket_size)


def _aggregate_candle_groups(
    grouped: dict[datetime, list[SourceCandle]], expected_size: int
) -> list[CandleView]:
    result: list[CandleView] = []
    for bucket, items in sorted(grouped.items()):
        ordered = sorted(items, key=lambda item: item.candle_start_at)
        required_size = (
            monthrange(bucket.year, bucket.month)[1] if expected_size == 0 else expected_size
        )
        result.append(
            CandleView(
                started_at=bucket,
                open=ordered[0].open_price,
                high=max(item.high_price for item in ordered),
                low=min(item.low_price for item in ordered),
                close=ordered[-1].close_price,
                volume=sum((item.trade_volume for item in ordered), Decimal("0")),
                trade_amount=sum((item.trade_amount for item in ordered), Decimal("0")),
                completeness="complete" if len(ordered) == required_size else "partial",
            )
        )
    return result


def _rollup_candle(row: Row) -> CandleView:
    return CandleView(
        started_at=cast(datetime, row["candle_start_at"]),
        open=Decimal(row["open_price"]),
        high=Decimal(row["high_price"]),
        low=Decimal(row["low_price"]),
        close=Decimal(row["close_price"]),
        volume=Decimal(row["trade_volume"]),
        trade_amount=Decimal(row["trade_amount"]),
        completeness=cast(Literal["complete", "partial", "empty"], row["completeness"]),
    )


def _candle_aggregation_target(row: Row) -> CandleAggregationJobTarget:
    return CandleAggregationJobTarget(
        job_id=int(row["job_id"]),
        instrument_id=int(row["instrument_id"]),
        candle_unit=str(row["candle_unit"]),
        status=cast(Literal["pending", "running", "succeeded", "failed"], row["status"]),
        rows_written=int(row["rows_written"]),
    )


def _collection_run(row: dict[str, Any]) -> CollectionRun:
    return CollectionRun(
        id=int(row["id"]),
        run_type=str(row["run_type"]),
        data_type=str(row["data_type"]),
        status=cast(
            Literal["running", "succeeded", "partial", "failed", "cancelled"],
            row["status"],
        ),
        started_at=cast(datetime, row["started_at"]),
        finished_at=cast(datetime | None, row["finished_at"]),
    )


def _notification(row: dict[str, Any]) -> NotificationEvent:
    return NotificationEvent(
        id=int(row["id"]),
        severity=cast(Literal["info", "warning", "error", "critical"], row["severity"]),
        event_type=str(row["event_type"]),
        title=str(row["title"]),
        message=str(row["message"]),
        status=cast(Literal["open", "acknowledged", "resolved"], row["status"]),
        created_at=cast(datetime, row["created_at"]),
    )


def _backfill_job(
    row: dict[str, Any],
    targets: list[Instrument] | None = None,
    current_target: Instrument | None = None,
) -> BackfillJob:
    return BackfillJob(
        id=int(row["id"]),
        status=cast(
            Literal[
                "planned",
                "pending",
                "leased",
                "running",
                "retry_wait",
                "paused",
                "stopped",
                "succeeded",
                "failed",
                "dead_letter",
                "cancelled",
            ],
            row["status"],
        ),
        data_type=str(row["data_type"]),
        progress_percent=_progress_decimal(row.get("progress_percent")),
        estimated_request_count=int(row.get("estimated_request_count") or 0),
        total_target_count=int(row.get("total_target_count") or 0),
        completed_target_count=int(row.get("completed_target_count") or 0),
        running_target_index=(
            int(row["running_target_index"])
            if row.get("running_target_index") is not None
            else None
        ),
        current_target=current_target,
        current_target_backfill_row_count=int(row.get("current_target_backfill_row_count") or 0),
        processed_missing_range_count=int(row.get("processed_missing_range_count") or 0),
        estimated_missing_range_count=int(row.get("estimated_missing_range_count") or 0),
        target_start_at=cast(datetime, row["target_start_at"]),
        target_end_at=cast(datetime, row["target_end_at"]),
        targets=targets or [],
        created_at=cast(datetime, row["created_at"]),
        attempt_count=int(row.get("attempt_count") or 0),
        max_attempts=int(row.get("max_attempts") or 0),
        next_retry_at=cast(datetime | None, row.get("next_retry_at")),
        last_error_code=cast(str | None, row.get("last_error_code")),
        dead_letter_reason=cast(str | None, row.get("dead_letter_reason")),
    )


def _backfill_job_detail(row: dict[str, Any]) -> BackfillJobDetail:
    return BackfillJobDetail(
        id=int(row["id"]),
        status=cast(
            Literal[
                "planned",
                "pending",
                "leased",
                "running",
                "retry_wait",
                "paused",
                "stopped",
                "succeeded",
                "failed",
                "dead_letter",
                "cancelled",
            ],
            row["status"],
        ),
        data_type=str(row["data_type"]),
        target_start_at=cast(datetime, row["target_start_at"]),
        target_end_at=cast(datetime, row["target_end_at"]),
        estimated_request_count=int(row["estimated_request_count"]),
        estimated_row_count=int(row["estimated_row_count"]),
        created_at=cast(datetime, row["created_at"]),
    )


def _backfill_target(row: dict[str, Any]) -> BackfillJobTarget:
    return BackfillJobTarget(
        job_id=int(row["backfill_job_id"]),
        instrument_id=int(row["instrument_id"]),
        status=cast(
            Literal["pending", "running", "paused", "stopped", "succeeded", "failed"],
            row["status"],
        ),
        last_completed_at=cast(datetime | None, row["last_completed_at"]),
        error_code=cast(str | None, row["error_code"]),
        error_message=cast(str | None, row["error_message"]),
    )


def _diagnostic_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return isoformat_kst(value)
