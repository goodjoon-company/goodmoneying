from pathlib import Path

import yaml

from goodmoneying_upbit_gateway.auth import Credentials, load_credentials


def test_upbit_gateway_has_container_and_local_compose_service() -> None:
    dockerfile = Path("apps/upbit_gateway/Dockerfile").read_text()
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())
    service = compose["services"]["upbit-gateway"]

    assert "goodmoneying_upbit_gateway.main:app" in dockerfile
    assert "EXPOSE 8001" in dockerfile
    assert service["build"]["dockerfile"] == "apps/upbit_gateway/Dockerfile"
    assert service["ports"] == ["127.0.0.1:8001:8001"]
    assert service["healthcheck"]["test"][-1].find("127.0.0.1:8001/health") >= 0
    assert service["environment"]["UPBIT_ACCESS_KEY"] == "${UPBIT_ACCESS_KEY:-}"
    assert service["environment"]["UPBIT_SECRET_KEY"] == "${UPBIT_SECRET_KEY:-}"
    assert service["environment"]["UPBIT_GATEWAY_OPERATOR_TOKEN"] == (
        "${GOODMONEYING_OPERATOR_TOKEN:-local-dev-token}"
    )
    assert service["environment"]["UPBIT_GATEWAY_ALLOWED_ORIGINS"] == (
        "${UPBIT_GATEWAY_ALLOWED_ORIGINS:-http://localhost:5173,http://127.0.0.1:5173}"
    )

    web = compose["services"]["web"]
    assert web["environment"]["GOODMONEYING_UPBIT_GATEWAY_INTERNAL_URL"] == (
        "http://upbit-gateway:8001"
    )
    assert "upbit-gateway" in web["depends_on"]


def test_web_proxies_gateway_upgrade_and_injects_operator_token_server_side() -> None:
    nginx = Path("apps/web/nginx.conf.template").read_text()
    vite = Path("apps/web/vite.config.ts").read_text()
    workbench = Path(
        "apps/web/src/features/upbitWebSocket/UpbitWebSocketWorkbench.tsx"
    ).read_text()

    gateway_location = nginx.split("location /upbit-gateway/ {", maxsplit=1)[1].split(
        "\n  }", maxsplit=1
    )[0]
    assert "proxy_pass ${GOODMONEYING_UPBIT_GATEWAY_INTERNAL_URL};" in gateway_location
    assert "proxy_set_header Upgrade $http_upgrade;" in gateway_location
    assert 'proxy_set_header Connection "upgrade";' in gateway_location
    assert 'proxy_set_header X-Operator-Token "${GOODMONEYING_OPERATOR_TOKEN}";' in (
        gateway_location
    )
    assert "proxy_set_header X-Forwarded-Host $http_host;" in gateway_location
    assert "proxy_read_timeout 3600s;" in gateway_location
    assert "proxy_send_timeout 3600s;" in gateway_location
    assert '"/upbit-gateway"' in vite
    assert "ws: true" in vite
    assert '"X-Operator-Token": operatorToken' in vite
    api_proxy = vite.split('"/api": {', maxsplit=1)[1].split('"/upbit-gateway": {', maxsplit=1)[0]
    assert '"X-Operator-Token": operatorToken' in api_proxy
    assert "VITE_OPERATOR_TOKEN" not in vite
    assert "VITE_OPERATOR_TOKEN" not in Path("apps/web/src/api.ts").read_text()
    assert "X-Operator-Token" not in workbench


def test_compose_secret_override_mounts_read_only_key_files() -> None:
    override = yaml.safe_load(Path("docker-compose.upbit-secrets.yml").read_text())
    service = override["services"]["upbit-gateway"]

    assert service["environment"] == {
        "UPBIT_ACCESS_KEY_FILE": "/run/secrets/upbit_access_key",
        "UPBIT_SECRET_KEY_FILE": "/run/secrets/upbit_secret_key",
        "UPBIT_ACCESS_KEY": "",
        "UPBIT_SECRET_KEY": "",
    }
    assert service["secrets"] == ["upbit_access_key", "upbit_secret_key"]
    assert override["secrets"]["upbit_access_key"]["file"] == "${UPBIT_ACCESS_KEY_FILE}"
    assert override["secrets"]["upbit_secret_key"]["file"] == "${UPBIT_SECRET_KEY_FILE}"


def test_compose_secret_override_clears_inherited_direct_keys_for_file_runtime(
    tmp_path: Path,
) -> None:
    override = yaml.safe_load(Path("docker-compose.upbit-secrets.yml").read_text())
    service_environment = override["services"]["upbit-gateway"]["environment"]
    access_file = tmp_path / "access"
    secret_file = tmp_path / "secret"
    access_file.write_text("fake-file-access", encoding="utf-8")
    secret_file.write_text("s" * 64, encoding="utf-8")
    access_file.chmod(0o400)
    secret_file.chmod(0o400)
    runtime_environment = {
        "UPBIT_ACCESS_KEY": "inherited-host-access",
        "UPBIT_SECRET_KEY": "inherited-host-secret",
        **service_environment,
        "UPBIT_ACCESS_KEY_FILE": str(access_file),
        "UPBIT_SECRET_KEY_FILE": str(secret_file),
    }

    assert load_credentials(runtime_environment) == Credentials(
        "fake-file-access", "s" * 64
    )
