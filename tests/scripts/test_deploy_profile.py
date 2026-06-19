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


def test_prod_home_readme_documents_required_env_files() -> None:
    readme = (ROOT / "deploy/profiles/prod-home/README.md").read_text()

    assert "GOODMONEYING_DATABASE_URL" in readme
    assert "GOODMONEYING_OPERATOR_TOKEN" in readme
    assert "GOODMONEYING_INFRA_POSTGRES_DATA_DIR" in readme
    assert "GOODMONEYING_APP_CONFIG_DIR" in readme
    assert "stdout/stderr" in readme
    assert "CR_PAT" in readme
    assert "docker login ghcr.io" in readme
    assert "Tailscale" in readme


def test_web_dockerfile_accepts_api_base_build_arg() -> None:
    dockerfile = (ROOT / "apps/web/Dockerfile").read_text()

    assert "ARG VITE_API_BASE_URL=http://127.0.0.1:8000" in dockerfile
    assert "ENV VITE_API_BASE_URL=$VITE_API_BASE_URL" in dockerfile


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

    assert "${GOODMONEYING_INFRA_BASE_DIR}/env/infra.env" in infra["postgres"][
        "env_file"
    ]
    assert "${GOODMONEYING_APP_BASE_DIR}/env/app.env" in app["api"]["env_file"]
    assert "${GOODMONEYING_APP_BASE_DIR}/env/app.env" in app["worker"]["env_file"]
    assert "${GOODMONEYING_WEB_BASE_DIR}/env/web.env" in web["web"]["env_file"]


def test_prod_home_hosts_env_defines_server_specific_data_and_config_paths() -> None:
    hosts_env = (ROOT / "deploy/profiles/prod-home/hosts.env").read_text()

    assert (
        "GOODMONEYING_INFRA_BASE_DIR=/Users/goodjoon/DATA/applications/goodmoneying"
        in hosts_env
    )
    assert "GOODMONEYING_APP_BASE_DIR=/home/goodjoon/project/goodmoneying" in hosts_env
    assert (
        "GOODMONEYING_WEB_BASE_DIR=/home/goodjoon/applications/goodmoneying"
        in hosts_env
    )
    assert "GOODMONEYING_REMOTE_BASE_DIR=" not in hosts_env
    assert "GOODMONEYING_INFRA_POSTGRES_DATA_DIR=" in hosts_env
    assert "GOODMONEYING_INFRA_CONFIG_DIR=" in hosts_env
    assert "GOODMONEYING_APP_API_DATA_DIR=" in hosts_env
    assert "GOODMONEYING_APP_WORKER_DATA_DIR=" in hosts_env
    assert "GOODMONEYING_APP_CONFIG_DIR=" in hosts_env
    assert "GOODMONEYING_WEB_NGINX_CACHE_DIR=" in hosts_env
    assert "GOODMONEYING_WEB_CONFIG_DIR=" in hosts_env
    assert "LOG_DIR=" not in hosts_env


def test_prod_home_compose_mounts_data_cache_and_config_dirs_from_host_variables() -> None:
    infra = services(load_compose("compose.infra.yml"))
    app = services(load_compose("compose.app.yml"))
    web = services(load_compose("compose.web.yml"))

    assert "${GOODMONEYING_INFRA_POSTGRES_DATA_DIR}:/var/lib/postgresql/data" in infra[
        "postgres"
    ]["volumes"]
    assert "${GOODMONEYING_APP_API_DATA_DIR}:/var/lib/goodmoneying/api" in app["api"][
        "volumes"
    ]
    assert "${GOODMONEYING_APP_CONFIG_DIR}:/etc/goodmoneying:ro" in app["api"][
        "volumes"
    ]
    assert "${GOODMONEYING_APP_WORKER_DATA_DIR}:/var/lib/goodmoneying/worker" in app[
        "worker"
    ]["volumes"]
    assert "${GOODMONEYING_APP_CONFIG_DIR}:/etc/goodmoneying:ro" in app[
        "worker"
    ]["volumes"]
    assert "${GOODMONEYING_WEB_NGINX_CACHE_DIR}:/var/cache/nginx" in web["web"][
        "volumes"
    ]
    assert "${GOODMONEYING_WEB_CONFIG_DIR}:/etc/goodmoneying:ro" in web["web"][
        "volumes"
    ]
    for compose_services in (infra, app, web):
        for service in compose_services.values():
            for volume in service.get("volumes", []):
                assert "/var/log" not in volume


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
    assert "deploy.hosts.env" in result.stdout
    assert (
        "docker compose --env-file "
        "'/Users/goodjoon/DATA/applications/goodmoneying/deploy.hosts.env'"
    ) in result.stdout
    assert (
        "docker compose --env-file "
        "'/home/goodjoon/project/goodmoneying/deploy.hosts.env'"
    ) in result.stdout
    assert (
        "docker compose --env-file "
        "'/home/goodjoon/applications/goodmoneying/deploy.hosts.env'"
    ) in result.stdout
    assert "ssh app-server01" in result.stdout
    assert "ssh bmax-ubuntu" in result.stdout
    assert (
        "ssh Mac-Mini-M4.local \"mkdir -p "
        "'/Users/goodjoon/DATA/applications/goodmoneying'\""
    ) in result.stdout
    assert (
        "mkdir -p '/Users/goodjoon/DATA/applications/goodmoneying/infra/postgres-data'"
        in result.stdout
    )
    assert (
        "mkdir -p '/Users/goodjoon/DATA/applications/goodmoneying/infra/config'"
        in result.stdout
    )
    assert "mkdir -p '/home/goodjoon/project/goodmoneying/app/api-data'" in result.stdout
    assert (
        "mkdir -p '/home/goodjoon/project/goodmoneying/app/worker-data'"
        in result.stdout
    )
    assert "mkdir -p '/home/goodjoon/project/goodmoneying/app/config'" in result.stdout
    assert (
        "mkdir -p '/home/goodjoon/applications/goodmoneying/web/nginx-cache'"
        in result.stdout
    )
    assert (
        "mkdir -p '/home/goodjoon/applications/goodmoneying/web/config'"
        in result.stdout
    )
    assert "logs" not in result.stdout
    assert (
        f"scp {ROOT}/deploy/profiles/prod-home/hosts.env "
        "Mac-Mini-M4.local:/Users/goodjoon/DATA/applications/goodmoneying/"
        "deploy.hosts.env"
    ) in result.stdout
    assert (
        f"scp {ROOT}/deploy/profiles/prod-home/compose.infra.yml "
        "Mac-Mini-M4.local:/Users/goodjoon/DATA/applications/goodmoneying/"
        "compose.infra.yml"
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
