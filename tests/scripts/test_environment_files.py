from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", maxsplit=1)[0].removeprefix("export ").strip()
        keys.add(key)
    return keys


def test_local_env_sample_lists_code_configurable_runtime_keys() -> None:
    keys = _env_keys(ROOT / ".env.sample")

    assert {
        "GOODMONEYING_DEV_DIR",
        "GOODMONEYING_API_HOST",
        "GOODMONEYING_API_PORT",
        "GOODMONEYING_WEB_HOST",
        "GOODMONEYING_WEB_PORT",
        "GOODMONEYING_POSTGRES_PORT",
        "GOODMONEYING_DATABASE_URL",
        "GOODMONEYING_OPERATOR_TOKEN",
        "GOODMONEYING_TIMEZONE",
        "GOODMONEYING_DASHBOARD_REFRESH_CONFIG",
        "GOODMONEYING_LIVE_UPBIT",
        "GOODMONEYING_REALTIME_COLLECTION_INTERVAL_SECONDS",
        "GOODMONEYING_BACKFILL_POLL_SECONDS",
        "GOODMONEYING_BACKFILL_BATCH_SIZE",
        "GOODMONEYING_LOG_LEVEL",
        "GOODMONEYING_PYTHON_BIN",
        "VITE_DEV_API_PROXY_TARGET",
        "VITE_API_BASE_URL",
        "VITE_OPERATOR_TOKEN",
    }.issubset(keys)
    assert "GOODMONEYING_WORKER_INTERVAL_SECONDS" not in keys


def test_prod_home_profile_env_lists_deploy_script_runtime_keys() -> None:
    keys = _env_keys(ROOT / "deploy/profiles/prod-home/runner/profile.env")

    assert {
        "GOODMONEYING_DEPLOY_PROFILE",
        "GOODMONEYING_DEPLOY_ENVIRONMENT",
        "GOODMONEYING_DEPLOY_INFRASTRUCTURE",
        "GOODMONEYING_IMAGE_REGISTRY",
        "GOODMONEYING_API_IMAGE",
        "GOODMONEYING_WORKER_IMAGE",
        "GOODMONEYING_WEB_IMAGE",
        "GOODMONEYING_API_INTERNAL_URL",
        "GOODMONEYING_WEB_INTERNAL_URL",
        "GOODMONEYING_DEPLOY_DRY_RUN",
        "GOODMONEYING_HEALTHCHECK_RETRIES",
        "GOODMONEYING_HEALTHCHECK_RETRY_INTERVAL_SECONDS",
    }.issubset(keys)


def test_prod_home_hosts_env_lists_target_path_and_docker_keys() -> None:
    keys = _env_keys(ROOT / "deploy/profiles/prod-home/runner/hosts.env")

    assert {
        "GOODMONEYING_INFRA_HOST",
        "GOODMONEYING_INFRA_COMPOSE",
        "GOODMONEYING_INFRA_BASE_DIR",
        "GOODMONEYING_INFRA_DOCKER_CONFIG",
        "GOODMONEYING_INFRA_POSTGRES_DATA_DIR",
        "GOODMONEYING_INFRA_CONFIG_DIR",
        "GOODMONEYING_APP_HOST",
        "GOODMONEYING_APP_COMPOSE",
        "GOODMONEYING_APP_BASE_DIR",
        "GOODMONEYING_APP_DOCKER_CONFIG",
        "GOODMONEYING_APP_API_DATA_DIR",
        "GOODMONEYING_APP_REALTIME_COLLECTION_WORKER_DATA_DIR",
        "GOODMONEYING_APP_BACKFILL_COLLECTION_WORKER_DATA_DIR",
        "GOODMONEYING_APP_CONFIG_DIR",
        "GOODMONEYING_WEB_HOST",
        "GOODMONEYING_WEB_COMPOSE",
        "GOODMONEYING_WEB_BASE_DIR",
        "GOODMONEYING_WEB_DOCKER_CONFIG",
        "GOODMONEYING_WEB_NGINX_CACHE_DIR",
        "GOODMONEYING_WEB_CONFIG_DIR",
    }.issubset(keys)


def test_prod_home_env_samples_list_server_runtime_keys() -> None:
    samples_dir = ROOT / "deploy/profiles/prod-home/env-samples"
    infra_keys = _env_keys(samples_dir / "infra.env.sample")
    app_keys = _env_keys(samples_dir / "app.env.sample")
    web_keys = _env_keys(samples_dir / "web.env.sample")

    assert {"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"}.issubset(infra_keys)
    assert {
        "GOODMONEYING_DATABASE_URL",
        "GOODMONEYING_OPERATOR_TOKEN",
        "GOODMONEYING_DASHBOARD_REFRESH_CONFIG",
        "GOODMONEYING_LIVE_UPBIT",
        "GOODMONEYING_REALTIME_COLLECTION_INTERVAL_SECONDS",
        "GOODMONEYING_BACKFILL_POLL_SECONDS",
        "GOODMONEYING_BACKFILL_BATCH_SIZE",
        "GOODMONEYING_LOG_LEVEL",
    }.issubset(app_keys)
    assert {
        "GOODMONEYING_WEB_INTERNAL_URL",
        "GOODMONEYING_API_INTERNAL_URL",
        "GOODMONEYING_OPERATOR_TOKEN",
    }.issubset(web_keys)
