from __future__ import annotations

from pathlib import Path

import yaml

from goodmoneying_shared.time import KST, isoformat_kst, minute_bucket


def test_shared_time_helpers_use_kst() -> None:
    value = minute_bucket("2026-01-01T00:00:30+09:00")

    assert value.tzinfo == KST
    assert value.isoformat() == "2026-01-01T00:00:00+09:00"
    assert isoformat_kst(value) == "2026-01-01T00:00:00+09:00"


def test_db_contract_declares_kst_timezone_for_current_database() -> None:
    schema = Path(
        "docs/contracts/db/migrations/20260715000100_initial_schema.sql"
    ).read_text()
    contract_readme = Path("docs/contracts/db/README.md").read_text()

    assert "SET TIME ZONE 'Asia/Seoul';" in schema
    assert "current_database()" in schema
    assert "ALTER DATABASE %I SET timezone TO %L" in schema
    assert "ALTER DATABASE goodmoneying" not in schema
    assert "DB 소유자(database owner)" in contract_readme
    assert "current_database()" in contract_readme


def test_docker_runtime_uses_kst_timezone() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())

    assert (
        compose["services"]["postgres"]["command"]
        == ["postgres", "-c", "timezone=Asia/Seoul"]
    )
    for service_name in [
        "postgres",
        "api",
        "realtime-collection-worker",
        "backfill-collection-worker",
        "web",
    ]:
        assert compose["services"][service_name]["environment"]["TZ"] == "Asia/Seoul"

    for dockerfile in [
        Path("apps/api/Dockerfile"),
        Path("apps/worker/Dockerfile"),
        Path("apps/web/Dockerfile"),
    ]:
        contents = dockerfile.read_text()
        assert "TZ=Asia/Seoul" in contents
        assert "tzdata" in contents
