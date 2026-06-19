from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run_deploy_script(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GOODMONEYING_DEPLOY_DRY_RUN"] = "1"
    return subprocess.run(
        ["bash", "deploy/scripts/deploy-profile.sh", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def test_prod_home_profile_has_required_files() -> None:
    profile_dir = ROOT / "deploy/profiles/prod-home"

    assert (profile_dir / "profile.env").is_file()
    assert (profile_dir / "hosts.env").is_file()
    assert (profile_dir / "compose.infra.yml").is_file()
    assert (profile_dir / "compose.app.yml").is_file()
    assert (profile_dir / "compose.web.yml").is_file()
    assert (profile_dir / "README.md").is_file()


def test_deploy_script_rejects_unknown_profile() -> None:
    result = run_deploy_script("unknown", "release-abc1234")

    assert result.returncode != 0
    assert "지원하지 않는 배포 프로필입니다: unknown" in result.stderr


def test_deploy_script_dry_run_prints_prod_home_steps() -> None:
    result = run_deploy_script("prod-home", "release-abc1234")

    assert result.returncode == 0
    assert "profile=prod-home" in result.stdout
    assert "tag=release-abc1234" in result.stdout
    assert "infra host=Mac-Mini-M4.local compose=compose.infra.yml" in result.stdout
    assert "app host=app-server01 compose=compose.app.yml" in result.stdout
    assert "web host=bmax-ubuntu compose=compose.web.yml" in result.stdout
