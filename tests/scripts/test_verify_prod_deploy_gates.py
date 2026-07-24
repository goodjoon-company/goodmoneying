from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/scripts/verify-prod-deploy-gates.sh"
SHA = "a" * 40


def run_gate(
    tmp_path: Path,
    *,
    approved_sha: str = SHA,
    sha: str = SHA,
    gh_mode: str = "ok",
) -> subprocess.CompletedProcess[str]:
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        "#!/bin/sh\n"
        "if [ \"${FAKE_GH_MODE:-ok}\" = failure ]; then exit 1; fi\n"
        "case \"$*\" in\n"
        "  *commits/main*) printf '%s\\n' \"$GITHUB_SHA\" ;;\n"
        "  *commits/release*) printf '%s\\n' \"$GITHUB_SHA\" ;;\n"
        "  *branches/main/protection*) printf 'ok\\n' ;;\n"
        "  *branches/release/protection*) printf 'ok\\n' ;;\n"
        "  *environments/prod*) printf 'ok\\n' ;;\n"
        "  *check-runs*) printf '1\\n' ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n"
    )
    fake_gh.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "GH_TOKEN": "test-token",
        "GITHUB_REPOSITORY": "example/goodmoneying",
        "GITHUB_REF": "refs/heads/release",
        "GITHUB_SHA": sha,
        "APPROVED_SHA": approved_sha,
        "DEPLOY_ENABLE_SHA": sha,
        "FAKE_GH_MODE": gh_mode,
    }
    return subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )


def test_gate_accepts_same_protected_release_sha_with_successful_ci(tmp_path: Path) -> None:
    result = run_gate(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "운영 배포 사전점검 통과" in result.stdout


def test_gate_script_uses_bash_3_2_compatible_sha_comparison() -> None:
    script = SCRIPT.read_text()

    assert ",," not in script


def test_gate_rejects_invalid_or_different_sha_before_github_calls(tmp_path: Path) -> None:
    invalid = run_gate(tmp_path, approved_sha="main")
    different = run_gate(tmp_path, approved_sha="b" * 40)

    assert invalid.returncode != 0
    assert "40자리" in invalid.stderr
    assert different.returncode != 0
    assert "일치하지" in different.stderr


def test_gate_fails_closed_when_github_proof_is_unavailable(tmp_path: Path) -> None:
    result = run_gate(tmp_path, gh_mode="failure")

    assert result.returncode != 0


def test_gate_stays_locked_until_p8_enables_the_exact_sha(tmp_path: Path) -> None:
    fake_gh = tmp_path / "gh"
    fake_gh.write_text("#!/bin/sh\nexit 99\n")
    fake_gh.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "GH_TOKEN": "test-token",
        "GITHUB_REPOSITORY": "example/goodmoneying",
        "GITHUB_REF": "refs/heads/release",
        "GITHUB_SHA": SHA,
        "APPROVED_SHA": SHA,
    }

    result = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )

    assert result.returncode != 0
    assert "P8 배포 잠금" in result.stderr
