from __future__ import annotations

from pathlib import Path

import yaml

from goodmoneying_shared.time import KST, isoformat_kst, minute_bucket


def test_shared_time_helpers_use_kst() -> None:
    value = minute_bucket("2026-01-01T00:00:30+09:00")

    assert value.tzinfo == KST
    assert value.isoformat() == "2026-01-01T00:00:00+09:00"
    assert isoformat_kst(value) == "2026-01-01T00:00:00+09:00"


def test_db_contract_declares_kst_timezone() -> None:
    schema = Path("docs/contracts/db/schema.sql").read_text()

    assert "SET TIME ZONE 'Asia/Seoul';" in schema
    assert "ALTER DATABASE goodmoneying SET timezone TO 'Asia/Seoul';" in schema


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
