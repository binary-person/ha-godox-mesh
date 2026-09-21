"""Constants for the Godox Bluetooth Mesh integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "godox_mesh"
INTEGRATION_TITLE: Final = "Godox Bluetooth Mesh"

#: Shown first in the light's effect list. Home Assistant gives no other way to
#: clear a running effect, and the protocol leaves effect mode when it receives
#: a colour-temperature command -- so this maps onto one.
#: The readback patch in tools/lk8620/ makes a light report this BLE firmware
#: version, so a version query distinguishes a patched light from stock (66+).
#: The integration no longer flashes anything; this only reports what it finds.
PATCHED_FIRMWARE_VERSION: Final = 1

EFFECT_OFF: Final = "Off"

#: Dispatcher signal, formatted with the node's unique id, sent when a light's
#: effect changes. The effect-speed control listens so it can re-advertise its
#: range: each effect accepts a different number of speeds, and a slider whose
#: maximum does not follow the running effect cannot tell the user that.
SIGNAL_EFFECT_CHANGED: Final = "godox_mesh_effect_changed_{node_id}"

#: Dispatcher signal, formatted the same way, sent when a light's green/magenta
#: tint changes. Tint has no command of its own -- it rides the
#: colour-temperature frame -- so the light re-sends that frame on this signal,
#: rather than the tint control having to find the light and guess its state.
SIGNAL_TINT_CHANGED: Final = "godox_mesh_tint_changed_{node_id}"

#: Entry option: drive colour through CIE xy instead of hue/saturation, on the
#: 40 models that accept the xy command. Off by default -- hue and saturation
#: are what a dashboard colour wheel speaks natively, and xy is only worth the
#: swap to someone entering coordinates from a colour meter.
#:
#: It has to be a swap rather than an addition. Home Assistant resolves a
#: colour wheel's ``hs_color`` against the first mode a light advertises from
#: RGB, RGBW, RGBWW, then XY -- so a light advertising HS or RGBW alongside XY
#: never reaches its xy command from the wheel at all.
CONF_USE_XY: Final = "use_xy"

#: Dispatcher signal, formatted with the node's unique id, sent when one of the
#: xy coordinate controls moves. Like the tint, a coordinate has no command of
#: its own -- x and y travel together in one frame -- so the light re-sends it.
SIGNAL_XY_CHANGED: Final = "godox_mesh_xy_changed_{node_id}"

#: Dispatcher signal, formatted with the node's unique id, sent when a light is
#: switched between its normal and selfie colour-temperature ranges. The light
#: re-advertises its bounds and re-sends on the matching command.
SIGNAL_CCT_RANGE_CHANGED: Final = "godox_mesh_cct_range_changed_{node_id}"

MANUFACTURER: Final = "Godox"

# Config entry data (identity and secrets — changes rarely).
CONF_MESH: Final = "mesh"
CONF_NODES: Final = "nodes"
CONF_NODE_ADDRESS: Final = "node_address"
CONF_MESH_STATE_JSON: Final = "mesh_state_json"
CONF_MODEL: Final = "model"
# The 4-hex Godox radioId (e.g. "003F"), the key into per-model capabilities.
CONF_RADIO_ID: Final = "radio_id"
# Opt-in: poll the light for its actual state instead of assuming the last
# command took effect. This works on **stock** firmware -- no patch is required
# for brightness, commanded colour temperature, or battery. Off by default only
# because polling costs a round trip per update.
CONF_READBACK: Final = "readback"
# Some lights report a colour temperature that is not their real setting after
# it is changed on the light's own panel: the SL200III Bi does this, the SL60II
# Bi does not, and nothing in the reply distinguishes them. So the integration
# reports what it is given, and this lets a user switch colour-temperature
# polling off if their light is one of the wrong ones.
CONF_POLL_CCT: Final = "poll_cct"
# Each node has its own device key, needed only to re-bind the application key.
CONF_DEVICE_KEY: Final = "device_key"

# Bluetooth Mesh service UUIDs, expanded from their 16-bit forms.
MESH_PROVISIONING_SERVICE_UUID: Final = "00001827-0000-1000-8000-00805f9b34fb"
MESH_PROXY_SERVICE_UUID: Final = "00001828-0000-1000-8000-00805f9b34fb"

# Names the light is known to advertise. Used to sort likely Godox devices to
# the top of the picker, never to reject a device: the mesh proxy beacon does
# not always carry a local name, and model names vary across the range.
# The device name Godox's own app filters on. Model names are deliberately not
# listed here: a per-model name list only ever recognises the models someone
# thought to add, and the range is 190 lights.
GODOX_DEVICE_NAME: Final = "gd_led"
GODOX_NAME_HINTS: Final = ("gd_", "godox")

# The device speaks whole percent. 0 means "off" rather than "dimmest", so the
# scale starts at 1 — see homeassistant.util.color.brightness_to_value.
BRIGHTNESS_SCALE: Final = (1, 100)

# Mesh sequence numbers must never be reused, or the node's replay protection
# list silently drops the PDU. Claim a block up front so an unclean shutdown
# costs a harmless gap instead of a dead light.
SEQUENCE_BLOCK_SIZE: Final = 256

# Holding the proxy connection open keeps a brightness slider responsive; the
# handshake costs a beacon echo plus two filter PDUs on every reconnect. Held
# long enough that normal use rarely pays for a reconnect, but not forever, so
# the adapter's connection slot is released when the lights are idle.
IDLE_DISCONNECT_SECONDS: Final = 300.0

# How often to poll a light for battery charge. Battery moves slowly and
# each poll wakes the shared connection, so this is deliberately infrequent.
BATTERY_POLL_SECONDS: Final = 600.0

# The device only acknowledges proxy filter configuration during the original
# provisioning session, so on every later connection the library's default
# five-second wait per filter PDU is ten seconds of guaranteed dead time. Wait
# briefly for an acknowledgement that may come, then get on with it.
PROXY_CONFIG_ACK_TIMEOUT: Final = 0.5
BEACON_WAIT_TIMEOUT: Final = 2.0


# Each of these lights is a two-element mesh node: element 0 holds the vendor
# model and most SIG models, element 1 the Light CTL Temperature server. A node
# occupies that many consecutive unicast addresses, so the allocator must
# advance by the element count or a second light collides with the first light's
# element 1.
ELEMENTS_PER_NODE: Final = 2

# Per-node element count, recorded from the device's own Provisioning
# Capabilities PDU. ELEMENTS_PER_NODE above is only the fallback used before a
# node has told us, and for entries written before this was stored.
CONF_NUM_ELEMENTS: Final = "num_elements"

DEFAULT_PROVISIONER_ADDRESS: Final = 1
DEFAULT_NODE_ADDRESS: Final = 2
