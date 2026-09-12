"""Parsing and validation for user-supplied mesh state.

Kept free of Home Assistant imports so the rules can be tested on their own.
"""

from __future__ import annotations

import json
from typing import Any

from ._lib import MeshState

from .const import DEFAULT_NODE_ADDRESS, DEFAULT_PROVISIONER_ADDRESS

# Written by ``godox-mesh provision`` and shown in the project README.
REQUIRED_KEYS = ("network_key", "app_key")


class InvalidMeshState(Exception):
    """Raised when pasted mesh state cannot be turned into a usable network."""


def parse_mesh_state(raw: str) -> MeshState:
    """Parse the contents of a ``mesh_state.json`` file.

    Parameters
    ----------
    raw
        JSON text as produced by ``godox-mesh provision``.

    Returns
    -------
    MeshState
        Validated mesh state.

    Raises
    ------
    InvalidMeshState
        If the text is not JSON, is missing a key, or holds a malformed key.
    """
    try:
        values = json.loads(raw)
    except ValueError as err:
        raise InvalidMeshState("not valid JSON") from err

    if not isinstance(values, dict):
        raise InvalidMeshState("expected a JSON object")

    missing = [key for key in REQUIRED_KEYS if not values.get(key)]
    if missing:
        raise InvalidMeshState(f"missing {', '.join(missing)}")

    try:
        return MeshState(
            network_key=str(values["network_key"]),
            app_key=str(values["app_key"]),
            device_key=str(values.get("device_key") or ""),
            device_address=str(values.get("device_address") or ""),
            provisioner_address=int(
                values.get("provisioner_address", DEFAULT_PROVISIONER_ADDRESS)
            ),
            node_address=int(values.get("node_address", DEFAULT_NODE_ADDRESS)),
            sequence_number=int(values.get("sequence_number", 0)),
            iv_index=int(values.get("iv_index", 0)),
        )
    except (TypeError, ValueError) as err:
        raise InvalidMeshState(str(err)) from err


def mesh_state_to_dict(state: MeshState) -> dict[str, Any]:
    """Return mesh state as plain JSON-serializable config entry data."""
    return dict(state.to_dict())


def mesh_state_from_dict(data: dict[str, Any]) -> MeshState:
    """Rebuild mesh state from config entry data."""
    return MeshState(
        network_key=data["network_key"],
        app_key=data["app_key"],
        device_key=data.get("device_key", ""),
        device_address=data.get("device_address", ""),
        provisioner_address=data["provisioner_address"],
        node_address=data["node_address"],
        sequence_number=data.get("sequence_number", 0),
        iv_index=data.get("iv_index", 0),
    )


def parse_node_address(raw: str | int) -> int:
    """Parse a unicast node address given as decimal or ``0x``-prefixed hex.

    Raises
    ------
    InvalidMeshState
        If the value is not a valid mesh unicast address.
    """
    try:
        value = int(str(raw), 0)
    except (TypeError, ValueError) as err:
        raise InvalidMeshState(f"{raw!r} is not a number") from err
    # Unicast addresses are 0x0001-0x7FFF; 0x0000 is unassigned and the top bit
    # marks group and virtual addresses.
    if not 0x0001 <= value <= 0x7FFF:
        raise InvalidMeshState("node address must be between 0x0001 and 0x7FFF")
    return value
