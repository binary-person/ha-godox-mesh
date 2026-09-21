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
    #: Green/magenta tint per node address, set by its number entity and read
    #: by the light when it sends a colour-temperature command. Like the effect
    #: speed, no status record reports it back.
    tints: dict[int, int] = field(default_factory=dict)
    #: Gel currently selected per node address, by display label.
    color_chips: dict[int, str] = field(default_factory=dict)
    #: Brightness per node address, as the percentage last sent. The gel
    #: control needs it: its command carries brightness, so without this
    #: picking a gel would also jump the light to full.
    brightness_pct: dict[int, float] = field(default_factory=dict)
    #: CIE xy per node address. The two coordinates travel in one frame, so
    #: the sliders share a value here rather than each holding half of it.
    xy: dict[int, tuple[float, float]] = field(default_factory=dict)
    #: Control mode and mains frequency per node address. They travel in one
    #: frame, so the two selects share a value here.
    control_mode: dict[int, tuple[int, int]] = field(default_factory=dict)
    #: Whether the light entity is in selfie colour-temperature mode, which
    #: swaps the colour-temperature range it advertises.
    selfie: dict[int, bool] = field(default_factory=dict)


# Plain assignment rather than a PEP 695 type statement: this repository
# is linted against Python 3.10, where that syntax does not parse.
GodoxConfigEntry = ConfigEntry[GodoxRuntimeData]
