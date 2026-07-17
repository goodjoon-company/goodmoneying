from __future__ import annotations

from typing import Any

import psycopg

LATEST_P1_MIGRATION = "20260717000800"
LATEST_PLATFORM_MIGRATION = "20260717000900"
RUNTIME_READ_TABLES = frozenset(
    {
        "backfill_job_targets",
        "backfill_jobs",
        "backfill_safety_gate",
        "candidate_universe_entries",
        "candidate_universe_snapshots",
        "candle_aggregation_job_targets",
        "candle_aggregation_jobs",
        "candle_rollups",
        "collection_plans",
        "collection_policies",
        "collection_runs",
        "collection_subscription_desires",
        "collection_target_changes",
        "collection_target_specs",
        "collection_targets",
        "collection_worker_heartbeats",
        "command_idempotency_records",
        "coverage_intervals",
        "fetch_manifests",
        "instruments",
        "market_status_history",
        "markets",
        "notification_events",
        "orderbook_snapshots",
        "orderbook_summaries",
        "p1_audit_recovery_gate",
        "schema_migrations",
        "source_candles",
        "source_candle_revisions",
        "source_receipts",
        "target_collection_results",
        "ticker_snapshots",
        "trade_events",
    }
)
RUNTIME_INSERT_TABLES = frozenset(
    {
        "audit_logs",
        "backfill_job_targets",
        "backfill_jobs",
        "candidate_universe_entries",
        "candidate_universe_snapshots",
        "candle_aggregation_job_targets",
        "candle_aggregation_jobs",
        "candle_rollups",
        "collection_plans",
        "collection_policies",
        "collection_runs",
        "collection_subscription_desires",
        "collection_target_changes",
        "collection_target_specs",
        "collection_targets",
        "collection_worker_heartbeats",
        "command_idempotency_records",
        "coverage_intervals",
        "data_quality_events",
        "fetch_manifests",
        "instruments",
        "market_status_history",
        "markets",
        "notification_events",
        "orderbook_snapshot_levels",
        "orderbook_snapshots",
        "orderbook_summaries",
        "source_candles",
        "source_candle_revisions",
        "source_receipts",
        "target_collection_results",
        "ticker_snapshots",
        "trade_events",
    }
)
RUNTIME_UPDATE_TABLES = frozenset(
    {
        "backfill_job_targets",
        "backfill_jobs",
        "candle_aggregation_job_targets",
        "candle_aggregation_jobs",
        "candle_rollups",
        "collection_plans",
        "collection_policies",
        "collection_runs",
        "collection_subscription_desires",
        "collection_target_specs",
        "collection_targets",
        "collection_worker_heartbeats",
        "command_idempotency_records",
        "coverage_intervals",
        "fetch_manifests",
        "instruments",
        "market_status_history",
        "markets",
        "orderbook_summaries",
        "source_candles",
        "ticker_snapshots",
        "trade_events",
    }
)
RUNTIME_DELETE_TABLES = frozenset(
    {
        "backfill_jobs",
        "coverage_intervals",
        "orderbook_snapshots",
        "source_receipts",
    }
)


def assert_p1_runtime_ready(connection: psycopg.Connection[Any]) -> None:
    version = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = %s",
        (LATEST_PLATFORM_MIGRATION,),
    ).fetchone()
    if version is None:
        raise RuntimeError("최신 플랫폼 DB 마이그레이션이 적용되지 않았다.")
    recovery = connection.execute(
        """
        SELECT recovery_required, confirmed_at, confirmed_by, backup_reference
        FROM p1_audit_recovery_gate
        WHERE singleton
        """
    ).fetchone()
    if recovery is None:
        raise RuntimeError("P1 감사 복구 게이트가 없다.")
    recovery_required = bool(recovery["recovery_required"])
    recovery_confirmed = (
        recovery["confirmed_at"] is not None
        and isinstance(recovery["confirmed_by"], str)
        and bool(recovery["confirmed_by"].strip())
        and isinstance(recovery["backup_reference"], str)
        and bool(recovery["backup_reference"].strip())
    )
    if recovery_required and not recovery_confirmed:
        raise RuntimeError("P1 감사 백업 비교와 복구 확인이 완료되지 않았다.")
    runtime_tables = (
        RUNTIME_READ_TABLES | RUNTIME_INSERT_TABLES | RUNTIME_UPDATE_TABLES | RUNTIME_DELETE_TABLES
    )
    for table in sorted(runtime_tables):
        privileges = connection.execute(
            """
            SELECT has_table_privilege(current_user, %s, 'SELECT') AS can_read,
                   has_table_privilege(current_user, %s, 'INSERT') AS can_insert,
                   has_table_privilege(current_user, %s, 'UPDATE') AS can_update,
                   has_table_privilege(current_user, %s, 'DELETE') AS can_delete
            """,
            (table, table, table, table),
        ).fetchone()
        required_privileges = {
            "SELECT": table in RUNTIME_READ_TABLES,
            "INSERT": table in RUNTIME_INSERT_TABLES,
            "UPDATE": table in RUNTIME_UPDATE_TABLES,
            "DELETE": table in RUNTIME_DELETE_TABLES,
        }
        privilege_columns = {
            "SELECT": "can_read",
            "INSERT": "can_insert",
            "UPDATE": "can_update",
            "DELETE": "can_delete",
        }
        missing = [
            privilege
            for privilege, required in required_privileges.items()
            if required and (privileges is None or not privileges[privilege_columns[privilege]])
        ]
        if missing:
            raise RuntimeError(f"P1 런타임 테이블 권한이 부족하다: {table} ({', '.join(missing)})")
    sequence_privileges = connection.execute(
        """
        SELECT COALESCE(bool_and(
          has_sequence_privilege(
            current_user,
            sequence.oid,
            'USAGE'
          )
        ), true) AS can_use_sequences
        FROM pg_class AS sequence
        JOIN pg_depend AS dependency
          ON dependency.classid = 'pg_class'::regclass
         AND dependency.objid = sequence.oid
         AND dependency.refclassid = 'pg_class'::regclass
         AND dependency.deptype IN ('a', 'i')
         AND dependency.refobjsubid > 0
        JOIN pg_class AS owner_table ON owner_table.oid = dependency.refobjid
        JOIN pg_namespace AS owner_namespace
          ON owner_namespace.oid = owner_table.relnamespace
        WHERE sequence.relkind = 'S'
          AND owner_namespace.nspname = current_schema()
          AND owner_table.relname = ANY(%s)
        """,
        (sorted(RUNTIME_INSERT_TABLES),),
    ).fetchone()
    if sequence_privileges is None or not sequence_privileges["can_use_sequences"]:
        raise RuntimeError("P1 런타임 identity sequence 권한이 부족하다.")
