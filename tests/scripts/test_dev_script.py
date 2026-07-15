from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx


def run_dev_script(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "dev.sh", *args],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def install_fake_dbmate(tmp_path: Path) -> tuple[Path, Path]:
    log_file = tmp_path / "dbmate.log"
    executable = tmp_path / "dbmate"
    executable.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'args=%s\\n' \"$*\" >> \"$DEV_DBMATE_LOG\"\n"
        "printf 'database_url=%s\\n' \"$GOODMONEYING_DATABASE_URL\" >> \"$DEV_DBMATE_LOG\"\n"
        "printf 'strict=%s\\n' \"${DBMATE_STRICT:-}\" >> \"$DEV_DBMATE_LOG\"\n"
        "if [[ \"$*\" == *\" status\" && -n \"${DEV_DBMATE_STATUS:-}\" ]]; "
        "then printf '%b' \"$DEV_DBMATE_STATUS\"; fi\n"
        "exit \"${DEV_DBMATE_EXIT_CODE:-0}\"\n"
    )
    executable.chmod(0o755)
    return executable, log_file


def install_fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    log_file = tmp_path / "docker.log"
    executable = tmp_path / "docker"
    executable.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'args=%s\\n' \"$*\" >> \"$DEV_DOCKER_LOG\"\n"
        "printf 'database_url=%s\\n' \"${GOODMONEYING_DATABASE_URL:-}\" "
        ">> \"$DEV_DOCKER_LOG\"\n"
        "if [[ \"$1\" == \"info\" && \"$*\" == *\"OperatingSystem\"* ]]; then\n"
        "  printf '%s\\n' \"$DEV_DOCKER_OPERATING_SYSTEM\"\n"
        "elif [[ \"$1\" == \"info\" && \"$*\" == *\"OSType\"* ]]; then\n"
        "  printf '%s\\n' \"$DEV_DOCKER_OS_TYPE\"\n"
        "fi\n"
    )
    executable.chmod(0o755)
    return executable, log_file


def test_dev_script_without_arguments_prints_usage() -> None:
    result = run_dev_script()

    assert result.returncode == 0
    assert "사용법" in result.stdout
    assert "infra start" in result.stdout
    assert "app start" in result.stdout
    assert "db migrate" in result.stdout
    assert "upbit-gateway 단독" in result.stdout
    assert "DB 마이그레이션을 생략" in result.stdout


def test_dev_script_status_lists_infra_and_app_units() -> None:
    result = run_dev_script("status")

    assert result.returncode == 0
    assert "infra" in result.stdout
    assert "postgres" in result.stdout
    assert "app" in result.stdout
    assert "api" in result.stdout
    assert "web" in result.stdout
    assert "upbit-gateway" in result.stdout
    assert "realtime-collection-worker" in result.stdout
    assert "backfill-collection-worker" in result.stdout


def test_dev_script_rejects_unknown_command() -> None:
    result = run_dev_script("unknown")

    assert result.returncode != 0
    assert "사용법" in result.stdout


def test_dev_script_loads_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GOODMONEYING_API_PORT=19000\nGOODMONEYING_WEB_PORT=19001\n")
    env = os.environ.copy()
    env["GOODMONEYING_ENV_FILE"] = str(env_file)

    result = run_dev_script("app", "status", env=env)

    assert result.returncode == 0
    assert "endpoint=http://127.0.0.1:19000" in result.stdout
    assert "endpoint=http://127.0.0.1:19001/" in result.stdout


def test_dev_script_reports_upbit_gateway_configured_port(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GOODMONEYING_UPBIT_GATEWAY_PORT=19002\n")
    env = os.environ.copy()
    env["GOODMONEYING_ENV_FILE"] = str(env_file)

    result = run_dev_script("app", "status", "upbit-gateway", env=env)

    assert result.returncode == 0
    assert "endpoint=http://127.0.0.1:19002" in result.stdout


def test_dev_script_starts_ready_gateway_without_database_migration(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    fake_dbmate, dbmate_log = install_fake_dbmate(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "GOODMONEYING_ENV_FILE": str(tmp_path / "missing.env"),
            "GOODMONEYING_DEV_DIR": str(tmp_path / ".dev"),
            "GOODMONEYING_UPBIT_GATEWAY_PORT": str(port),
            "GOODMONEYING_PYTHON_BIN": sys.executable,
            "GOODMONEYING_DBMATE_BIN": str(fake_dbmate),
            "DEV_DBMATE_LOG": str(dbmate_log),
        }
    )
    try:
        started = run_dev_script("app", "start", "upbit-gateway", env=env)
        response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)
    finally:
        stopped = run_dev_script("app", "stop", "upbit-gateway", env=env)

    assert started.returncode == 0, started.stderr
    assert "준비 완료" in started.stdout
    assert response.status_code == 200
    assert stopped.returncode == 0
    assert not dbmate_log.exists()


