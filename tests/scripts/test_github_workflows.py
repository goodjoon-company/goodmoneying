from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_workflow(name: str) -> dict[object, object]:
    workflow_text = (ROOT / f".github/workflows/{name}").read_text()
    return cast(dict[object, object], yaml.safe_load(workflow_text))


def workflow_on(workflow: dict[object, object]) -> dict[str, object]:
    # PyYAML treats GitHub Actions' `on` key as a boolean under YAML 1.1.
    return cast(dict[str, object], workflow[True])


def workflow_job(workflow: dict[object, object], job_name: str) -> dict[str, object]:
    jobs = cast(dict[str, object], workflow["jobs"])
    return cast(dict[str, object], jobs[job_name])


def workflow_step_runs(workflow: dict[object, object], job_name: str) -> list[str]:
    job = workflow_job(workflow, job_name)
    steps = cast(list[dict[str, object]], job["steps"])
    return [cast(str, step["run"]) for step in steps if "run" in step]


def test_ci_workflow_runs_on_push_and_pull_request() -> None:
    workflow = load_workflow("ci.yml")
    triggers = workflow_on(workflow)

    assert "push" in triggers
    assert cast(dict[str, object], triggers["push"])["branches"] == ["**"]
    assert "pull_request" in triggers


def test_ci_workflow_uses_minimum_permissions_and_timeout() -> None:
    workflow = load_workflow("ci.yml")
    permissions = cast(dict[str, object], workflow["permissions"])
    job = workflow_job(workflow, "verify")

    assert permissions == {"contents": "read"}
    assert job["timeout-minutes"] == 30


def test_ci_workflow_has_required_quality_commands() -> None:
    workflow = load_workflow("ci.yml")
    runs = workflow_step_runs(workflow, "verify")

    assert "uv run ruff check ." in runs
    assert "uv run mypy apps/api apps/worker apps/upbit_gateway packages/shared tests" in runs
    assert "uv run pytest" in runs
    assert "npm test" in runs
    assert "npm run build" in runs
    assert "tests/e2e/run_dbmate_migration_e2e.sh" in runs
    assert "docker build -f apps/api/Dockerfile -t goodmoneying-api:ci ." in runs
    assert "docker build -f apps/worker/Dockerfile -t goodmoneying-worker:ci ." in runs
    assert (
        "docker build -f apps/upbit_gateway/Dockerfile -t goodmoneying-upbit-gateway:ci ."
        in runs
    )
    assert "docker build -f apps/web/Dockerfile -t goodmoneying-web:ci ." in runs
    assert (
        "docker build -f apps/migrations/Dockerfile -t goodmoneying-migrations:ci ."
        in runs
    )



def test_ci_workflow_runs_isolated_e2e() -> None:
    workflow = load_workflow("ci.yml")
    runs = workflow_step_runs(workflow, "verify")

    assert "npx playwright install --with-deps chromium" in runs
    assert "npm run e2e" in runs


def test_deploy_workflow_is_manual_and_requires_approved_sha() -> None:
    workflow = load_workflow("deploy.yml")
    triggers = workflow_on(workflow)
    dispatch = cast(dict[str, object], triggers["workflow_dispatch"])
    inputs = cast(dict[str, object], dispatch["inputs"])
    profile = cast(dict[str, object], inputs["profile"])
    approved_sha = cast(dict[str, object], inputs["approved_sha"])

    assert "push" not in triggers
    assert profile["options"] == ["prod-home"]
    assert approved_sha["required"] is True
    assert approved_sha["type"] == "string"


def test_deploy_workflow_runs_fail_closed_preflight_before_build() -> None:
    workflow = load_workflow("deploy.yml")
    job = workflow_job(workflow, "deploy")
    steps = cast(list[dict[str, object]], job["steps"])
    names = [cast(str, step.get("name", "")) for step in steps]
    preflight_index = names.index("Verify production deployment gates")
    image_tag_index = names.index("Set image tag")
    preflight = steps[preflight_index]

    assert preflight_index < image_tag_index
    assert preflight["run"] == "deploy/scripts/verify-prod-deploy-gates.sh"
    preflight_env = cast(dict[str, object], preflight["env"])
    assert preflight_env["APPROVED_SHA"] == "${{ inputs.approved_sha }}"
    assert preflight_env["DEPLOY_ENABLE_SHA"] == (
        "${{ vars.GOODMONEYING_PROD_DEPLOY_ENABLE_SHA }}"
    )
    assert preflight_env["GH_TOKEN"] == (
        "${{ secrets.GOODMONEYING_DEPLOY_GITHUB_TOKEN }}"
    )


def test_deploy_workflow_uses_self_hosted_runner_and_prod_home_concurrency() -> None:
    workflow = load_workflow("deploy.yml")
    job = workflow_job(workflow, "deploy")
    concurrency = cast(dict[str, object], workflow["concurrency"])

    assert "self-hosted" in cast(list[str], job["runs-on"])
    assert "mac-mini-m4" in cast(list[str], job["runs-on"])
    assert job["environment"] == "prod"
    assert job["timeout-minutes"] == 60
    assert concurrency["group"] == "deploy-prod-home-v3"
    assert concurrency["cancel-in-progress"] is False


