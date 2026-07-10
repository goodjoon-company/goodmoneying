from __future__ import annotations

import argparse
import threading
from collections.abc import Callable
from functools import wraps
from typing import Any, cast

import uvicorn
from fastapi import FastAPI

from goodmoneying_api.main import create_app
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_worker.collector import seed_repository
from goodmoneying_worker.upbit_client import FixtureUpbitClient


class _SerializedOperationsRepository:
    def __init__(self, repository: OperationsRepository) -> None:
        self._repository = repository
        self._lock = threading.RLock()

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._repository, name)
        if not callable(attribute):
            return attribute

        callable_attribute = cast(Callable[..., Any], attribute)

        @wraps(callable_attribute)
        def synchronized(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                return callable_attribute(*args, **kwargs)

        return synchronized


def create_seeded_e2e_app() -> FastAPI:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    serialized_repository = cast(
        OperationsRepository,
        _SerializedOperationsRepository(repository),
    )
    return create_app(serialized_repository)


def main() -> None:
    parser = argparse.ArgumentParser(description="격리된 goodmoneying E2E API 서버")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18000)
    args = parser.parse_args()
    uvicorn.run(create_seeded_e2e_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
