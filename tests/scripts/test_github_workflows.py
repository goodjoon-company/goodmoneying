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
    assert "uv run mypy apps/api apps/worker packages/shared tests" in runs
    assert "uv run pytest" in runs
    assert "npm test" in runs
    assert "npm run build" in runs
    assert "docker build -f apps/api/Dockerfile -t goodmoneying-api:ci ." in runs
    assert "docker build -f apps/worker/Dockerfile -t goodmoneying-worker:ci ." in runs
    assert "docker build -f apps/web/Dockerfile -t goodmoneying-web:ci ." in runs
