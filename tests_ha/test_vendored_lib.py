"""The vendored library must match the source it was generated from.

Home Assistant runs the copy under ``custom_components/``, while the CLI and
the published package run ``src/``. A fix applied to one and not the other
would leave the two behaving differently with nothing to say so.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from vendor_lib import MODULES, VENDOR_DIR, build  # noqa: E402


def test_vendored_copy_is_up_to_date() -> None:
    """Regenerating must produce exactly what is committed."""
    expected = build()
    actual = {path.name: path.read_text() for path in VENDOR_DIR.glob("*.py")}

    assert set(actual) == set(expected), (
        "vendored file list differs from the generator; "
        "run: uv run python scripts/vendor_lib.py"
    )
    stale = sorted(name for name in expected if actual[name] != expected[name])
    assert not stale, (
        f"vendored copy is out of date for {stale}; "
        "run: uv run python scripts/vendor_lib.py"
    )


def test_vendored_modules_carry_no_absolute_self_imports() -> None:
    """An absolute self-import would reach outside the integration."""
    offenders = [
        f"{path.name}:{number}"
        for path in VENDOR_DIR.glob("*.py")
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if line.startswith(("from godox_mesh_bt", "import godox_mesh_bt"))
    ]
    assert not offenders, offenders


@pytest.mark.parametrize("module", MODULES)
def test_every_vendored_module_imports(module: str) -> None:
    """A module missing from the closure would fail only at runtime."""
    __import__(f"custom_components.godox_mesh._lib.{module.lstrip('_')}"
               if module != "__init__" else "custom_components.godox_mesh._lib")


def test_integration_does_not_import_the_installed_library() -> None:
    """Nothing under custom_components/ may depend on the PyPI package."""
    integration = REPO_ROOT / "custom_components" / "godox_mesh"
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{number}"
        for path in integration.glob("*.py")
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if line.startswith(("from godox_mesh_bt", "import godox_mesh_bt"))
    ]
    assert not offenders, offenders


def test_manifest_keys_are_sorted_for_hassfest() -> None:
    """hassfest requires domain, name, then alphabetical order.

    The manifest is edited programmatically in this repo, and a plain rewrite
    loses the ordering, so this guards a check that otherwise only fails in CI.
    """
    import json

    manifest = json.loads(
        (REPO_ROOT / "custom_components/godox_mesh/manifest.json").read_text()
    )
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"], keys[:2]
    assert keys[2:] == sorted(keys[2:]), (
        f"expected alphabetical after domain/name, got {keys[2:]}"
    )
