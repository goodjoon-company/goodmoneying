from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml

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


def run_healthcheck_script(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GOODMONEYING_DEPLOY_DRY_RUN"] = "1"
    return subprocess.run(
        ["bash", "deploy/scripts/healthcheck-profile.sh", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def load_compose(name: str) -> Mapping[str, Any]:
    path = ROOT / f"deploy/profiles/prod-home/{name}"
    return cast("Mapping[str, Any]", yaml.safe_load(path.read_text()))


def services(compose: Mapping[str, Any]) -> Mapping[str, Any]:
    return cast("Mapping[str, Any]", compose["services"])


def test_prod_home_profile_has_required_files() -> None:
    profile_dir = ROOT / "deploy/profiles/prod-home"

    assert (profile_dir / "profile.env").is_file()
    assert (profile_dir / "hosts.env").is_file()
    assert (profile_dir / "compose.infra.yml").is_file()
    assert (profile_dir / "compose.app.yml").is_file()
    assert (profile_dir / "compose.web.yml").is_file()
    assert (profile_dir / "README.md").is_file()


def test_prod_home_compose_files_assign_expected_services() -> None:
    infra = load_compose("compose.infra.yml")
    app = load_compose("compose.app.yml")
    web = load_compose("compose.web.yml")

    assert set(services(infra)) == {"postgres"}
    assert set(services(app)) == {"api", "worker"}
    assert set(services(web)) == {"web"}


def test_prod_home_compose_uses_external_env_files() -> None:
    infra = services(load_compose("compose.infra.yml"))
    app = services(load_compose("compose.app.yml"))
    web = services(load_compose("compose.web.yml"))

    assert "/opt/goodmoneying/env/infra.env" in infra["postgres"]["env_file"]
    assert "/opt/goodmoneying/env/app.env" in app["api"]["env_file"]
    assert "/opt/goodmoneying/env/app.env" in app["worker"]["env_file"]
    assert "/opt/goodmoneying/env/web.env" in web["web"]["env_file"]


def test_prod_home_compose_binds_ports_to_tailscale_ips() -> None:
    infra = services(load_compose("compose.infra.yml"))
    app = services(load_compose("compose.app.yml"))
    web = services(load_compose("compose.web.yml"))

    assert infra["postgres"]["ports"] == ["100.107.98.22:5432:5432"]
    assert app["api"]["ports"] == ["100.115.38.59:8000:8000"]
    assert web["web"]["ports"] == ["100.68.208.102:8080:80"]


def test_prod_home_compose_uses_fixed_ghcr_image_names() -> None:
    app = services(load_compose("compose.app.yml"))
    web = services(load_compose("compose.web.yml"))

    assert (
        app["api"]["image"]
        == "ghcr.io/goodjoon-company/goodmoneying-api:${GOODMONEYING_IMAGE_TAG}"
    )
    assert (
        app["worker"]["image"]
        == "ghcr.io/goodjoon-company/goodmoneying-worker:${GOODMONEYING_IMAGE_TAG}"
    )
    assert (
        web["web"]["image"]
        == "ghcr.io/goodjoon-company/goodmoneying-web:${GOODMONEYING_IMAGE_TAG}"
    )


def test_deploy_script_rejects_unknown_profile() -> None:
    result = run_deploy_script("unknown", "release-abc1234")

    assert result.returncode != 0
    assert "지원하지 않는 배포 프로필입니다: unknown" in result.stderr


def test_deploy_script_rejects_invalid_image_tag() -> None:
    result = run_deploy_script("prod-home", "release-bad;rm")

    assert result.returncode != 0
    assert "잘못된 이미지 태그입니다: release-bad;rm" in result.stderr


def test_deploy_script_dry_run_prints_prod_home_steps() -> None:
    result = run_deploy_script("prod-home", "release-abc1234")

    assert result.returncode == 0
    assert "profile=prod-home" in result.stdout
    assert "tag=release-abc1234" in result.stdout
    assert "infra host=Mac-Mini-M4.local compose=compose.infra.yml" in result.stdout
    assert "app host=app-server01 compose=compose.app.yml" in result.stdout
    assert "web host=bmax-ubuntu compose=compose.web.yml" in result.stdout


def test_deploy_script_dry_run_prints_remote_commands() -> None:
    result = run_deploy_script("prod-home", "release-def5678")

    assert result.returncode == 0
    assert "ssh Mac-Mini-M4.local" in result.stdout
    assert "docker compose --env-file '/opt/goodmoneying/env/infra.env'" in result.stdout
    assert "ssh app-server01" in result.stdout
    assert "docker compose --env-file '/opt/goodmoneying/env/app.env'" in result.stdout
    assert "ssh bmax-ubuntu" in result.stdout
    assert "docker compose --env-file '/opt/goodmoneying/env/web.env'" in result.stdout
    assert 'ssh Mac-Mini-M4.local "mkdir -p \'/opt/goodmoneying\'"' in result.stdout
    assert (
        f"scp {ROOT}/deploy/profiles/prod-home/compose.infra.yml "
        "Mac-Mini-M4.local:/opt/goodmoneying/compose.infra.yml"
    ) in result.stdout
    assert "compose.infra.yml' pull" in result.stdout
    assert "compose.infra.yml' up -d" in result.stdout
    assert "compose.app.yml" in result.stdout
    assert "compose.web.yml" in result.stdout
    assert result.stdout.index("ssh Mac-Mini-M4.local") < result.stdout.index(
        "ssh app-server01"
    )
    assert result.stdout.index("ssh app-server01") < result.stdout.index(
        "ssh bmax-ubuntu"
    )


def test_healthcheck_script_dry_run_prints_checks() -> None:
    result = run_healthcheck_script("prod-home")

    assert result.returncode == 0
    assert (
        "curl -fsS --connect-timeout 5 --max-time 10 "
        "http://app-server01:8000/health"
    ) in result.stdout
    assert (
        "curl -fsS --connect-timeout 5 --max-time 10 http://bmax-ubuntu:8080/"
    ) in result.stdout
    assert "ssh -o BatchMode=yes -o ConnectTimeout=10" in result.stdout
    assert (
        "ssh -o BatchMode=yes -o ConnectTimeout=10 Mac-Mini-M4.local "
        "docker exec goodmoneying-postgres sh -c "
        '\'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"\''
    ) in result.stdout.splitlines()
    assert "docker inspect -f '{{.State.Running}}' goodmoneying-worker" in result.stdout


def test_healthcheck_script_rejects_unknown_profile() -> None:
    result = run_healthcheck_script("unknown")

    assert result.returncode != 0
    assert "지원하지 않는 배포 프로필입니다: unknown" in result.stderr


def test_healthcheck_script_dry_run_prints_checks_in_order() -> None:
    result = run_healthcheck_script("prod-home")

    assert result.returncode == 0
    api_index = result.stdout.index("http://app-server01:8000/health")
    web_index = result.stdout.index("http://bmax-ubuntu:8080/")
    postgres_index = result.stdout.index("docker exec goodmoneying-postgres")
    worker_index = result.stdout.index("goodmoneying-worker")
    assert api_index < web_index < postgres_index < worker_index
