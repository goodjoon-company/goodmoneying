from __future__ import annotations

import os
import subprocess
from pathlib import Path


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


def test_dev_script_without_arguments_prints_usage() -> None:
    result = run_dev_script()

    assert result.returncode == 0
    assert "사용법" in result.stdout
    assert "infra start" in result.stdout
    assert "app start" in result.stdout


def test_dev_script_status_lists_infra_and_app_units() -> None:
    result = run_dev_script("status")

    assert result.returncode == 0
    assert "infra" in result.stdout
    assert "postgres" in result.stdout
    assert "app" in result.stdout
    assert "api" in result.stdout
    assert "web" in result.stdout
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


def test_dev_script_shell_environment_overrides_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GOODMONEYING_API_PORT=19000\n")
    env = os.environ.copy()
    env["GOODMONEYING_ENV_FILE"] = str(env_file)
    env["GOODMONEYING_API_PORT"] = "19100"

    result = run_dev_script("app", "status", "api", env=env)

    assert result.returncode == 0
    assert "endpoint=http://127.0.0.1:19100" in result.stdout


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


def test_dev_background_launcher_starts_process_in_new_session() -> None:
    launcher = Path("scripts/dev-start-background.py").read_text()

    assert "start_new_session=True" in launcher
    assert "stdin=subprocess.DEVNULL" in launcher


def test_dev_script_passes_operator_token_to_vite_dev_server() -> None:
    script = Path("dev.sh").read_text()

    start_web_body = script.split("start_web() {", maxsplit=1)[1].split(
        "\n}", maxsplit=1
    )[0]

    assert 'VITE_OPERATOR_TOKEN="$OPERATOR_TOKEN"' in start_web_body


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
