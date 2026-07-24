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


def run_start_script(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GOODMONEYING_DEPLOY_DRY_RUN"] = "1"
    return subprocess.run(
        ["bash", "deploy/scripts/start-profile.sh", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def run_stop_script(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GOODMONEYING_DEPLOY_DRY_RUN"] = "1"
    return subprocess.run(
        ["bash", "deploy/scripts/stop-profile.sh", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def load_compose(name: str) -> Mapping[str, Any]:
    path = ROOT / f"deploy/profiles/prod-home/target/{name}/compose.yml"
    return cast("Mapping[str, Any]", yaml.safe_load(path.read_text()))


def services(compose: Mapping[str, Any]) -> Mapping[str, Any]:
    return cast("Mapping[str, Any]", compose["services"])


def test_prod_home_profile_has_required_files() -> None:
    profile_dir = ROOT / "deploy/profiles/prod-home"

    assert (profile_dir / "runner/profile.env").is_file()
    assert (profile_dir / "runner/hosts.env").is_file()
    assert (profile_dir / "env-samples/infra.env.sample").is_file()
    assert (profile_dir / "env-samples/app.env.sample").is_file()
    assert (profile_dir / "env-samples/web.env.sample").is_file()
    assert (profile_dir / "target/infra/compose.yml").is_file()
    assert (profile_dir / "target/app/compose.yml").is_file()
    assert (ROOT / "apps/migrations/Dockerfile").is_file()
    assert (profile_dir / "target/web/compose.yml").is_file()
    assert (profile_dir / "target/infra/start.sh").is_file()
    assert (profile_dir / "target/infra/stop.sh").is_file()
    assert (profile_dir / "target/app/start.sh").is_file()
    assert (profile_dir / "target/app/migrate.sh").is_file()
    assert (profile_dir / "target/app/stop.sh").is_file()
    assert (profile_dir / "target/app/start-api.sh").is_file()
    assert (profile_dir / "target/app/stop-api.sh").is_file()
    assert (profile_dir / "target/app/start-upbit-gateway.sh").is_file()
    assert (profile_dir / "target/app/stop-upbit-gateway.sh").is_file()
    assert (profile_dir / "target/app/start-realtime-collection-worker.sh").is_file()
    assert (profile_dir / "target/app/stop-realtime-collection-worker.sh").is_file()
    assert (profile_dir / "target/app/start-backfill-collection-worker.sh").is_file()
    assert (profile_dir / "target/app/stop-backfill-collection-worker.sh").is_file()
    assert (profile_dir / "target/app/start-risk-evaluation-worker.sh").is_file()
    assert (profile_dir / "target/app/stop-risk-evaluation-worker.sh").is_file()
    assert (profile_dir / "target/web/start.sh").is_file()
    assert (profile_dir / "target/web/stop.sh").is_file()
    assert (profile_dir / "README.md").is_file()


def test_worker_container_does_not_hide_startup_failure_in_shell_restart_loop() -> None:
    dockerfile = (ROOT / "apps/worker/Dockerfile").read_text()

    assert "while true" not in dockerfile
    assert 'CMD ["python", "-m"' in dockerfile
    assert "USER app" in dockerfile


def test_prod_home_readme_documents_required_env_files() -> None:
    readme = (ROOT / "deploy/profiles/prod-home/README.md").read_text()

    assert "GOODMONEYING_DATABASE_URL" in readme
    assert "GOODMONEYING_OPERATOR_TOKEN" in readme
    assert "GOODMONEYING_API_INTERNAL_URL" in readme
    assert "GOODMONEYING_UPBIT_GATEWAY_INTERNAL_URL" in readme
    assert "UPBIT_GATEWAY_ALLOWED_ORIGINS" in readme
    assert "Nginx 프록시(proxy)" in readme
    assert "GOODMONEYING_INFRA_POSTGRES_DATA_DIR" in readme
    assert "GOODMONEYING_APP_CONFIG_DIR" in readme
    assert "stdout/stderr" in readme
    assert "CR_PAT" in readme
    assert "docker login ghcr.io" in readme
    assert "Tailscale" in readme


def test_web_dockerfile_accepts_api_base_build_arg() -> None:
    dockerfile = (ROOT / "apps/web/Dockerfile").read_text()

    assert "ARG VITE_API_BASE_URL=/api" in dockerfile
    assert "ENV VITE_API_BASE_URL=$VITE_API_BASE_URL" in dockerfile
    assert "nginx.conf.template" in dockerfile
    assert "envsubst" in dockerfile


def test_migration_dockerfile_pins_dbmate_image_digest() -> None:
    dockerfile = (ROOT / "apps/migrations/Dockerfile").read_text()

    assert (
        "FROM ghcr.io/amacneil/dbmate:2.34.1@sha256:"
        "3298bdfcb651af608e06d2e13ae12398c4056e5326d38ef31885973d83248d9c" in dockerfile
    )


def test_web_nginx_proxy_preserves_websocket_upgrade_headers() -> None:
    nginx_template = (ROOT / "apps/web/nginx.conf.template").read_text()

    assert "proxy_set_header Upgrade $http_upgrade;" in nginx_template
    assert 'proxy_set_header Connection "upgrade";' in nginx_template
    assert "location /upbit-gateway/" in nginx_template
    assert "GOODMONEYING_UPBIT_GATEWAY_INTERNAL_URL" in nginx_template
    assert "proxy_set_header X-Forwarded-Host $http_host;" in nginx_template


def test_prod_home_compose_files_assign_expected_services() -> None:
    infra = load_compose("infra")
    app = load_compose("app")
    web = load_compose("web")

    assert set(services(infra)) == {"postgres"}
    assert services(infra)["postgres"]["image"] == "postgres:17"
    assert set(services(app)) == {
        "migrate",
        "api",
        "upbit-gateway",
        "market-sync-worker",
        "realtime-collection-worker",
        "backfill-collection-worker",
        "candle-aggregation-worker",
        "risk-evaluation-worker",
    }
    assert set(services(web)) == {"web"}


def test_prod_home_compose_uses_external_env_files() -> None:
    infra = services(load_compose("infra"))
    app = services(load_compose("app"))
    web = services(load_compose("web"))

    assert "${GOODMONEYING_INFRA_BASE_DIR}/env/infra.env" in infra["postgres"]["env_file"]
    assert "${GOODMONEYING_APP_BASE_DIR}/env/app.env" in app["api"]["env_file"]
    assert "${GOODMONEYING_APP_BASE_DIR}/env/app.env" in app["upbit-gateway"]["env_file"]
    assert "${GOODMONEYING_APP_BASE_DIR}/env/app.env" in app["migrate"]["env_file"]
    assert "${GOODMONEYING_APP_BASE_DIR}/env/app.env" in app["market-sync-worker"]["env_file"]
    assert (
        "${GOODMONEYING_APP_BASE_DIR}/env/app.env" in app["realtime-collection-worker"]["env_file"]
    )
    assert (
        "${GOODMONEYING_APP_BASE_DIR}/env/app.env" in app["backfill-collection-worker"]["env_file"]
    )
    assert (
        "${GOODMONEYING_APP_BASE_DIR}/env/app.env" in app["candle-aggregation-worker"]["env_file"]
    )
    assert (
        "${GOODMONEYING_APP_BASE_DIR}/env/app.env" in app["risk-evaluation-worker"]["env_file"]
    )
    assert "${GOODMONEYING_WEB_BASE_DIR}/env/web.env" in web["web"]["env_file"]


def test_prod_home_web_environment_documents_upbit_gateway_proxy_target() -> None:
    sample = (ROOT / "deploy/profiles/prod-home/env-samples/web.env.sample").read_text()
    readme = (ROOT / "deploy/profiles/prod-home/README.md").read_text()

    assert "GOODMONEYING_UPBIT_GATEWAY_INTERNAL_URL=" in sample
    assert "GOODMONEYING_UPBIT_GATEWAY_INTERNAL_URL=" in readme
    assert "업비트 API 게이트웨이" in readme


def test_prod_home_app_environment_configures_gateway_without_direct_keys() -> None:
    sample = (ROOT / "deploy/profiles/prod-home/env-samples/app.env.sample").read_text()

    assert "UPBIT_GATEWAY_ALLOWED_ORIGINS=http://100.68.208.102:8080" in sample
    assert "UPBIT_ACCESS_KEY_FILE=/etc/goodmoneying/upbit-access-key" in sample
    assert "UPBIT_SECRET_KEY_FILE=/etc/goodmoneying/upbit-secret-key" in sample
    assert "UPBIT_ACCESS_KEY=" not in sample
    assert "UPBIT_SECRET_KEY=" not in sample


def test_prod_home_app_workers_do_not_override_runtime_env_file_values() -> None:
    app = services(load_compose("app"))

    for service_name in (
        "market-sync-worker",
        "realtime-collection-worker",
        "backfill-collection-worker",
        "candle-aggregation-worker",
        "risk-evaluation-worker",
    ):
        environment = app[service_name].get("environment", {})

        assert "GOODMONEYING_REALTIME_COLLECTION_INTERVAL_SECONDS" not in environment


def test_prod_home_app_services_force_production_and_deployed_release_sha() -> None:
    app = services(load_compose("app"))

    for service_name in (
        "api",
        "upbit-gateway",
        "market-sync-worker",
        "realtime-collection-worker",
        "backfill-collection-worker",
        "candle-aggregation-worker",
        "risk-evaluation-worker",
    ):
        environment = app[service_name]["environment"]
        assert environment["GOODMONEYING_RUNTIME_MODE"] == "production"
        assert environment["GOODMONEYING_RELEASE_SHA"] == "${GOODMONEYING_RELEASE_SHA:?}"
        assert "GOODMONEYING_MARKET_SYNC_INTERVAL_SECONDS" not in environment
        assert "GOODMONEYING_BACKFILL_POLL_SECONDS" not in environment
        assert "GOODMONEYING_BACKFILL_BATCH_SIZE" not in environment
        assert "GOODMONEYING_AGGREGATION_POLL_SECONDS" not in environment
        assert "GOODMONEYING_RISK_EVALUATION_POLL_SECONDS" not in environment
        assert "GOODMONEYING_LOG_LEVEL" not in environment


def test_prod_home_hosts_env_defines_server_specific_data_and_config_paths() -> None:
    hosts_env = (ROOT / "deploy/profiles/prod-home/runner/hosts.env").read_text()

    assert "GOODMONEYING_INFRA_BASE_DIR=/Users/goodjoon/DATA/applications/goodmoneying" in hosts_env
    assert "GOODMONEYING_APP_BASE_DIR=/home/goodjoon/project/goodmoneying" in hosts_env
    assert "GOODMONEYING_WEB_BASE_DIR=/home/goodjoon/applications/goodmoneying" in hosts_env
    assert "GOODMONEYING_REMOTE_BASE_DIR=" not in hosts_env
    assert "GOODMONEYING_INFRA_POSTGRES_DATA_DIR=" in hosts_env
    assert "GOODMONEYING_INFRA_CONFIG_DIR=" in hosts_env
    assert (
        "GOODMONEYING_INFRA_DOCKER_CONFIG=/Users/goodjoon/DATA/applications/goodmoneying/.docker"
    ) in hosts_env
    assert "GOODMONEYING_APP_API_DATA_DIR=" in hosts_env
    assert "GOODMONEYING_APP_REALTIME_COLLECTION_WORKER_DATA_DIR=" in hosts_env
    assert "GOODMONEYING_APP_BACKFILL_COLLECTION_WORKER_DATA_DIR=" in hosts_env
    assert "GOODMONEYING_APP_CANDLE_AGGREGATION_WORKER_DATA_DIR=" in hosts_env
    assert "GOODMONEYING_APP_CONFIG_DIR=" in hosts_env
    assert "GOODMONEYING_APP_DOCKER_CONFIG=/home/goodjoon/.docker" in hosts_env
    assert "GOODMONEYING_WEB_NGINX_CACHE_DIR=" in hosts_env
    assert "GOODMONEYING_WEB_CONFIG_DIR=" in hosts_env
    assert "GOODMONEYING_WEB_DOCKER_CONFIG=/home/goodjoon/.docker" in hosts_env
    assert "LOG_DIR=" not in hosts_env


def test_prod_home_compose_mounts_data_cache_and_config_dirs_from_host_variables() -> None:
    infra = services(load_compose("infra"))
    app = services(load_compose("app"))
    web = services(load_compose("web"))

    assert (
        "${GOODMONEYING_INFRA_POSTGRES_DATA_DIR}:/var/lib/postgresql/data"
        in infra["postgres"]["volumes"]
    )
    assert "${GOODMONEYING_APP_API_DATA_DIR}:/var/lib/goodmoneying/api" in app["api"]["volumes"]
    assert "${GOODMONEYING_APP_CONFIG_DIR}:/etc/goodmoneying:ro" in app["api"]["volumes"]
    assert (
        "${GOODMONEYING_APP_REALTIME_COLLECTION_WORKER_DATA_DIR}"
        ":/var/lib/goodmoneying/realtime-collection-worker"
    ) in app["realtime-collection-worker"]["volumes"]
    assert (
        "${GOODMONEYING_APP_CONFIG_DIR}:/etc/goodmoneying:ro"
        in app["realtime-collection-worker"]["volumes"]
    )
    assert (
        "${GOODMONEYING_APP_BACKFILL_COLLECTION_WORKER_DATA_DIR}"
        ":/var/lib/goodmoneying/backfill-collection-worker"
    ) in app["backfill-collection-worker"]["volumes"]
    assert (
        "${GOODMONEYING_APP_CONFIG_DIR}:/etc/goodmoneying:ro"
        in app["backfill-collection-worker"]["volumes"]
    )
    assert (
        "${GOODMONEYING_APP_CANDLE_AGGREGATION_WORKER_DATA_DIR}"
        ":/var/lib/goodmoneying/candle-aggregation-worker"
    ) in app["candle-aggregation-worker"]["volumes"]
    assert (
        "${GOODMONEYING_APP_RISK_EVALUATION_WORKER_DATA_DIR}"
        ":/var/lib/goodmoneying/risk-evaluation-worker"
    ) in app["risk-evaluation-worker"]["volumes"]
    assert "${GOODMONEYING_WEB_NGINX_CACHE_DIR}:/var/cache/nginx" in web["web"]["volumes"]
    assert "${GOODMONEYING_WEB_CONFIG_DIR}:/etc/goodmoneying:ro" in web["web"]["volumes"]
    for compose_services in (infra, app, web):
        for service in compose_services.values():
            for volume in service.get("volumes", []):
                assert "/var/log" not in volume


def test_prod_home_compose_binds_ports_to_tailscale_ips() -> None:
    infra = services(load_compose("infra"))
    app = services(load_compose("app"))
    web = services(load_compose("web"))

    assert infra["postgres"]["ports"] == ["100.107.98.22:5432:5432"]
    assert app["api"]["ports"] == ["100.115.38.59:8000:8000"]
    assert app["upbit-gateway"]["ports"] == ["100.115.38.59:8001:8001"]
    assert web["web"]["ports"] == ["100.68.208.102:8080:8080"]


def test_prod_home_compose_uses_fixed_ghcr_image_names() -> None:
    app = services(load_compose("app"))
    web = services(load_compose("web"))

    assert (
        app["migrate"]["image"]
        == "ghcr.io/goodjoon-company/goodmoneying-migrations:${GOODMONEYING_IMAGE_TAG}"
    )
    assert app["migrate"]["profiles"] == ["migration"]
    assert app["migrate"]["restart"] == "no"
    assert app["migrate"]["command"][-1] == "migrate"
    assert app["migrate"]["environment"]["DBMATE_STRICT"] == "true"
    assert "--strict" not in app["migrate"]["command"]
    assert "--no-dump-schema" in app["migrate"]["command"]
    assert "--wait" in app["migrate"]["command"]
    assert "--wait-timeout" in app["migrate"]["command"]
    assert "60s" in app["migrate"]["command"]
    assert (
        app["api"]["image"] == "ghcr.io/goodjoon-company/goodmoneying-api:${GOODMONEYING_IMAGE_TAG}"
    )
    assert app["upbit-gateway"]["image"] == (
        "ghcr.io/goodjoon-company/goodmoneying-upbit-gateway:${GOODMONEYING_IMAGE_TAG}"
    )
    assert app["upbit-gateway"]["healthcheck"]["test"][-1].find("127.0.0.1:8001/health") >= 0
    assert "${GOODMONEYING_APP_CONFIG_DIR}:/etc/goodmoneying:ro" in app["upbit-gateway"]["volumes"]
    assert (
        app["realtime-collection-worker"]["image"]
        == "ghcr.io/goodjoon-company/goodmoneying-worker:${GOODMONEYING_IMAGE_TAG}"
    )
    assert (
        app["backfill-collection-worker"]["image"]
        == "ghcr.io/goodjoon-company/goodmoneying-worker:${GOODMONEYING_IMAGE_TAG}"
    )
    assert (
        app["risk-evaluation-worker"]["image"]
        == "ghcr.io/goodjoon-company/goodmoneying-worker:${GOODMONEYING_IMAGE_TAG}"
    )
    assert (
        web["web"]["image"] == "ghcr.io/goodjoon-company/goodmoneying-web:${GOODMONEYING_IMAGE_TAG}"
    )


def test_prod_home_target_local_scripts_use_local_compose_env() -> None:
    target_dir = ROOT / "deploy/profiles/prod-home/target"
    role_scripts = {
        "infra": ["start.sh", "stop.sh"],
        "app": [
            "migrate.sh",
            "start.sh",
            "stop.sh",
            "start-api.sh",
            "stop-api.sh",
            "start-upbit-gateway.sh",
            "stop-upbit-gateway.sh",
            "start-realtime-collection-worker.sh",
            "stop-realtime-collection-worker.sh",
            "start-backfill-collection-worker.sh",
            "stop-backfill-collection-worker.sh",
            "start-risk-evaluation-worker.sh",
            "stop-risk-evaluation-worker.sh",
        ],
        "web": ["start.sh", "stop.sh"],
    }

    for role, scripts in role_scripts.items():
        for script in scripts:
            path = target_dir / role / script
            text = path.read_text()
            assert os.access(path, os.X_OK)
            assert "SCRIPT_DIR=" in text
            assert "deploy.compose.env" in text
            assert 'source "$COMPOSE_ENV"' in text
            assert "GOODMONEYING_DOCKER_CONFIG" in text
            assert "export DOCKER_CONFIG=" in text
            assert "docker compose --env-file" in text
            assert '-f "$COMPOSE_FILE"' in text

    assert '"$@"' in (target_dir / "app/start-api.sh").read_text()
    assert "up -d api" in (target_dir / "app/start-api.sh").read_text()
    assert "stop api" in (target_dir / "app/stop-api.sh").read_text()
    assert "up -d upbit-gateway" in (target_dir / "app/start-upbit-gateway.sh").read_text()
    assert "stop upbit-gateway" in (target_dir / "app/stop-upbit-gateway.sh").read_text()
    assert (
        "up -d realtime-collection-worker"
        in (target_dir / "app/start-realtime-collection-worker.sh").read_text()
    )
    assert (
        "stop realtime-collection-worker"
        in (target_dir / "app/stop-realtime-collection-worker.sh").read_text()
    )
    assert (
        "up -d backfill-collection-worker"
        in (target_dir / "app/start-backfill-collection-worker.sh").read_text()
    )
    assert (
        "stop backfill-collection-worker"
        in (target_dir / "app/stop-backfill-collection-worker.sh").read_text()
    )
    assert (
        "up -d risk-evaluation-worker"
        in (target_dir / "app/start-risk-evaluation-worker.sh").read_text()
    )
    assert (
        "stop risk-evaluation-worker"
        in (target_dir / "app/stop-risk-evaluation-worker.sh").read_text()
    )
    migrate_script = (target_dir / "app/migrate.sh").read_text()
    assert "--profile migration run --rm migrate" in migrate_script
    for script_name in (
        "start.sh",
        "start-api.sh",
        "start-realtime-collection-worker.sh",
        "start-backfill-collection-worker.sh",
        "start-risk-evaluation-worker.sh",
    ):
        script = (target_dir / "app" / script_name).read_text()
        assert '"$SCRIPT_DIR/migrate.sh"' in script
        assert script.index('"$SCRIPT_DIR/migrate.sh"') < script.index("up -d")


def test_deploy_script_rejects_unknown_profile() -> None:
    result = run_deploy_script("unknown", "release-abc1234")

    assert result.returncode != 0
    assert "지원하지 않는 배포 프로필입니다: unknown" in result.stderr


def test_deploy_script_rejects_invalid_image_tag() -> None:
    result = run_deploy_script("prod-home", "release-bad;rm")

    assert result.returncode != 0
    assert "잘못된 이미지 태그입니다." in result.stderr
    assert "release-bad;rm" in result.stderr


def test_deploy_script_rejects_short_sha_image_tag() -> None:
    result = run_deploy_script("prod-home", "release-abc1234")

    assert result.returncode != 0
    assert "40자리" in result.stderr


def test_deploy_script_dry_run_prints_prod_home_steps() -> None:
    result = run_deploy_script("prod-home", f"release-{'a' * 40}")

    assert result.returncode == 0
    assert "profile=prod-home" in result.stdout
    assert f"tag=release-{'a' * 40}" in result.stdout
    assert "infra host=Mac-Mini-M4.local compose=compose.infra.yml" in result.stdout
    assert "app host=app-server01 compose=compose.app.yml" in result.stdout
    assert "web host=bmax-ubuntu compose=compose.web.yml" in result.stdout


def test_deploy_script_dry_run_prints_remote_commands() -> None:
    result = run_deploy_script("prod-home", f"release-{'d' * 40}")

    assert result.returncode == 0
    assert "ssh Mac-Mini-M4.local" in result.stdout
    assert "deploy.compose.env" in result.stdout
    assert (
        "PATH=/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH docker compose"
    ) in result.stdout
    assert (
        "docker compose --env-file "
        "'/Users/goodjoon/DATA/applications/goodmoneying/deploy.compose.env'"
    ) in result.stdout
    assert (
        "DOCKER_CONFIG='/Users/goodjoon/DATA/applications/goodmoneying/.docker' "
        "PATH=/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH "
        "docker compose"
    ) in result.stdout
    assert (
        "docker compose --env-file '/home/goodjoon/project/goodmoneying/deploy.compose.env'"
    ) in result.stdout
    assert (
        "DOCKER_CONFIG='/home/goodjoon/.docker' "
        "PATH=/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH "
        "docker compose"
    ) in result.stdout
    assert (
        "docker compose --env-file '/home/goodjoon/applications/goodmoneying/deploy.compose.env'"
    ) in result.stdout
    assert "ssh app-server01" in result.stdout
    assert "ssh bmax-ubuntu" in result.stdout
    assert (
        "ssh Mac-Mini-M4.local \"mkdir -p '/Users/goodjoon/DATA/applications/goodmoneying'\""
    ) in result.stdout
    assert "mkdir -p '/Users/goodjoon/DATA/applications/goodmoneying/env'" in result.stdout
    assert (
        "mkdir -p '/Users/goodjoon/DATA/applications/goodmoneying/infra/postgres-data'"
        in result.stdout
    )
    assert "mkdir -p '/Users/goodjoon/DATA/applications/goodmoneying/infra/config'" in result.stdout
    assert "mkdir -p '/home/goodjoon/project/goodmoneying/app/api-data'" in result.stdout
    assert (
        "mkdir -p '/home/goodjoon/project/goodmoneying/app/realtime-collection-worker-data'"
    ) in result.stdout
    assert (
        "mkdir -p '/home/goodjoon/project/goodmoneying/app/backfill-collection-worker-data'"
    ) in result.stdout
    assert (
        "mkdir -p '/home/goodjoon/project/goodmoneying/app/risk-evaluation-worker-data'"
    ) in result.stdout
    assert "mkdir -p '/home/goodjoon/project/goodmoneying/app/config'" in result.stdout
    assert "mkdir -p '/home/goodjoon/project/goodmoneying/env'" in result.stdout
    assert "mkdir -p '/home/goodjoon/applications/goodmoneying/web/nginx-cache'" in result.stdout
    assert "mkdir -p '/home/goodjoon/applications/goodmoneying/web/config'" in result.stdout
    assert "mkdir -p '/home/goodjoon/applications/goodmoneying/env'" in result.stdout
    assert "logs" not in result.stdout
    assert (
        f"scp {ROOT}/deploy/profiles/prod-home/runner/hosts.env "
        "Mac-Mini-M4.local:/Users/goodjoon/DATA/applications/goodmoneying/"
        "deploy.hosts.env"
    ) in result.stdout
    assert (
        f"scp {ROOT}/deploy/profiles/prod-home/env-samples/infra.env.sample "
        "Mac-Mini-M4.local:/Users/goodjoon/DATA/applications/goodmoneying/"
        "env/infra.env.sample"
    ) in result.stdout
    assert (
        f"scp {ROOT}/deploy/profiles/prod-home/env-samples/app.env.sample "
        "app-server01:/home/goodjoon/project/goodmoneying/env/app.env.sample"
    ) in result.stdout
    assert (
        f"scp {ROOT}/deploy/profiles/prod-home/env-samples/web.env.sample "
        "bmax-ubuntu:/home/goodjoon/applications/goodmoneying/env/web.env.sample"
    ) in result.stdout
    assert (
        'ssh Mac-Mini-M4.local "printf '
        f"'GOODMONEYING_IMAGE_TAG=%s\\n' 'release-{'d' * 40}' >> "
        "'/Users/goodjoon/DATA/applications/goodmoneying/deploy.compose.env'\""
    ) in result.stdout
    assert (
        "rm -f '/Users/goodjoon/DATA/applications/goodmoneying/.docker/"
        "cli-plugins/docker-compose' && ln -s "
        "/Applications/Docker.app/Contents/Resources/cli-plugins/"
        "docker-compose "
        "'/Users/goodjoon/DATA/applications/goodmoneying/.docker/"
        "cli-plugins/docker-compose'"
    ) in result.stdout
    assert (
        'ssh Mac-Mini-M4.local "printf '
        "'GOODMONEYING_DOCKER_CONFIG=%s\\n' "
        "'/Users/goodjoon/DATA/applications/goodmoneying/.docker' >> "
        "'/Users/goodjoon/DATA/applications/goodmoneying/deploy.compose.env'\""
    ) in result.stdout
    assert (
        f"scp {ROOT}/deploy/profiles/prod-home/target/infra/compose.yml "
        "Mac-Mini-M4.local:/Users/goodjoon/DATA/applications/goodmoneying/"
        "compose.infra.yml"
    ) in result.stdout
    assert (
        f"scp {ROOT}/deploy/profiles/prod-home/target/infra/start.sh "
        "Mac-Mini-M4.local:/Users/goodjoon/DATA/applications/goodmoneying/"
        "start.sh"
    ) in result.stdout
    assert (
        f"scp {ROOT}/deploy/profiles/prod-home/target/app/start-api.sh "
        "app-server01:/home/goodjoon/project/goodmoneying/start-api.sh"
    ) in result.stdout
    assert (
        f"scp {ROOT}/deploy/profiles/prod-home/target/web/stop.sh "
        "bmax-ubuntu:/home/goodjoon/applications/goodmoneying/stop.sh"
    ) in result.stdout
    assert "compose.infra.yml' pull" in result.stdout
    assert "compose.infra.yml' up -d" in result.stdout
    assert "compose.app.yml" in result.stdout
    assert "compose.web.yml" in result.stdout
    assert result.stdout.index("ssh Mac-Mini-M4.local") < result.stdout.index("ssh app-server01")
    assert result.stdout.index("ssh app-server01") < result.stdout.index("ssh bmax-ubuntu")
    app_pull = result.stdout.index("compose.app.yml' --profile migration pull")
    app_migrate = result.stdout.index("compose.app.yml' --profile migration run --rm migrate")
    app_up = result.stdout.index("compose.app.yml' up -d")
    assert app_pull < app_migrate < app_up


def test_healthcheck_script_dry_run_prints_checks() -> None:
    result = run_healthcheck_script("prod-home")

    assert result.returncode == 0
    assert (
        "retry 30 2s curl -fsS --connect-timeout 5 --max-time 10 http://100.115.38.59:8000/health"
    ) in result.stdout
    assert (
        "retry 30 2s curl -fsS --connect-timeout 5 --max-time 10 http://100.68.208.102:8080/"
    ) in result.stdout
    assert (
        "retry 30 2s curl -fsS --connect-timeout 5 --max-time 10 http://100.115.38.59:8001/health"
    ) in result.stdout
    assert "ssh -o BatchMode=yes -o ConnectTimeout=10" in result.stdout
    assert (
        "ssh -o BatchMode=yes -o ConnectTimeout=10 Mac-Mini-M4.local "
        "PATH=/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH "
        "docker exec goodmoneying-postgres sh -c "
        '\'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"\''
    ) in result.stdout.splitlines()
    assert (
        "docker inspect -f '{{.State.Running}}' goodmoneying-realtime-collection-worker"
    ) in result.stdout
    assert (
        "docker inspect -f '{{.State.Running}}' goodmoneying-backfill-collection-worker"
    ) in result.stdout
    assert "goodmoneying-market-sync-worker" in result.stdout
    assert "goodmoneying-candle-aggregation-worker" in result.stdout
    assert "goodmoneying-risk-evaluation-worker" in result.stdout


def test_healthcheck_script_rejects_unknown_profile() -> None:
    result = run_healthcheck_script("unknown")

    assert result.returncode != 0
    assert "지원하지 않는 배포 프로필입니다: unknown" in result.stderr


def test_healthcheck_script_dry_run_prints_checks_in_order() -> None:
    result = run_healthcheck_script("prod-home")

    assert result.returncode == 0
    api_index = result.stdout.index("http://100.115.38.59:8000/health")
    gateway_index = result.stdout.index("http://100.115.38.59:8001/health")
    web_index = result.stdout.index("http://100.68.208.102:8080/")
    postgres_index = result.stdout.index("docker exec goodmoneying-postgres")
    realtime_worker_index = result.stdout.index("goodmoneying-realtime-collection-worker")
    backfill_worker_index = result.stdout.index("goodmoneying-backfill-collection-worker")
    market_sync_worker_index = result.stdout.index("goodmoneying-market-sync-worker")
    aggregation_worker_index = result.stdout.index("goodmoneying-candle-aggregation-worker")
    risk_worker_index = result.stdout.index("goodmoneying-risk-evaluation-worker")
    assert api_index < gateway_index < web_index < postgres_index < realtime_worker_index
    assert realtime_worker_index < backfill_worker_index
    assert backfill_worker_index < market_sync_worker_index < aggregation_worker_index
    assert aggregation_worker_index < risk_worker_index


def test_deploy_workflow_builds_and_checks_upbit_gateway() -> None:
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text()

    assert "uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests" in workflow
    assert "Build and push upbit gateway" in workflow
    assert "apps/upbit_gateway/Dockerfile" in workflow
    assert "goodmoneying-upbit-gateway:${IMAGE_TAG}" in workflow


def test_start_script_dry_run_uses_target_compose_env_in_start_order() -> None:
    result = run_start_script("prod-home")

    assert result.returncode == 0
    assert (
        "PATH=/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH "
        "docker compose --env-file"
    ) in result.stdout
    assert "DOCKER_CONFIG='/Users/goodjoon/DATA/applications/goodmoneying/.docker'" in result.stdout
    assert "DOCKER_CONFIG='/home/goodjoon/.docker'" in result.stdout
    assert "deploy.compose.env" in result.stdout
    assert "compose.infra.yml' up -d" in result.stdout
    assert "compose.app.yml' up -d" in result.stdout
    assert "compose.web.yml' up -d" in result.stdout
    assert result.stdout.index("Mac-Mini-M4.local") < result.stdout.index("app-server01")
    assert result.stdout.index("app-server01") < result.stdout.index("bmax-ubuntu")
    app_migrate = result.stdout.index("compose.app.yml' --profile migration run --rm migrate")
    app_up = result.stdout.index("compose.app.yml' up -d")
    assert app_migrate < app_up


def test_stop_script_dry_run_uses_target_compose_env_in_stop_order() -> None:
    result = run_stop_script("prod-home")

    assert result.returncode == 0
    assert (
        "PATH=/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH "
        "docker compose --env-file"
    ) in result.stdout
    assert "DOCKER_CONFIG='/Users/goodjoon/DATA/applications/goodmoneying/.docker'" in result.stdout
    assert "DOCKER_CONFIG='/home/goodjoon/.docker'" in result.stdout
    assert "deploy.compose.env" in result.stdout
    assert "compose.web.yml' stop" in result.stdout
    assert "compose.app.yml' stop" in result.stdout
    assert "compose.infra.yml' stop" in result.stdout
    assert result.stdout.index("bmax-ubuntu") < result.stdout.index("app-server01")
    assert result.stdout.index("app-server01") < result.stdout.index("Mac-Mini-M4.local")
