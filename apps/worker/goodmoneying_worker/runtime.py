from __future__ import annotations

import logging
import os

from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_worker.upbit_client import LiveUpbitClient, UpbitClient

DEFAULT_LOG_LEVEL = "INFO"
HEARTBEAT_IO_TIMEOUT_SECONDS = 2.0


def log_level_from_environment() -> int:
    value = os.getenv("GOODMONEYING_LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
    level = logging.getLevelName(value)
    if not isinstance(level, int):
        raise ValueError(
            "GOODMONEYING_LOG_LEVEL은 DEBUG, INFO, WARNING, ERROR, CRITICAL 중 하나여야 합니다."
        )
    return level


def configure_logging_from_environment() -> None:
    logging.basicConfig(
        level=log_level_from_environment(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )


def create_repository_from_environment(database: str = ":memory:") -> OperationsRepository:
    database_url = os.getenv("GOODMONEYING_DATABASE_URL")
    if database_url and database_url.startswith(("postgres://", "postgresql://")):
        return PostgresOperationsRepository(database_url)
    return SQLiteOperationsRepository(database)


def create_heartbeat_repository_from_environment(
    source_repository: OperationsRepository | None = None,
) -> OperationsRepository:
    database_url = os.getenv("GOODMONEYING_DATABASE_URL")
    if database_url and database_url.startswith(("postgres://", "postgresql://")):
        return PostgresOperationsRepository(
            database_url,
            io_timeout_seconds=HEARTBEAT_IO_TIMEOUT_SECONDS,
        )
    database = ":memory:"
    if isinstance(source_repository, SQLiteOperationsRepository):
        database = source_repository._database_url
        if database == ":memory:":
            return source_repository
    return SQLiteOperationsRepository(
        database,
        busy_timeout_seconds=HEARTBEAT_IO_TIMEOUT_SECONDS,
    )


def create_upbit_client_from_environment() -> UpbitClient:
    if os.getenv("GOODMONEYING_LIVE_UPBIT") == "1":
        return LiveUpbitClient()
    raise RuntimeError(
        "운영 수집 런타임은 GOODMONEYING_LIVE_UPBIT=1 live 프로필만 허용한다. "
        "fixture 데이터는 테스트에서 클라이언트를 직접 주입할 때만 사용할 수 있다."
    )
