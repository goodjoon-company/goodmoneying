#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml


@dataclass(frozen=True)
class DockerImageFinding:
    path: str
    reason: str


@dataclass(frozen=True)
class SecretFinding:
    path: str
    line: int
    key: str


_RUNTIME_DOCKERFILES = (
    "apps/api/Dockerfile",
    "apps/worker/Dockerfile",
    "apps/upbit_gateway/Dockerfile",
    "apps/web/Dockerfile",
)
_LOCKFILES = ("package-lock.json", "uv.lock")
_SECRET_KEYS = (
    "UPBIT_ACCESS_KEY",
    "UPBIT_SECRET_KEY",
    "GOODMONEYING_OPERATOR_TOKEN",
    "UPBIT_GATEWAY_OPERATOR_TOKEN",
    "E2E_OPERATOR_TOKEN",
)
_SECRET_ASSIGNMENT = re.compile(
    r"^\s*(?:-\s*)?(?P<key>"
    + "|".join(re.escape(key) for key in _SECRET_KEYS)
    + r")\s*(?::|=)\s*(?P<value>.+?)\s*$"
)
_TEXT_SUFFIXES = {
    "",
    ".conf",
    ".env",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "dist",
    "node_modules",
    "playwright-report",
    "test-results",
}


def check_dependency_security(root: Path) -> dict[str, Any]:
    missing_lockfiles = [path for path in _LOCKFILES if not (root / path).is_file()]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    run_steps = [
        step["run"]
        for step in workflow["jobs"]["verify"]["steps"]
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    ]
    required_ci_install = ["npm ci", "uv sync --frozen"]
    missing_ci_install = [command for command in required_ci_install if command not in run_steps]

    npm_audit = _run_npm_audit(root)
    vulnerabilities = cast(
        dict[str, int],
        npm_audit.get("metadata", {}).get("vulnerabilities", {}),
    )
    high_or_critical = vulnerabilities.get("high", 0) + vulnerabilities.get("critical", 0)
    ok = not missing_lockfiles and not missing_ci_install and high_or_critical == 0

    return {
        "ok": ok,
        "lockfiles": list(_LOCKFILES),
        "missing_lockfiles": missing_lockfiles,
        "ci_install": required_ci_install,
        "missing_ci_install": missing_ci_install,
        "npm_audit": npm_audit,
    }


def check_docker_image_security(
    root: Path,
    *,
    dockerfiles: tuple[str, ...] = _RUNTIME_DOCKERFILES,
) -> dict[str, Any]:
    findings: list[DockerImageFinding] = []
    web_container_port: int | None = None
    for relative_path in dockerfiles:
        dockerfile = root / relative_path
        if not dockerfile.is_file():
            findings.append(DockerImageFinding(relative_path, "Dockerfile이 없습니다."))
            continue
        text = dockerfile.read_text()
        instructions = _docker_instructions(text)
        user_values = [
            instruction.removeprefix("USER").strip()
            for instruction in instructions
            if instruction.startswith("USER ")
        ]
        if not user_values:
            findings.append(DockerImageFinding(relative_path, "USER 지시문이 없습니다."))
        elif user_values[-1] in {"0", "root"}:
            findings.append(DockerImageFinding(relative_path, "root USER로 실행됩니다."))

        if any(
            instruction.startswith("ENV GOODMONEYING_OPERATOR_TOKEN=")
            for instruction in instructions
        ):
            findings.append(
                DockerImageFinding(relative_path, "운영자 토큰을 이미지 ENV 기본값으로 넣습니다.")
            )

        if relative_path == "apps/web/Dockerfile":
            expose_values = [
                instruction.removeprefix("EXPOSE").strip()
                for instruction in instructions
                if instruction.startswith("EXPOSE ")
            ]
            web_container_port = int(expose_values[-1]) if expose_values else None
            if web_container_port != 8080:
                findings.append(
                    DockerImageFinding(
                        relative_path,
                        "web 런타임 이미지는 비특권 포트 8080을 노출해야 합니다.",
                    )
                )

    return {
        "ok": not findings,
        "runtime_images": list(dockerfiles),
        "web_container_port": web_container_port,
        "findings": findings,
    }


def check_secret_exposure(
    root: Path,
    *,
    scan_paths: tuple[str, ...] = (
        ".github/workflows",
        "apps",
        "deploy/profiles/prod-home/target",
        "docker-compose.yml",
        "docker-compose.upbit-secrets.yml",
    ),
) -> dict[str, Any]:
    findings: list[SecretFinding] = []
    for path in _iter_scan_files(root, scan_paths):
        relative_path = path.relative_to(root).as_posix()
        for line_number, line in enumerate(path.read_text(errors="ignore").splitlines(), start=1):
            match = _SECRET_ASSIGNMENT.match(line)
            if match is None:
                continue
            if _is_safe_placeholder(match.group("value")):
                continue
            findings.append(SecretFinding(relative_path, line_number, match.group("key")))
    return {"ok": not findings, "findings": findings}


def check_auth_input_security(root: Path) -> dict[str, Any]:
    command = [
        "uv",
        "run",
        "pytest",
        "tests/upbit_gateway/test_auth.py",
        "tests/upbit_gateway/test_websocket_security.py",
        "tests/api",
        "-q",
    ]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    return {
        "ok": completed.returncode == 0,
        "command": " ".join(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="P7 보안 품질 게이트를 검증합니다.")
    parser.add_argument(
        "gate",
        choices=("dependencies", "images", "secrets", "auth-input", "all"),
    )
    args = parser.parse_args()

    root = Path.cwd()
    gate = cast(Literal["dependencies", "images", "secrets", "auth-input", "all"], args.gate)
    results: dict[str, Any] = {}
    if gate in {"dependencies", "all"}:
        results["dependencies"] = check_dependency_security(root)
    if gate in {"images", "all"}:
        results["images"] = check_docker_image_security(root)
    if gate in {"secrets", "all"}:
        results["secrets"] = check_secret_exposure(root)
    if gate in {"auth-input", "all"}:
        results["auth_input"] = check_auth_input_security(root)

    ok = all(cast(dict[str, Any], result)["ok"] for result in results.values())
    print(json.dumps({"ok": ok, "results": _json_safe(results)}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _run_npm_audit(root: Path) -> dict[str, Any]:
    completed = subprocess.run(
        ["npm", "audit", "--audit-level=high", "--json"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        parsed = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        parsed = {"metadata": {"vulnerabilities": {"high": 1, "critical": 1}}}
    parsed["returncode"] = completed.returncode
    return cast(dict[str, Any], parsed)


def _docker_instructions(text: str) -> tuple[str, ...]:
    instructions: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        instructions.append(stripped)
    return tuple(instructions)


def _iter_scan_files(root: Path, scan_paths: tuple[str, ...]) -> tuple[Path, ...]:
    files: list[Path] = []
    for relative_scan_path in scan_paths:
        scan_root = root / relative_scan_path
        if not scan_root.exists():
            continue
        if scan_root.is_file():
            if scan_root.suffix in _TEXT_SUFFIXES:
                files.append(scan_root)
            continue
        for path in scan_root.rglob("*"):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
                continue
            files.append(path)
    return tuple(sorted(set(files)))


def _is_safe_placeholder(raw_value: str) -> bool:
    value = raw_value.strip().strip('"').strip("'")
    if value == "":
        return True
    if value.startswith("${") and value.endswith("}"):
        return True
    if value in {"local-dev-token", "migration-e2e-token"}:
        return True
    return "example" in value or "sample" in value or "rotate" in value


def _json_safe(value: object) -> object:
    if isinstance(value, DockerImageFinding | SecretFinding):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
