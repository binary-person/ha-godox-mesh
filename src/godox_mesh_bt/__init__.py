"""Public Python API for Godox Bluetooth Mesh control.

Typical usage
-------------
Control a light that has already been provisioned and rebound::

    import asyncio
    from godox_mesh_bt import GodoxController

    async def main():
        async with GodoxController("304BCD50-...", "mesh_state.json") as light:
            await light.power_on()
            await light.set_params(brightness=80, cct=4000)

    asyncio.run(main())

For first-time setup use the CLI::

    godox-mesh provision   # provision factory-reset device
    godox-mesh rebind      # push app key
    godox-mesh set --brightness 80 --cct 4000

Examples
--------
>>> sorted(__all__)
['BatteryPower', 'BatteryTimeout', 'GodoxController', 'MeshState', 'ProxyClient', 'SequenceReserver', 'StatusResponse', 'StatusTimeout', 'GodoxMeshClient', 'VersionTimeout']
"""

from __future__ import annotations

import logging

from godox_mesh_bt.client import ProxyClient, GodoxMeshClient
from godox_mesh_bt.controller import (
    BatteryTimeout,
    GodoxController,
    StatusTimeout,
    VersionTimeout,
)
from godox_mesh_bt.protocol import BatteryPower, StatusResponse
from godox_mesh_bt.sequence import SequenceReserver
from godox_mesh_bt.state import MeshState

logger = logging.getLogger(__name__)

__all__ = [
    "ProxyClient",
    "GodoxMeshClient",
    "GodoxController",
    "MeshState",
    "BatteryPower",
    "BatteryTimeout",
    "SequenceReserver",
    "StatusResponse",
    "StatusTimeout",
    "VersionTimeout",
]
