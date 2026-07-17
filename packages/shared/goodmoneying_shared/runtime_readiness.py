from __future__ import annotations

from typing import Any

import psycopg

LATEST_P1_MIGRATION = "20260717000700"
RUNTIME_WRITE_TABLES = (
    "instruments",
    "markets",
    "market_status_history",
    "collection_policies",
    "collection_target_specs",
    "collection_subscription_desires",
    "collection_targets",
    "collection_plans",
    "collection_worker_heartbeats",
    "collection_runs",
    "target_collection_results",
    "backfill_jobs",
    "backfill_job_targets",
    "fetch_manifests",
    "source_receipts",
    "source_candles",
    "trade_events",
    "ticker_snapshots",
    "orderbook_snapshots",
    "orderbook_snapshot_levels",
    "orderbook_summaries",
    "candle_aggregation_jobs",
    "candle_aggregation_job_targets",
    "candle_rollups",
    "coverage_intervals",
    "data_quality_events",
    "audit_logs",
    "command_idempotency_records",
)
RUNTIME_DELETE_TABLES = frozenset(
    {"source_receipts", "orderbook_snapshots", "orderbook_snapshot_levels"}
)


def assert_p1_runtime_ready(connection: psycopg.Connection[Any]) -> None:
    version = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = %s",
        (LATEST_P1_MIGRATION,),
    ).fetchone()
    if version is None:
        raise RuntimeError("P1 최신 DB 마이그레이션이 적용되지 않았다.")
    recovery = connection.execute(
        """
        SELECT recovery_required, confirmed_at
        FROM p1_audit_recovery_gate
        WHERE singleton
        """
    ).fetchone()
    if recovery is None:
        raise RuntimeError("P1 감사 복구 게이트가 없다.")
    recovery_required = bool(recovery["recovery_required"])
    if recovery_required and recovery["confirmed_at"] is None:
        raise RuntimeError("P1 감사 백업 비교와 복구 확인이 완료되지 않았다.")
    for table in RUNTIME_WRITE_TABLES:
        privileges = connection.execute(
            """
            SELECT has_table_privilege(current_user, %s, 'SELECT') AS can_read,
                   has_table_privilege(current_user, %s, 'INSERT')
                     AND has_table_privilege(current_user, %s, 'UPDATE') AS can_write,
                   has_table_privilege(current_user, %s, 'DELETE') AS can_delete
            """,
            (table, table, table, table),
        ).fetchone()
        if (
            privileges is None
            or not privileges["can_read"]
            or not privileges["can_write"]
            or (table in RUNTIME_DELETE_TABLES and not privileges["can_delete"])
        ):
            raise RuntimeError(f"P1 런타임 테이블 권한이 부족하다: {table}")
    sequence_privileges = connection.execute(
        """
        SELECT COALESCE(bool_and(
          has_sequence_privilege(
            current_user,
            quote_ident(schemaname) || '.' || quote_ident(sequencename),
            'USAGE'
          )
        ), true) AS can_use_sequences
        FROM pg_sequences
        WHERE schemaname = current_schema()
        """
    ).fetchone()
    if sequence_privileges is None or not sequence_privileges["can_use_sequences"]:
        raise RuntimeError("P1 런타임 identity sequence 권한이 부족하다.")
