from __future__ import annotations

import json
import argparse
from pathlib import Path
from typing import Any

import pytest

from godox_mesh_bt.cli import main
from godox_mesh_bt.inspector import CharacteristicInfo, InspectionResult, ServiceInfo
from godox_mesh_bt.scanner import Advertisement, DiscoveredDevice
from godox_mesh_bt.state import MeshState


def _minimal_mesh_state(tmp_path: Path, **overrides: Any) -> Path:
    """Save a minimal MeshState to tmp_path/mesh_state.json and return its path."""
    defaults: dict[str, Any] = dict(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=1,
        node_address=2,
        sequence_number=1,
        iv_index=0,
    )
    defaults.update(overrides)
    path = tmp_path / "mesh_state.json"
    MeshState(**defaults).save(path)
    return path


def _make_fake_controller(calls: list[Any], instances: list[Any] | None = None):
    """Return a FakeController class that records calls and loads state from state_path."""

    class FakeController:
        def __init__(self, address: str, state_path: Any) -> None:
            self.address = address
            self.state = MeshState.load(state_path)
            if instances is not None:
                instances.append(self)

        async def __aenter__(self) -> "FakeController":
            return self

        async def __aexit__(self, *a: Any) -> None:
            pass

        async def power_on(self) -> None:
            calls.append("power_on")

        async def power_off(self) -> None:
            calls.append("power_off")

        async def set_params(self, brightness: float | None = None, cct: int | None = None) -> None:
            calls.append((brightness, cct))

    return FakeController


def test_scan_cli_outputs_json_from_injected_scanner(capsys) -> None:
    async def fake_scan(*, timeout: float) -> list[DiscoveredDevice]:
        assert timeout == 1.5
        return [
            DiscoveredDevice(
                name="GD_LED",
                address="device-id",
                rssi=-55,
                advertisement=Advertisement(local_name="GD_LED"),
                likely_godox=True,
            )
        ]

    exit_code = main(["scan", "--timeout", "1.5"], scan_fn=fake_scan)

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == [
        {
            "name": "GD_LED",
            "address": "device-id",
            "rssi": -55,
            "likely_godox": True,
            "advertisement": {
                "local_name": "GD_LED",
                "service_uuids": [],
                "manufacturer_data": {},
            },
        }
    ]


def test_inspect_cli_outputs_markdown_from_injected_inspector(capsys) -> None:
    async def fake_inspect(address: str) -> InspectionResult:
        assert address == "device-id"
        return InspectionResult(
            address=address,
            services=(
                ServiceInfo(
                    uuid="service",
                    handle=1,
                    characteristics=(
                        CharacteristicInfo(uuid="writer", handle=2, properties=("write",)),
                    ),
                ),
            ),
        )

    exit_code = main(["inspect", "device-id", "--format", "markdown"], inspect_fn=fake_inspect)

    assert exit_code == 0
    assert "Characteristic `writer`" in capsys.readouterr().out


