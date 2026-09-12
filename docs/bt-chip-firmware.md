# The LK8620 Bluetooth firmware, and how status readback actually works

> [!IMPORTANT]
> **Superseded in part by [readback-hardware-findings.md](readback-hardware-findings.md).**
> This document's conclusion that readback is impossible on stock firmware was
> drawn from static analysis and is **wrong**. Testing on a real SL200III Bi
> showed the status request was being built with the wrong end byte: selecting
> the `0xA0` record returns live brightness on stock firmware. Only *panel*
> colour-temperature changes are missing. The mechanism described below is
> otherwise accurate.


> [!NOTE]
> Paths beginning `reverse/` are a **local scratch directory, not part of this
> repository** — it holds Godox's firmware, their decompiled app and live mesh
> keys, none of which are redistributable. They are cited so each finding says
> where it came from. The publishable inputs are committed under
> [../reverse-artifacts/](../reverse-artifacts/), which also carries the script
> that rebuilds them; see [README.md](README.md#reproducing-the-inputs).

This documents the Telink **LK8620** Bluetooth-mesh firmware — the chip that
terminates the mesh on a Godox continuous light — reverse-engineered from
`reverse/LK8620_MESH_GD_v000066_20250403_beta.bin`. It is the companion to
[protocol.md](protocol.md) (the wire protocol) and
[state-readback-investigation.md](state-readback-investigation.md) (the
original question: can these lights report their state?).

**The headline, as corrected by hardware.** These lights report brightness and
commanded colour temperature over stock firmware. The Bluetooth chip does
answer the status query itself, from a RAM record, rather than forwarding it to
the MCU — that part of the analysis below is accurate. What the analysis got
wrong was assuming nothing ever refills that record. The MCU volunteers `0xB0`
frames when something changes on the light, and those refill it, so the record
tracks live brightness. The one field it does not carry is colour temperature
changed on the light's own dial, and that is an MCU limit: flashing a patch
that forwards `0xFD` did **not** produce it.

Everything here was read with a TC32 disassembler written for this work
(`tools/tc32/tc32dis.py`, see its README), from an opcode table Ryan Govostes recovered from
Telink's own `tc32-elf-objdump`. There is no p-code/decompiler; this is
instruction-level reading, not recovered C.

## The two-chip architecture

A Godox continuous light is two processors:

```
                 mesh vendor msg 0x0211F0
   Home Assistant ───────────────────────▶ LK8620 (Telink TC32, BLE mesh)
                 status reply  0x0211F1              │
                                                     │ UART, same V2/V3 framing
                                                     ▼
                                          main MCU (ARM Cortex-M)
                                          drives the LEDs, owns real state
```

The V2 8-byte frame with its CRC8 is **not** a mesh construct — it is the
serial protocol between these two chips, tunnelled inside the mesh vendor model
so the app can address the MCU end to end. That is why the identical Godox CRC8
table is compiled into *both* firmwares (LK8620 at file `0x21bc4`; the MCU at
its own offset).

The LK8620 is a generic image shared by 114 products (see
[firmware-api.md](firmware-api.md)); the MCU firmware is per-model. So the mesh
side cannot contain light-specific logic, and the light side cannot contain
mesh logic. They meet at the UART.

### Address convention

The images are linked at **non-zero flash bases**, so `flash_base + file_offset`
is the address the code's own literals use — not `0x08000000 + offset`. Base is
`reset_vector & ~0xFFF`.

| Image | Flash base |
|---|---|
| LK8620 (TC32) | `0x00000000` (flat; offsets are addresses) |
| UL150Bi II MCU | `0x08007000` |
| LP400Bi / LP600Bi MCU | `0x0800c000` |

Addresses below are the true linked addresses. An earlier draft of the MCU
analysis quoted UL150Bi II addresses `0x7000` too low; they are corrected here.

## Inbound: what the LK8620 does with each sub-command

The vendor-model receive handler is at `0x02ea0`. It validates the CRC8, then
switches on the sub-command byte at `0x02ec2`:

```
tloadrb r3, [frame, #0]
tcmp r3, #0xFD  -> handled LOCALLY  (0x02f34)
tcmp r3, #0xFC  -> handled LOCALLY  (0x02f92)
                -> everything else: tjl 0x0e548  (enqueue to the MCU over UART)
```

So of the whole command set, **only `0xFD` and `0xFC` are terminated on the BT
chip.** `0xF0` (set CCT), `0xF3` (effect), `0xF4`, `0xF5` (fan), `0xFE` (power)
and the rest are forwarded to the MCU unchanged. The Godox opcodes are
registered in the vendor-model dispatch table at `0x23880` (`0xF0`) and
`0x2388c` (`0xF1`), each paired with company id `0x0211`.

### The status cache

`0xFD` is answered from RAM. The chip keeps eleven shadow records, one per
reply sub-command `0xA0`–`0xAA`, each an 8-byte buffer of the last frame of
that type. They are populated by an inbound jump table (index `= (sub + 0x60)
& 0xFF`, table at `0x00021a94`) whose handlers `memcpy` a received frame into
its record. On query, the `0xFD` handler reads the record and re-emits it on
opcode `0x0211F1`.

At boot the records are initialised from **flash defaults**. The `0xA0` default
is the literal `08 a0 0a 1b 32 ff ff 01 f9` — length byte, then
`A0 0a 1b 32 ff ff 01` and its CRC — which decodes as brightness 10, colour
temperature 2700 K. This exact constant is what an SL200III Bi returns to a
`0xFD` query, CRC included.

### Firmware version is answered locally and correctly

Not everything the cache returns is stale. `FD 02` (BLE firmware version) is
built from an immediate — `tmov r3, #0x66` at `0x3016`, i.e. this image's own
version `000066`. That reply is always genuine. `FD 03` (MCU version) is read
from a cache at `0x0084b15c` that inbound handling does write. Only the
brightness/CCT/battery records depend on the MCU having pushed them.

## Why the cache is never filled with live values

The cache is write-through with **no fill-on-demand**, and nothing else fills
the `A0` record either.

- The record is written **only** by an inbound frame whose *first* byte is
  `0xA0` (jump table → handler `0x03180` → `memcpy`).
- The MCU builds a first-byte-`A0` frame from live state **only** in response
  to a UART `0xFD`.
- The BT chip **never forwards `0xFD`** — it terminates it locally.

The one event that could have refreshed the cache — the `0xB0` "local change"
notification the MCU sends when the panel is used — does not. Its handler at
`0x03228` dispatches on `frame[1]`, and for `A0` it builds an `E0` **acknowledge**
frame, enqueues that back to the MCU (`0x0e548`), and **discards the payload**.
Nothing reaches the record.

The static reading was that nothing reaches the record, so it would hold the
flash default for the life of the device. **Hardware disagrees**: turning the
brightness knob does change it, because the MCU's unsolicited `0xB0` frames
refill it by a path this analysis missed. `a0 0a 1b 32 ff ff 01 f9`
(`10% @ 2700K`) is the flash default seen on a factory-reset light, or when the
request's end byte is padded to `0xFF` instead of selecting record `0xA0`.

## Proof the MCU's live-state path is dead over Bluetooth

The claim is specific: in the MCU firmware, the code that reports *live*
brightness and colour temperature is reachable only via sub-command `0xFD`,
which the BT chip never delivers. Proven on the UL150Bi II image
(`reverse/1729857629339846.zip`) by exhaustive branch-encoding search — every
16- and 32-bit branch/call encoding, at both halfword alignments:

- The live-state builder at `0x0800aee8`–`0x0800af12` is entered by **exactly
  one** branch: `0x0800aec8`, inside the `0xFD` handler.
- The `0xFD` handler entry `0x0800aeac` is reached by **exactly one** branch:
  `0x0800ad88`, the dispatch's `beq` for `cmp r0,#0xfd`.
- **No branch from anywhere else reaches either.**

Because branch encodings use `dst - (src+4)`, this proof is independent of the
flash-base correction above.

### It is dead, but the A0 *format* is not

There are two producers of `A0` frames in the MCU, and only one is dead:

| Producer | Trigger | Content | Status |
|---|---|---|---|
| `0x0800ad3e` (tail of the `0xF0` handler) | every set-command | **echoes** the received bytes back | alive |
| `0x0800aee8` (the `0xFD` handler) | `0xFD` only | reads live `0x20000064` / `0x20000063` | **dead** |

The dead path is the valuable one. Those two variables are written from ~26
(brightness) and ~15 (temperature) sites *outside* the protocol code — the
panel/encoder handlers. Confirmed by finding read-modify-write step routines on
the exact bytes the reply reports (`ldrb / subs #1 / strb` for a knob-down).
Only the dead path would report a physical change made at the light; the echo
only reflects commands.

## The proposed fix — built, flashed, and it changed nothing

Everything needed already exists on the BT chip: the UART TX/RX queues, the RX
jump table that stores a first-byte-`A0` frame into the record, a
pending-request struct at `0x0084b03c` (saved request + a system-timer
timestamp), a timeout poller at `0x03864`, and a mesh-send primitive at
`0x0e3b8` already used by the `0xB0 A2` path. The only missing behaviour is
forwarding `0xFD` to the MCU so the MCU's live reply can populate the cache.

No MCU change is required. The MCU already answers `0xFD` correctly.

> [!WARNING]
> This reasoning is sound and the patch was built, verified and flashed to a
> real SL200III Bi — and it delivered **no** observable change. Forwarding
> `0xFD` to the MCU cannot help, because the MCU routes an `0xFD` request and a
> panel change to the *same* frame builder: asking yields exactly what the
> panel change already pushed, placeholder included. The ceiling is the MCU's
> frame builder, not the Bluetooth chip's decision to answer locally.

### This applies to 16 products, not one

All 16 ARM Cortex-M MCU images in `reverse/mcu_lk8620/` — every product that
pairs with this same LK8620 image — implement a live-state `0xFD` responder
reading panel-written state. None is echo-only.

| radioId | product | live-state `0xFD`? | notes |
|---|---|---|---|
| 002A/2B/45 | TP2R / TP4R / TP8R | yes | byte-identical trio; struct state, multi-frame `A0 A1 A2 A7 A8` |
| 0058/59/5A | TPC2R / TPC4R / TPC8R | yes | byte-identical trio |
| 0073 | MG1200R | yes | struct state; second payload appended past the 1 MB window |
| 007A/7B | LP400Bi / LP600Bi | yes | inline build, closest to UL150Bi II shape |
| 007C | LP1200Bi | yes | |
| 007D/7E/7F | LP400R / LP600R / LP1200R | yes | |
| 008F | ML100R | yes | re-ordered CRC loop; register-cached pointers |
| 0097 | ML80Bi | yes | newest effect set (11 effects) |
| EC7B | AD00-02 | yes, **caveated** | stamps `0xAC` frames carrying mains telemetry (voltage/frequency), not `A0` brightness/CCT — not interchangeable |

There are only 8 distinct firmwares among the 16 (two byte-identical triples).
The 9 images whose CRC8 table shows a one-byte anomaly before its final entry
are exactly the 9 that copy the table to RAM; the flash blob holds
`TBL[0..254]`, one foreign byte, then `0x35` at index 256. The library's own
`GODOX_CRC8_TABLE` is correct — reflected CRC-8, poly `0x8C`.

Independent corroboration of the whole model: several MCUs emit **multi-frame
rotating status**, cycling `A0/A1/A2/A3/A5/A7/A8` across successive queries —
which is exactly why the LK8620 maintains eleven records `A0`–`AA`. Both sides
of the UART agree record-for-record.

## Patching the LK8620

A minimal patch makes the `0xFD` handler forward the request to the MCU *and*
keep answering from cache. Because the cached answer is sent before the MCU's
fresh reply to the same query returns over the UART, each reply carries the
value fetched by the *previous* query — a fixed one-query lag, not convergence
to the current value. Querying twice back-to-back yields current state.

| Property | Value |
|---|---|
| Patch point | `0x02f34`, the local `0xFD` handler entry (a single branch) |
| Free space at the entry | none — zero `0xFF` runs ≥ 32 bytes |
| Trampoline site | `0x09f04` — a dead Telink-demo-opcode handler, 0 direct callers, 538 bytes of headroom |
| Patch size | 22-byte trampoline + a 4-byte detour = **26 bytes changed** |
| Image integrity | no signature, no whole-image checksum; header `0x18` is just the length, `KNLT` at `0x08` is the bootloader validity marker |

The trampoline replicates the enqueue-argument setup verbatim from the existing
forward path (`0x02eca`–`0x02ed4`), calls the UART enqueue `0x0e548`, re-runs
the two displaced instructions, and branches back — using a 32-bit `tjl` for
the return rather than a stack pop, to avoid depending on unverified TC32 flag
behaviour, since the instruction immediately after the patch point consumes
flags. The host function stack-saves `lr`, so `lr` is free to clobber.

**Verified:** the hand-encoded trampoline disassembles correctly and stays
within the dead handler. It was later assembled into a flashable image and
written to a real light, which accepted and booted it — see
[lk8620-flashing.md](lk8620-flashing.md). It changed no observable behaviour.

### Feasibility and risk

- **Assembling it.** There is a working TC32 disassembler here but no
  assembler; the 26 bytes were hand-encoded. A handful of instructions, but
  every one must be encoded by hand and checked.
- **Integrity.** Nothing to forge — no signature, no image-wide checksum. A
  byte-modified image is structurally acceptable to the OTA transport (see
  [firmware-api.md](firmware-api.md): the app is a dumb pipe with only a
  file-length check). Whether the Telink **bootloader** validates anything
  after `CMD_OTA_END` is not determinable from any available binary.
- **Bricking.** The image references alternate flash bases (`0x20000`,
  `0x40000`) consistent with Telink dual-bank OTA, so a bad image most likely
  fails to boot into the new bank rather than bricking. Unconfirmed.
- **No failure signal.** The app's OTA reports "success" on write-success,
  write-error *and* timeout (see [firmware-api.md](firmware-api.md) §5). A light
  that rejects the image looks identical to one that accepted it. After
  flashing, verify by querying `FD 02` and checking whether the reported BLE
  firmware version changed.

## Corrections this analysis makes to earlier notes

- **The BT chip is not a pure "dumb pipe."** It forwards `0xF0/0xF3/0xF4/0xF5/
  0xFE` but *terminates* `0xFD`/`0xFC` locally and answers them from a RAM
  cache with flash defaults. The dumb-pipe description in
  [firmware-api.md](firmware-api.md) is right for control and wrong for status;
  that distinction is the entire reason readback fails.
- **The direct-GATT OTA characteristics are 128-bit** —
  `00010203-…-0C0D1912` (service) / `…2B12` (data), both present in the LK8620
  image. They are not `0x7FDD/DE/DF`; those three are an alternative container
  for the *mesh* services.
- **UL150Bi II flash base is `0x08007000`.** Earlier notes quoted its addresses
  `0x7000` too low. The reachability proof is unaffected (it uses relative
  branch encodings).

## What remains unknown

- Whether the Telink bootloader validates the image after `CMD_OTA_END`. No
  Android or firmware code available touches it.
- Whether `0xFD` can reach the MCU's live-state handler by a non-Bluetooth
  route (USB / panel / DMX feeding the same dispatch). Not ruled out; not
  relevant to the mesh path.
- Device-side semantics of `openPaUpgrade` (`FD 04`), skipped by all PA-0
  lights including the SL200III Bi.
- The static analysis above is drawn from the UL150Bi II and LP400Bi MCU
  images and the one LK8620 image. Patched and debug images have since been
  flashed to a real SL200III Bi and the light restored to stock, so the
  *flashing* is hardware-proven; the *benefit* was disproved by the same test.
