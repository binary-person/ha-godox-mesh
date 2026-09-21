# Godox Bluetooth Mesh — Home Assistant integration

Control Godox Bluetooth Mesh lights from Home Assistant, installable through
HACS.

## What you get

One `light` entity per light, with brightness and colour temperature over that
model's own range — a 1800–10000 K light gets its full span, and a fixed-daylight
model gets no colour slider at all. Lights are discovered automatically over
Bluetooth, and can be provisioned onto a mesh network from inside Home Assistant
— no phone app, no command line.

Several lights on one mesh network share a single Bluetooth connection, so ten
lights cost one connection slot rather than ten.

## Supported hardware

**186 Godox Bluetooth Mesh lights**, across all three of Godox's mesh radios
(LK8620, LK8720, LK8728B). They all speak one identical vendor protocol, so
support is not per-model code — the integration ships a capability table and
reads the light's own model ID during pairing.

| | |
|---|---|
| On/off, brightness, effects | all 186 |
| Colour temperature, per-model range | 159 bi-colour models |
| Brightness only (fixed daylight) | 27 models |
| Battery level | 25 battery-capable models, opt-in |

Families include SL, ML, P, M, LA, F, MG, LE, LC, LP, WT, DL, TL, UL, LDX, RS,
OP and others. Godox's mesh range is 190 products in total; the other 4 are
motorised accessories (the AD00-01/AD00-02 soft-light modifiers, AD88 and
LF100MPY) that report no colour temperature and are not exposed as lights.

**Find your model:** [docs/models.md](docs/models.md) is a generated,
one-row-per-model list — what each light gets, how confident that support is,
and anything the vendor app can do that this integration cannot. Regenerate it
with `uv run python scripts/generate_model_support.py`.