def test_raw_cli_validates_hex_before_connecting(capsys) -> None:
    exit_code = main(["raw", "--device", "device-id", "--char", "char-id", "--hex", "not-hex"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "invalid hex payload" in captured.err


def test_raw_cli_writes_explicit_payload_with_injected_client() -> None:
    writes: list[tuple[str, bytes, bool]] = []

    class FakeClient:
        def __init__(self, address: str) -> None:
            self.address = address

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def write_raw(self, characteristic: str, payload: bytes, *, response: bool) -> None:
            writes.append((characteristic, payload, response))

    exit_code = main(
        ["raw", "--device", "device-id", "--char", "char-id", "--hex", "0102", "--response"],
        client_factory=FakeClient,
    )

    assert exit_code == 0
    assert writes == [("char-id", b"\x01\x02", True)]


def test_on_cli_delegates_to_controller_power_on(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[Any] = []
    state_path = _minimal_mesh_state(tmp_path)
    monkeypatch.setattr("godox_mesh_bt.cli.GodoxController", _make_fake_controller(calls))

    exit_code = main(
        ["on", "--device", "test-device", "--state", str(state_path)],
    )

    assert exit_code == 0
    assert calls == ["power_on"]


def test_set_cli_keeps_ble_device_separate_from_hex_mesh_destination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[Any] = []
    instances: list[Any] = []
    state_path = _minimal_mesh_state(tmp_path)
    monkeypatch.setattr("godox_mesh_bt.cli.GodoxController", _make_fake_controller(calls, instances))

    exit_code = main(
        [
            "set",
            "--device",
            "304BCD50-D2C2-4FA6-A666-F4867E54F267",
            "--state",
            str(state_path),
            "--node-address",
            "0x0002",
            "--brightness",
            "95",
        ],
    )

    assert exit_code == 0
    assert calls == [(95.0, None)]
    assert instances[0].address == "304BCD50-D2C2-4FA6-A666-F4867E54F267"
    assert instances[0].state.node_address == 0x0002


def test_set_rejects_address_flag() -> None:
    """--address is no longer supported; CLI should exit with an error."""
    import argparse

    from godox_mesh_bt.cli import _build_parser

    parser = _build_parser()
    with pytest.raises((SystemExit, argparse.ArgumentError)):
        parser.parse_args(["set", "--address", "foo", "--brightness", "50"])


@pytest.mark.parametrize(
    ("brightness_arg", "expected_brightness"),
    [
        ("95", 95.0),
        ("95.0", 95.0),
        ("95.5", 95.5),
    ],
)
def test_set_cli_brightness_values_are_explicit_floats(
    brightness_arg: str,
    expected_brightness: float,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[Any] = []
    state_path = _minimal_mesh_state(tmp_path)
    monkeypatch.setattr("godox_mesh_bt.cli.GodoxController", _make_fake_controller(calls))

    exit_code = main(
        [
            "set",
            "--device",
            "device-id",
            "--state",
            str(state_path),
            "--node-address",
            "0x0002",
            "--brightness",
            brightness_arg,
        ],
    )

    assert exit_code == 0
    assert calls == [(expected_brightness, None)]


def test_set_cli_dry_run_prints_control_plan(tmp_path: Path, capsys) -> None:

    state_path = tmp_path / "mesh_state.json"
    MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=0x0001,
        node_address=0xC000,
        sequence_number=100044,
        iv_index=0,
    ).save(state_path)

    exit_code = main(
        [
            "set",
            "--device",
            "device-id",
            "--state",
            str(state_path),
            "--node-address",
            "0x0002",
            "--brightness",
            "95",
            "--dry-run",
        ],
    )

    plan = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert plan["ble_device"] == "device-id"
    assert plan["mesh_src"] == "0x0001"
    assert plan["mesh_dst"] == "0x0002"
    assert plan["proxy_config_sequences"] == [100044, 100045]
    assert plan["sequence"] == 100046
    assert plan["vendor_opcode"] == 135664
    assert plan["godox_v2_payload_hex"].startswith("f05f3832000000")
    assert plan["proxy_pdu_hex"]


def test_set_cli_dry_run_overrides_state_without_saving(tmp_path: Path, capsys) -> None:

    state_path = tmp_path / "mesh_state.json"
    MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=0x0004,
        node_address=0x0002,
        sequence_number=100049,
        iv_index=1,
    ).save(state_path)
    before = state_path.read_text()

    exit_code = main(
        [
            "set",
            "--device",
            "device-id",
            "--state",
            str(state_path),
            "--provisioner-address",
            "0x0001",
            "--node-address",
            "0x0002",
            "--sequence-number",
            "0x0186d1",
            "--iv-index",
            "0",
            "--brightness",
            "95",
            "--dry-run",
        ],
    )

    plan = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert plan["mesh_src"] == "0x0001"
    assert plan["mesh_dst"] == "0x0002"
    assert plan["proxy_config_sequences"] == [100049, 100050]
    assert plan["sequence"] == 100051
    assert plan["iv_index"] == 0
    assert state_path.read_text() == before


@pytest.mark.parametrize(
    ("command", "expected_payload_prefix"),
    [
        ("on", "fe00ffffffffff"),
        ("off", "fe01ffffffffff"),
    ],
)
def test_power_cli_dry_run_prints_control_plan(
    tmp_path: Path,
    capsys,
    command: str,
    expected_payload_prefix: str,
) -> None:

    state_path = tmp_path / "mesh_state.json"
    MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=0x0001,
        node_address=0x0002,
        sequence_number=100044,
        iv_index=0,
    ).save(state_path)

    exit_code = main(
        [
            command,
            "--device",
            "device-id",
            "--state",
            str(state_path),
            "--dry-run",
        ],
    )

    plan = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert plan["ble_device"] == "device-id"
    assert plan["mesh_dst"] == "0x0002"
    assert plan["godox_v2_payload_hex"].startswith(expected_payload_prefix)
    assert plan["proxy_pdu_hex"]


def test_cli_set_atomic_params(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[Any] = []
    state_path = _minimal_mesh_state(tmp_path)
    monkeypatch.setattr("godox_mesh_bt.cli.GodoxController", _make_fake_controller(calls))

    exit_code = main(
        [
            "set",
            "--device",
            "discovered-id",
            "--state",
            str(state_path),
            "--brightness",
            "95.5",
            "--cct",
            "2900",
        ],
    )

    assert exit_code == 0
    assert calls == [(95.5, 2900)]


@pytest.mark.asyncio
async def test_get_controller_applies_mesh_overrides_to_existing_state(tmp_path: Path) -> None:
    from godox_mesh_bt.cli import _get_controller

    state_path = tmp_path / "mesh_state.json"
    MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=4,
        node_address=0xC000,
        sequence_number=100044,
        iv_index=1,
    ).save(state_path)

    args = argparse.Namespace(
        device="device-id",
        state=str(state_path),
        network_key=None,
        app_key=None,
        provisioner_address=None,
        node_address=2,
        sequence_number=None,
        iv_index=None,
    )

    async def fake_scan(*, timeout: float) -> list[DiscoveredDevice]:
        return []

    controller = await _get_controller(args, fake_scan)

    assert controller.state.node_address == 2
    assert controller.state.network_key == "bc6bf99f840f9ca379562468a110d2a9"
    assert controller.state.sequence_number == 100044


@pytest.mark.asyncio
async def test_get_controller_saves_effective_mesh_override_on_sequence_advance(tmp_path: Path) -> None:
    from godox_mesh_bt.cli import _get_controller

    state_path = tmp_path / "mesh_state.json"
    MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=4,
        node_address=0xC000,
        sequence_number=100044,
        iv_index=1,
    ).save(state_path)

    args = argparse.Namespace(
        device="device-id",
        state=str(state_path),
        network_key=None,
        app_key=None,
        provisioner_address=None,
        node_address=0x0002,
        sequence_number=None,
        iv_index=None,
    )

    async def fake_scan(*, timeout: float) -> list[DiscoveredDevice]:
        return []

    controller = await _get_controller(args, fake_scan)
    controller._advance_state()
    saved_state = MeshState.load(state_path)

    assert saved_state.node_address == 0x0002
    assert saved_state.sequence_number == 100045


