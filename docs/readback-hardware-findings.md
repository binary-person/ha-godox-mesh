# Status readback: what the hardware actually does

**This document supersedes the conclusions in
[state-readback-investigation.md](state-readback-investigation.md) and the
"readback is impossible" claims in
[bt-chip-firmware.md](bt-chip-firmware.md) and
[model-support.md](model-support.md).** Those were written from static analysis
of the firmware. Testing against a real light contradicted them.

Verified on an **SL200III Bi** (`radioId` 003F, LK8620 chip, BLE firmware
version 66 / `0x42`), stock firmware, no patch.

## The correction

The earlier investigation concluded that a Godox light answers a status request
with a boot-time constant, that the real values never reach the client, and that
a firmware patch was therefore required for any readback at all.

**That was wrong**, and the cause was a methodology error rather than anything
about the firmware: the status request was built with its end byte padded to
`0xFF`. On real hardware that request gets **no reply at all**. The end byte is
not padding — it *selects which record the light reports*. Asking for the
`0xA0` record returns live data.

Selecting the record correctly, readback largely works on stock firmware.

## What is readable, on stock firmware

| What changed | Brightness | Colour temperature |
|---|---|---|
| A command from Home Assistant or the CLI | live and exact | live and exact |
| A change on the light's own panel | **live and exact** | **not reported** |

Commanded values, three for three:

```
commanded  75% 5600K  ->  read  75% 5600K
commanded  15% 2800K  ->  read  15% 2800K
commanded 100% 6500K  ->  read 100% 6500K
commanded  55% 4500K  ->  read  55% 4500K
commanded  20% 3000K  ->  read  20% 3000K
```

Panel brightness, captured live while the light's own knob was turned (the 77 %
was a requested target, hit exactly):

```
t= 1.5s  b1=31     t=22.5s  b1=77
t=18.6s  b1=46     t=26.3s  b1=75
t=20.5s  b1=63     t=28.2s  b1=39 ... then 10, then 5
```

Panel colour temperature never appeared. Across changes to 3000 K, 2800 K and
back, byte 2 stayed at `0x38` (5600 K) — a placeholder, not the light's actual
setting.

## Telling the two apart — there is no reliable way, and the attempt was wrong

The record's shape *looked* like it identified which path wrote it:

```
SL200III command echo:  a0 64 41 32 00 00 00      b4/b5 = 0x00
SL200III panel write:   a0 4d 38 00 ff ff 01      b4/b5 = 0xFF   <- stale CCT
```

So `cct_is_live` was computed from those `0xFF` markers. **Testing a second
light disproved it.** An SL60II Bi (`003A`) carries the same markers on records
whose colour temperature is live and exact:

```
SL60II commanded 25% / 3100K  ->  a0 19 1f 32 ff ff 03
SL60II panel     31% / 6500K  ->  a0 1f 41 32 ff ff 03    <- live and correct
```

The rule therefore discarded good data on that model, on both command echoes and
panel changes. It has been removed: the colour temperature is now reported as
received.

Byte 6 differs by source across the samples so far (`0x00`/`0x03` where the value
is trustworthy, `0x01` where it is not), but that is three models' worth of
evidence for a two-value rule and is not relied upon.

## The record map

`FD 01` with each end byte, stock firmware:

| Selector | Content |
|---|---|
| `A0` | **live** brightness; colour temperature live only after a command |
| `A1`, `A3`, `A4` | flash defaults, unchanged by anything tested |
| `A2` | static in testing |
| `A5` | empty (`ff` filled) |
| `A6` | battery: state 1, 100 % — a mains light reports "full" |
| `A7`–`AA` | no reply |
| `FD 02` | BLE firmware version (66 stock; the patch reports 1) |
| `FD 03` | MCU version, unset (`0xFF`) on this light |

`A1`–`A6` were byte-identical across a 40 %/3200 K reading and a 5 % reading, so
no other record carries live colour temperature.

## Two behaviours worth knowing

**Replies are retransmitted until acknowledged.** An unacknowledged reply keeps
arriving and will be mistaken for the answer to a later query. Send the `0xE0`
acknowledgement (:func:`build_status_ack`) or correlate replies by sub-command.
This caused a false reading during testing.

**A factory reset leaves the `A0` record empty** until the first command or panel
change populates it.

## What this means for the firmware patch — tested, and it does not help

The patch was built for exactly one field: colour temperature changed on the
light's own panel. **It has now been flashed to a real SL200III Bi and it does
not deliver that field.**

With the patch running (reported version 1), the panel set to 59 % / 2800 K:

```
stock firmware   a0 4d 38 00 ff ff 01     brightness live, CCT placeholder
patched firmware a0 3b 38 00 ff ff 01     brightness live, CCT placeholder
```

Identical behaviour, and commanded readback still exact (45 %/3800 K and
80 %/5200 K both read back correctly). So the patch is harmless but pointless.

This matches what disassembly of the LK8620 MCU images predicted: the MCU's
`0xFD` handler sets **the same flag bit** that a panel change sets (bit 17 of
`0x20000050` on the LP family), so both converge on one frame builder. Asking
the MCU cannot produce more than the panel change already pushes. **The ceiling
is the MCU, not the Bluetooth chip**, and no BLE-side patch can lift it.

The flashing pipeline itself is worth keeping — see
[ota-login-gate.md](ota-login-gate.md) — but the readback patch has no
demonstrated benefit and the integration should not offer to install it.

## Other models — the quirk looks specific to this light

A second light was tested: an **SL60II Bi** (`003A`), chosen because it is the
closest available match — same LK8620 chip, same 2800-6500 K range, and like the
SL200III it has no MCU firmware published. **It reports panel colour temperature
correctly**, so it does not share the quirk.

Static analysis of the 59 downloadable MCU images pointed the same way at the
time: 52 were read as taking colour temperature from a live variable into their
status frame, with none showing the SL200III's pattern.

> [!NOTE]
> This figure is an observation, not evidence: the tooling that produced it was
> not retained, so it cannot be reproduced from this repository. The corpus it
> ran over is identified by SHA-256 in
> [../reverse-artifacts/firmware-inventory.md](../reverse-artifacts/firmware-inventory.md).
> The hardware result below is what the conclusion rests on.

The load-bearing evidence is the hardware: an **SL60II Bi reports panel colour
temperature correctly**. That alone establishes that **the SL200III Bi is
unusual rather than representative**, and it does not depend on the sweep. Note
also that those 59 are exactly the models Godox publishes MCU firmware for, so
they were never a random sample.

An architectural theory (that the ~131 models without published MCU firmware
would share the quirk) was proposed and then **refuted** by the SL60II test.
