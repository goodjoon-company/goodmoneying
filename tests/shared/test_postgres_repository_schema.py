from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from goodmoneying_shared.postgres_repository import PostgresOperationsRepository


class FakeCursor:
    def __init__(self, row: dict[str, str | None]) -> None:
        self._row = row

    def fetchone(self) -> dict[str, str | None]:
        return self._row


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, *_args: Any) -> FakeCursor:
        self.statements.append(statement)
        if "to_regclass('public.instruments')" in statement:
            return FakeCursor({"table_name": "instruments"})
        return FakeCursor({"table_name": None})


def test_postgres_repository_applies_schema_even_when_existing_tables_are_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_path = tmp_path / "schema.sql"
    schema_path.write_text("CREATE TABLE IF NOT EXISTS collection_plans (id BIGINT);\n")
    repository = PostgresOperationsRepository.__new__(PostgresOperationsRepository)
    connection = FakeConnection()
    repository._schema_path = schema_path
    monkeypatch.setattr(repository, "_connect", lambda: connection)

    repository._apply_schema_if_empty()

    assert schema_path.read_text() in connection.statements


def test_postgres_repository_serializes_schema_application_with_advisory_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_path = tmp_path / "schema.sql"
    schema_path.write_text("CREATE TABLE IF NOT EXISTS collection_runs (id BIGINT);\n")
    repository = PostgresOperationsRepository.__new__(PostgresOperationsRepository)
    connection = FakeConnection()
    repository._schema_path = schema_path
    monkeypatch.setattr(repository, "_connect", lambda: connection)

    repository._apply_schema_if_empty()

    assert connection.statements[0] == (
        "SELECT pg_advisory_lock(hashtext('goodmoneying_schema_contract'))"
    )
    assert connection.statements[1] == schema_path.read_text()
    assert connection.statements[2] == (
        "SELECT pg_advisory_unlock(hashtext('goodmoneying_schema_contract'))"
    )


def test_postgres_repository_rejects_fixture_candidate_entries_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = PostgresOperationsRepository.__new__(PostgresOperationsRepository)
    monkeypatch.setattr(
        repository,
        "_connect",
        lambda: pytest.fail("fixture 후보는 PostgreSQL 접속 전에 거부되어야 한다."),
    )

    with pytest.raises(ValueError, match="fixture"):
        repository.refresh_candidate_universe(
            [("KRW-GM006", "굿머니코인 006", "1000000000")]
        )