@pytest.mark.asyncio
async def test_get_controller_defaults_group_destination_to_unicast(tmp_path: Path, caplog) -> None:
    from godox_mesh_bt.cli import _get_controller

    state_path = tmp_path / "mesh_state.json"
    MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=4,
        node_address=0xC000,
        sequence_number=100044,
        iv_index=1,
    ).save(state_path)

    args = argparse.Namespace(
        device="device-id",
        state=str(state_path),
        network_key=None,
        app_key=None,
        provisioner_address=None,
        node_address=None,
        sequence_number=None,
        iv_index=None,
    )

    async def fake_scan(*, timeout: float) -> list[DiscoveredDevice]:
        return []

    controller = await _get_controller(args, fake_scan)

    assert controller.state.node_address == 2
    assert "defaulting to 0x0002" in caplog.text


def test_inspect_scans_if_device_not_specified(capsys) -> None:
    async def fake_scan(*, timeout: float) -> list[DiscoveredDevice]:
        return [
            DiscoveredDevice(
                name="GD_LED",
                address="discovered-id",
                rssi=-55,
                advertisement=Advertisement(local_name="GD_LED"),
                likely_godox=True,
            )
        ]

    async def fake_inspect(address: str) -> InspectionResult:
        assert address == "discovered-id"
        return InspectionResult(address=address, services=())

    exit_code = main(
        ["inspect"],
        scan_fn=fake_scan,
        inspect_fn=fake_inspect,
    )

    assert exit_code == 0
    assert "No device specified, scanning..." in capsys.readouterr().err


@pytest.mark.asyncio
async def test_get_controller_uses_env_mesh_state_when_state_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from godox_mesh_bt.cli import _get_controller

    state_path = tmp_path / "env_mesh_state.json"
    MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=1,
        node_address=2,
        sequence_number=100044,
        iv_index=0,
    ).save(state_path)
    monkeypatch.setenv("GODOX_MESH_BT_STATE", str(state_path))

    args = argparse.Namespace(
        device="device-id",
        state=None,
        network_key=None,
        app_key=None,
        provisioner_address=None,
        node_address=None,
        sequence_number=None,
        iv_index=None,
    )

    async def fake_scan(*, timeout: float) -> list[DiscoveredDevice]:
        return []

    controller = await _get_controller(args, fake_scan)

    assert controller.state_path == state_path
    assert controller.state.sequence_number == 100044


