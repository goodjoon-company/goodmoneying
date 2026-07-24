#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tracemalloc
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, cast

ROOT = Path(__file__).resolve().parents[1]
for import_path in ("apps/api", "apps/worker", "apps/upbit_gateway", "packages/shared", "."):
    sys.path.insert(0, str(ROOT / import_path))
os.environ.setdefault("GOODMONEYING_RUNTIME_MODE", "test")

from fastapi.testclient import TestClient  # noqa: E402

from goodmoneying_api.main import create_app  # noqa: E402
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository  # noqa: E402
from goodmoneying_worker.collector import seed_repository  # noqa: E402
from goodmoneying_worker.upbit_client import FixtureUpbitClient  # noqa: E402

_LOAD_ENDPOINTS = (
    "/health",
    "/v1/dashboard/summary",
    "/v1/dashboard/overview",
    "/v1/dashboard/targets",
    "/v1/dashboard/coverage",
    "/v1/dashboard/operations-trend",
    "/v1/dashboard/storage-breakdown",
    "/v1/dashboard/audit-log-summary",
    "/v1/candidate-universe",
    "/v1/market-list",
)


class IntermittentDashboardRepository:
    def __init__(self, delegate: SQLiteOperationsRepository, *, failures: int) -> None:
        self._delegate = delegate
        self._remaining_failures = failures

    def dashboard_summary(self) -> Any:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise RuntimeError("P7 chaos probe injected dashboard failure")
        return self._delegate.dashboard_summary()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def run_load_probe(
    root: Path,
    *,
    request_count: int = 120,
    p95_budget_ms: float = 250.0,
    max_budget_ms: float = 1000.0,
) -> dict[str, Any]:
    _ = root
    client = _seeded_client()
    latencies_ms: list[float] = []
    failures: list[dict[str, object]] = []

    for index in range(request_count):
        endpoint = _LOAD_ENDPOINTS[index % len(_LOAD_ENDPOINTS)]
        started = time.perf_counter()
        response = client.get(endpoint)
        latency_ms = (time.perf_counter() - started) * 1000
        latencies_ms.append(latency_ms)
        if response.status_code != 200:
            failures.append({"endpoint": endpoint, "status_code": response.status_code})

    p95_ms = _percentile(latencies_ms, 95)
    max_ms = max(latencies_ms) if latencies_ms else 0.0
    ok = not failures and p95_ms <= p95_budget_ms and max_ms <= max_budget_ms
    return {
        "ok": ok,
        "profile": "local",
        "requests": request_count,
        "failures": len(failures),
        "p95_ms": round(p95_ms, 3),
        "max_ms": round(max_ms, 3),
        "p95_budget_ms": p95_budget_ms,
        "max_budget_ms": max_budget_ms,
        "failure_samples": failures[:5],
    }


def run_soak_probe(
    root: Path,
    *,
    duration_seconds: float = 10.0,
    interval_seconds: float = 0.5,
    peak_budget_mb: float = 64.0,
) -> dict[str, Any]:
    _ = root
    client = _seeded_client()
    failures: list[dict[str, object]] = []
    iterations = 0
    tracemalloc.start()
    started_current, _started_peak = tracemalloc.get_traced_memory()
    deadline = time.perf_counter() + duration_seconds
    while time.perf_counter() < deadline or iterations == 0:
        for endpoint in ("/health", "/v1/dashboard/summary"):
            response = client.get(endpoint)
            if response.status_code != 200:
                failures.append({"endpoint": endpoint, "status_code": response.status_code})
        iterations += 1
        if time.perf_counter() < deadline:
            time.sleep(interval_seconds)

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    drift_mb = (current - started_current) / 1024 / 1024
    peak_mb = peak / 1024 / 1024
    ok = not failures and peak_mb <= peak_budget_mb
    return {
        "ok": ok,
        "profile": "local",
        "duration_seconds": duration_seconds,
        "iterations": iterations,
        "failures": len(failures),
        "drift_mb": round(drift_mb, 3),
        "peak_mb": round(peak_mb, 3),
        "peak_budget_mb": peak_budget_mb,
        "failure_samples": failures[:5],
    }


def run_chaos_probe(
    root: Path,
    *,
    injected_failures: int = 1,
) -> dict[str, Any]:
    _ = root
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    flaky_repository = IntermittentDashboardRepository(repository, failures=injected_failures)
    client = TestClient(create_app(cast(Any, flaky_repository)), raise_server_exceptions=False)

    health = client.get("/health")
    first_summary = client.get("/v1/dashboard/summary")
    recovered_summary = client.get("/v1/dashboard/summary")
    recovered_trend = client.get("/v1/dashboard/operations-trend")
    statuses = [
        health.status_code,
        first_summary.status_code,
        recovered_summary.status_code,
        recovered_trend.status_code,
    ]
    observed_failures = sum(1 for status_code in statuses if status_code >= 500)
    recovered_requests = sum(1 for status_code in statuses if status_code == 200)
    ok = (
        health.status_code == 200
        and observed_failures == injected_failures
        and recovered_summary.status_code == 200
        and recovered_trend.status_code == 200
    )
    return {
        "ok": ok,
        "profile": "local",
        "injected_failures": injected_failures,
        "observed_failures": observed_failures,
        "recovered_requests": recovered_requests,
        "statuses": statuses,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="P7 resilience probe를 실행합니다.")
    parser.add_argument("probe", choices=("load", "soak", "chaos"))
    parser.add_argument("--profile", choices=("local",), default="local")
    args = parser.parse_args()

    root = Path.cwd()
    probe = cast(Literal["load", "soak", "chaos"], args.probe)
    if probe == "load":
        result = run_load_probe(root)
    elif probe == "soak":
        result = run_soak_probe(root)
    else:
        result = run_chaos_probe(root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if cast(bool, result["ok"]) else 1


def _seeded_client() -> TestClient:
    repository = SQLiteOperationsRepository()
    seed_repository(repository, FixtureUpbitClient())
    return TestClient(create_app(repository))


def _percentile(values: Sequence[float], percentile: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, int(len(sorted_values) * percentile / 100))
    return sorted_values[index]


if __name__ == "__main__":
    raise SystemExit(main())