def test_dev_script_shell_environment_overrides_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GOODMONEYING_API_PORT=19000\n")
    env = os.environ.copy()
    env["GOODMONEYING_ENV_FILE"] = str(env_file)
    env["GOODMONEYING_API_PORT"] = "19100"

    result = run_dev_script("app", "status", "api", env=env)

    assert result.returncode == 0
    assert "endpoint=http://127.0.0.1:19100" in result.stdout


def test_dev_script_checks_local_database_url_port(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "GOODMONEYING_DATABASE_URL=postgresql://user:password@127.0.0.1:15432/goodmoneying",
                "GOODMONEYING_POSTGRES_PORT=5432",
                "GOODMONEYING_PYTHON_BIN=/usr/bin/false",
                f"GOODMONEYING_DEV_DIR={tmp_path / '.dev'}",
            ]
        )
        + "\n"
    )
    lsof_log = tmp_path / "lsof.log"
    fake_lsof = tmp_path / "lsof"
    fake_lsof.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$DEV_LSOF_LOG\"\n"
        "[[ \" $* \" == *\" -iTCP:15432 \"* ]]\n"
    )
    fake_lsof.chmod(0o755)
    fake_dbmate, dbmate_log = install_fake_dbmate(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "GOODMONEYING_ENV_FILE": str(env_file),
            "DEV_LSOF_LOG": str(lsof_log),
            "DEV_DBMATE_LOG": str(dbmate_log),
            "GOODMONEYING_DBMATE_BIN": str(fake_dbmate),
            "PATH": f"{tmp_path}:{env['PATH']}",
        }
    )

    result = run_dev_script("app", "start", "api", env=env)

    assert result.returncode != 0
    assert "-iTCP:15432" in lsof_log.read_text()
    assert "PostgreSQL 포트 5432" not in result.stderr


def test_dev_script_does_not_require_local_port_for_remote_database_url(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "GOODMONEYING_DATABASE_URL=postgresql://user:password@db.example.test:5432/goodmoneying",
                "GOODMONEYING_POSTGRES_PORT=5432",
                "GOODMONEYING_PYTHON_BIN=/usr/bin/false",
                f"GOODMONEYING_DEV_DIR={tmp_path / '.dev'}",
            ]
        )
        + "\n"
    )
    lsof_log = tmp_path / "lsof.log"
    fake_lsof = tmp_path / "lsof"
    fake_lsof.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$DEV_LSOF_LOG\"\n"
        "exit 1\n"
    )
    fake_lsof.chmod(0o755)
    fake_dbmate, dbmate_log = install_fake_dbmate(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "GOODMONEYING_ENV_FILE": str(env_file),
            "DEV_LSOF_LOG": str(lsof_log),
            "DEV_DBMATE_LOG": str(dbmate_log),
            "GOODMONEYING_DBMATE_BIN": str(fake_dbmate),
            "PATH": f"{tmp_path}:{env['PATH']}",
        }
    )

    result = run_dev_script("app", "start", "api", env=env)

    assert result.returncode != 0
    assert "PostgreSQL 포트 5432" not in result.stderr
    assert "-iTCP:5432" not in lsof_log.read_text()


def test_dev_script_db_status_uses_env_database_url_without_exposing_secret(
    tmp_path: Path,
) -> None:
    secret = "secret-password"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GOODMONEYING_DATABASE_URL="
        f"postgresql://user:{secret}@db.example.test:5432/goodmoneying\n"
    )
    fake_dbmate, dbmate_log = install_fake_dbmate(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "GOODMONEYING_ENV_FILE": str(env_file),
            "GOODMONEYING_DBMATE_BIN": str(fake_dbmate),
            "DEV_DBMATE_LOG": str(dbmate_log),
        }
    )

    result = run_dev_script("db", "status", env=env)

    assert result.returncode == 0
    log = dbmate_log.read_text()
    assert "--env GOODMONEYING_DATABASE_URL" in log
    assert "--migrations-dir" in log
    assert "docs/contracts/db/migrations" in log
    assert "--schema-file" in log
    assert "docs/contracts/db/schema.sql" in log
    assert log.count("args=") == 1
    assert log.rstrip().splitlines()[-1] == "strict=true"
    assert log.splitlines()[0].endswith(" status")
    assert f"database_url=postgresql://user:{secret}@" in log
    assert secret not in result.stdout
    assert secret not in result.stderr


