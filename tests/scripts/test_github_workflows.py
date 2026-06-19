from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_workflow(name: str) -> dict[object, object]:
    workflow_text = (ROOT / f".github/workflows/{name}").read_text()
    return cast(dict[object, object], yaml.safe_load(workflow_text))


def workflow_on(workflow: dict[object, object]) -> dict[str, object]:
    return cast(dict[str, object], workflow[True])


def test_ci_workflow_runs_on_push_and_pull_request() -> None:
    workflow = load_workflow("ci.yml")
    triggers = workflow_on(workflow)

    assert "push" in triggers
    assert "pull_request" in triggers


def test_ci_workflow_has_required_quality_commands() -> None:
    workflow_text = (ROOT / ".github/workflows/ci.yml").read_text()

    assert "uv run ruff check ." in workflow_text
    assert "uv run mypy apps/api apps/worker packages/shared tests" in workflow_text
    assert "uv run pytest" in workflow_text
    assert "npm test" in workflow_text
    assert "npm run build" in workflow_text
    assert "docker build -f apps/api/Dockerfile" in workflow_text
    assert "docker build -f apps/worker/Dockerfile" in workflow_text
    assert "docker build -f apps/web/Dockerfile" in workflow_text
