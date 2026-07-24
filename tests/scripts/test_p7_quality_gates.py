from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from scripts.verify_p7_quality_gates import (
    REQUIRED_GATE_IDS,
    QualityGateError,
    load_manifest,
    scan_unresolved_artifacts,
    validate_readiness_manifest,
    validate_release_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


def test_P7_품질_매니페스트는_모든_운영품질_gate를_정의한다() -> None:
    manifest = load_manifest(ROOT / "docs/contracts/quality/p7-quality-evidence.yaml")
    result = validate_readiness_manifest(manifest, root=ROOT)

    assert result.gate_ids == REQUIRED_GATE_IDS
    assert result.required_evidence_paths
    assert all(path.startswith("docs/Test/") for path in result.required_evidence_paths)
    assert "성능(Web Vitals)" in result.gate_labels
    assert "복구 리허설(restore rehearsal)" in result.gate_labels


def test_P7_품질_매니페스트는_CI_readiness_gate와_동일한_파일을_사용한다() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    runs = [
        step["run"]
        for step in workflow["jobs"]["verify"]["steps"]
        if isinstance(step, dict) and "run" in step
    ]

    assert (
        "uv run python scripts/verify_p7_quality_gates.py "
        "--mode readiness --manifest docs/contracts/quality/p7-quality-evidence.yaml"
    ) in runs


def test_P7_release_gate는_모든_gate_증적이_passed일_때까지_실패한다() -> None:
    manifest = load_manifest(ROOT / "docs/contracts/quality/p7-quality-evidence.yaml")

    with pytest.raises(QualityGateError, match="P7 release gate 미통과"):
        validate_release_manifest(manifest, root=ROOT)


def test_P7_unresolved_스캐너는_제품코드의_TODO와_mock_경계를_거부한다(
    tmp_path: Path,
) -> None:
    production = tmp_path / "apps/api/goodmoneying_api"
    production.mkdir(parents=True)
    (production / "main.py").write_text(
        "def endpoint():\n"
        "    # TODO: 운영에서 지워야 하는 임시 절차\n"
        "    return {'mode': 'mock'}\n",
    )

    tests_dir = tmp_path / "tests/api"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_main.py").write_text("def test_uses_mock_transport():\n    pass\n")

    with pytest.raises(QualityGateError, match="main.py"):
        scan_unresolved_artifacts(tmp_path)
