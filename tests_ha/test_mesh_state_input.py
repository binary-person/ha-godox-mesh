"""Tests for parsing user-supplied mesh state."""

from __future__ import annotations

import json

import pytest
from custom_components.godox_mesh.mesh_state_input import (
    InvalidMeshState,
    mesh_state_from_dict,
    mesh_state_to_dict,
    parse_mesh_state,
    parse_node_address,
)

from tests_ha.conftest import MESH_STATE


def test_parses_a_full_mesh_state_file() -> None:
    state = parse_mesh_state(json.dumps(MESH_STATE))

    assert state.network_key == MESH_STATE["network_key"]
    assert state.node_address == 2
    assert state.sequence_number == 300000


def test_defaults_optional_fields() -> None:
    state = parse_mesh_state(
        json.dumps(
            {"network_key": "00" * 16, "app_key": "11" * 16}
        )
    )

    assert state.provisioner_address == 1
    assert state.node_address == 2
    assert state.iv_index == 0
    assert state.device_key == ""


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("not json at all", "not valid JSON"),
        ("[1, 2, 3]", "expected a JSON object"),
        ('{"app_key": "11111111111111111111111111111111"}', "missing network_key"),
        ('{"network_key": "00", "app_key": "11"}', "16 bytes hex"),
    ],
)
def test_rejects_bad_mesh_state(raw: str, reason: str) -> None:
    with pytest.raises(InvalidMeshState, match=reason):
        parse_mesh_state(raw)


def test_round_trips_through_config_entry_data() -> None:
    state = parse_mesh_state(json.dumps(MESH_STATE))

    assert mesh_state_from_dict(mesh_state_to_dict(state)) == state


@pytest.mark.parametrize(
    ("raw", "expected"), [("2", 2), ("0x0003", 3), ("0x7FFF", 0x7FFF), (5, 5)]
)
def test_parses_node_addresses(raw, expected) -> None:
    assert parse_node_address(raw) == expected


@pytest.mark.parametrize("raw", ["0", "0x8000", "-1", "banana", "0xFFFF"])
def test_rejects_invalid_node_addresses(raw) -> None:
    with pytest.raises(InvalidMeshState):
        parse_node_address(raw)
