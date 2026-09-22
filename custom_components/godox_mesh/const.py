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

#: Dispatcher signal, formatted the same way, sent when the effect-speed control
#: changes. Speed rides the effect frame, so -- like tint -- the light re-sends
#: the running effect on this signal, and the change takes effect at once rather
#: than waiting for the effect to be picked again.
SIGNAL_EFFECT_SPEED_CHANGED: Final = "godox_mesh_effect_speed_changed_{node_id}"

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
# Whether to trust the light's reported brightness. On by default; a user can
# switch it off to keep the commanded brightness if their light reports a wrong
# level (some do after a firmware glitch), while still reading colour and state.
CONF_POLL_BRIGHTNESS: Final = "poll_brightness"
# How often (seconds) to poll a light for its live state, when readback is on.
# Per-node; the entry-wide value, if any, is only a migration fallback.
CONF_POLL_INTERVAL: Final = "poll_interval"
DEFAULT_POLL_INTERVAL: Final = 10
MIN_POLL_INTERVAL: Final = 5
MAX_POLL_INTERVAL: Final = 3600

# How long a Bluetooth discovery waits for an advert that carries the model id
# before naming the light. These lights alternate advert packets and discovery
# often fires on the one without the manufacturer data, so a brief wait lets the
# discovered card show the model rather than the bare advertised name. The mesh
# packet is broadcast every few seconds, so this rarely runs to the end; on
# timeout the light is named from what triggered discovery.
DISCOVERY_MODEL_WAIT_SECONDS: Final = 15
# Each node has its own device key, needed only to re-bind the application key.
CONF_DEVICE_KEY: Final = "device_key"
# The node's own BLE address, captured when it was provisioned/joined. Any node
# on the mesh can be the proxy the integration connects through, so knowing each
# node's address lets it fail over to a reachable one when the usual gateway is
# off, without waiting for a Network-ID advert.
CONF_MAC: Final = "mac"

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

# bleak-retry-connector's per-address retry count. Held at one attempt: every
# retry it does is to the *same* node (the gateway is chosen before the connect),
# so retrying there only delays trying a different node. One try, then the link
# re-selects the head of the gateway list -- which drops the node that just
# failed and picks up any that advertised in the meantime -- and a node re-tries
# on its own next advertisement anyway.
MESH_CONNECT_MAX_ATTEMPTS: Final = 1

# A command that fails while the proxy connection still reports connected does
# not drop it -- an off node that ignores a status poll must not cost the shared
# connection every other light rides on. But a *run* of failures with nothing
# succeeding in between means the connection has most likely wedged silently
# (the disconnect never surfaced), so after this many the link forces a clean
# reconnect. A single sibling command succeeding resets the count, so one off
# light among healthy ones never trips it.
MAX_CONSECUTIVE_FAILURES: Final = 3

# A mesh node whose most recent advertisement is younger than this is treated as
# "fresh" and tried before one still lingering in Home Assistant's device cache
# after it went quiet. Only a ranking hint -- a stale node is still tried, just
# second -- so erring short is safe; a node that is genuinely connected stops
# advertising and would look stale, which is why freshness never *excludes* a
# candidate, and the currently-used node is protected by stickiness regardless.
FRESH_ADVERT_SECONDS: Final = 60.0

# A connection that drops on its own within this many seconds of opening did not
# hold a useful session -- the node "will not hold". Such a node is deprioritised
# so a steadier sibling is tried first. A drop after a longer session is treated
# as a one-off blip, and the node is reconnected to as normal.
SHORT_HOLD_SECONDS: Final = 60.0

# How long a node that dropped quickly is kept below steadier nodes in the
# gateway order. It is only a ranking penalty -- the node is still used when it
# is the only one reachable, and stickiness keeps a settled sibling once chosen.
DROP_PENALTY_SECONDS: Final = 180.0

# Consecutive failed readback polls before a light is shown "unavailable". A
# light answers its own status request over the mesh, so a run of no-answers
# means it is off or out of range; a few strikes rather than one avoids flipping
# on a single missed reply. Only lights with readback on poll, so this is the
# only availability signal they get -- an un-polled light stays available.
FAILED_POLLS_BEFORE_UNAVAILABLE: Final = 3

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
