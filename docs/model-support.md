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

(Of the 190 mesh products, 186 are lights in the shipped capability table; the
other 4 are motorised accessories — the AD00-01/AD00-02 soft-light modifiers,
AD88 and LF100MPY — which report no colour temperature and are not exposed as
lights.)

The bi-colour ranges present among mesh products, all handled from the table:

```
2800-6500  ×63     1800-10000 ×60     2500-8500 ×8
2000-10000 ×8      2500-10000 ×6      2700-6500 ×5     (plus a few more)
```

### What full support needs — the CCT range is data-driven (done)

The colour-temperature range and daylight-vs-bi-colour decision are per model,
from a bundled capability table (`capabilities_data.json`, distilled from
`product.json`) keyed by `radioId`. The config flow asks which model the light is
and the entity builds its controls from the lookup: the model's real Kelvin
range, or `ColorMode.BRIGHTNESS` for a fixed-daylight light. An unknown model
falls back to a safe 2800–6500 K colour-temperature light. Adding a model is a
new table row, not new code. The node-address allocator strides by the
two-element node size, so a second light on one network does not land on the
first light's element 1.

### What each model gets

Everything below is decided per model from Godox's own catalogue, chiefly its
`modeType` list — the same field the vendor app uses to decide which control
screens to show. Counts are out of the 186 mesh models in the table.

| Capability | Models | Entity |
|---|---|---|
| Colour temperature | 183 | `light`, `color_temp` |
| Brightness only (fixed daylight) | 3 | `light`, `brightness` |
| Hue / saturation (HSI, `0xF1`) | 86 | `light`, `hs` |
| Direct channels (RGBW/RGBWW, `0xF2` / `0xF9`) | 81 | `light`, `rgbw` / `rgbww` |
| Effects | 177 | `light`, effect list |
| Effect speed | 177 | `number` |
| Lighting gels (`0xF4` / `0xF8`) | 70 | `select` |
| Green/magenta tint | 83 | `number` |
| Fan speed (`0xF5`) | 73 | `select` |
| Battery charge | 25 | `sensor` |
| Brightness in tenths of a percent | 53 | carried in the `light` commands |

Two parts of that table turn on distinctions the catalogue does not spell out:

- **Colour** is built by dedicated frames — `build_hsi_command` (`0xF1`) and
  `build_rgbw_command` (`0xF2`/`0xF9`) — not the generic `build_v2_command`.
  Eighty-six models offer HSI.
- **Effects come in two generations.** `effectVersion` 0 takes the eight-byte
  `0xF3` command; `effectVersion` 1 — **122 of the 177 models with effects** —
  takes a V3 frame on `0xF7` whose third byte is a per-effect *selector that is
  not the symbol* (Flash is symbol 5 but selector 0). Those models report
  `gear: 0` for every effect, so the step count does not apply to them; their
  speed is a 0–100 value in the V3 frame.

### Colour modes

A model is given exactly the colour modes its catalogue lists, with one
entry-level choice on top. 40 models accept a CIE xy command (`0xFA`), and
every one of them also accepts HSI — so by default they get hue/saturation and
direct channels, and `xy_color` sent to the light is converted by Home
Assistant (accurately: the round trip is within 0.001 in xy across the useful
region).

The **Use CIE xy** option in the entry's settings swaps that. It has to be a
swap rather than an addition, because Home Assistant resolves a colour wheel's
`hs_color` against RGB, then RGBW, then RGBWW, and only then XY: a light
advertising any of those alongside XY would never reach its xy command from the
dashboard at all. With the option on, the light advertises `{color_temp, xy}`
only, the wheel's `hs_color` is converted to xy by Home Assistant and sent
natively, and two `number` entities give exact coordinate entry — which is the
real point, since Home Assistant's frontend has no way to type one.

`RGBACL` — red/green/blue plus amber, cyan and lime — has no Home Assistant
colour mode. No model in the catalogue offers it without also offering `RGBW`,
so nothing is lost today; a future model that did would still get HSI.

### Complete audit against the vendor app's command surface

