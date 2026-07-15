from pathlib import Path

import yaml


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
