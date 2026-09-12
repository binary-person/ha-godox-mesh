from __future__ import annotations

from pathlib import Path


def test_package_has_module_entrypoint() -> None:
    entrypoint = Path("src/godox_mesh_bt/__main__.py")
    text = entrypoint.read_text()

    assert "from godox_mesh_bt.cli import run" in text
    assert "if __name__ == \"__main__\":" in text
    assert "run()" in text
