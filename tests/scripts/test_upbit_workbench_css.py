from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_adjacent_upbit_inputs_share_grid_rows_without_magic_height() -> None:
    stylesheet = (ROOT / "apps/web/src/styles/upbit-workbench.css").read_text()

    assert "grid-template-rows: subgrid" in stylesheet
    assert "min-height: 2.9em" not in stylesheet
