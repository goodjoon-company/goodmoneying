from __future__ import annotations

import json
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]


def test_playwright_web_servers_receive_host_and_port_through_environment() -> None:
    config = (ROOT / "playwright.config.ts").read_text()

    assert '--host "$E2E_API_HOST" --port "$E2E_API_PORT"' in config
    assert '--host "$E2E_WEB_HOST" --port "$E2E_WEB_PORT"' in config
    assert "E2E_API_HOST: apiURL.hostname" in config
    assert "E2E_API_PORT: apiURL.port" in config
    assert "E2E_WEB_HOST: webURL.hostname" in config
    assert "E2E_WEB_PORT: webURL.port" in config


def test_e2e_script_verifies_local_test_servers_are_stopped() -> None:
    package = cast(
        dict[str, object],
        json.loads((ROOT / "package.json").read_text()),
    )
    scripts = cast(dict[str, str], package["scripts"])

    assert scripts["e2e"] == (
        "playwright test && node tests/e2e/assert_servers_stopped.mjs"
    )