`GodoxCommandApi` exposes 75 public methods. Six are callback plumbing and four
are marked `@Deprecated` by Godox itself (`changeBrightnessOffset`, whose own
comment says it has no effect; `changeElectricFan`, superseded by its V2;
`onSendPixelLightFrameComplete`; and `onChangePixelLightNumberSpeed`, "the
hardware does not do this yet"). Of the 65 that remain, everything is
implemented except the rows below.

| Command(s) | Models | Why not |
|---|---|---|
| `openPaUpgrade` | all | `0xFD` data byte 4 -- the OTA gate. Documented in [ota-login-gate.md](ota-login-gate.md); the integration does not flash firmware, so nothing here needs to open it. |
| `enableGodoxGattAgreementNotify`, `sendGodoxGattAgreementData` and 8 pixel methods | 1 | The LT1's bulk-data path, a raw GATT write on `fff0`/`fff3` rather than a mesh PDU. Library-only. |
| 8 motion / electronic methods | 12 | Drive an accessory's motors -- angle, calibration, smoothness -- not the light. That is a `cover` for a different device. |
| `changeLightXYEx` gamut byte | 40 | **The app never calls it.** `build_xy_command` accepts `color_gamut` for a caller who knows better; no catalogue field says which gamut a model wants. |
| `changeLightFXRainbow` | ? | **The app never calls it** either, and Rainbow has no `FxSymbolType` entry, so nothing says which models offer it. `build_fx_rainbow_command` exists but nothing sends it: it shares selector 19 with Pixel Candle and is told apart only by frame length, so guessing wrong runs the wrong effect. |

Everything else is implemented: `changeControlModeParam` and
`changeSmoothnessParam` as `select` entities on the 10 and 12 models that list
them, `onLightMotionRecognize` as a `switch` on the 7 with `attachmentSupport`,
`changeSelfieModeParam` as a colour-temperature range swap on the 2 models with
a second range, `changeLightRGBWEx` as `build_rgb_ex_command`, and
`getMcuVersion` as `GodoxController.request_mcu_version`.

### The app has no live state readback at all

The app sends `0xFD` four times: data byte 1 with the `0xA6` end byte
(battery), byte 2 (BLE version), byte 3 (MCU version) and byte 4 (the OTA
gate). It **never** asks for the `0xA0` record -- the live brightness and
colour temperature this integration polls.

Nor does it get the information pushed. `SendDataCallback.onGodoxDataResponse`
decodes exactly three V2 replies: `0xA6` battery, and `0xAF` sub-types `0x20`
(BLE version), `0x30` (MCU version) and `0x40` (OTA gate acknowledged). There
is no handler for `0xA0`, and none for the `0xB0` event. The `0xDF`
(`GodoxOrder.Report`) path does exist, but `MainViewModel.onReportCallback`
gates it to `GodoxOrderType.Special` + `SpecialType.PixelLight`: it carries a
pixel light's switch state and playing effect number, nothing else, and the
LT1 is the only model that can send one.

So the app shows what it last sent, and is simply wrong about a light whose own
panel has been touched. Polling the `0xA0` record is something this integration
does that the vendor app cannot.

### Not implemented, and why

- **Pixel-light animations** (1 model). Per-pixel frames are uploaded to the
  light as a multi-packet stream. Home Assistant has no concept that fits, and
  a `light` entity is the wrong shape for it.
- **Electronic control** (12 models). These commands drive motorised
  accessories — barndoors, softbox arms, a pitch axis — not the light. They
  belong to a `cover`/`fan` entity for the accessory, which is a separate
  integration-shaped problem.
- **`modeType` 8** (34 models). The vendor app has no branch for it either —
  `getSceneModeTypeList` falls through — so those models get nothing from it in
  the Godox app. Nothing to implement until something is known about it.
- **Per-effect parameters beyond speed.** The V3 effects take further
  arguments (Lightning's trigger and twinkling, Flash's mode, the colour-block
  lists of the RGB chase and flow effects). They are sent at the vendor app's
  own defaults, which are recorded in `FX_V3_DEFAULTS` and `ColorBlock`.
  Exposing each would mean a number or select entity per parameter per effect —
  roughly forty extra entities on one light.

### How much of this is trustworthy without a light

Both lights available while the colour half was written — an SL200III Bi and an
SL60II Bi — are bi-colour, `effectVersion` 0, no tint, no gels. So none of it
has been seen to work. The uncertainty is not uniform, though:

**Near-certain: the frames.** Every command here is built to the byte layout
read straight out of the vendor app's `GodoxCommandApi`, over the same CRC and
mesh path already proven on hardware, and each is asserted byte-for-byte in the
tests. Where this library sends the app's frame built from the app's own inputs
— HSI, the CCT-with-tint frame, the fan frame, the V3 effects at the defaults
in `FX_V3_DEFAULTS` — if the Godox app works, this works.

**The real risk is in the choices and the data, not the bytes.** Three places
where a perfectly well-formed frame could still be the wrong one:

1. **Channel rescaling is this repository's, not Godox's.** Home Assistant
   hands over 0-255 per channel; `rgbDisplay` 1 and 2 models take 0-1000 on the
   wire. `_async_send_channels` scales linearly between them. If Godox's own
   0-1000 is not linear in the same sense, the colour lands close but not
   exact. The frame is valid either way, so nothing would flag it.
2. **The gel number is inferred.** `build_color_chip_command` sends the
   catalogue's `sortNum` as the number, because the app's
   `getSingleColorChip(brandComb, sortNum, version)` is called with
   `colorChipJson.getNumber()`. That is a strong inference, not a read of the
   value being assigned. If it is wrong the light shows the wrong gel, silently
   — no record reports the selection back.
3. **Which frame a model takes comes from the catalogue.** `rgbDisplay`,
   `effectVersion` and `colorChipVersion` decide the format; none of them is
   derivable from the protocol. A model mis-classified in Godox's own data, or
   mis-read here, gets a well-formed frame in a format it does not parse.

Community reports on a full-colour model would settle all three quickly. The
first thing to check is whether a mid-range colour looks right rather than
merely present -- that is what would catch (1).

## Readback support

**Verified on hardware** — see
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
strides by the element count, so a second light onto one network does not land
its primary address on the first light's element 1. (A per-node query for
a SIG model on element 1 must still target `node_address + 1` — relevant only if
the standard models are ever used, which they are not for control.)

## What this integration cannot reach — the 36 non-mesh products

Godox's catalogue holds **226** products; **190** are Bluetooth mesh and are in
scope. The remaining **36 are out of reach**, and the reason is not a different
radio. **33 of the 36 report `hasBtFirmware = true`** —
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
| **Control** (on/off, brightness, CCT, effects, fan) | all 186 light models, from the capability table | same |
| **Full colour** (HSI/RGBW/xy, gels, tint) | implemented for the models that report it | same |
| **Readback** (live brightness, commanded CCT, battery) | **works** — select record `0xA0` | same |
| **Readback** (CCT changed on the light's own dial) | model-dependent; correct on an SL60II Bi, stale on an SL200III Bi | **no change** — tested |

The patch column is deliberately dull: flashing works, but the patch delivers
nothing readback-wise, because the remaining limit is in the MCU. The
integration reaches the whole Godox mesh range on stock firmware — control, full
colour, effects, gels and readback — with no patch required.
