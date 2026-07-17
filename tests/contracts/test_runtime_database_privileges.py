from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

from goodmoneying_shared import runtime_readiness

RUNTIME_SQL_SOURCES = (
    Path("packages/shared/goodmoneying_shared/postgres_repository.py"),
    Path("packages/shared/goodmoneying_shared/data_foundation_repository.py"),
    Path("packages/shared/goodmoneying_shared/coverage_transition.py"),
)
MIGRATIONS_DIR = Path("docs/contracts/db/migrations")


def _database_tables() -> set[str]:
    tables: set[str] = set()
    for migration in MIGRATIONS_DIR.glob("*.sql"):
        tables.update(
            table.lower()
            for table in re.findall(
                r"CREATE TABLE IF NOT EXISTS ([a-z_][a-z0-9_]*)",
                migration.read_text(),
                flags=re.IGNORECASE,
            )
        )
    return tables


def _sql_literals(path: Path) -> list[str]:
    literals: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            literals.append(
                "".join(
                    value.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str)
                    else " "
                    for value in node.values
                )
            )
    return literals


def _runtime_sql_privileges() -> dict[str, frozenset[str]]:
    database_tables = _database_tables()
    operations: dict[str, set[str]] = {
        "read": set(),
        "insert": set(),
        "update": set(),
        "delete": set(),
    }
    for source in RUNTIME_SQL_SOURCES:
        for sql in _sql_literals(source):
            for table in re.findall(
                r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)", sql, re.IGNORECASE
            ):
                if table.lower() in database_tables:
                    operations["read"].add(table.lower())
            for table in re.findall(
                r"\bINSERT\s+INTO\s+([a-z_][a-z0-9_]*)", sql, re.IGNORECASE
            ):
                table = table.lower()
                if table not in database_tables:
                    continue
                operations["insert"].add(table)
                if re.search(
                    r"ON\s+CONFLICT[\s\S]*?DO\s+UPDATE", sql, re.IGNORECASE
                ):
                    operations["update"].add(table)
            for table in re.findall(
                r"\bUPDATE\s+([a-z_][a-z0-9_]*)", sql, re.IGNORECASE
            ):
                if table.lower() in database_tables:
                    operations["update"].add(table.lower())
            for table in re.findall(
                r"\bDELETE\s+FROM\s+([a-z_][a-z0-9_]*)", sql, re.IGNORECASE
            ):
                if table.lower() in database_tables:
                    operations["delete"].add(table.lower())
    operations["read"].update({"schema_migrations", "p1_audit_recovery_gate"})
    return {operation: frozenset(tables) for operation, tables in operations.items()}


def test_runtime_privilege_sets_match_every_postgres_sql_target() -> None:
    expected = _runtime_sql_privileges()

    assert getattr(runtime_readiness, "RUNTIME_READ_TABLES", None) == expected["read"]
    assert getattr(runtime_readiness, "RUNTIME_INSERT_TABLES", None) == expected["insert"]
    assert getattr(runtime_readiness, "RUNTIME_UPDATE_TABLES", None) == expected["update"]
    assert getattr(runtime_readiness, "RUNTIME_DELETE_TABLES", None) == expected["delete"]


@pytest.mark.parametrize(
    ("confirmed_by", "backup_reference"),
    [(None, "backup://verified"), ("operator", None), ("   ", "backup://verified")],
)
def test_runtime_readiness_rejects_incomplete_recovery_confirmation(
    confirmed_by: str | None, backup_reference: str | None
) -> None:
    connection = _ReadyConnection(
        {
            "recovery_required": True,
            "confirmed_at": "2026-07-17T00:00:00Z",
            "confirmed_by": confirmed_by,
            "backup_reference": backup_reference,
        }
    )

    with pytest.raises(RuntimeError, match="감사 백업 비교와 복구 확인"):
        runtime_readiness.assert_p1_runtime_ready(connection)  # type: ignore[arg-type]


def test_runtime_readiness_accepts_complete_confirmation_and_exact_privileges() -> None:
    connection = _ReadyConnection(
        {
            "recovery_required": True,
            "confirmed_at": "2026-07-17T00:00:00Z",
            "confirmed_by": "operator",
            "backup_reference": "backup://verified",
        }
    )

    runtime_readiness.assert_p1_runtime_ready(connection)  # type: ignore[arg-type]


class _Result:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any]:
        return self._row


class _ReadyConnection:
    def __init__(self, recovery: dict[str, Any]) -> None:
        self._recovery = recovery

    def execute(
        self, query: str, params: tuple[object, ...] | None = None
    ) -> _Result:
        del params
        if "schema_migrations" in query:
            return _Result({"version": 1})
        if "p1_audit_recovery_gate" in query:
            return _Result(self._recovery)
        if "has_table_privilege" in query:
            return _Result(
                {
                    "can_read": True,
                    "can_insert": True,
                    "can_update": True,
                    "can_delete": True,
                }
            )
        if "pg_sequences" in query:
            return _Result({"can_use_sequences": True})
        raise AssertionError(f"예상하지 못한 준비성 SQL: {query}")
