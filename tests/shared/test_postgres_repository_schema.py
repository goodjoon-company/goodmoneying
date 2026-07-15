from __future__ import annotations

from typing import Any

import pytest

from goodmoneying_shared.postgres_repository import PostgresOperationsRepository


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
