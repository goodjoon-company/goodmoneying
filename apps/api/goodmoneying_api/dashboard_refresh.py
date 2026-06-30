from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_DASHBOARD_REFRESH_SECONDS = {
    "overview": 10,
    "targets": 15,
    "coverage": 30,
    "collectionActivity": 15,
    "realtimeHeatmap": 10,
    "storageBreakdown": 60,
    "operationsTrend": 60,
    "missingRanges": 60,
    "auditLogSummary": 60,
}

DEFAULT_DASHBOARD_REFRESH_CONFIG_PATH = Path("config/operations-api.yaml")


def load_dashboard_refresh_seconds() -> dict[str, int]:
    config_path = Path(
        os.getenv(
            "GOODMONEYING_DASHBOARD_REFRESH_CONFIG",
            str(DEFAULT_DASHBOARD_REFRESH_CONFIG_PATH),
        )
    )
    if not config_path.exists():
        return DEFAULT_DASHBOARD_REFRESH_SECONDS.copy()

    raw_config = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(raw_config, dict):
        raise ValueError(f"{config_path} 설정은 YAML object여야 한다.")

    raw_refresh = raw_config.get("dashboardRefreshSeconds", {})
    if raw_refresh is None:
        raw_refresh = {}
    if not isinstance(raw_refresh, dict):
        raise ValueError("dashboardRefreshSeconds 설정은 YAML object여야 한다.")

    refresh_seconds = DEFAULT_DASHBOARD_REFRESH_SECONDS.copy()
    for key, value in raw_refresh.items():
        if key not in refresh_seconds:
            raise ValueError(f"알 수 없는 dashboardRefreshSeconds 키다: {key}")
        refresh_seconds[key] = _positive_int(key, value)
    return refresh_seconds


def _positive_int(key: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"dashboardRefreshSeconds.{key} 값은 1 이상의 정수여야 한다.")
    return value