@pytest.mark.asyncio
async def test_get_controller_uses_local_mesh_state_before_config_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from godox_mesh_bt.cli import _get_controller

    work_dir = tmp_path / "work"
    config_dir = tmp_path / "config"
    work_dir.mkdir()
    config_dir.mkdir()
    local_state_path = work_dir / "mesh_state.json"
    config_state_path = config_dir / "mesh_state.json"
    MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=1,
        node_address=2,
        sequence_number=100044,
        iv_index=0,
    ).save(local_state_path)
    MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=1,
        node_address=2,
        sequence_number=200000,
        iv_index=0,
    ).save(config_state_path)
    monkeypatch.setenv("GODOX_MESH_BT_CONFIG_DIR", str(config_dir))
    monkeypatch.chdir(work_dir)

    args = argparse.Namespace(
        device="device-id",
        state=None,
        network_key=None,
        app_key=None,
        provisioner_address=None,
        node_address=None,
        sequence_number=None,
        iv_index=None,
    )

    async def fake_scan(*, timeout: float) -> list[DiscoveredDevice]:
        return []

    controller = await _get_controller(args, fake_scan)

    assert controller.state_path == local_state_path
    assert controller.state.sequence_number == 100044


def test_setup_import_writes_default_mesh_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    from godox_mesh_bt.state import default_mesh_state_path

    config_dir = tmp_path / "config"
    imported_state_path = tmp_path / "imported.json"
    imported = MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=1,
        node_address=2,
        sequence_number=100044,
        iv_index=0,
    )
    imported.save(imported_state_path)
    monkeypatch.setenv("GODOX_MESH_BT_CONFIG_DIR", str(config_dir))

    exit_code = main(["setup", "--import", str(imported_state_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert MeshState.load(default_mesh_state_path()) == imported
    assert str(default_mesh_state_path()) in captured.out


def test_control_without_mesh_state_explains_setup_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.setenv("GODOX_MESH_BT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("GODOX_MESH_BT_STATE", raising=False)
    monkeypatch.chdir(tmp_path)

    exit_code = main(["set", "--device", "device-id", "--brightness", "50", "--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "No mesh state found" in captured.err
    assert "godox-mesh setup" in captured.err


def test_set_saves_state_back_to_original_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After set, sequence_number is saved back to the SAME file that was loaded."""
    from godox_mesh_bt.state import MeshState

    state_file = tmp_path / "my_state.json"
    MeshState(
        network_key="98b2e7ef8211c6deca2401adbe52e715",
        app_key="fa0a2c615756eca3f896ce061ed4d890",
        device_key="",
        provisioner_address=1,
        node_address=2,
        sequence_number=5000,
        iv_index=0,
    ).save(state_file)

    monkeypatch.chdir(tmp_path)

    class MockController:
        def __init__(self, address: str, state_path: str | Path, **kwargs: object) -> None:
            self.address = address
            self.state_path = Path(state_path)
            self.state = MeshState.load(state_path)

        async def __aenter__(self) -> MockController:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def set_params(self, *, brightness: float | None, cct: int | None) -> None:
            self.state = self.state.next_sequence()
            self.state.save(self.state_path)

    monkeypatch.setattr("godox_mesh_bt.cli.GodoxController", MockController)

    exit_code = main(
        ["set", "--state", str(state_file), "--device", "AA:BB:CC:DD:EE:FF", "--brightness", "50"]
    )

    assert exit_code == 0
    saved = MeshState.load(state_file)
    assert saved.sequence_number > 5000
    assert not (tmp_path / "tmp_mesh_state.json").exists()


def test_set_discovers_state_from_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If --state is not given, mesh_state.json in cwd is used automatically."""
    from godox_mesh_bt.state import MeshState

    state_file = tmp_path / "mesh_state.json"
    MeshState(
        network_key="98b2e7ef8211c6deca2401adbe52e715",
        app_key="fa0a2c615756eca3f896ce061ed4d890",
        device_key="",
        provisioner_address=1,
        node_address=2,
        sequence_number=1000,
        iv_index=0,
    ).save(state_file)

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GODOX_MESH_BT_STATE", raising=False)
    monkeypatch.setenv("GODOX_MESH_BT_CONFIG_DIR", str(tmp_path / "config"))

    discovered_paths: list[Path] = []

    class MockController:
        def __init__(self, address: str, state_path: str | Path, **kwargs: object) -> None:
            self.address = address
            self.state_path = Path(state_path)
            discovered_paths.append(self.state_path)
            self.state = MeshState.load(state_path)

        async def __aenter__(self) -> MockController:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def set_params(self, *, brightness: float | None, cct: int | None) -> None:
            self.state = self.state.next_sequence()
            self.state.save(self.state_path)

    monkeypatch.setattr("godox_mesh_bt.cli.GodoxController", MockController)

    exit_code = main(["set", "--device", "AA:BB:CC:DD:EE:FF", "--brightness", "50"])

    assert exit_code == 0
    assert discovered_paths == [state_file]
    saved = MeshState.load(state_file)
    assert saved.sequence_number > 1000


def test_set_seq_bump_increases_sequence_number(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """--seq-bump N adds N to sequence_number in the state file before connecting."""
    import json

    state_file = tmp_path / "mesh_state.json"
    state_file.write_text(
        json.dumps(
            {
                "network_key": "98b2e7ef8211c6deca2401adbe52e715",
                "app_key": "fa0a2c615756eca3f896ce061ed4d890",
                "device_key": "",
                "provisioner_address": 1,
                "node_address": 2,
                "sequence_number": 1000,
                "iv_index": 0,
            },
            indent=2,
        )
    )

    captured_seq: dict[str, int] = {}

    class FakeController:
        def __init__(self, address: str, state_path: Any) -> None:
            from godox_mesh_bt.state import MeshState as _MeshState

            self.address = address
            self.state = _MeshState.load(state_path)
            captured_seq["seq"] = self.state.sequence_number

        async def __aenter__(self) -> "FakeController":
            return self

        async def __aexit__(self, *a: Any) -> None:
            pass

        async def set_params(self, **kwargs: Any) -> None:
            pass

    monkeypatch.setattr("godox_mesh_bt.cli.GodoxController", FakeController)

    exit_code = main(
        [
            "set",
            "--state",
            str(state_file),
            "--device",
            "AA:BB:CC:DD:EE:FF",
            "--brightness",
            "50",
            "--seq-bump",
            "50000",
        ],
    )

    assert exit_code == 0
    assert captured_seq["seq"] >= 51000  # 1000 + 50000


def test_set_cli_rejects_out_of_range_values(capsys) -> None:
    # Brightness out of range
    exit_code = main(["set", "--device", "dev", "--brightness", "101"])
    assert exit_code == 2
    assert "brightness must be between 0 and 100" in capsys.readouterr().err

    # CCT out of range: the CLI does not know the model, so it bounds by what
    # the protocol can encode rather than by one light's range.
    exit_code = main(["set", "--device", "dev", "--cct", "50"])
    assert exit_code == 2
    assert "CCT must be between" in capsys.readouterr().err


def test_rebind_subcommand_exists() -> None:
    from godox_mesh_bt.cli import _build_parser

    parser = _build_parser()
    # Verify the subcommand parses without error
    args = parser.parse_args(["rebind", "--device", "test-device"])
    assert args.command == "rebind"


def test_rebind_cli_delegates_to_controller(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    state_path = _minimal_mesh_state(
        tmp_path,
        device_key="aabbccddeeff00112233445566778899",
    )

    class FakeController:
        def __init__(self, address: str, state_path: Any) -> None:
            self.state = MeshState.load(state_path)

        async def __aenter__(self) -> "FakeController":
            return self

        async def __aexit__(self, *a: Any) -> None:
            pass

        async def rebind(self) -> None:
            calls.append("rebind")

    monkeypatch.setattr("godox_mesh_bt.cli.GodoxController", FakeController)

    exit_code = main(["rebind", "--device", "test-device", "--state", str(state_path)])

    assert exit_code == 0
    assert calls == ["rebind"]


def test_rebind_controller_raises_without_device_key(tmp_path: Path) -> None:
    import asyncio
    from godox_mesh_bt.controller import GodoxController

    state_path = _minimal_mesh_state(tmp_path)  # no device_key

    controller = GodoxController.__new__(GodoxController)
    controller.state = MeshState.load(state_path)

    with pytest.raises(ValueError, match="device_key"):
        asyncio.run(controller.rebind())


# --- provision subcommand tests ---


class FakeProvisioningSession:
    def __init__(self, address: str, net_key: bytes, key_index: int, iv_index: int, unicast_address: int, **kwargs: Any) -> None:
        self.address = address

    async def run(self) -> MeshState:
        return MeshState(
            network_key="aa" * 16,
            app_key="bb" * 16,
            device_key="cc" * 16,
            provisioner_address=1,
            node_address=0x0002,
            iv_index=0,
            sequence_number=1,
        )


def test_provision_subcommand_saves_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """provision saves MeshState to --output file."""
    import json
    from godox_mesh_bt.cli import sync_main
    from godox_mesh_bt.scanner import DiscoveredDevice, Advertisement

    output = tmp_path / "state.json"

    async def fake_scan_unprovisioned(timeout: float = 10.0) -> list[DiscoveredDevice]:
        return [DiscoveredDevice(name="GD_LED", address="AA:BB:CC:DD:EE:FF", rssi=None, advertisement=Advertisement(), likely_godox=False)]

    monkeypatch.setattr("godox_mesh_bt.cli.scan_unprovisioned", fake_scan_unprovisioned)
    monkeypatch.setattr("godox_mesh_bt.cli.ProvisioningSession", FakeProvisioningSession)

    sync_main(["provision", "--output", str(output)])

    assert output.exists()
    data = json.loads(output.read_text())
    assert "network_key" in data or "device_key" in data


def test_provision_subcommand_with_address(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """provision --address skips scanning."""
    from godox_mesh_bt.cli import sync_main

    output = tmp_path / "state.json"
    scan_called: list[bool] = []

    async def fake_scan_unprovisioned(timeout: float = 10.0) -> list[DiscoveredDevice]:
        scan_called.append(True)
        return []

    monkeypatch.setattr("godox_mesh_bt.cli.scan_unprovisioned", fake_scan_unprovisioned)
    monkeypatch.setattr("godox_mesh_bt.cli.ProvisioningSession", FakeProvisioningSession)

    sync_main(["provision", "--address", "AA:BB:CC:DD:EE:FF", "--output", str(output)])

    assert not scan_called  # No scan when address given


# ---------------------------------------------------------------------------
# device_address caching tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_controller_uses_device_address_from_state_skipping_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the state file has device_address, no scan should happen."""
    from godox_mesh_bt.cli import _get_controller

    state_path = tmp_path / "mesh_state.json"
    MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=1,
        node_address=2,
        sequence_number=100,
        iv_index=0,
        device_address="SAVED-DEVICE-ID",
    ).save(state_path)

    scan_called: list[bool] = []

    async def fake_scan(*, timeout: float) -> list[DiscoveredDevice]:
        scan_called.append(True)
        return []

    args = argparse.Namespace(
        device=None,
        state=str(state_path),
        network_key=None,
        app_key=None,
        provisioner_address=None,
        node_address=None,
        sequence_number=None,
        iv_index=None,
    )

    controller = await _get_controller(args, fake_scan)

    assert not scan_called, "scan should be skipped when state has device_address"
    assert controller.address == "SAVED-DEVICE-ID"


@pytest.mark.asyncio
async def test_get_controller_saves_device_address_to_state_after_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a scan finds a device, device_address is persisted to the state file."""
    from godox_mesh_bt.cli import _get_controller

    state_path = tmp_path / "mesh_state.json"
    MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=1,
        node_address=2,
        sequence_number=100,
        iv_index=0,
    ).save(state_path)

    async def fake_scan(*, timeout: float) -> list[DiscoveredDevice]:
        return [
            DiscoveredDevice(
                name="GD_LED",
                address="DISCOVERED-DEVICE-ID",
                rssi=-55,
                advertisement=Advertisement(local_name="GD_LED"),
                likely_godox=True,
            )
        ]

    args = argparse.Namespace(
        device=None,
        state=str(state_path),
        network_key=None,
        app_key=None,
        provisioner_address=None,
        node_address=None,
        sequence_number=None,
        iv_index=None,
    )

    controller = await _get_controller(args, fake_scan)

    assert controller.address == "DISCOVERED-DEVICE-ID"
    saved = MeshState.load(state_path)
    assert saved.device_address == "DISCOVERED-DEVICE-ID"
