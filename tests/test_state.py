from __future__ import annotations

import pytest

from godox_mesh_bt.state import (
    MeshState,
    audit_mesh_state,
    default_mesh_state_path,
    import_mesh_state,
    resolve_mesh_state_path,
    save_default_mesh_state,
)


def test_mesh_state_round_trips_through_json(tmp_path) -> None:
    state = MeshState(
        network_key="125b33087af5d8f300114c2d4891378b",
        app_key="414bf26e7af1eb6a0f642628470ebf8d",
        provisioner_address=1,
        node_address=2,
        sequence_number=256,
        iv_index=0,
    )

    path = tmp_path / "state.json"
    state.save(path)

    assert MeshState.load(path) == state


def test_mesh_state_device_address_round_trips(tmp_path) -> None:
    state = MeshState(
        network_key="125b33087af5d8f300114c2d4891378b",
        app_key="414bf26e7af1eb6a0f642628470ebf8d",
        provisioner_address=1,
        node_address=2,
        sequence_number=256,
        iv_index=0,
        device_address="304BCD50-D2C2-4FA6-A666-F4867E54F267",
    )

    path = tmp_path / "state.json"
    state.save(path)
    loaded = MeshState.load(path)

    assert loaded.device_address == "304BCD50-D2C2-4FA6-A666-F4867E54F267"
    assert loaded == state


def test_mesh_state_device_address_defaults_to_empty() -> None:
    state = MeshState(
        network_key="125b33087af5d8f300114c2d4891378b",
        app_key="414bf26e7af1eb6a0f642628470ebf8d",
        provisioner_address=1,
        node_address=2,
        sequence_number=1,
        iv_index=0,
    )
    assert state.device_address == ""


def test_mesh_state_next_sequence_increments() -> None:
    state = MeshState(
        network_key="125b33087af5d8f300114c2d4891378b",
        app_key="414bf26e7af1eb6a0f642628470ebf8d",
        provisioner_address=1,
        node_address=2,
        sequence_number=256,
        iv_index=0,
    )

    assert state.next_sequence().sequence_number == 257


def test_mesh_state_validates_inputs() -> None:
    with pytest.raises(ValueError, match="sequence_number must be non-negative"):
        MeshState(
            network_key="125b33087af5d8f300114c2d4891378b",
            app_key="414bf26e7af1eb6a0f642628470ebf8d",
            provisioner_address=1,
            node_address=2,
            sequence_number=-1,
            iv_index=0,
        )

    with pytest.raises(ValueError, match="network_key must be 16 bytes hex"):
        MeshState(
            network_key="deadbeef",
            app_key="414bf26e7af1eb6a0f642628470ebf8d",
            provisioner_address=1,
            node_address=2,
            sequence_number=0,
            iv_index=0,
        )


def test_mesh_state_parses_telink_shared_xml() -> None:
    xml = """
    <map>
      <entry name="network_key" value="125b33087af5d8f300114c2d4891378b" />
      <entry name="app_key" value="414bf26e7af1eb6a0f642628470ebf8d" />
      <entry name="provisioner_address" value="0x0001" />
      <entry name="node_address" value="0x0002" />
      <entry name="sequence_number" value="0x0100" />
      <entry name="iv_index" value="0x00000000" />
    </map>
    """

    state = MeshState.from_telink_shared_xml(xml)

    assert state.sequence_number == 256
    assert state.provisioner_address == 1


def test_mesh_state_parses_telink_shared_xml_string_element_format() -> None:
    # Android SharedPreferences XML uses <string name="key">value</string>
    xml = """<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="network_key">125b33087af5d8f300114c2d4891378b</string>
    <string name="app_key">414bf26e7af1eb6a0f642628470ebf8d</string>
    <string name="provisioner_address">0x0001</string>
    <string name="node_address">0x0002</string>
    <string name="sequence_number">256</string>
    <string name="iv_index">0x00000000</string>
</map>
"""
    state = MeshState.from_telink_shared_xml(xml)
    assert state.network_key == "125b33087af5d8f300114c2d4891378b"
    assert state.provisioner_address == 0x0001
    assert state.sequence_number == 256
    assert state.iv_index == 0