def test_deploy_workflow_has_required_permissions_and_profile_env() -> None:
    workflow = load_workflow("deploy.yml")
    permissions = cast(dict[str, object], workflow["permissions"])
    job = workflow_job(workflow, "deploy")
    env = cast(dict[str, object], job["env"])

    assert permissions == {"checks": "read", "contents": "read", "packages": "write"}
    assert env["DEPLOY_PROFILE"] == "prod-home"
    assert env["REGISTRY"] == "ghcr.io"
    assert env["IMAGE_NAMESPACE"] == "goodjoon-company"
    assert env["DOCKER_CONFIG"] == "/Users/goodjoon/DATA/applications/goodmoneying/.docker"
    assert env["DOCKER_HOST"] == "unix:///Users/goodjoon/.docker/run/docker.sock"
    assert env["DOCKER_BUILDKIT"] == "0"
    assert env["BUILD_PLATFORMS"] == "linux/amd64,linux/arm64"
    assert env["RUNNER_LOGIN_SHELL_HOST"] == "goodjoon@Mac-Mini-M4.local"
    assert env["RUNNER_DOCKER_BIN"] == "/usr/local/bin/docker"


def test_github_actions_are_pinned_to_commit_sha() -> None:
    allowed_actions = {
        "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5",
        "actions/setup-node@a0853c24544627f65ddf259abe73b1d18a591444",
        "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
        "astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78",
    }
    for name in ("ci.yml", "deploy.yml"):
        workflow = load_workflow(name)
        for job in cast(dict[str, dict[str, object]], workflow["jobs"]).values():
            for step in cast(list[dict[str, object]], job["steps"]):
                action = step.get("uses")
                if action is None:
                    continue
                assert cast(str, action) in allowed_actions


def test_deploy_workflow_pushes_ghcr_and_runs_profile_scripts() -> None:
    workflow = load_workflow("deploy.yml")
    workflow_text = (ROOT / ".github/workflows/deploy.yml").read_text()
    runs = workflow_step_runs(workflow, "deploy")

    assert "docker/login-action" not in workflow_text
    assert 'echo "/Users/goodjoon/.local/bin" >> "$GITHUB_PATH"' in workflow_text
    assert (
        'echo "/Users/goodjoon/.nvm/versions/node/v24.14.1/bin" >> "$GITHUB_PATH"'
        in workflow_text
    )
    assert (
        'echo "/opt/homebrew/opt/python@3.14/libexec/bin" >> "$GITHUB_PATH"'
        in workflow_text
    )
    assert 'echo "/opt/homebrew/bin" >> "$GITHUB_PATH"' in workflow_text
    assert 'echo "/usr/local/bin" >> "$GITHUB_PATH"' in workflow_text
    assert (
        'echo "/Applications/Docker.app/Contents/Resources/bin" >> "$GITHUB_PATH"'
        in workflow_text
    )
    assert 'echo "IMAGE_TAG=release-${GITHUB_SHA}" >> "$GITHUB_ENV"' in runs
    assert "python3 --version\nuv --version\nnode --version\nnpm --version\n" in runs
    assert "npx playwright install chromium" in runs
    assert "Prepare runner Docker CLI plugins" in workflow_text
    assert "docker-buildx" in workflow_text
    assert "docker-compose" in workflow_text
    assert "Verify runner login shell Docker" in workflow_text
    assert "ssh -o BatchMode=yes -o ConnectTimeout=10" in workflow_text
    assert "'${RUNNER_DOCKER_BIN}' buildx version" in workflow_text
    assert "deploy/scripts/deploy-profile.sh prod-home \"${IMAGE_TAG}\"" in runs
    assert "deploy/scripts/healthcheck-profile.sh prod-home" in runs
    assert "ghcr.io/${IMAGE_NAMESPACE}/goodmoneying-api:${IMAGE_TAG}" in workflow_text
    assert "ghcr.io/${IMAGE_NAMESPACE}/goodmoneying-worker:${IMAGE_TAG}" in workflow_text
    assert "ghcr.io/${IMAGE_NAMESPACE}/goodmoneying-web:${IMAGE_TAG}" in workflow_text
    assert "ghcr.io/${IMAGE_NAMESPACE}/goodmoneying-migrations:${IMAGE_TAG}" in workflow_text
    assert "cd '${GITHUB_WORKSPACE}'" in workflow_text
    assert (
        "'${RUNNER_DOCKER_BIN}' buildx build --platform '${BUILD_PLATFORMS}' "
        "--push -f apps/api/Dockerfile"
    ) in workflow_text
    assert "'${RUNNER_DOCKER_BIN}' push ghcr.io" not in workflow_text
    assert (
        "'${RUNNER_DOCKER_BIN}' buildx build --platform '${BUILD_PLATFORMS}' "
        "--push --build-arg VITE_API_BASE_URL=/api"
        in workflow_text
    )
    assert (
        "'${RUNNER_DOCKER_BIN}' buildx build --platform '${BUILD_PLATFORMS}' "
        "--push -f apps/migrations/Dockerfile"
        in workflow_text
    )


def test_deploy_workflow_runs_e2e_against_deployed_urls() -> None:
    workflow = load_workflow("deploy.yml")
    workflow_text = (ROOT / ".github/workflows/deploy.yml").read_text()
    runs = workflow_step_runs(workflow, "deploy")

    assert 'E2E_SKIP_WEBSERVER: "1"' in workflow_text
    assert "E2E_API_BASE_URL: http://100.115.38.59:8000" in workflow_text
    assert "E2E_WEB_BASE_URL: http://100.68.208.102:8080" in workflow_text
    assert "Load prod-home E2E operator token" in workflow_text
    assert "GOODMONEYING_OPERATOR_TOKEN" in workflow_text
    assert "::add-mask::$token" in workflow_text
    assert "E2E_OPERATOR_TOKEN=$token" in workflow_text
    assert "npm run e2e" in runs
