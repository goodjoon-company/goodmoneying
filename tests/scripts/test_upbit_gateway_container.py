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
    assert service["ports"] == ["8001:8001"]
    assert service["healthcheck"]["test"][-1].find("127.0.0.1:8001/health") >= 0
    assert service["environment"]["UPBIT_ACCESS_KEY"] == "${UPBIT_ACCESS_KEY:-}"
    assert service["environment"]["UPBIT_SECRET_KEY"] == "${UPBIT_SECRET_KEY:-}"


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
