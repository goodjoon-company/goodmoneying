from __future__ import annotations

import json
from pathlib import Path

import yaml
from scripts.p7_resilience_probe import run_chaos_probe, run_load_probe, run_soak_probe

ROOT = Path(__file__).resolve().parents[2]


def test_P7_resilience_gate는_package_script와_증적_파일에_연결된다() -> None:
    manifest = yaml.safe_load(
        (ROOT / "docs/contracts/quality/p7-quality-evidence.yaml").read_text()
    )
    package = json.loads((ROOT / "package.json").read_text())
    scripts = package["scripts"]
    gates = {gate["id"]: gate for gate in manifest["gates"]}

    expected_commands = {
        "resilience.load": "npm run p7:load",
        "resilience.soak": "npm run p7:soak",
        "resilience.chaos": "npm run p7:chaos",
    }

    for gate_id, command in expected_commands.items():
        gate = gates[gate_id]
        script_name = command.removeprefix("npm run ")
        assert gate["status"] == "passed"
        assert gate["command"] == command
        assert script_name in scripts
        assert (ROOT / gate["evidence_path"]).is_file()


def test_P7_load_probe는_핵심_API를_반복_호출하고_p95_예산을_검증한다() -> None:
    result = run_load_probe(ROOT, request_count=20, p95_budget_ms=250.0)

    assert result["ok"] is True
    assert result["requests"] == 20
    assert result["failures"] == 0
    assert result["p95_ms"] <= 250.0


def test_P7_soak_probe는_짧은_지속_실행에서_오류와_메모리_drift를_검증한다() -> None:
    result = run_soak_probe(ROOT, duration_seconds=0.2, interval_seconds=0.05, peak_budget_mb=64.0)

    assert result["ok"] is True
    assert result["iterations"] >= 1
    assert result["failures"] == 0
    assert result["peak_mb"] <= 64.0


def test_P7_chaos_probe는_주입된_일시_장애_뒤_헬스와_대시보드_회복을_확인한다() -> None:
    result = run_chaos_probe(ROOT, injected_failures=1)

    assert result["ok"] is True
    assert result["injected_failures"] == 1
    assert result["observed_failures"] == 1
    assert result["recovered_requests"] >= 2