**Is your model verified?** Only a handful are confirmed on real hardware so far
— [docs/models.md](docs/models.md) marks which, with any known quirks. The rest
are catalogue-derived: the integration sends the vendor app's own frames, but
the specific light has not been exercised here. If yours works — or misbehaves —
please post it on the [model support
board](https://github.com/binary-person/ha-godox-mesh/issues/1); confirmed
models and quirks are curated into `docs/model_notes.json`.

**Not supported:** 36 Godox products that are Bluetooth but *not* mesh —
including the FL100BI/200Bi/400BI/600BI, LF20BI/LF30BI, LA150D/200D/300D II,
LC500R II and Mini, MG4800R/D, and AT20 Battery. Godox's own app cannot reach
them over mesh either. See
[docs/model-support.md](docs/model-support.md) for the full list and the
evidence.

## Requirements

- Home Assistant **2026.6.0** or newer — the first release shipping `bleak` 3.x,
  which this integration requires.
- A Bluetooth adapter or an ESPHome Bluetooth proxy within range of the light.
  The connection must be *connectable*; a listen-only proxy is not enough.

Nothing is installed from PyPI — the library ships inside the integration.

## Install

1. In HACS, choose **Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/binary-person/ha-godox-mesh` with category
   **Integration**, then install *Godox Bluetooth Mesh*.
3. Restart Home Assistant.
4. The light is usually discovered automatically. Otherwise go to
   **Settings → Devices & services → Add integration → Godox Bluetooth Mesh**.

## Setup

The config flow offers two paths.

### Provision this light

For a light in pairing mode. How you get there varies by model — usually
holding the Bluetooth button until the light flashes — but whatever steps you
would follow to pair it with the Godox app are the right ones here. Home
Assistant then generates a fresh network key and application key, runs the
Bluetooth Mesh provisioning exchange, and binds the key to the light's vendor
model.

This replaces any pairing with the Godox app; the app will not see the light
again until you reset it and re-add it there.

### Use existing mesh keys

For a light already provisioned elsewhere — by the upstream CLI, or imported
from the Godox app. Paste the contents of its `mesh_state.json`:

```json
{
  "network_key": "<32 hex characters>",
  "app_key": "<32 hex characters>",
  "device_key": "<32 hex characters>",
  "provisioner_address": 1,
  "node_address": 2,
  "iv_index": 0,
  "sequence_number": 300000
}
```

Only `network_key` and `app_key` are required; everything else falls back to
the values shown. On import the sequence number is raised to at least 300000 —
see [Troubleshooting](#the-light-ignores-commands).

## Several lights

Two shapes work, and the choice is a trade of Bluetooth connection slots
against convenience.

### One network, many lights

After adding the first light, use **Configure** on the integration:

- **Provision a new light onto this network** — factory reset the light, pick
  it from the list. It joins using the keys this entry already holds, is
  assigned the next free unicast address, and has the application key bound.
- **Add a light that is already provisioned** — for a light you enrolled onto
  this same network yourself, give its name and unicast address.
- **Remove a light** — removes its entity and device. The last light cannot be
  removed; delete the integration entry instead.

All of them share one Bluetooth connection. This is the slot-efficient shape.

### One entry per light

Add the integration once per light, provisioning each with its own keys. Every
light gets its own direct connection, so nothing is shared and no light can
affect another — but it costs one connection slot each.

### Which to choose

An ESP32 Bluetooth proxy has **three** connection slots by default, five if you
raise `bluetooth_proxy: connection_slots` and `esp32_ble: max_connections`
together. Use separate entries while you are inside that budget, and one network
once you are not — for example when a scene switches on more lights at once
than you have slots.

## Which light Home Assistant connects to

A Bluetooth Mesh network is entered through any one node: Home Assistant opens
an ordinary connection to a single light, and that light relays to the rest of
the mesh.

The node is chosen dynamically. Proxy-capable nodes advertise the network's
Network ID, so Home Assistant can recognise any light on this network — even
one it never provisioned — and enter through whichever is reachable. Unplugging
the light an entry was created against no longer takes the network down.

Selection is sticky: keep the node in use, otherwise prefer the light the entry
was created against, otherwise the strongest signal. Each reconnect costs a
beacon echo and two filter messages, so churning between nodes as signal drifts
would be worse than staying put. If no node is advertising a recognisable
Network ID, this falls back to the configured address, so behaviour is never
worse than a fixed gateway.

The connection is held for five minutes of idle time, then released so the
adapter's slot is free.

## What the entity does

Each light is a colour-temperature entity — brightness plus its colour range —
and the lighting effects the firmware supports. Values are restored across
restarts.

**Controls follow the model.** At pairing, the config flow asks which model the
light is (a searchable list) and gives it the right controls from there: the
correct colour-temperature range — 2800–6500 K, 1800–10000 K, and so on — or, for
a fixed-daylight light, brightness only with no colour slider. This comes from a
per-model capability table, so a new Godox mesh light is a new table row, not new
code. If you leave the model unset, the light still works on a standard
2800–6500 K colour-temperature control.

**Effects are named, and per-model.** An SL200III Bi offers Lightning, Candle
and Firework — the effects that model actually ships — rather than a numbered
list shared across the range. Selecting an effect and setting a colour
temperature are mutually exclusive in the protocol, so doing either clears the
other. A model the table does not know falls back to a small set named by
number.

**Effect speed** is a separate `number` entity, because Home Assistant's light
platform has no concept of it. Set the speed, then pick an effect. It appears
only for the 55 models with at least one multi-speed effect.

**Most effects ignore it**, and you can see which before choosing: the effect
list annotates the ones that have speeds — *Lightning (2 speeds)* — and leaves
the rest plain. On an SL200III Bi two of seven are annotated; on a TL60,
thirteen of fourteen.

Once an effect is running the slider re-bounds itself to that effect, so it
collapses to a single position on one that has no speeds. Both the plain and
annotated names work in automations.

The same information is in the entity's attributes:

| attribute | |
|---|---|
| `applies_to_effects` | the effects on this light that respond to the slider |
| `speeds_per_effect` | how many steps each of those accepts |

An SL200III Bi lists two (Flash Light and Lightning, two speeds each); a TL60
lists thirteen, most with three. The value is clamped per effect, so asking for
speed 2 while running a one-speed effect sends 0 rather than being rejected.

Speed is deliberately *not* folded into the effect list. It would read well on a
light like the SL200III (7 effects becoming 9 entries), but 51 of the 55
affected models have three-speed effects, which would turn a 14-entry dropdown
into 39 rows of near-duplicates.

**Fan speed** is a separate `select` entity, for the 73 models that expose
controllable speeds. Its first position is *Silent*, not off — you cannot stop
the cooling fan over the protocol.

### What can be read back, and what cannot

This is the distinction that matters in daily use, because a light that cannot
report something will always show you what Home Assistant last sent it:

| | Polled | If your light misreports it |
|---|---|---|
| Brightness | yes | — reliable on every light tested, panel changes included |
| Colour temperature | yes, **can be switched off** | turn off *Include colour temperature* |
| Battery | yes, battery models | — |
| Effect | **no** | shows the last effect you selected |
| Effect speed | **no** | shows the last speed you selected |
| Fan speed | **no** | shows the last speed you selected |

Effect and fan are write-only: no status record reports them, and Godox's own
app never reads them back either. Those entities are `assumed_state` and restore
their last value across restarts.

Colour temperature is the one that can go wrong in a way you would notice, and
the only one with a switch. On most lights it is accurate. On a few — the
SL200III Bi among them — a change made on the light's *own dial* is answered
with a fixed placeholder instead of the real value, so the slider in Home
Assistant would jump to something that is not what the light is doing. Turning
*Include colour temperature* off leaves brightness polling untouched and keeps
the colour slider on whatever was last commanded.

**Live state can be polled on stock firmware** — no patch, no flashing. Turn on
*Show the light's actual state* in the integration's options and Home Assistant
asks the light what it is actually doing instead of showing what was last
commanded:

- **Brightness** is live, including changes made on the light's own control
  panel. Verified on an SL200III Bi and an SL60II Bi.
- **Colour temperature** is live when it was set from Home Assistant. For a
  change made on the light's own *panel*, behaviour differs by model: the
  SL60II Bi reports it correctly, the SL200III Bi returns a fixed placeholder.
  The integration shows what the light reports rather than guessing which is
  which. If your light reports a colour temperature that is not its real
  setting, turn off *Include colour temperature*.
- **Battery** is read the same way, on stock firmware.

With polling off, every entity is `assumed_state` and shows what was last
commanded. See [docs/readback-hardware-findings.md](docs/readback-hardware-findings.md)
for exactly what was measured, on which light.

**Battery** needs no patch either. Godox does not implement the standard
Bluetooth Mesh battery model — charge is carried over their vendor protocol, in
the same family of status records as brightness, and that record answers on
stock firmware. Enable polling and a sensor appears for each battery-capable
model. Mains-powered lights (most of the range, including the SL200III Bi) have
no battery and get no sensor.

> Measured honestly: the battery record was only ever read from a *mains*
> light, which answers a constant 100 %. That it responds on stock firmware is
> measured; that a real battery light reports a *changing* percentage is
> expected but untested.

## Troubleshooting

### The light ignores commands

Every mesh command consumes a sequence number, and a node silently drops any
message at or below the highest it has already seen — its replay protection
list. No error is reported; commands simply do nothing.

This is the most confusing failure this integration has, and it usually means
the stored counter is behind the light's. It happens if the Godox app or the
CLI has driven the light since Home Assistant last did.

The integration keeps its counter ahead of use and persists it before sending,
so it recovers on its own in normal operation. If a light stays unresponsive,
re-import its mesh state with a higher `sequence_number`, or re-provision it.

**Do not drive one light from both Home Assistant and the CLI** without
re-syncing the counter between them.

### The light will not connect

These lights accept **one connection at a time**. If Home Assistant times out
connecting to a light that is clearly powered on and in range, something else
is holding the session:

- The Godox phone app. Close it fully, and disable Bluetooth on the phone or
  move it out of range — it reconnects aggressively.
- Another Home Assistant instance, or the upstream CLI, still connected.

Power-cycling the light frees the connection and triggers a fresh burst of
advertising, which also helps Home Assistant rediscover it. A provisioned light
advertises intermittently, so a device missing from a single scan is not
necessarily gone.

### Discovery offers devices that are not Godox lights

Discovery matches the Bluetooth Mesh service UUIDs only, so any mesh node in
range may appear. This is deliberate: filtering on the advertised name would
fail silently for any model that names itself differently. The picker sorts
likely Godox devices to the top and hides nothing.

### Re-provisioning

Put the light back into pairing mode — usually holding the Bluetooth button
until it flashes, the same as pairing with the Godox app — then remove and
re-add the integration entry, choosing **Provision this light**.

## How it works

The lights are Bluetooth Mesh nodes, not simple BLE peripherals. Commands are
encrypted Mesh Network PDUs sent over the standard Mesh Proxy service
(`0x1828`), addressed to each light's unicast address.

Light control uses a Telink vendor model (company `0x0211`, model `0x0000`).
The standard Bluetooth Mesh models are present on the node but are not wired to
the hardware, so they can be neither used for control nor trusted for state.

Commands are fire-and-forget on opcode `0x0211F0`. A separate status request is
answered on `0x0211F1`. [docs/protocol.md](docs/protocol.md) maps the command
set, the framing, the ranges the firmware enforces, and what is still unknown.

[docs/firmware-api.md](docs/firmware-api.md) documents Godox's firmware
distribution API, reverse-engineered from the Android app — how to look up and
download an image for any model by `radioId`, and why the images it serves for
these lights are not the ones you would want for protocol work.

## Beyond brightness and colour temperature

Everything the Godox app offers for a light-shaped device is exposed as an
entity, and which controls a given light gets is decided from Godox's own
per-model catalogue rather than from code. Counts are out of 186 mesh models:

| | Models | Where it appears |
|---|---|---|
| Colour temperature | 183 | `light` |
| Hue / saturation (HSI) | 86 | `light` |
| Direct RGBW / RGBWW channels | 81 | `light` |
| Effects | 177 | `light` effect list |
| Effect speed | 177 | `number` |
| Lighting gels | 70 | `select` |
| Green/magenta tint | 83 | `number` |
| Fan speed | 73 | `select` |
| Battery charge | 25 | `sensor` |
| Output mode + mains frequency | 10 | two `select`s, one command |
| Dimming smoothness | 12 | `select` |
| Accessory recognition | 7 | `switch` |
| Second (selfie) colour-temperature range | 2 | `select`, swaps the light's range |
| CIE xy colour | 40 | opt-in; `light` plus two `number`s |

What is deliberately **not** covered, with reasons, is in
[docs/model-support.md](docs/model-support.md): pixel-light animations, the
motorised-accessory commands, and the per-effect parameters beyond speed.

> [!NOTE]
> Colour, gels, tint and the newer effect frame have not been tested on
> hardware — both lights available are bi-colour. The *frames* are read
> byte-for-byte out of the vendor app and asserted as such in the tests, so
> they should be sound. What would benefit from a report on a colour model is
> narrower: channel rescaling, the gel number, and whether each model is
> classified into the right frame format.
> [docs/model-support.md](docs/model-support.md#how-much-of-this-is-trustworthy-without-a-light)
> says exactly which three things those are.

**Arbitrary frames** go out with `send_v2_command_raw` (or `send_payload` for
the variable-length V3 frames), for anything unmodelled.

The library is usable directly:

```python
from godox_mesh_bt import GodoxController, StatusTimeout

async with GodoxController(address, "mesh_state.json") as light:
    await light.set_hsi(hue=240, saturation=100, brightness=60)
    await light.set_params(brightness=80, cct=5600, gm=-10, supports_gm=True)
    await light.set_effect(10, brightness=60, speed=60, effect_version=1)
    await light.set_fan_mode(1)
    try:
        print(await light.request_status())
    except StatusTimeout:
        pass          # not every model answers
```

**Effects** appear in the light entity's effect list, named and per-model: an
SL200III Bi offers Lightning, Candle and Firework rather than a generic list.
177 of the 186 known models ship effects. An unknown model falls back to a
small set named by number.

**Fan speed** appears as a `select` entity, for the 73 models that expose
controllable fan speeds over the protocol. Many lights have a cooling fan the
protocol cannot drive; those get no control. The fan cannot be read back, so
the entity shows the last speed selected.

See [docs/protocol.md](docs/protocol.md) for the full command set.

## How this was worked out

The protocol, the model table and the firmware behaviour were all reverse-engineered
from Godox's own app, firmware and public APIs. [docs/README.md](docs/README.md)
is the index: what to read in what order, and which documents are authoritative
where they overlap.

The publishable research inputs are committed under
[reverse-artifacts/](reverse-artifacts/), so the 186-model capability table
regenerates from a clean checkout — `uv run python scripts/generate_capabilities.py`
should reproduce it byte-for-byte. Vendor firmware and the decompiled app are
not redistributed; the transformation and the checksums are.

## Repository layout

[repo-map.json](repo-map.json) is a structural map of this repository for anyone
— human or agent — arriving cold: what each directory is for, which files are
generated and by what, the derivation chains as explicit edges, where the
authoritative definition of each concept lives, and what is deliberately absent.

## Development

The integration **vendors** the library into
`custom_components/godox_mesh/_lib/` rather than installing it from PyPI, so
this fork installs and runs without publishing anything. That copy is generated,
never hand-edited — after changing anything under `src/godox_mesh_bt`, run:

```bash
uv run python scripts/vendor_lib.py
```

`tests_ha/test_vendored_lib.py` fails if the two drift apart in either
direction.

### Tests

The library suite:

```bash
uv run pytest
uv run ruff check .
uv run ty check
```

The integration suite runs against a real Home Assistant, with the library
deliberately *not* installed so the tests exercise the vendored copy that
actually runs. Home Assistant requires Python 3.14+ while the library supports
3.10+, so its dependencies are kept out of `pyproject.toml`:

```bash
uv run --isolated --python 3.14 \
    --with-requirements requirements-ha-test.txt pytest tests_ha/ -q
```

## Trademarks and affiliation

**This project is not affiliated with, endorsed by, or sponsored by Godox
Photo Equipment Co., Ltd.**

"Godox" and the Godox logo are trademarks of Godox Photo Equipment Co., Ltd.
Product names such as SL200III, UL60Bi and ML100R are used here solely to
identify the hardware this software interoperates with — nominative use, not a
claim of origin, endorsement or association.

No Godox firmware, application code or other proprietary material is
redistributed by this repository. Firmware images are downloaded at run time
from Godox's own public endpoints; what is published here is the *transformation*
and the *checksums*. See [reverse-artifacts/README.md](reverse-artifacts/README.md).

This software is independent, unofficial, and provided under the terms in
[LICENSE](LICENSE) — without warranty. Interoperability was achieved by
analysing Godox's publicly distributed application and firmware. Using it may
void your warranty; the firmware-flashing tooling in particular can render a
light unusable.

## Credits

Protocol work and the underlying library are
[mattharrison/godox-ul60bi-bt](https://github.com/mattharrison/godox-ul60bi-bt).

[ndricjaho/godox-mesh-ha](https://github.com/ndricjaho/godox-mesh-ha) covers
Godox TL-series RGB lights and independently documented the vendor protocol;
its findings on state readback match this project's.

Not affiliated with Godox or Telink Semiconductor.

## Licence

MIT — see [LICENSE](LICENSE). The Godox brand assets under
`custom_components/godox_mesh/brand/` are excluded: they are Godox's trademarks,
used to identify the hardware this integration controls. See [NOTICE](NOTICE).

## Upstream

Fork of [mattharrison/godox-ul60bi-bt](https://github.com/mattharrison/godox-ul60bi-bt).
That project is the Python library and command-line tool; this one is the Home
Assistant integration, plus the library changes it needed. For CLI and Python
API documentation, see upstream.

Tested against a UL60Bi Lite and an SL200III Bi.
