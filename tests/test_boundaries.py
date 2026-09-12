"""Import boundaries between the parts of this repository.

Four rules, checked against the real import graph. They are the whole
enforcement value of a much larger self-description scheme that previously
lived here: nine `repo-zone.json` files, a marker system, a collector and a
generated map, roughly 1,300 lines. Of its fifteen declared rules, seven could
not fire under any realistic edit and the four that mattered are these.

Two holes that scheme had are closed here:

* **Prefix rules are unioned, not overridden.** The vendored copy sits inside
  the integration, and under most-specific-wins it silently escaped the
  integration's rule -- exempting the twelve files most likely to break it.
* **Relative imports are resolved.** ``from ..const import DOMAIN`` is the most
  plausible way for the vendored library to reach back into Home Assistant, and
  a check that only reads absolute imports cannot see it.

Rule names are validated, so a typo or a dotted path fails loudly instead of
becoming a rule that never fires.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", "__pycache__", ".venv", "reverse", "nocommit", "dist", "build"}

#: directory prefix -> top-level modules files beneath it must not import.
BOUNDARIES: dict[str, set[str]] = {
    "src/godox_mesh_bt": {"homeassistant", "custom_components"},
    "custom_components/godox_mesh": {"godox_mesh_bt"},
    "tests": {"homeassistant", "custom_components"},
    "tests_ha": {"godox_mesh_bt"},
    # Unioned with the integration rule above, not replacing it.
    "custom_components/godox_mesh/_lib": {"custom_components", "homeassistant"},
}

WHY = {
    "src/godox_mesh_bt": "the library must stay usable without Home Assistant",
    "custom_components/godox_mesh": (
        "the library is not installed at runtime; the integration uses the "
        "vendored copy in _lib/ instead"
    ),
    "tests": "the library suite must run without Home Assistant installed",
    "tests_ha": (
        "the integration suite must exercise the vendored copy, not an "
        "installed library"
    ),
    "custom_components/godox_mesh/_lib": (
        "the vendored copy is overwritten wholesale by scripts/vendor_lib.py, "
        "so anything it reaches back into the integration for is silently lost "
        "on the next regeneration"
    ),
}


def _python_files() -> list[Path]:
    return [
        p
        for p in sorted(REPO_ROOT.rglob("*.py"))
        if not set(p.parts) & SKIP_PARTS
    ]


def _imports(path: Path) -> tuple[set[str], set[str]]:
    """Return ``(absolute_roots, resolved_relative_targets)``.

    Relative imports are resolved to repo-relative module paths so the caller
    can tell an import that stays inside a package from one that climbs out of
    it. ``from .client import X`` inside ``_lib`` is internal;
    ``from ..const import X`` escapes into the integration, and only the second
    is a boundary crossing.
    """
    try:
        tree = ast.parse(path.read_text(errors="ignore"))
    except (SyntaxError, ValueError, OSError):
        return set(), set()

    package = list(path.relative_to(REPO_ROOT).parent.parts)
    absolute: set[str] = set()
    relative: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            absolute |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if not node.level:
                if node.module:
                    absolute.add(node.module.split(".")[0])
                continue
            # level 1 is the containing package, 2 is its parent, and so on.
            climbed = package[: len(package) - (node.level - 1)]
            if node.module:
                climbed = climbed + [node.module.split(".")[0]]
            relative.add("/".join(climbed))
    return absolute, relative




def _rules_for(rel: str) -> dict[str, set[str]]:
    """Every boundary whose directory is a prefix of *rel*, unioned."""
    return {
        prefix: forbidden
        for prefix, forbidden in BOUNDARIES.items()
        if rel == prefix or rel.startswith(prefix + "/")
    }


@pytest.mark.parametrize("prefix", sorted(BOUNDARIES))
def test_each_boundary_names_a_real_directory(prefix: str) -> None:
    """A rule on a directory that no longer exists enforces nothing."""
    assert (REPO_ROOT / prefix).is_dir(), f"{prefix} does not exist"


@pytest.mark.parametrize("forbidden", sorted({m for s in BOUNDARIES.values() for m in s}))
def test_each_forbidden_name_is_a_real_top_level_module(forbidden: str) -> None:
    """Guard against typos and dotted paths, which would never match."""
    assert "." not in forbidden, "rules match top-level modules only"
    found = (
        (REPO_ROOT / "src" / forbidden).is_dir()
        or (REPO_ROOT / forbidden).is_dir()
        or forbidden == "homeassistant"  # supplied by the HA test environment
    )
    assert found, f"{forbidden!r} matches no module in this repo"


def test_no_file_crosses_a_boundary() -> None:
    """The rules, applied to every file beneath every matching prefix."""
    violations = []
    for path in _python_files():
        rel = str(path.relative_to(REPO_ROOT))
        rules = _rules_for(rel)
        if not rules:
            continue
        absolute, relative = _imports(path)
        for prefix, forbidden in rules.items():
            for name in sorted(forbidden & absolute):
                violations.append(f"{rel} imports {name} ({WHY[prefix]})")
            # A relative import crosses the boundary only if it resolves
            # outside the directory the rule governs.
            for target in sorted(relative):
                if target.startswith(prefix + "/") or target == prefix:
                    continue
                root = target.split("/")[0]
                if root in forbidden:
                    violations.append(
                        f"{rel} reaches {target} via a relative import ({WHY[prefix]})"
                    )
    assert not violations, "boundary violations:\n  " + "\n  ".join(violations)


def test_the_vendored_copy_is_covered_by_the_integration_rule() -> None:
    """The nested copy must inherit the enclosing rule, not replace it.

    This is the case the previous scheme got wrong, so it is asserted directly
    rather than left implicit in the union logic.
    """
    rules = _rules_for("custom_components/godox_mesh/_lib/client.py")

    assert "custom_components/godox_mesh" in rules
    assert "godox_mesh_bt" in rules["custom_components/godox_mesh"]


def test_the_editorial_map_is_well_formed() -> None:
    """repo-map.json must at least parse.

    Nothing imports it -- deliberately, since a document that gates tests on its
    own existence can disable them by being deleted. The cost of that choice is
    that corruption is invisible, and it happened: a stray shell fragment made
    the file invalid JSON and the whole suite stayed green. Parsing it is the
    smallest check that closes the gap without making anything depend on it.
    """
    import json

    path = REPO_ROOT / "repo-map.json"
    if not path.exists():
        pytest.skip("editorial map absent")
    data = json.loads(path.read_text())
    assert data.get("known_gaps"), "the map should record what is not enforced"
