from __future__ import annotations

import subprocess


def run_dev_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "dev.sh", *args],
        check=False,
        capture_output=True,
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
    assert "worker" in result.stdout


def test_dev_script_rejects_unknown_command() -> None:
    result = run_dev_script("unknown")

    assert result.returncode != 0
    assert "사용법" in result.stdout
