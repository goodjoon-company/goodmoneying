from __future__ import annotations

import ast
import re
from itertools import product
from pathlib import Path
from typing import Any, cast

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
                r"CREATE TABLE(?: IF NOT EXISTS)? ([a-z_][a-z0-9_]*)",
                migration.read_text(),
                flags=re.IGNORECASE,
            )
        )
    return tables


def _sql_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    collector = _SqlLiteralCollector(tree)
    collector.visit(tree)
    return collector.literals


class _SqlLiteralCollector(ast.NodeVisitor):
    def __init__(self, tree: ast.Module) -> None:
        self.literals: list[str] = []
        self._bindings: dict[str, tuple[str, ...]] = {}
        self._call_bindings = _literal_call_bindings(tree)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.literals.append(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        previous = self._bindings
        self._bindings = {
            **previous,
            **self._call_bindings.get(node.name, {}),
        }
        for statement in node.body:
            self.visit(statement)
        self._bindings = previous

    def visit_For(self, node: ast.For) -> None:
        loop_bindings = _literal_loop_bindings(node)
        if not loop_bindings:
            self.generic_visit(node)
            return
        previous = self._bindings
        for binding in loop_bindings:
            self._bindings = {**previous, **binding}
            for statement in node.body:
                self.visit(statement)
        self._bindings = previous
        for statement in node.orelse:
            self.visit(statement)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        parts: list[tuple[str, ...]] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append((value.value,))
            elif isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name):
                parts.append(self._bindings.get(value.value.id, (" ",)))
            else:
                parts.append((" ",))
        self.literals.extend("".join(values) for values in product(*parts))