def test_dev_script_app_start_stops_before_launcher_when_migration_fails(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    python_log = tmp_path / "python.log"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$DEV_PYTHON_LOG\"\n"
    )
    fake_python.chmod(0o755)
    fake_dbmate, dbmate_log = install_fake_dbmate(tmp_path)
    env_file.write_text(
        "\n".join(
            [
                "GOODMONEYING_DATABASE_URL=postgresql://user:secret@db.example.test:5432/goodmoneying",
                f"GOODMONEYING_PYTHON_BIN={fake_python}",
                f"GOODMONEYING_DEV_DIR={tmp_path / '.dev'}",
            ]
        )
        + "\n"
    )
    env = os.environ.copy()
    env.update(
        {
            "GOODMONEYING_ENV_FILE": str(env_file),
            "GOODMONEYING_DBMATE_BIN": str(fake_dbmate),
            "DEV_DBMATE_LOG": str(dbmate_log),
            "DEV_DBMATE_EXIT_CODE": "23",
            "DEV_PYTHON_LOG": str(python_log),
        }
    )

    result = run_dev_script("app", "start", "api", env=env)

    assert result.returncode == 23
    log = dbmate_log.read_text()
    assert "--no-dump-schema migrate" in log
    assert "strict=true" in log
    assert not python_log.exists()
    assert "secret" not in result.stdout
    assert "secret" not in result.stderr


def test_dev_script_explicit_migrate_updates_schema_snapshot(tmp_path: Path) -> None:
    fake_dbmate, dbmate_log = install_fake_dbmate(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "GOODMONEYING_ENV_FILE": str(tmp_path / "missing.env"),
            "GOODMONEYING_DATABASE_URL": (
                "postgresql://user:password@db.example.test:5432/goodmoneying"
            ),
            "GOODMONEYING_DBMATE_BIN": str(fake_dbmate),
            "DEV_DBMATE_LOG": str(dbmate_log),
        }
    )

    result = run_dev_script("db", "migrate", env=env)

    assert result.returncode == 0
    log = dbmate_log.read_text()
    args = [line for line in log.splitlines() if line.startswith("args=")]
    assert len(args) == 2
    assert args[0].endswith(" --no-dump-schema migrate")
    assert args[1].endswith(" dump")
    assert log.count("strict=true") == 2


def test_dev_script_has_pinned_docker_schema_dump_fallback() -> None:
    script = Path("dev.sh").read_text()

    assert "ghcr.io/amacneil/dbmate:2.34.1" in script
    assert "command -v pg_dump" in script
    assert "docker info" in script
    assert "docker run --rm" in script
    assert "DBMATE_DOCKER_CONFIG" in script
    assert 'DOCKER_CONFIG="$DBMATE_DOCKER_CONFIG"' in script
    assert "host.docker.internal" in script
    assert "--add-host host.docker.internal:host-gateway" in script
    assert '[[ "$docker_operating_system" == "Docker Desktop" ]]' in script
    assert "docker_network_args=(--network host)" in script
    assert "GOODMONEYING_DB_SCHEMA_FILE" in script
    assert "GOODMONEYING_FORCE_DOCKER_DB_DUMP" in script
    assert "normalize_schema_snapshot" in script


