from __future__ import annotations

from pathlib import Path

import yaml
from scripts.verify_p7_quality_gates import (
    load_manifest,
    scan_from_manifest,
    validate_release_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


def test_P7_backup_restore와_hygiene_gate가_release_manifest에_연결된다() -> None:
    manifest = yaml.safe_load(
        (ROOT / "docs/contracts/quality/p7-quality-evidence.yaml").read_text()
    )
    gates = {gate["id"]: gate for gate in manifest["gates"]}

    assert gates["recovery.backup_restore"] == {
        "id": "recovery.backup_restore",
        "label": "복구 리허설(restore rehearsal)",
        "status": "passed",
        "command": "tests/e2e/run_dbmate_migration_e2e.sh",
        "evidence_path": "docs/Test/2026-07-25-P7-backup-restore-검증.md",
    }
    assert gates["hygiene.unresolved_artifacts"] == {
        "id": "hygiene.unresolved_artifacts",
        "label": "미해결 TODO·FIXME·목·수동 절차(unresolved artifacts)",
        "status": "passed",
        "command": (
            "uv run python scripts/verify_p7_quality_gates.py --mode release "
            "--manifest docs/contracts/quality/p7-quality-evidence.yaml"
        ),
        "evidence_path": "docs/Test/2026-07-25-P7-unresolved-artifacts-검증.md",
    }
    assert manifest["unresolved_artifacts"].get("allowed_paths", []) == []


def test_P7_release_gate는_모든_품질_gate가_passed이면_통과한다() -> None:
    manifest = load_manifest(ROOT / "docs/contracts/quality/p7-quality-evidence.yaml")

    result = validate_release_manifest(manifest, root=ROOT)
    findings = scan_from_manifest(manifest, root=ROOT)

    assert len(result.gate_ids) == 14
    assert findings == ()
