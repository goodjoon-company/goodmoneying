from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from goodmoneying_shared.postgres_repository import PostgresOperationsRepository


def test_postgres_repositories_share_one_coverage_transition_implementation() -> None:
    operation_source = Path(
        "packages/shared/goodmoneying_shared/postgres_repository.py"
    ).read_text()
    foundation_source = Path(
        "packages/shared/goodmoneying_shared/data_foundation_repository.py"
    ).read_text()
    shared_import = (
        "from goodmoneying_shared.coverage_transition import "
        "replace_coverage_with_classification"
    )

    assert shared_import in operation_source
    assert shared_import in foundation_source
    assert "def _replace_coverage_with_classification" not in operation_source
    assert "def _replace_coverage_with_classification" not in foundation_source


def test_postgres_repository_initialization_does_not_connect_or_apply_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "goodmoneying_shared.postgres_repository.psycopg.connect",
        lambda *_args, **_kwargs: pytest.fail(
            "저장소 생성은 DB에 연결하거나 스키마를 적용하면 안 된다."
        ),
    )

    repository = PostgresOperationsRepository("postgresql://example.invalid/goodmoneying")

    assert repository._database_url == "postgresql://example.invalid/goodmoneying"
    assert not hasattr(repository, "_schema_path")
    assert not hasattr(repository, "_apply_schema_if_empty")


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


def test_postgres_heartbeat_저장소는_연결과_문장_실행을_2초로_제한한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    connection = object()

    def connect(database_url: str, **kwargs: Any) -> Any:
        captured["database_url"] = database_url
        captured.update(kwargs)
        return connection

    monkeypatch.setattr(
        "goodmoneying_shared.postgres_repository.psycopg.connect",
        connect,
    )
    repository = PostgresOperationsRepository(
        "postgresql://example.invalid/goodmoneying",
        connect_and_statement_timeout_seconds=2.0,
    )

    assert repository._connect() is connection
    assert captured["connect_timeout"] == 2
    assert "statement_timeout=2000" in captured["options"]


def test_postgres_dashboard_24시간_실시간_row_집계는_병렬_쿼리를_끄고_실행한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CapturingConnection:
        def __init__(self) -> None:
            self.statements: list[str] = []
            self.params: list[object] = []

        def __enter__(self) -> CapturingConnection:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, statement: str, _params: object = None) -> CapturingConnection:
            self.statements.append(statement)
            self.params.append(_params)
            return self

        def fetchone(self) -> dict[str, int]:
            return {"count": 17}

    connection = CapturingConnection()
    repository = PostgresOperationsRepository("postgresql://example.invalid/goodmoneying")
    monkeypatch.setattr(repository, "_connect", lambda: connection)

    row_count = repository._realtime_collected_row_count_24h()

    assert row_count == 17
    assert connection.statements[0] == "SET LOCAL max_parallel_workers_per_gather = 0"
    assert "FROM collection_runs cr" in connection.statements[1]
    assert "JOIN LATERAL" in connection.statements[1]
    assert "tcr.collection_run_id = cr.id" in connection.statements[1]
    assert "cr.run_type = 'incremental' AND cr.started_at >= %s" in connection.statements[1]
    assert "FROM target_collection_results tcr\n                JOIN collection_runs cr" not in (
        connection.statements[1]
    )
    assert connection.params[1] is not None