def test_dev_script_uses_host_network_for_linux_docker_schema_dump(
    tmp_path: Path,
) -> None:
    fake_docker, docker_log = install_fake_docker(tmp_path)
    schema_file = tmp_path / "schema.sql"
    schema_file.write_text("-- test schema\n")
    env = os.environ.copy()
    env.update(
        {
            "GOODMONEYING_ENV_FILE": str(tmp_path / "missing.env"),
            "GOODMONEYING_DATABASE_URL": (
                "postgresql://user:password@127.0.0.1:15432/goodmoneying"
            ),
            "GOODMONEYING_DB_SCHEMA_FILE": str(schema_file),
            "GOODMONEYING_DBMATE_DOCKER_CONFIG": str(tmp_path / "docker-config"),
            "GOODMONEYING_FORCE_DOCKER_DB_DUMP": "1",
            "DEV_DOCKER_LOG": str(docker_log),
            "DEV_DOCKER_OPERATING_SYSTEM": "Ubuntu 24.04 LTS",
            "DEV_DOCKER_OS_TYPE": "linux",
            "PATH": f"{tmp_path}:{env['PATH']}",
        }
    )

    result = run_dev_script("db", "dump", env=env)

    assert result.returncode == 0, result.stderr
    log = docker_log.read_text()
    run_args = next(line for line in log.splitlines() if line.startswith("args=run "))
    assert "--network host" in run_args
    assert "--add-host" not in run_args
    assert "database_url=postgresql://user:password@127.0.0.1:15432/" in log


def test_dev_script_uses_host_gateway_for_docker_desktop_schema_dump(
    tmp_path: Path,
) -> None:
    fake_docker, docker_log = install_fake_docker(tmp_path)
    schema_file = tmp_path / "schema.sql"
    schema_file.write_text("-- test schema\n")
    env = os.environ.copy()
    env.update(
        {
            "GOODMONEYING_ENV_FILE": str(tmp_path / "missing.env"),
            "GOODMONEYING_DATABASE_URL": (
                "postgresql://user:password@127.0.0.1:15432/goodmoneying"
            ),
            "GOODMONEYING_DB_SCHEMA_FILE": str(schema_file),
            "GOODMONEYING_DBMATE_DOCKER_CONFIG": str(tmp_path / "docker-config"),
            "GOODMONEYING_FORCE_DOCKER_DB_DUMP": "1",
            "DEV_DOCKER_LOG": str(docker_log),
            "DEV_DOCKER_OPERATING_SYSTEM": "Docker Desktop",
            "DEV_DOCKER_OS_TYPE": "linux",
            "PATH": f"{tmp_path}:{env['PATH']}",
        }
    )

    result = run_dev_script("db", "dump", env=env)

    assert result.returncode == 0, result.stderr
    log = docker_log.read_text()
    run_args = next(line for line in log.splitlines() if line.startswith("args=run "))
    assert "--add-host host.docker.internal:host-gateway" in run_args
    assert "--network host" not in run_args
    assert "database_url=postgresql://user:password@host.docker.internal:15432/" in log


def test_dev_script_rejects_unsupported_docker_os_for_schema_dump(
    tmp_path: Path,
) -> None:
    fake_docker, docker_log = install_fake_docker(tmp_path)
    schema_file = tmp_path / "schema.sql"
    schema_file.write_text("-- test schema\n")
    env = os.environ.copy()
    env.update(
        {
            "GOODMONEYING_ENV_FILE": str(tmp_path / "missing.env"),
            "GOODMONEYING_DATABASE_URL": (
                "postgresql://user:password@127.0.0.1:15432/goodmoneying"
            ),
            "GOODMONEYING_DB_SCHEMA_FILE": str(schema_file),
            "GOODMONEYING_DBMATE_DOCKER_CONFIG": str(tmp_path / "docker-config"),
            "GOODMONEYING_FORCE_DOCKER_DB_DUMP": "1",
            "DEV_DOCKER_LOG": str(docker_log),
            "DEV_DOCKER_OPERATING_SYSTEM": "Unknown Docker OS",
            "DEV_DOCKER_OS_TYPE": "windows",
            "PATH": f"{tmp_path}:{env['PATH']}",
        }
    )

    result = run_dev_script("db", "dump", env=env)

    assert result.returncode != 0
    assert "지원하지 않는 Docker 운영체제" in result.stderr
    assert not any(
        line.startswith("args=run ") for line in docker_log.read_text().splitlines()
    )


