from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_P7_성능_gate는_package_script와_증적_파일에_연결된다() -> None:
    manifest = yaml.safe_load(
        (ROOT / "docs/contracts/quality/p7-quality-evidence.yaml").read_text()
    )
    package = json.loads((ROOT / "package.json").read_text())
    scripts = package["scripts"]
    gates = {gate["id"]: gate for gate in manifest["gates"]}

    expected_commands = {
        "performance.web_vitals": "npm run p7:web-vitals",
        "performance.first_shell": "npm run p7:first-shell",
        "performance.realtime_event": "npm run p7:realtime-event",
    }

    for gate_id, command in expected_commands.items():
        gate = gates[gate_id]
        script_name = command.removeprefix("npm run ")
        assert gate["status"] == "passed"
        assert gate["command"] == command
        assert script_name in scripts
        assert (ROOT / gate["evidence_path"]).is_file()
