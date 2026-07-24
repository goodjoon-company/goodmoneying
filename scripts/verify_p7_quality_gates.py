#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml

REQUIRED_GATE_IDS: tuple[str, ...] = (
    "performance.web_vitals",
    "performance.first_shell",
    "performance.realtime_event",
    "accessibility.wcag_2_2_aa",
    "accessibility.viewports",
    "security.dependencies",
    "security.images",
    "security.secrets",
    "security.auth_input",
    "resilience.load",
    "resilience.soak",
    "resilience.chaos",
    "recovery.backup_restore",
    "hygiene.unresolved_artifacts",
)

_UNRESOLVED_TOKENS = (
    "TO" + "DO",
    "FIX" + "ME",
    "X" * 3,
    "HA" + "CK",
    r"\bmanual st" + r"ep\b",
    "수동 " + "절차",
    "임시 " + "목 데이터",
)
_UNRESOLVED_PATTERN = re.compile("|".join(_UNRESOLVED_TOKENS), re.IGNORECASE)
_SUSPICIOUS_MOCK_TOKENS = (
    r"(?:['\"]mode['\"]\s*:\s*['\"]mo" + r"ck['\"])",
    r"(?:\bmode\s*=\s*['\"]mo" + r"ck['\"])",
    r"(?:\bmo" + r"ck(?:_?(?:data|server|response|transport|client))?\s*=)",
)
_SUSPICIOUS_MOCK_PATTERN = re.compile(
    "|".join(_SUSPICIOUS_MOCK_TOKENS),
    re.IGNORECASE,
)
_TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".sql",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_DEFAULT_SCAN_PATHS = (
    ".github/workflows",
    "apps/api",
    "apps/upbit_gateway",
    "apps/web/src",
    "apps/worker",
    "deploy/scripts",
    "packages/shared",
    "scripts",
)
_SKIP_DIRS = {
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "dist",
    "node_modules",
}


class QualityGateError(RuntimeError):
    """P7 품질 게이트(Quality Gate) 실패."""


@dataclass(frozen=True)
class ReadinessValidation:
    gate_ids: tuple[str, ...]
    gate_labels: tuple[str, ...]
    required_evidence_paths: tuple[str, ...]


@dataclass(frozen=True)
class UnresolvedArtifact:
    path: str
    line: int
    token: str
    text: str


Manifest = dict[str, Any]


def load_manifest(path: Path) -> Manifest:
    if not path.exists():
        raise QualityGateError(f"P7 품질 매니페스트가 없습니다: {path}")
    loaded = yaml.safe_load(path.read_text())
    if not isinstance(loaded, dict):
        raise QualityGateError(f"P7 품질 매니페스트는 객체여야 합니다: {path}")
    return cast(Manifest, loaded)


def validate_readiness_manifest(manifest: Manifest, *, root: Path) -> ReadinessValidation:
    gates = manifest.get("gates")
    if not isinstance(gates, list):
        raise QualityGateError("P7 매니페스트의 gates는 배열이어야 합니다.")

    gate_by_id: dict[str, dict[str, Any]] = {}
    for raw_gate in gates:
        if not isinstance(raw_gate, dict):
            raise QualityGateError("P7 gate 항목은 객체여야 합니다.")
        gate_id = _required_text(raw_gate, "id")
        if gate_id in gate_by_id:
            raise QualityGateError(f"중복 P7 gate id: {gate_id}")
        gate_by_id[gate_id] = cast(dict[str, Any], raw_gate)

    missing = [gate_id for gate_id in REQUIRED_GATE_IDS if gate_id not in gate_by_id]
    extra = [gate_id for gate_id in gate_by_id if gate_id not in REQUIRED_GATE_IDS]
    if missing or extra:
        raise QualityGateError(
            "P7 gate id 불일치: "
            f"missing={','.join(missing) or '-'} extra={','.join(extra) or '-'}"
        )

    labels: list[str] = []
    evidence_paths: list[str] = []
    for gate_id in REQUIRED_GATE_IDS:
        gate = gate_by_id[gate_id]
        labels.append(_required_text(gate, "label"))
        command = _required_text(gate, "command")
        evidence_path = _required_text(gate, "evidence_path")
        status = _required_text(gate, "status")
        if status not in {"planned", "running", "passed", "blocked"}:
            raise QualityGateError(f"{gate_id} status 값이 올바르지 않습니다: {status}")
        if not command.strip():
            raise QualityGateError(f"{gate_id} command가 비어 있습니다.")
        if not evidence_path.startswith("docs/Test/"):
            raise QualityGateError(f"{gate_id} evidence_path는 docs/Test/ 아래여야 합니다.")
        if Path(evidence_path).is_absolute() or ".." in Path(evidence_path).parts:
            raise QualityGateError(f"{gate_id} evidence_path는 안전한 상대 경로여야 합니다.")
        if status == "passed" and not (root / evidence_path).exists():
            raise QualityGateError(f"{gate_id} passed 증적 파일이 없습니다: {evidence_path}")
        evidence_paths.append(evidence_path)

    return ReadinessValidation(
        gate_ids=tuple(gate_by_id),
        gate_labels=tuple(labels),
        required_evidence_paths=tuple(evidence_paths),
    )


