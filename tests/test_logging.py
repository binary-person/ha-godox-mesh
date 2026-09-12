from __future__ import annotations

import logging

from godox_mesh_bt.cli import main
from godox_mesh_bt.logging_utils import configure_logging, verbosity_to_log_level


def test_verbosity_to_log_level_maps_repeatable_flags() -> None:
    assert verbosity_to_log_level(0) == logging.WARNING
    assert verbosity_to_log_level(1) == logging.INFO
    assert verbosity_to_log_level(2) == logging.DEBUG
    assert verbosity_to_log_level(3) == logging.DEBUG


def test_configure_logging_enables_debug_messages(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    configure_logging(2)

    logger = logging.getLogger("godox_mesh_bt.test")
    logger.debug("hello world")

    assert "hello world" in caplog.text


def test_cli_verbose_emits_debug_logs_from_scan() -> None:
    async def fake_scan(*, timeout: float):
        return []

    exit_code = main(["-vv", "scan", "--timeout", "1.5"], scan_fn=fake_scan)

    assert exit_code == 0


def test_cli_verbose_before_control_subcommand_is_preserved(
    monkeypatch,
    tmp_path,
) -> None:
    from pathlib import Path
    from godox_mesh_bt.state import MeshState
    from godox_mesh_bt.logging_utils import configure_logging

    state_path = tmp_path / "mesh_state.json"
    MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=1,
        node_address=2,
        sequence_number=1,
        iv_index=0,
    ).save(state_path)

    captured_verbose: list[int] = []

    def capturing_configure(verbose: int) -> None:
        captured_verbose.append(verbose)
        configure_logging(verbose)

    monkeypatch.setattr("godox_mesh_bt.cli.configure_logging", capturing_configure)

    class FakeController:
        def __init__(self, address: str, stp: Path) -> None:
            self.address = address
            self.state = MeshState.load(stp)

        async def __aenter__(self) -> "FakeController":
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def set_params(self, brightness: float | None = None, cct: int | None = None) -> None:
            return None

    monkeypatch.setattr("godox_mesh_bt.cli.GodoxController", FakeController)

    exit_code = main(
        [
            "-vv",
            "set",
            "--device",
            "device-id",
            "--state",
            str(state_path),
            "--node-address",
            "0x0002",
            "--brightness",
            "95",
        ],
    )

    assert exit_code == 0
    assert captured_verbose == [2]


def test_cli_verbose_flag_is_accepted_after_subcommand() -> None:
    async def fake_scan(*, timeout: float):
        return []

    assert main(["scan", "--timeout", "1.5", "-v"], scan_fn=fake_scan) == 0


def test_cli_verbose_long_flag_is_accepted_after_subcommand() -> None:
    async def fake_scan(*, timeout: float):
        return []

    assert main(["scan", "--timeout", "1.5", "--verbose"], scan_fn=fake_scan) == 0
