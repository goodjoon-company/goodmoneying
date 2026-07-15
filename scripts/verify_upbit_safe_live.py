#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from goodmoneying_upbit_gateway.live_verification import run_safe_verification


def main() -> int:
    parser = argparse.ArgumentParser(
        description="실제 상태 변경 없이 Upbit 공개 조회·인증 읽기·공식 주문 테스트만 검증합니다."
    )
    parser.add_argument("--key-file", type=Path, required=True)
    args = parser.parse_args()
    with httpx.Client(timeout=10) as client:
        report = run_safe_verification(args.key_file, http_client=client)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    public = report["allowed_results"][0]
    return 0 if public["status_code"] == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
