"""Runtime types shared across the Godox Bluetooth Mesh platforms."""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry

from .capabilities import GodoxCapabilities, capabilities_for_radio_id
from .mesh import GodoxMeshLink
from .store import GodoxSequenceStore


@dataclass(frozen=True, slots=True)
class GodoxNode:
    """One addressable light on the mesh network."""

    address: int
    name: str
    model: str | None = None
    radio_id: str | None = None

    @property
    def capabilities(self) -> GodoxCapabilities:
        """Per-model controls (colour range, fan), from the ``radio_id``."""
        return capabilities_for_radio_id(self.radio_id)


@dataclass
class GodoxRuntimeData:
    """Everything a platform needs, hung off the config entry."""

    link: GodoxMeshLink
    store: GodoxSequenceStore
    nodes: list[GodoxNode]
    #: Effect speed per node address, set by the number entity and read by the
    #: light when it sends an effect. The light cannot report its speed back,
    #: so this is the only record of it.
    effect_speeds: dict[int, int] = field(default_factory=dict)
    #: Effect currently running per node address, or None. The speed control
    #: reads it to bound itself to what that effect actually accepts.
    current_effect: dict[int, str | None] = field(default_factory=dict)


# Plain assignment rather than a PEP 695 type statement: this repository
# is linted against Python 3.10, where that syntax does not parse.
GodoxConfigEntry = ConfigEntry[GodoxRuntimeData]
