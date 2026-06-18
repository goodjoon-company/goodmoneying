from __future__ import annotations

import os
import subprocess
import sys


def test_live_backfill_script_help_runs_without_pythonpath() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, "scripts/live_backfill_january_2026.py", "--help"],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 0
    assert "실제 업비트 2026년 1월 1분봉 백필 검증" in result.stdout
