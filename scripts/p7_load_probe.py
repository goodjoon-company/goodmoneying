#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from p7_resilience_probe import run_load_probe

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P7 load probe를 실행합니다.")
    parser.add_argument("--profile", choices=("local",), default="local")
    parser.parse_args()
    result = run_load_probe(Path.cwd())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
