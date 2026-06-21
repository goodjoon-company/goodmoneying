from __future__ import annotations

import logging
import os

from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_worker.upbit_client import FixtureUpbitClient, LiveUpbitClient, UpbitClient

DEFAULT_LOG_LEVEL = "INFO"


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


def create_upbit_client_from_environment() -> UpbitClient:
    if os.getenv("GOODMONEYING_LIVE_UPBIT") == "1":
        return LiveUpbitClient()
    return FixtureUpbitClient()
