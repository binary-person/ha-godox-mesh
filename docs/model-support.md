# Which Godox lights this integration can support

Scope question: this integration was tested against an SL200III Bi and an
SL60II Bi (and upstream against a UL60Bi Lite), but the Godox Bluetooth-mesh
protocol is shared across a large product range.
This documents how far the integration reaches today, what each other model would
need, and how that intersects the firmware-patch work in
[bt-chip-firmware.md](bt-chip-firmware.md).

Two independent axes:

- **Control** (turn on/off, set brightness and colour temperature) — works over
  stock firmware, because the mesh chip forwards control commands to the MCU.
- **Readback** (report live brightness/CCT) — works on **stock** firmware for
  brightness and for commanded colour temperature, once the status request
  selects the `0xA0` record. Only panel-changed colour temperature falls short,
  on some models, and no BLE patch lifts that.

## The protocol is the same on every mesh light

All 190 Bluetooth-mesh Godox products — across all three BLE chips (LK8620,
LK8720, LK8728B) — speak the identical vendor protocol: company `0x0211`, model
`0x0000`, the V2 8-byte frame with the same CRC8. The mesh chip's dispatch was
disassembled on all three and is structurally identical: it terminates only the
status queries (`0xFD`/`0xFC`) locally and **forwards every control command
(`0xF0` set-CCT, `0xF3` effect, `0xF5` fan, `0xFE` power) to the MCU unchanged**.

So the control path this integration already uses is not SL200III-specific. It
is the same bytes on every Godox mesh light. What differs between models is not
the protocol but the *capabilities* — colour-temperature range, whether the
light is full-colour, effect count, fan, battery.

## Control support

The integration drives brightness and colour temperature on **any** of the 190
mesh products, with the range and colour mode taken from a per-model capability
table rather than hardcoded.

| Model class | Count | How it presents |
|---|---|---|
| Bi-colour | 159 | colour-temperature control over the model's own range |
| Daylight, fixed CCT | 27 | brightness only (no meaningless colour slider) |

(Of the 190 mesh products, 186 are in the shipped capability table; the other 4
have no usable colour data upstream and fall back to a default range.)

The bi-colour ranges present among mesh products, all handled from the table:

```
2800-6500  ×63     1800-10000 ×60     2500-8500 ×8
2000-10000 ×8      2500-10000 ×6      2700-6500 ×5     (plus a few more)
```

### What full support needs — the CCT range is now data-driven (done)

The colour-temperature range and daylight-vs-bi-colour decision are now per
model, from a bundled capability table (`capabilities_data.json`, distilled from
`product.json`) keyed by `radioId`. The config flow asks which model the light is
and the entity builds its controls from the lookup: the model's real Kelvin
range, or `ColorMode.BRIGHTNESS` for a fixed-daylight light. An unknown model
falls back to a safe 2800–6500 K colour-temperature light. Adding a model is a
new table row, not new code. The node-address allocator was also fixed to stride
by the two-element node size, so a second light on one network no longer collides
with the first light's element 1.

What is done: the CCT range is per-model, daylight models are brightness-only,
and the config flow captures the model at pairing. What remains, as capability
gating rather than protocol work:

- **Per-model effects — done.** Each model offers exactly the effects Godox
  lists for it, named (Lightning, Candle, Firework). The wire symbol is the
  catalogue id minus one, which for the SL200III Bi reproduces its firmware's
  `0xF3` comparison chain exactly.
- **Fan as an entity — done.** A `select`, for the 73 models with controllable
  speeds. Its first position is *Silent*, not off.
- **Effect speed — done.** A `number`, for the 55 models with a multi-speed
  effect, clamped per effect.

What remains is full-colour (RGB/HSI) support, which needs an added colour mode
rather than new reverse engineering.

### Full-colour (RGB/HSI) models

The vendor protocol has HSI (`0xF1`) and RGBW (`0xF2`) commands, and the
library can already build V2 frames for them, but the light entity is
colour-temperature only. Full-colour Godox mesh lights (the RGB tubes and
panels) would need an `hs`/`rgbw` colour mode wired to those commands. That is
more work than the CCT-range change but is still protocol-complete — nothing
new to discover, just entity code.

## Readback support

**Corrected by hardware testing** — see
[readback-hardware-findings.md](readback-hardware-findings.md). Brightness
readback (commanded *and* panel-changed) works on stock firmware once the status
request selects the `0xA0` record, as does commanded colour temperature. The one
field that would need more is colour temperature changed on the light's own
panel — and the BLE patch has now been **tested on hardware and does not deliver
it**, because the MCU routes an `0xFD` request and a panel change to the same
frame builder. That ceiling is the MCU's.

The 26-byte BLE-firmware patch is built and verified for all three chips
([lk8620-flashing.md](lk8620-flashing.md)), and flashing it is reliable — but it
has **no demonstrated benefit**, so nothing below should be read as a reason to
install it. The table that follows describes what each MCU *would* report; it is
static analysis, and on the one light where it was checked against hardware the
patch changed nothing.

MCU-responder status, by chip family (from disassembling the downloadable MCU
images — see [bt-chip-firmware.md](bt-chip-firmware.md)):

