from __future__ import annotations

import os

from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_worker.upbit_client import FixtureUpbitClient, LiveUpbitClient, UpbitClient


def create_repository_from_environment(database: str = ":memory:") -> OperationsRepository:
    database_url = os.getenv("GOODMONEYING_DATABASE_URL")
    if database_url and database_url.startswith(("postgres://", "postgresql://")):
        return PostgresOperationsRepository(database_url)
    return SQLiteOperationsRepository(database)


def create_upbit_client_from_environment() -> UpbitClient:
    if os.getenv("GOODMONEYING_LIVE_UPBIT") == "1":
        return LiveUpbitClient()
    return FixtureUpbitClient()
