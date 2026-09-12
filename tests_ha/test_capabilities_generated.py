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
GENERATOR = REPO_ROOT / "scripts" / "generate_capabilities.py"
ARTIFACTS = REPO_ROOT / "reverse-artifacts"


@pytest.mark.skipif(
    not GENERATOR.exists() or not (ARTIFACTS / "product-catalogue.json").exists(),
    reason="generator or committed artifacts not present",
)
def test_table_matches_a_regeneration(tmp_path: Path) -> None:
    """Regenerating from the committed artifacts must reproduce the table."""
    out = tmp_path / "regenerated.json"
    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--products", str(ARTIFACTS / "product-catalogue.json"),
            "--firmware", str(ARTIFACTS / "firmware-coverage.json"),
            "--output", str(out),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr

    regenerated = json.loads(out.read_text())
    committed = json.loads(TABLE.read_text())
    # _inputs records the paths used, which differ between runs.
    for table in (regenerated, committed):
        table.pop("_inputs", None)

    assert regenerated == committed, (
        "capabilities_data.json does not match a regeneration -- either it was "
        "hand-edited, or the generator changed and the table was not rebuilt"
    )


def test_table_declares_its_provenance() -> None:
    """Opening the file should say what made it, without hunting for the script."""
    table = json.loads(TABLE.read_text())

    assert table["_generated_by"] == "scripts/generate_capabilities.py"
    assert "do not hand-edit" in table["_note"]
    # Metadata keys must not be mistaken for models.
    assert all(k.startswith("_") or len(k) == 4 for k in table)
