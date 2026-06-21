from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 4:
        print(
            "사용법: dev-start-background.py <pid-file> <log-file> <command...>",
            file=sys.stderr,
        )
        return 2

    pid_file = Path(sys.argv[1])
    log_file = Path(sys.argv[2])
    command = sys.argv[3:]

    log_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log = log_file.open("ab", buffering=0)
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    pid_file.write_text(f"{process.pid}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