def _literal_call_bindings(tree: ast.Module) -> dict[str, dict[str, tuple[str, ...]]]:
    parameters: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        names = [argument.arg for argument in node.args.args]
        if names and names[0] in {"self", "cls"}:
            names = names[1:]
        parameters[node.name] = names
    values: dict[str, dict[str, set[str]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function_name = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else node.func.id
            if isinstance(node.func, ast.Name)
            else None
        )
        if function_name not in parameters:
            continue
        for name, argument in zip(parameters[function_name], node.args, strict=False):
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                values.setdefault(function_name, {}).setdefault(name, set()).add(argument.value)
    return {
        function_name: {
            name: tuple(sorted(literal_values)) for name, literal_values in bindings.items()
        }
        for function_name, bindings in values.items()
    }


def _literal_loop_bindings(node: ast.For) -> list[dict[str, tuple[str, ...]]]:
    if not isinstance(node.target, (ast.Tuple, ast.List)) or not isinstance(
        node.iter, (ast.Tuple, ast.List)
    ):
        return []
    names = [element.id if isinstance(element, ast.Name) else None for element in node.target.elts]
    if any(name is None for name in names):
        return []
    bindings: list[dict[str, tuple[str, ...]]] = []
    for row in node.iter.elts:
        if not isinstance(row, (ast.Tuple, ast.List)) or len(row.elts) != len(names):
            return []
        binding: dict[str, tuple[str, ...]] = {}
        for name, value in zip(names, row.elts, strict=True):
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                assert name is not None
                binding[name] = (value.value,)
        bindings.append(binding)
    return bindings


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
            _record_sql_privileges(sql, database_tables, operations)
    operations["read"].update({"schema_migrations", "p1_audit_recovery_gate"})
    return {operation: frozenset(tables) for operation, tables in operations.items()}


def _record_sql_privileges(
    sql: str, database_tables: set[str], operations: dict[str, set[str]]
) -> None:
    relations = _sql_relations(sql, database_tables)
    operations["read"].update(table for table, _alias in relations)
    for table in re.findall(r"\bINSERT\s+INTO\s+([a-z_][a-z0-9_]*)", sql, re.IGNORECASE):
        table = table.lower()
        if table not in database_tables:
            continue
        operations["insert"].add(table)
        conflict = re.search(
            r"\bON\s+CONFLICT\b(?P<target>[\s\S]*?)\bDO\s+"
            r"(?P<action>NOTHING|UPDATE)\b",
            sql,
            re.IGNORECASE,
        )
        if re.search(r"\bRETURNING\b", sql, re.IGNORECASE) or (
            conflict is not None
            and (
                conflict.group("action").upper() == "UPDATE"
                or bool(conflict.group("target").strip())
            )
        ):
            operations["read"].add(table)
        if conflict is not None and conflict.group("action").upper() == "UPDATE":
            operations["update"].add(table)
    for table in re.findall(r"\bUPDATE\s+([a-z_][a-z0-9_]*)", sql, re.IGNORECASE):
        if table.lower() in database_tables:
            operations["update"].add(table.lower())
            operations["read"].add(table.lower())
    for table in re.findall(r"\bDELETE\s+FROM\s+([a-z_][a-z0-9_]*)", sql, re.IGNORECASE):
        if table.lower() in database_tables:
            operations["delete"].add(table.lower())
            operations["read"].add(table.lower())
    lock = re.search(r"\bFOR\s+(?:NO\s+KEY\s+)?UPDATE\b", sql, re.IGNORECASE)
    if lock is None:
        return
    locked_names = re.search(
        r"\bFOR\s+(?:NO\s+KEY\s+)?UPDATE\s+OF\s+"
        r"([a-z_][a-z0-9_]*(?:\s*,\s*[a-z_][a-z0-9_]*)*)",
        sql,
        re.IGNORECASE,
    )
    aliases = {alias: table for table, alias in relations}
    aliases.update({table: table for table, _alias in relations})
    if locked_names is None:
        operations["update"].update(table for table, _alias in relations)
        return
    operations["update"].update(
        aliases[name.lower()]
        for name in re.split(r"\s*,\s*", locked_names.group(1))
        if name.lower() in aliases
    )


def _sql_relations(sql: str, database_tables: set[str]) -> list[tuple[str, str]]:
    keyword_aliases = {
        "cross",
        "for",
        "full",
        "group",
        "inner",
        "join",
        "left",
        "limit",
        "on",
        "order",
        "outer",
        "right",
        "where",
    }
    relations: list[tuple[str, str]] = []
    for match in re.finditer(
        r"\b(?:FROM|JOIN)\s+(?P<table>[a-z_][a-z0-9_]*)"
        r"(?:\s+(?:AS\s+)?(?P<alias>[a-z_][a-z0-9_]*))?",
        sql,
        re.IGNORECASE,
    ):
        table = match.group("table").lower()
        if table not in database_tables:
            continue
        candidate = (match.group("alias") or table).lower()
        alias = table if candidate in keyword_aliases else candidate
        relations.append((table, alias))
    return relations


def test_runtime_privilege_sets_match_every_postgres_sql_target() -> None:
    expected = _runtime_sql_privileges()

    assert getattr(runtime_readiness, "RUNTIME_READ_TABLES", None) == expected["read"]
    assert getattr(runtime_readiness, "RUNTIME_INSERT_TABLES", None) == expected["insert"]
    assert getattr(runtime_readiness, "RUNTIME_UPDATE_TABLES", None) == expected["update"]
    assert getattr(runtime_readiness, "RUNTIME_DELETE_TABLES", None) == expected["delete"]


@pytest.mark.parametrize(
    ("sql", "table"),
    [
        ("INSERT INTO fetch_manifests (source) VALUES ('UPBIT') RETURNING id", "fetch_manifests"),
        (
            "INSERT INTO instruments (market_code) VALUES ('KRW-BTC') "
            "ON CONFLICT (market_code) DO UPDATE SET status = excluded.status",
            "instruments",
        ),
        ("UPDATE backfill_jobs SET status = 'running' WHERE id = 1", "backfill_jobs"),
        ("DELETE FROM coverage_intervals WHERE id = 1 RETURNING id", "coverage_intervals"),
    ],
)
def test_postgres_implicit_select_targets_are_classified_as_read(sql: str, table: str) -> None:
    operations: dict[str, set[str]] = {
        operation: set() for operation in ("read", "insert", "update", "delete")
    }

    _record_sql_privileges(sql, {table}, operations)

    assert table in operations["read"]


@pytest.mark.parametrize(
    ("sql", "expected_tables"),
    [
        (
            "SELECT * FROM coverage_intervals WHERE id = 1 FOR UPDATE",
            {"coverage_intervals"},
        ),
        (
            "SELECT * FROM coverage_intervals WHERE id = 1 FOR NO KEY UPDATE",
            {"coverage_intervals"},
        ),
        (
            "SELECT job.id FROM backfill_jobs AS job "
            "JOIN collection_targets target ON target.id = job.id "
            "FOR UPDATE OF job, target",
            {"backfill_jobs", "collection_targets"},
        ),
    ],
)
def test_postgres_row_lock_targets_are_classified_as_update(
    sql: str, expected_tables: set[str]
) -> None:
    operations: dict[str, set[str]] = {
        operation: set() for operation in ("read", "insert", "update", "delete")
    }

    _record_sql_privileges(
        sql,
        {"coverage_intervals", "backfill_jobs", "collection_targets"},
        operations,
    )

    assert operations["update"] == expected_tables


@pytest.mark.parametrize(
    ("suffix", "requires_read"),
    [
        ("ON CONFLICT DO NOTHING", False),
        ("ON CONFLICT (fingerprint) DO NOTHING", True),
        ("ON CONFLICT DO UPDATE SET evidence = excluded.evidence", True),
        ("RETURNING id", True),
    ],
)
def test_insert_read_classification_distinguishes_unqualified_do_nothing(
    suffix: str, requires_read: bool
) -> None:
    operations: dict[str, set[str]] = {
        operation: set() for operation in ("read", "insert", "update", "delete")
    }

    _record_sql_privileges(
        f"INSERT INTO data_quality_events (fingerprint) VALUES ('same') {suffix}",
        {"data_quality_events"},
        operations,
    )

    assert ("data_quality_events" in operations["read"]) is requires_read


def test_dynamic_runtime_sql_literals_expand_each_known_table_binding() -> None:
    literals = _sql_literals(Path("packages/shared/goodmoneying_shared/postgres_repository.py"))

    for table in ("source_candles", "ticker_snapshots", "orderbook_summaries"):
        assert any(
            f"FROM {table}" in statement and "date_trunc('day'" in statement
            for statement in literals
        ), table
        assert any(f"COUNT(*) AS count FROM {table}" in statement for statement in literals), table


def test_all_runtime_returning_update_and_delete_targets_require_read() -> None:
    expected = _runtime_sql_privileges()

    assert "fetch_manifests" in expected["read"]
    assert expected["update"] <= expected["read"]
    assert expected["delete"] <= expected["read"]


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


def test_sequence_readiness_checks_only_sequences_owned_by_insert_tables() -> None:
    connection = _ReadyConnection(
        {
            "recovery_required": False,
            "confirmed_at": None,
            "confirmed_by": None,
            "backup_reference": None,
        }
    )

    runtime_readiness.assert_p1_runtime_ready(connection)  # type: ignore[arg-type]

    assert connection.sequence_query is not None
    assert "FROM pg_class AS sequence" in connection.sequence_query
    assert "JOIN pg_depend AS dependency" in connection.sequence_query
    assert "pg_sequences" not in connection.sequence_query
    assert connection.sequence_tables == runtime_readiness.RUNTIME_INSERT_TABLES
    assert connection.sequence_tables.isdisjoint(
        {
            "collection_coverage_segments",
            "collection_coverage_snapshots",
            "missing_ranges",
            "raw_response_samples",
        }
    )


class _Result:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any]:
        return self._row


class _ReadyConnection:
    def __init__(self, recovery: dict[str, Any]) -> None:
        self._recovery = recovery
        self.sequence_query: str | None = None
        self.sequence_tables: frozenset[str] = frozenset()

    def execute(self, query: str, params: tuple[object, ...] | None = None) -> _Result:
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
        if "has_sequence_privilege" in query:
            self.sequence_query = query
            if params:
                self.sequence_tables = frozenset(cast(list[str], params[0]))
            return _Result({"can_use_sequences": True})
        raise AssertionError(f"예상하지 못한 준비성 SQL: {query}")