| Chip | Products | MCU images examined | Live responder |
|---|---|---|---|
| LK8620 | 114 | 16 (+ UL150Bi II) | all live; AD00-02 reports mains telemetry, not CCT |
| LK8720 | 62 | 36 | **all 36 live** — every one reads panel-written brightness/CCT (or, for the OP family, live device state) |
| LK8728B | 14 | 7 | 6 live; **LT1 is info-only** — its `0xFD` returns a fixed identity frame, no brightness/CCT |

Across all three chips, **59 of 60 downloadable MCU images have a live `0xFD`
responder** — the sole exception is LT1. So the MCU ceiling is, empirically,
almost never the limiter: nearly every Godox mesh light reads its own state and
would report it once the BLE chip forwards the query. The LK8720 survey also
found several models (SL/RS/LE/OP-series RF and battery lights) carry an
*additional* `0xAC` mains/power/temperature telemetry reply alongside
brightness/CCT — a bonus, not a replacement.

LK8728B detail (from disassembly): MA5R, C30R, ML40Bi, ML40R, MA5R_Plus and
MS15R all read live brightness/CCT from a panel-written device-state block —
the same architecture as the LK8620 MCUs, just a newer firmware family (a
deferred pending-reply builder instead of the inline one). **LT1** is the
exception: it has a `0xFD` handler, but it only returns a static identity frame
(`AD 00 CF 00 64 00 FF`, `0xCF` = LT1's own radioId) and an explicit
"unsupported" marker for a status request — it reads no live state. So LT1
cannot report brightness/CCT even with the patch: the ceiling here is its MCU,
not the BLE chip.

Where an MCU image is not downloadable (most of the 190), the responder is
expected but unproven — nearly every Godox mesh MCU examined has one, LT1 being
the sole exception so far. The hard ceiling is the MCU, not the chip.

## The two-element addressing fix (done)

Each of these lights occupies **two** unicast addresses — element 0 (the vendor
model and most SIG models) and element 1 (Light CTL Temperature). The allocator
now strides by the element count, so a second light onto one network no longer
lands its primary address on the first light's element 1. (A per-node query for
a SIG model on element 1 must still target `node_address + 1` — relevant only if
the standard models are ever used, which they are not for control.)

## What this integration cannot reach — the 36 non-mesh products

Godox's catalogue holds **226** products; **190** are Bluetooth mesh and are in
scope. The remaining **36 are out of reach**, and it is worth being precise about
why, because the obvious guess is wrong.

They are not a different radio. **33 of the 36 report `hasBtFirmware = true`** —
they *are* Bluetooth devices. They are simply not *mesh* devices, and Godox's
firmware API serves them none of the three mesh images. Querying every radioId at
once (necessary, because `supportRadioIds` is filtered to whatever you ask for)
gives exactly 190 covered and 36 not:

    LED6R, LED6Bi, G1200D, M400R, P2400R, MG800R, RGB01, F600Bi, AT200Bi,
    FR200Bi, FR150Bi, DL150D, DL200D, M1000R, MG4800R, MG4800D, AT20 Battery,
    AT20C Battery, LA300D II, LA200D II, LA150D II, LF30BI, FL200Bi 22K, LF20BI,
    FL100BI, MG6KR, HybridLite P02, LC500R II, LC500R Mini, FL600BI, FL400BI,
    ZB03  (+ AD11, AD21, GF18M, which report no BT firmware at all)

### The app cannot reach them either

The `fff0`/`fff3` GATT channel in the Godox Light app looks at first like a
non-mesh transport — it carries the *same* command frames through the same
encoder. It is not one. It is a vendor side-channel on an already-provisioned,
already-mesh-logged-in connection, used only for pixel-light material upload and
MCU OTA. Three independent gates:

- `MeshGattUtils.enableNotifyRequest()` returns early unless
  `MeshLogin.isLogin()` — you cannot even subscribe without a mesh proxy login.
- `connectDeviceByMac()` looks the MAC up in the mesh database and refuses when
  the device is not provisioned.
- The GATT notify handler resolves MAC → mesh node and **drops** the payload
  when there is no match.

And the app has no concept of a non-mesh product: its only scan filters are the
mesh service UUIDs `0x1827`/`0x1828` plus the device name `GD_LED`, `0xFFF0` is
never matched in an advertisement, and no branch on `paVersion`,
`hasBtFirmware`, `lightType` or any radioId list ever selects GATT over mesh.

So this APK yields **no discovery signature, no handshake and no protocol** for
those 36. Supporting them would mean reverse-engineering an unknown protocol
from scratch, with no captured traffic. The plausible next step, untried, is
that the lights among them belong to a *different Godox app* — this catalogue
was fetched with `AppName: GodoxLight`.

## Summary

| | Stock firmware | With the BLE patch |
|---|---|---|
| **Control** (on/off, brightness, CCT, effects, fan) | all 190 mesh models, from the capability table | same |
| **Full colour** (RGB/HSI) | needs an added colour mode; protocol already supports it | same |
| **Readback** (live brightness, commanded CCT, battery) | **works** — select record `0xA0` | same |
| **Readback** (CCT changed on the light's own dial) | model-dependent; correct on an SL60II Bi, stale on an SL200III Bi | **no change** — tested |

The patch column is deliberately dull: flashing works, but the patch delivers
nothing readback-wise, because the remaining limit is in the MCU. The
integration reaches the whole Godox mesh range today on stock firmware. What is
left is full colour (RGB/HSI), which needs an added colour mode rather than
further reverse engineering.
