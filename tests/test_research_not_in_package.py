"""The research helpers must not reach a user's Home Assistant install.

`src/godox_mesh_bt/research/` holds capture-parsing and replay tooling used
while reverse-engineering the protocol. It pulls in heavier dependencies and has
no business running inside Home Assistant, so it must stay out of the vendored
copy that ships with the integration.

An earlier version of this file asserted that ``godox_mesh_bt.analysis``,
``.captures`` and ``.replay`` were not importable. Those modules had already
been renamed into the ``research`` package, so all three assertions passed
vacuously and the guard protected nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDORED = REPO_ROOT / "custom_components" / "godox_mesh" / "_lib"
RUNTIME = REPO_ROOT / "src" / "godox_mesh_bt"


def test_research_is_not_vendored_into_the_integration() -> None:
    """The copy shipped to Home Assistant users must carry no research code."""
    if not VENDORED.exists():
        pytest.skip("vendored copy not built")

    vendored = {p.name for p in VENDORED.rglob("*.py")}
    research = {p.name for p in (RUNTIME / "research").glob("*.py")} - {"__init__.py"}

    assert research, "expected research modules to exist"
    assert not (vendored & research), f"research leaked into _lib: {vendored & research}"
    assert not (VENDORED / "research").exists()


def test_no_runtime_module_imports_research() -> None:
    """Nothing on the control path may depend on the research helpers.

    This is the import that would drag research code into the vendored copy, so
    it is the one worth policing.
    """
    offenders = []
    for module in RUNTIME.glob("*.py"):
        text = module.read_text()
        if re.search(r"^\s*(from\s+\.?research|import\s+.*\bresearch\b)", text, re.M):
            offenders.append(module.name)

    assert not offenders, f"runtime modules importing research: {offenders}"
