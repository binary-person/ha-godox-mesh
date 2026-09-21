"""The capability table must match a regeneration from the committed artifacts.

`capabilities_data.json` is generated, and the whole point of committing
`reverse-artifacts/` was that anyone can rebuild it. That claim is worth
enforcing: a hand-edit to the table, or a generator change that alters its
output, should fail here rather than being discovered by a user whose light gets
the wrong colour-temperature range.

This needs no vendor material — it runs off the committed artifacts alone.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TABLE = REPO_ROOT / "custom_components" / "godox_mesh" / "capabilities_data.json"
CHIPS = REPO_ROOT / "custom_components" / "godox_mesh" / "color_chips_data.json"
GENERATOR = REPO_ROOT / "scripts" / "generate_capabilities.py"
ARTIFACTS = REPO_ROOT / "reverse-artifacts"


@pytest.mark.skipif(
    not GENERATOR.exists() or not (ARTIFACTS / "product-catalogue.json").exists(),
    reason="generator or committed artifacts not present",
)
def _regenerate(tmp_path: Path) -> tuple[Path, Path]:
    """Run the generator into *tmp_path* and return its two outputs.

    Every output path is redirected, including the gel table: a run that left
    one at its default would rewrite the committed file and so could never fail
    on drift in it.
    """
    out = tmp_path / "regenerated.json"
    chips = tmp_path / "regenerated-chips.json"
    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--products", str(ARTIFACTS / "product-catalogue.json"),
            "--firmware", str(ARTIFACTS / "firmware-coverage.json"),
            "--color-chips", str(ARTIFACTS / "color-chips.json"),
            "--output", str(out),
            "--color-chip-output", str(chips),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    return out, chips


def _without_inputs(path: Path) -> dict:
    """Parse a generated table, dropping the paths that differ between runs."""
    table = json.loads(path.read_text())
    table.pop("_inputs", None)
    return table


def test_table_matches_a_regeneration(tmp_path: Path) -> None:
    """Regenerating from the committed artifacts must reproduce the table."""
    out, _ = _regenerate(tmp_path)

    assert _without_inputs(out) == _without_inputs(TABLE), (
        "capabilities_data.json does not match a regeneration -- either it was "
        "hand-edited, or the generator changed and the table was not rebuilt"
    )


def test_color_chip_table_matches_a_regeneration(tmp_path: Path) -> None:
    """The gel table is generated from the committed catalogue too."""
    _, chips = _regenerate(tmp_path)

    assert _without_inputs(chips) == _without_inputs(CHIPS), (
        "color_chips_data.json does not match a regeneration"
    )


def test_table_declares_its_provenance() -> None:
    """Opening the file should say what made it, without hunting for the script."""
    table = json.loads(TABLE.read_text())

    assert table["_generated_by"] == "scripts/generate_capabilities.py"
    assert "do not hand-edit" in table["_note"]
    # Metadata keys must not be mistaken for models.
    assert all(k.startswith("_") or len(k) == 4 for k in table)