def test_default_mesh_state_path_uses_config_dir_override(tmp_path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setenv("GODOX_MESH_BT_CONFIG_DIR", str(config_dir))

    assert default_mesh_state_path() == config_dir / "mesh_state.json"


def test_save_default_mesh_state_creates_user_config_file(tmp_path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setenv("GODOX_MESH_BT_CONFIG_DIR", str(config_dir))
    state = MeshState(
        network_key="125b33087af5d8f300114c2d4891378b",
        app_key="414bf26e7af1eb6a0f642628470ebf8d",
        provisioner_address=1,
        node_address=2,
        sequence_number=256,
        iv_index=0,
    )

    saved_path = save_default_mesh_state(state)

    assert saved_path == config_dir / "mesh_state.json"
    assert MeshState.load(saved_path) == state


def test_resolve_mesh_state_path_order(tmp_path, monkeypatch) -> None:
    explicit = tmp_path / "explicit.json"
    env_state = tmp_path / "env.json"
    cwd = tmp_path / "work"
    local = cwd / "mesh_state.json"
    config_dir = tmp_path / "config"
    config_state = config_dir / "mesh_state.json"
    for path in (explicit, env_state, local, config_state):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    monkeypatch.setenv("GODOX_MESH_BT_STATE", str(env_state))
    monkeypatch.setenv("GODOX_MESH_BT_CONFIG_DIR", str(config_dir))

    assert resolve_mesh_state_path(explicit, cwd=cwd) == explicit
    assert resolve_mesh_state_path(None, cwd=cwd) == env_state

    monkeypatch.delenv("GODOX_MESH_BT_STATE")
    assert resolve_mesh_state_path(None, cwd=cwd) == local

    local.unlink()
    assert resolve_mesh_state_path(None, cwd=cwd) == config_state

    config_state.unlink()
    assert resolve_mesh_state_path(None, cwd=cwd) is None


def test_import_mesh_state_reads_json_and_telink_shared_xml(tmp_path) -> None:
    json_path = tmp_path / "mesh_state.json"
    xml_path = tmp_path / "telink_shared.xml"
    state = MeshState(
        network_key="125b33087af5d8f300114c2d4891378b",
        app_key="414bf26e7af1eb6a0f642628470ebf8d",
        provisioner_address=1,
        node_address=2,
        sequence_number=256,
        iv_index=0,
    )
    state.save(json_path)
    xml_path.write_text(
        """
        <map>
          <entry name="network_key" value="125b33087af5d8f300114c2d4891378b" />
          <entry name="app_key" value="414bf26e7af1eb6a0f642628470ebf8d" />
          <entry name="provisioner_address" value="0x0001" />
          <entry name="node_address" value="0x0002" />
          <entry name="sequence_number" value="0x0100" />
          <entry name="iv_index" value="0x00000000" />
        </map>
        """
    )

    assert import_mesh_state(json_path) == state
    assert import_mesh_state(xml_path) == state


def test_audit_mesh_state_reports_matching_state() -> None:
    current = MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=1,
        node_address=2,
        sequence_number=100055,
        iv_index=0,
    )
    reference = MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=1,
        node_address=2,
        sequence_number=256,
        iv_index=0,
    )

    report = audit_mesh_state(current, reference)

    assert report == {
        "network_key": "match",
        "app_key": "match",
        "provisioner_address": "match",
        "node_address": "match",
        "iv_index": "match",
        "sequence_number": "current_ahead",
    }


def test_mesh_state_device_key_round_trips_json(tmp_path):
    state = MeshState(
        network_key="98b2e7ef8211c6deca2401adbe52e715",
        app_key="fa0a2c615756eca3f896ce061ed4d890",
        device_key="6277be2be27af9818c3d79b62a2a8ae7",
        provisioner_address=1,
        node_address=2,
        sequence_number=300000,
        iv_index=0,
    )
    path = tmp_path / "state.json"
    state.save(path)
    loaded = MeshState.load(path)
    assert loaded.device_key == "6277be2be27af9818c3d79b62a2a8ae7"


def test_mesh_state_empty_device_key_is_valid():
    state = MeshState(
        network_key="98b2e7ef8211c6deca2401adbe52e715",
        app_key="fa0a2c615756eca3f896ce061ed4d890",
        device_key="",
        provisioner_address=1,
        node_address=2,
        sequence_number=1,
        iv_index=0,
    )
    assert state.device_key == ""


def test_mesh_state_invalid_device_key_raises():
    with pytest.raises(ValueError, match="device_key"):
        MeshState(
            network_key="98b2e7ef8211c6deca2401adbe52e715",
            app_key="fa0a2c615756eca3f896ce061ed4d890",
            device_key="notvalidhex",
            provisioner_address=1,
            node_address=2,
            sequence_number=1,
            iv_index=0,
        )


def test_mesh_state_load_without_device_key_defaults_to_empty(tmp_path):
    import json
    data = {
        "network_key": "98b2e7ef8211c6deca2401adbe52e715",
        "app_key": "fa0a2c615756eca3f896ce061ed4d890",
        "provisioner_address": 1,
        "node_address": 2,
        "sequence_number": 1,
        "iv_index": 0,
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(data))
    state = MeshState.load(path)
    assert state.device_key == ""


def test_mesh_state_next_sequence_preserves_device_key():
    state = MeshState(
        network_key="98b2e7ef8211c6deca2401adbe52e715",
        app_key="fa0a2c615756eca3f896ce061ed4d890",
        device_key="6277be2be27af9818c3d79b62a2a8ae7",
        provisioner_address=1,
        node_address=2,
        sequence_number=1,
        iv_index=0,
    )
    next_state = state.next_sequence()
    assert next_state.device_key == "6277be2be27af9818c3d79b62a2a8ae7"


def test_mesh_state_save_produces_indented_json(tmp_path):
    state = MeshState(
        network_key="98b2e7ef8211c6deca2401adbe52e715",
        app_key="fa0a2c615756eca3f896ce061ed4d890",
        provisioner_address=1,
        node_address=2,
        sequence_number=1,
        iv_index=0,
    )
    path = tmp_path / "state.json"
    state.save(path)
    text = path.read_text()
    assert "\n" in text  # indented, not single line


def test_audit_mesh_state_reports_stale_or_changed_state() -> None:
    current = MeshState(
        network_key="bc6bf99f840f9ca379562468a110d2a9",
        app_key="9f360e3d7d27a7e9aa56ab7342f1e3e4",
        provisioner_address=4,
        node_address=2,
        sequence_number=100,
        iv_index=1,
    )
    reference = MeshState(
        network_key="00000000000000000000000000000000",
        app_key="11111111111111111111111111111111",
        provisioner_address=1,
        node_address=2,
        sequence_number=256,
        iv_index=0,
    )

    report = audit_mesh_state(current, reference)

    assert report == {
        "network_key": "mismatch",
        "app_key": "mismatch",
        "provisioner_address": "mismatch",
        "node_address": "match",
        "iv_index": "mismatch",
        "sequence_number": "current_behind",
    }