def test_dev_script_rejects_baseline_only_rollback(tmp_path: Path) -> None:
    fake_dbmate, dbmate_log = install_fake_dbmate(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "GOODMONEYING_ENV_FILE": str(tmp_path / "missing.env"),
            "GOODMONEYING_DATABASE_URL": (
                "postgresql://user:password@db.example.test:5432/goodmoneying"
            ),
            "GOODMONEYING_DBMATE_BIN": str(fake_dbmate),
            "DEV_DBMATE_LOG": str(dbmate_log),
            "DEV_DBMATE_STATUS": (
                "[X] 20260715000100_initial_schema.sql\\n\\nApplied: 1\\nPending: 0\\n"
            ),
        }
    )

    result = run_dev_script("db", "rollback", env=env)

    assert result.returncode != 0
    assert "기준선" in result.stderr
    args = [line for line in dbmate_log.read_text().splitlines() if line.startswith("args=")]
    assert len(args) == 1
    assert args[0].endswith(" status")


def test_dev_script_uses_python_binary_for_long_running_python_processes() -> None:
    script = Path("dev.sh").read_text()

    assert '"$PYTHON_BIN" -m uvicorn goodmoneying_api.main:app' in script
    assert (
        '"$GOODMONEYING_PYTHON_BIN" -m '
        "goodmoneying_worker.realtime_collection_worker"
    ) in script
    assert (
        '"$GOODMONEYING_PYTHON_BIN" -m '
        "goodmoneying_worker.backfill_collection_worker"
    ) in script
    assert '"$PYTHON_BIN" scripts/dev-start-background.py' in script


def test_dev_script_passes_backfill_batch_size_to_worker() -> None:
    script = Path("dev.sh").read_text()

    start_worker_body = script.split("start_backfill_collection_worker() {", maxsplit=1)[
        1
    ].split("\n}", maxsplit=1)[0]

    assert 'BACKFILL_BATCH_SIZE="${GOODMONEYING_BACKFILL_BATCH_SIZE:-3000}"' in script
    assert 'GOODMONEYING_BACKFILL_BATCH_SIZE="$BACKFILL_BATCH_SIZE"' in start_worker_body


def test_dev_script_passes_log_level_to_workers() -> None:
    script = Path("dev.sh").read_text()

    realtime_worker_body = script.split("start_realtime_collection_worker() {", maxsplit=1)[
        1
    ].split("\n}", maxsplit=1)[0]
    backfill_worker_body = script.split("start_backfill_collection_worker() {", maxsplit=1)[
        1
    ].split("\n}", maxsplit=1)[0]

    assert 'LOG_LEVEL="${GOODMONEYING_LOG_LEVEL:-INFO}"' in script
    assert "GOODMONEYING_LOG_LEVEL" in script
    assert 'GOODMONEYING_LOG_LEVEL="$LOG_LEVEL"' in realtime_worker_body
    assert 'GOODMONEYING_LOG_LEVEL="$LOG_LEVEL"' in backfill_worker_body


def test_dev_background_launcher_starts_process_in_new_session() -> None:
    launcher = Path("scripts/dev-start-background.py").read_text()

    assert "start_new_session=True" in launcher
    assert "stdin=subprocess.DEVNULL" in launcher


def test_dev_script_keeps_operator_token_out_of_vite_client_environment() -> None:
    script = Path("dev.sh").read_text()

    start_web_body = script.split("start_web() {", maxsplit=1)[1].split(
        "\n}", maxsplit=1
    )[0]

    assert 'VITE_OPERATOR_TOKEN="$OPERATOR_TOKEN"' not in start_web_body
    assert 'GOODMONEYING_OPERATOR_TOKEN="$OPERATOR_TOKEN"' in start_web_body
    assert 'VITE_API_BASE_URL="/api"' in start_web_body


def test_dev_script_runs_vite_directly_for_trackable_web_process() -> None:
    script = Path("dev.sh").read_text()
    vite_launcher = Path("scripts/dev-vite-server.mjs").read_text()

    start_web_body = script.split("start_web() {", maxsplit=1)[1].split(
        "\n}", maxsplit=1
    )[0]

    assert "npm --workspace apps/web run dev" not in start_web_body
    assert "node scripts/dev-vite-server.mjs" in start_web_body
    assert 'root: "apps/web"' in vite_launcher
    assert "createServer" in vite_launcher


def test_vite_dev_server_proxies_default_api_path() -> None:
    config = Path("apps/web/vite.config.ts").read_text()

    assert '"/api"' in config
    assert "GOODMONEYING_API_PORT" in config
    assert "VITE_DEV_API_PROXY_TARGET" in config
    assert 'path.replace(/^\\/api/, "")' in config