def validate_release_manifest(manifest: Manifest, *, root: Path) -> ReadinessValidation:
    result = validate_readiness_manifest(manifest, root=root)
    gate_by_id = {
        cast(str, gate["id"]): gate for gate in cast(list[dict[str, Any]], manifest["gates"])
    }
    not_passed = [
        gate_id
        for gate_id in REQUIRED_GATE_IDS
        if cast(str, gate_by_id[gate_id].get("status")) != "passed"
    ]
    if not_passed:
        raise QualityGateError(f"P7 release gate 미통과: {', '.join(not_passed)}")
    return result


def scan_unresolved_artifacts(
    root: Path,
    *,
    scan_paths: tuple[str, ...] = _DEFAULT_SCAN_PATHS,
    allowed_paths: tuple[str, ...] = (),
) -> tuple[UnresolvedArtifact, ...]:
    findings: list[UnresolvedArtifact] = []
    allowed = set(allowed_paths)
    for relative_scan_path in scan_paths:
        scan_root = root / relative_scan_path
        if not scan_root.exists():
            continue
        for path in _iter_text_files(scan_root):
            relative_path = path.relative_to(root).as_posix()
            if relative_path in allowed:
                continue
            lines = path.read_text(errors="ignore").splitlines()
            for line_number, line in enumerate(lines, start=1):
                match = _UNRESOLVED_PATTERN.search(line)
                if match is None:
                    match = _SUSPICIOUS_MOCK_PATTERN.search(line)
                if match is None:
                    continue
                findings.append(
                    UnresolvedArtifact(
                        path=relative_path,
                        line=line_number,
                        token=match.group(0),
                        text=line.strip(),
                    )
                )
    if findings:
        preview = "; ".join(
            f"{finding.path}:{finding.line} {finding.token}" for finding in findings[:10]
        )
        raise QualityGateError(f"P7 미해결 산출물 발견: {preview}")
    return tuple(findings)


def scan_from_manifest(manifest: Manifest, *, root: Path) -> tuple[UnresolvedArtifact, ...]:
    hygiene = manifest.get("unresolved_artifacts")
    if not isinstance(hygiene, dict):
        raise QualityGateError("P7 매니페스트에는 unresolved_artifacts 객체가 필요합니다.")
    scan_paths = _string_tuple(hygiene.get("scan_paths"), default=_DEFAULT_SCAN_PATHS)
    allowed_paths = _string_tuple(hygiene.get("allowed_paths"), default=())
    return scan_unresolved_artifacts(root, scan_paths=scan_paths, allowed_paths=allowed_paths)


def main() -> int:
    parser = argparse.ArgumentParser(description="P7 운영 품질 게이트를 검증합니다.")
    parser.add_argument(
        "--mode",
        choices=("readiness", "release"),
        default="readiness",
        help=(
            "readiness는 gate 구조와 명시적 예외만 검증하고, "
            "release는 모든 증적 통과를 요구합니다."
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("docs/contracts/quality/p7-quality-evidence.yaml"),
    )
    args = parser.parse_args()

    root = Path.cwd()
    try:
        manifest = load_manifest(args.manifest)
        mode = cast(Literal["readiness", "release"], args.mode)
        if mode == "release":
            result = validate_release_manifest(manifest, root=root)
        else:
            result = validate_readiness_manifest(manifest, root=root)
        scan_from_manifest(manifest, root=root)
    except QualityGateError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "mode": args.mode,
                "gates": len(result.gate_ids),
                "evidence_paths": list(result.required_evidence_paths),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _iter_text_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if ".test." in path.name:
            continue
        if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
            continue
        files.append(path)
    return tuple(sorted(files))


def _required_text(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise QualityGateError(f"필수 문자열 필드가 없습니다: {key}")
    return value


def _string_tuple(value: object, *, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise QualityGateError("문자열 배열이어야 합니다.")
    return tuple(cast(list[str], value))


if __name__ == "__main__":
    raise SystemExit(main())
