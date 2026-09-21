# Godox Bluetooth Mesh vendor protocol

> [!IMPORTANT]
> The status-reply section below described the reply as a constant. That is
> **wrong**: the request's end byte selects which record is reported, and the
> `0xA0` record carries live data. See
> [readback-hardware-findings.md](readback-hardware-findings.md).


What the lights speak, how far it has been mapped, and where each fact came
from. Sources are marked:

- **F** — read out of a UL150Bi II firmware image (`V1.02`, ARM Cortex-M)
- **H** — confirmed on hardware, an SL200III Bi over Bluetooth Mesh
- **A** — from [ndricjaho/godox-mesh-ha](https://github.com/ndricjaho/godox-mesh-ha),
  who decompiled the Godox Android app

## Transport

Standard Bluetooth SIG Mesh over the GATT Proxy bearer. Nothing about the
transport is proprietary — only the access-layer opcode.

| | |
|---|---|
| Mesh Proxy Service | `0x1828`, Data In `0x2ADD`, Data Out `0x2ADE` |
| Company ID | `0x0211` (Telink Semiconductor) |
| Model ID | `0x0000` |
| Request opcode | `0x0211F0` — wire bytes `F0 11 02` |
| **Response opcode** | `0x0211F1` — wire bytes `F1 11 02` |

The vendor model drives the LED. The standard SIG models are present but wired
to nothing, so they cannot be used for either control or readback (**F**, **H**).

Internally the light is two processors: a Telink BLE chip and a main MCU. The
framing below is really the UART protocol between them, tunnelled over the mesh
vendor model — which is why it carries its own CRC (**F**).

## Framing

**V2, fixed 8 bytes.** Everything mapped here uses it.

```
[0] sub-command   [1..5] five data bytes   [6] end byte   [7] CRC8 over [0..6]
```

**V3, variable length.** Carries effects; sub-commands appear to be passed
dynamically rather than from a fixed table (**A**).

```
[0] sub-command   [1] length = len(data)+3   [2..N+1] data   [N+2] CRC8
```

CRC8 is table-driven, `crc = TABLE[crc ^ byte]`, 256 entries. The same table
appears in the app, in this library, and embedded in the firmware image, where
the routine at `0x080008e4` validates every inbound frame against byte 7 (**F**).

## Commands the light accepts

Taken from the firmware's receive dispatch, which tests these values and no
others — anything else falls through unhandled (**F**).

| Sub-command | Meaning | Payload |
|---|---|---|
| `0xE0` | Acknowledge a `0xB0` notification | `[1]` = which notification (`0xA2` seen) |
| `0xF0` | Brightness and colour temperature | `[1]` brightness 0–100, `[2]` temperature ÷100 K, `[4]` mode 0–3 |
| `0xF3` | Effect | `[1]` brightness 0–100, `[2]` effect symbol, `[3]` speed |
| `0xF4` | Dispatched; handler only touches unidentified state | — |
| `0xF5` | Fan / cooling mode | `[1]` mode 0–4 (0 Silent, 1 Auto, 2 Low, 4 Medium, 3 High) |
| `0xFA` | Dispatched to the same stub as `0xF4` | — |
| `0xFD` | **Status request** | `[1]` non-zero for status, zero for an echo reply |
| `0xFE` | Power | `[1]` — **`0x00` turns the light on**, non-zero off |

Three of these — `0xE0`, `0xFA`, `0xFD` — do not appear in the app-derived
table, and `0xFD` is the one that matters.

### Ranges the firmware enforces

The `0xF0` handler clamps its inputs, which is where the light's real limits
come from (**F**):

```
brightness  cmp #0x64  -> clamped to 100
temperature cmp #0x1c  -> minimum 28   (2800 K)
            cmp #0x41  -> maximum 65   (6500 K)
```

### Effect identifiers

> [!WARNING]
> This section previously stated that the LK8620 mesh firmware's `0xF3` handler
> compares byte `[2]` against `1, 3, 4, 5, 6, 7, 8`. **That is not in the
> image.** Disassembly finds `tcmp #0xFD` and `tcmp #0xFC` (the status handlers
> this document describes elsewhere) but no `tcmp #0xF3` at all — consistent
> with [bt-chip-firmware.md](bt-chip-firmware.md), which says the mesh chip
> forwards `0xF3` to the MCU rather than decoding it. Where that value set came
> from is unknown; it happens to equal one particular light's symbols.

Effect symbols are decoded by the **MCU**, not the mesh chip. The MCU images
that carry an effect table accept a contiguous range: ML100R's is symbols
1–16.

That set is **this model's**, not a universal one. Godox's catalogue gives each
model its own effect list, and the wire symbol is the catalogue id minus one.
The SL200III Bi ships ids `[2,4,5,6,7,8,9]`, which map to symbols
`[1,3,4,5,6,7,8]` — exactly the chain above, which is why `2` appears to be
"missing": no id 3, so no symbol 2. Across the 186 models in the shipped
capability table there are 14 distinct effect symbol sets, spanning 0–19.

Data byte `[3]` is the effect **speed**, not a repeat of the symbol.

## Status readback

**A light can be asked for its state.** Send `0xFD` with a non-zero first data
byte; the reply comes back on the **response opcode** `0x0211F1`, not the
request opcode (**F**, **H**).

The end byte **selects which record** the light reports. It is not padding:
padding it to `0xFF` gets no reply at all. Ask for record `0xA0`:

```
request   fd 01 ff ff ff ff a0 95
reply     f1 11 02 | a0 4d 38 00 ff ff 01 f0
          ^opcode    ^V2 status frame — 77 % here, and live
```

Two reply shapes, chosen by whether an effect is running (**F**):

| Sub-command | Sent when | Payload |
|---|---|---|
| `0xA0` | colour temperature mode | `[1]` brightness, `[2]` temperature ÷100 K, `[3]` third `0xF0` byte |
| `0xA3` | an effect is running | `[1]` brightness, `[2]` effect id, `[3]` effect parameter |
| `0xAD` | request byte `[1]` was zero | `00 1D` then four bytes echoed from the request |

In the firmware the `0xA0` reply reads `0x20000064` and `0x20000063` — **the
same variables the `0xF0` handler writes** — so on that model the reply is
genuine live state rather than a placeholder (**F**).

### Record selection and the flash default

An `0xFD` request whose end byte is padded to `0xFF` returns the **flash
default** `a0 0a 1b 32 ff ff 01 f9` (10 % at 2700 K) on a factory-reset light:
the end byte selects the record, and the default record is what answers.
Selecting record `0xA0` returns live data. Verified on two lights; see
[readback-hardware-findings.md](readback-hardware-findings.md).

These lights are two chips — a Telink mesh chip and the main MCU, joined by a
UART carrying this same V2 framing — and the mesh chip answers `0xFD` from a RAM
cache. That cache is refilled live: it tracks brightness, including changes made
on the light's own panel, and colour temperature for anything commanded over the
mesh.

**Practical consequence:** on a stock light, select record `0xA0` and treat the
reply as live. The one field that can still be stale, on some models, is colour
temperature after a change made on the light's own dial — a limit in the MCU
that no Bluetooth-side patch lifts, as flashing one proved.

## Messages the light sends unprompted

The light reports changes made at its own controls, retrying until
acknowledged (**F**):

```
B0 A0 ff ff ff ff ff <crc>     retry counter 3   (RAM 0x200000be)
B0 A2 ff ff ff ff ff <crc>     retry counter 20  (RAM 0x200000b8)
```

The counters are set only from local UI code — panel interaction and mode
changes — never from the receive dispatch. A host clears one by replying
`E0 <A0|A2> …`, whose handler zeroes the counter and stores two bytes from the
acknowledgement.

Whether the BLE chip forwards these onto the mesh is **untested**. Nothing
resembling them was seen during listening on an SL200III, but that window
cannot be relied on — see the investigation notes.

## Using it from this library

```python
from godox_mesh_bt import GodoxController, StatusTimeout

async with GodoxController(address, "mesh_state.json") as light:
    await light.set_params(brightness=80, cct=4000)
    await light.set_effect(4, brightness=60)
    await light.set_fan_mode(1)

    try:
        status = await light.request_status()
    except StatusTimeout:
        status = None      # not every model answers
```

The Home Assistant integration exposes effects through the light entity's
effect list, named per-model from Godox's own catalogue, and fan speed as a
`select` entity for the models that have controllable speeds. Status **is**
polled, when the user opts in: the request selects a record with its end byte,
and record `0xA0` is live. See
[readback-hardware-findings.md](readback-hardware-findings.md).

`godox_mesh_bt.protocol` exposes the sub-command constants, `EFFECT_IDS`,
`FAN_MODES`, `build_status_request`, `parse_status_response`,
`build_effect_command` and `build_fan_command`.
`GodoxController.send_v2_command_raw` sends an arbitrary eight-byte frame for
anything not modelled here.

## What is still unmapped

- `0xF4` and `0xFA` reach a stub that writes two unidentified variables.
- The `0xF0` mode byte `[4]`, which the firmware range-checks to 0–3.
- The third byte of an `0xA0` reply. This library sends `50` in that position
  and the light echoes it back.
- V3 sub-commands beyond effects. **Do not sweep for them**: nothing is
  acknowledged, every probe is a valid command to something, and one stray
  frame already set an unwanted effect on a real light.
