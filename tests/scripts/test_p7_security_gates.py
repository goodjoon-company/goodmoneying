from __future__ import annotations

import json
from pathlib import Path

import yaml
from scripts.verify_p7_security_gates import (
    DockerImageFinding,
    SecretFinding,
    check_dependency_security,
    check_docker_image_security,
    check_secret_exposure,
)

ROOT = Path(__file__).resolve().parents[2]


def test_P7_보안_gate는_package_script와_증적_파일에_연결된다() -> None:
    manifest = yaml.safe_load(
        (ROOT / "docs/contracts/quality/p7-quality-evidence.yaml").read_text()
    )
    package = json.loads((ROOT / "package.json").read_text())
    scripts = package["scripts"]
    gates = {gate["id"]: gate for gate in manifest["gates"]}

    expected_commands = {
        "security.dependencies": "npm run p7:dependency-security",
        "security.images": "npm run p7:image-security",
        "security.secrets": "npm run p7:secret-scan",
        "security.auth_input": "npm run p7:auth-input",
    }

    for gate_id, command in expected_commands.items():
        gate = gates[gate_id]
        script_name = command.removeprefix("npm run ")
        assert gate["status"] == "passed"
        assert gate["command"] == command
        assert script_name in scripts
        assert (ROOT / gate["evidence_path"]).is_file()


def test_P7_dependency_security는_lockfile과_high_이상_npm_audit을_검증한다() -> None:
    result = check_dependency_security(ROOT)

    assert result["ok"] is True
    assert result["npm_audit"]["metadata"]["vulnerabilities"]["high"] == 0
    assert result["npm_audit"]["metadata"]["vulnerabilities"]["critical"] == 0
    assert result["lockfiles"] == ["package-lock.json", "uv.lock"]
    assert result["ci_install"] == ["npm ci", "uv sync --frozen"]


def test_P7_image_security는_런타임_Dockerfile의_root_실행과_비특권_port를_강제한다() -> None:
    result = check_docker_image_security(ROOT)

    assert result["ok"] is True
    assert result["runtime_images"] == [
        "apps/api/Dockerfile",
        "apps/worker/Dockerfile",
        "apps/upbit_gateway/Dockerfile",
        "apps/web/Dockerfile",
    ]
    assert result["web_container_port"] == 8080


def test_P7_web_image_security는_nginx_pid_경로를_비root_사용자에게_열어둔다() -> None:
    dockerfile = (ROOT / "apps/web/Dockerfile").read_text()

    assert "/run" in dockerfile
    assert "nginx.pid" in dockerfile


def test_P7_image_security는_USER_누락과_web_80_port를_거부한다(tmp_path: Path) -> None:
    dockerfile = tmp_path / "apps/web/Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM nginx:alpine\nEXPOSE 80\n", encoding="utf-8")

    findings = check_docker_image_security(
        tmp_path,
        dockerfiles=("apps/web/Dockerfile",),
    )["findings"]

    assert DockerImageFinding(
        path="apps/web/Dockerfile",
        reason="USER 지시문이 없습니다.",
    ) in findings
    assert DockerImageFinding(
        path="apps/web/Dockerfile",
        reason="web 런타임 이미지는 비특권 포트 8080을 노출해야 합니다.",
    ) in findings


def test_P7_secret_scan은_평문_업비트_key와_운영자_token을_거부한다(tmp_path: Path) -> None:
    env_file = tmp_path / "deploy/profiles/prod-home/target/app/compose.yml"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(
        "environment:\n"
        "  UPBIT_ACCESS_KEY: live-access-key\n"
        "  UPBIT_SECRET_KEY: live-secret-key\n"
        "  GOODMONEYING_OPERATOR_TOKEN: live-token\n",
        encoding="utf-8",
    )

    findings = check_secret_exposure(tmp_path, scan_paths=("deploy",))["findings"]

    assert SecretFinding(
        path="deploy/profiles/prod-home/target/app/compose.yml",
        line=2,
        key="UPBIT_ACCESS_KEY",
    ) in findings
    assert SecretFinding(
        path="deploy/profiles/prod-home/target/app/compose.yml",
        line=3,
        key="UPBIT_SECRET_KEY",
    ) in findings
    assert SecretFinding(
        path="deploy/profiles/prod-home/target/app/compose.yml",
        line=4,
        key="GOODMONEYING_OPERATOR_TOKEN",
    ) in findings


def test_P7_secret_scan은_env_치환과_empty_placeholder를_허용한다(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.upbit-secrets.yml"
    compose.write_text(
        "services:\n"
        "  upbit-gateway:\n"
        "    environment:\n"
        "      UPBIT_ACCESS_KEY: ${UPBIT_ACCESS_KEY:-}\n"
        "      UPBIT_SECRET_KEY: \"\"\n"
        "      GOODMONEYING_OPERATOR_TOKEN: ${GOODMONEYING_OPERATOR_TOKEN:?required}\n",
        encoding="utf-8",
    )

    result = check_secret_exposure(tmp_path, scan_paths=("docker-compose.upbit-secrets.yml",))

    assert result["ok"] is True
    assert result["findings"] == []
