# Flashing a status-readback patch to a Godox mesh light (LK8620 / LK8720 / LK8728B)

> [!IMPORTANT]
> **The OTA characteristic is gated behind a mesh login.** Writes on a bare
> connection are accepted by GATT and silently discarded — the light never
> reboots and nothing reports an error. Flashing must happen on a connection
> that has carried a mesh Network PDU. See
> [ota-login-gate.md](ota-login-gate.md). The procedure here matches the app's
> packet *format*, not its full connection procedure.



> [!NOTE]
> Paths beginning `reverse/` are a **local scratch directory, not part of this
> repository** — it holds Godox's firmware, their decompiled app and live mesh
> keys, none of which are redistributable. They are cited so each finding says
> where it came from. The publishable inputs are committed under
> [../reverse-artifacts/](../reverse-artifacts/), which also carries the script
> that rebuilds them; see [README.md](README.md#reproducing-the-inputs).

This is the practical companion to [bt-chip-firmware.md](bt-chip-firmware.md).
It builds the patched firmware, the flasher, and the evidence that both are
sound — and inventories exactly which lights it applies to.

> [!WARNING]
> **The patch has no demonstrated benefit. Do not install it.** Two facts
> remove any reason to (see
> [readback-hardware-findings.md](readback-hardware-findings.md)):
>
> - These lights report live brightness and colour temperature on **stock**
>   firmware. The status request's end byte selects which record the light
>   reports, and record `0xA0` is live; padding it to `0xFF` selects the flash
>   default instead.
> - The one field still missing — colour temperature changed on the light's own
>   dial — is **not** delivered by the patch: flashed to a real SL200III Bi, it
>   did not appear. The ceiling is the light's MCU, which no BLE-side patch can
>   lift.
>
> What remains useful here is the *flashing pipeline*, which is now
> hardware-proven: stock → patched → debug image → back to stock, with the
> reported version changing each time. Keep it for firmware work; do not use it
> expecting better readback.

> **Status: flashing verified on hardware.** The patched image is byte-verified,
> and the flasher has written stock, patched and debug images to a real
> SL200III Bi and restored it to stock. Flashing still carries real risk — read
> [Risks](#risks) first. Do not run the flasher against a light you are not
> prepared to recover or lose.

## What the patch does

Stock firmware answers vendor status request `0xFD` from a RAM cache that is
seeded with a boot default and refilled when the MCU volunteers a frame. On
hardware that cache tracks brightness live, and colour temperature live for
anything commanded over the mesh, so the premise below is narrower than it
appears — what is actually missing is only panel-changed colour temperature,
and the patch does not recover it. One combined patch
attempts this in
three parts:

1. **Readback.** A trampoline in the `0xFD` handler *also* forwards the request
   to the MCU over the UART. The MCU already builds a correct live reply, which
   lands in the cache through the existing inbound path. The cache answer is
   still sent first, so each reply carries the value fetched by the *previous*
   query — a fixed one-query lag. Query twice back-to-back for current state on
   demand.

2. **Refresh-on-change.** When the MCU signals a physical panel change it sends
   a payload-less `0xB0` notification which stock firmware acknowledges and
   throws away. A second trampoline makes the `0xB0` handler *also* send a
   `0xFD` to the MCU, so the live value is cached the moment the light changes
   at its panel — no client poll required to trigger it. Combined with the
   readback part, the client's next read reflects panel changes with no lag.

3. **Reversibility.** One more byte sets the *reported* BLE version to `1`, so
   the official Godox app always offers stock firmware as an "update" — a
   built-in restore path (see [Restoring stock firmware](#restoring-stock-firmware-built-in)).

Together this is ~62 bytes across two trampolines (in dead demo-opcode space),
two 4-byte detours, and one version byte. Making a query block until the MCU's
reply correlates — to remove the one-query lag entirely — is not attempted: the
chip has no request/reply correlator (its pending-request struct is a retry
timer), so it would be a far larger, riskier change than the refresh trampoline,
which achieves the same practical result for panel changes.

## Two ways to flash

**From Home Assistant (recommended).** The integration can do the whole thing:
*Configure → Flash readback firmware*. It downloads the stock firmware from
Godox, patches it on your machine, and flashes it — then verifies the light
actually took the patch and turns readback on. It is gated behind an explicit
risk acknowledgement, and needs the light's model to have been selected (that is
what tells it which of the three firmwares applies).

Nothing modified is redistributed: the integration fetches Godox's own stock
image and patches it locally. Two hashes gate it — the download must match the
pinned stock hash, and the patched result must match the pinned patched hash — so
only a byte-exact known-good image can ever reach a light. This is safe to pin
because the patch is deterministic.

**From the command line.** The scripts below do the same steps manually, and are
the better choice for the first flash of a new model or when something needs
inspecting.

## The toolchain

Four small scripts under `tools/lk8620/`, plus the TC32 disassembler in
`tools/tc32/`:

| Script | Does |
|---|---|
| `patch_status_readback.py` | turns a stock LK8620 image into the patched one |
| `verify_patch.py` | proves the patched image is structurally sound |
| `ota_packets.py` | builds the OTA packet stream, ported from the app |
| `flash_ota.py` | dry-runs or flashes the image over BLE |

`tools/tc32/` needs `generate_sleigh.py` fetched once — see its README.

## Dumb-proof procedure

### 0. One-time setup

```bash
cd tools/tc32
curl -sL https://raw.githubusercontent.com/trust1995/Ghidra_TELink_TC32/master/generate_sleigh.py \
  -o generate_sleigh.py
cd ../..
```

### 1. Get the stock firmware

The exact image this patch is verified against is the one the Godox app serves
for any LK8620 light. Fetch it with no account (see
[firmware-api.md](firmware-api.md)):

```bash
curl -sSL https://www.godox.net/godox/api/doc/download/1680457224855552 \
  -o LK8620_MESH_GD_v000066_20250403_beta.bin

# it MUST be this exact image, or the offsets are wrong:
shasum -a 256 LK8620_MESH_GD_v000066_20250403_beta.bin
#   aa56783357eae820be9af03e61d5bea3f816bc0f6e7ea52333411cdd5d7afb0c
```

### 2. Build the patched image

```bash
uv run python tools/lk8620/patch_status_readback.py \
  LK8620_MESH_GD_v000066_20250403_beta.bin  LK8620_patched.bin
```

It refuses to run on anything but the verified stock image (SHA-checked; pass
`--chip` for a non-LK8620 image). It reports the two trampolines, the version
downgrade, and the output hash:

```
readback trampoline : 0x09da8 (0xFD forwards to MCU)
refresh trampoline  : 0x09e64 (0xB0 panel-change -> 0xFD)
reported version    : 0x66 -> 0x01 (app offers stock as restore)
bytes changed       : 62
output sha256       : 6423853163126ec49b12eb391d866e0561e73e8aa152d1351ca4410255333cad
```

### 3. Verify the patched image

```bash
uv run python tools/lk8620/verify_patch.py \
  LK8620_MESH_GD_v000066_20250403_beta.bin  LK8620_patched.bin
```

Every line must read `[PASS]` and it must end `ALL CHECKS PASSED`. If not, stop.

### 4. Rehearse the flash (no device touched)

```bash
uv run python tools/lk8620/flash_ota.py LK8620_patched.bin
```

This is a **dry run** by default. It writes the exact OTA byte-stream to
`ota_writes.bin` and touches no hardware.

### 5. Flash (only when you mean it)

```bash
uv run --with bleak python tools/lk8620/flash_ota.py LK8620_patched.bin \
  --flash --address <BLE-ADDRESS> --i-understand-the-risk
```

`--address` is the light's BLE address (a MAC on Linux/Windows, a CoreBluetooth
UUID on macOS). The light must be connectable and idle — close the Godox app
and any other client first (these lights accept one connection at a time).

### 6. Confirm it worked

The flasher cannot tell you whether the light accepted the image — see
[Risks](#risks). Verify independently: reconnect and send `FD 02`
(`build_v2_command(0xFD, 0xFF, [2])` → `fd02ffffffffff56`). The reply carries
the BLE firmware version.

**Verify with the version byte, not with readback.** `FD 02` reports the BLE
firmware version: stock is 66 (`0x42`), the patch reports 1. That is the only
reliable proof the image took.

Readback is *not* a useful test, for two reasons. The status record is `0xA0`,
not `0xA6` (which is battery) — and more importantly stock firmware already
reports live brightness and commanded colour temperature from that record, so
there is no before/after difference to see. The one field the patch targets,
colour temperature changed on the light's own dial, **did not change** when
this was flashed to a real SL200III Bi.

## Why the patched binary is a good binary

`verify_patch.py` runs a battery of checks on all three chips; all pass. In
plain terms:

1. **It matches an independent rebuild.** The verifier recomputes the whole
   patch from the stock image and requires the candidate to be byte-for-byte
   equal — so the check does not merely trust the patcher.
2. **Header and size intact** — same length, `KNLT` magic, and the `0x18`
   length field still equal the file size. The *header* version byte is left
   untouched; only the value the light *reports* changes.
3. **Only the intended bytes changed** — the two 4-byte detours (at the `0xFD`
   and `0xB0` handler entries), the two trampolines (in dead demo-opcode space),
   and the one version byte. 62 bytes on each chip.
4. **The readback trampoline disassembles correctly** — forwards to the UART
   enqueue, re-runs its displaced instructions, returns into the `0xFD` handler.
5. **The refresh trampoline disassembles correctly** — computes the address of
   its embedded `FD 01` frame with a pc-relative add, enqueues it to the MCU,
   restores the `0xB0` subtype register the handler still needs, re-runs its
   displaced instructions, and returns into the `0xB0` handler.
6. **Both trampoline sites are genuinely dead.** They overwrite the heads of
   Telink demo-opcode handlers for vendor model `0x0001`, which the composition
   data does **not** bind to any element — the mesh stack never routes to them.
   No branch anywhere else in the image enters either window (only the two
   detours do).
7. **The reported version is downgraded to `0x01`** (unless `--keep-version`).
8. **Everything else is byte-for-byte stock.**

Two correctness points that needed proof rather than assertion:

- **Flags survive each trampoline's return.** The readback return lands right
  before a flag-dependent branch; the return is a `tjl`, and the authoritative
  TC32 SLEIGH spec defines `tjl` as writing only `lr` — it touches no flag
  register. `lr` being clobbered is harmless (the host functions return via a
  stacked address).
- **Registers the handlers still need are preserved.** The readback trampoline
  forwards via the UART enqueue only (not the stock forward-path tail that
  repurposes `r7`), so `r7` — the frame pointer the `0xFD` handler dereferences
  — is intact. The refresh trampoline explicitly restores `r3` (the `0xB0`
  subtype) after the enqueue clobbers it, before handing back to the handler.

## The flashing process is identical to the app — proof

`ota_packets.py` is a line-for-line port of
`com/telink/ble/mesh/util/OtaPacketParser.java` and
`com/telink/ble/mesh/core/ble/b.java` from the decompiled Godox Light 4.1.0.
Equivalence was proven four ways (all pass):

1. **The CRC-16 is standard.** The app's `crc16` is CRC-16/MODBUS (reflected,
   poly `0xA001`, init `0xFFFF`). It reproduces the canonical check value —
   `crc("123456789") == 0x4B37` — and agrees with an independent MODBUS
   implementation over 500 random buffers.
2. **Two independent transcriptions of `getPacket()` agree** on every one of
   the 9294 packets for the patched image (a slice-based port and a
   byte-copy port produce identical output).
3. **Round-trip.** Reassembling the image from the packet payloads yields the
   source bytes exactly.
4. **Framing matches.** The last packet's payload length and `0xFF` padding
   follow `getPacket()`'s length math, and the end command is
   `02 FF <last-index-LE> <~last-index-LE>` exactly as `b.i()` builds it.

Same service/characteristic (`00010203-…-0C0D1912` / `…2B12`, both present in
the LK8620 image), same 20-byte `[index][16 data][CRC16]` packet, same
`WRITE_NO_RESPONSE` with a 6 ms inter-packet delay, same `00 FF` / `01 FF`
start and `02 FF …` end.

### Where it differs from the app, and why

The differences are all in *reaching* the light, not in the OTA itself:

| The app does | The flasher does | Why it is safe to differ |
|---|---|---|
| Connects through the mesh proxy, then issues Node Identity Set and reconnects directly to the target node | Connects directly to the light's BLE address | The OTA is a direct GATT link either way; the app's dance just selects which node. Connecting straight to the one light reaches the same characteristic. |
| Skips `openPaUpgrade` for `paVersion 0`; sends it for `paVersion 1` | Never sends `openPaUpgrade` | Every LK8620 product is `paVersion 0` (see [firmware-api.md](firmware-api.md)), so the app skips it too. |
| Huawei phones use a 12 ms delay | Fixed 6 ms | 6 ms is the app's non-Huawei default. |
| Retries a failed packet up to 10× | No retry | A conservative simplification. A dropped packet fails the flash rather than silently recovering; re-run from the start. |

The OTA **byte stream** — the thing the light actually validates — is identical.

## What this applies to — model inventory

The patched firmware is **one binary shared by 114 products.** Flashing it to
any LK8620 light patches the Bluetooth side identically. Whether readback then
works depends on that light's MCU implementing the live `0xFD` responder.

### Directly supported: the LK8620 family (114 products, `paVersion 0`)

Includes the SL200III Bi (`003F`) and the whole SL III line, SL/SL II, the
UL60/UL150 continuous lights, TL tubes, ML/LP panels, and many more.

**Readback verified** on 17 of them — the 16 MCU images in
`reverse/mcu_lk8620/` plus the UL150Bi II — by confirming their MCU builds an
`0xFD` reply from panel-written live state (see
[bt-chip-firmware.md](bt-chip-firmware.md)):

TP2R, TP4R, TP8R, TPC2R, TPC4R, TPC8R, MG1200R, LP400Bi, LP600Bi, LP1200Bi,
LP400R, LP600R, LP1200R, ML100R, ML80Bi, AD00-02 (caveated), UL150Bi II.

**Very likely, but unproven** — the other 97 LK8620 products, including the
SL200III Bi itself. Their MCU firmware is not downloadable, but every LK8620
MCU examined so far implements the same responder, so the expectation is high.
It cannot be *stated* until one of their MCU images is seen or the light is
tested.

**One caveat model:** AD00-02 answers with `0xAC` mains-telemetry frames
(voltage/frequency), not `A0` brightness/CCT. The patch still forwards its
`0xFD`, but a brightness/CCT reader will not find what it expects.

### Also supported: LK8720 and LK8728B (76 more products)

The other two Godox BLE-mesh chips were disassembled and carry the **same
interception**, so the patcher handles all three (`--chip LK8720` / `LK8728B`):

| Chip | Products | Stock version | Detour | Enqueue | The `0xFD` interception |
|---|---|---|---|---|---|
| LK8620 | 114 | `000066` | `0x02f34` | `0x0e548` | identical structure |
| LK8720 | 62 | `000066` | `0x02f9e` | `0x0e618` | identical structure |
| LK8728B | 14 | `000067` | `0x02f34` | `0x0e5d0` | byte-identical to LK8620 at the dispatch |

All three: same Telink SDK (`V4.1.0.1`), same Godox CRC8 table, same
128-bit OTA service/characteristic (so the same flasher works), same status
cache seeded with the same `08 a0 0a 1b 32 ff ff 01 f9` default, and
composition data binding **only** vendor model `0x0000` (so the demo-handler
trampoline space is dead in every image). Each produces a 25–26-byte patch that
passes the full `verify_patch.py` suite. Only the per-image offsets differ, and
the patcher derives them from `--chip`.

So the LK8620 procedure above applies verbatim to an LK8720 or LK8728B light —
fetch that chip's stock image (the firmware API serves all three; see
[firmware-api.md](firmware-api.md)), pass `--chip`, and the rest is the same.

### Not supported

| Group | Count | Why |
|---|---|---|
| **No-BTF products** | 36 | Not Bluetooth-mesh lights — flash triggers, control systems, etc. No vendor status path to patch. |

Of the 226-product catalogue: **190 in scope across the three mesh chips, 36 out
of scope entirely.** Within the 190, readback then depends on the MCU (below).

### How far the *method* reaches

The technique — forward the intercepted `0xFD` so the MCU's existing live
responder refills the cache — is **confirmed on all three BLE chips**, not just
the LK8620. The LK8720 and LK8728B images were disassembled and carry the same
interception, the same shadow cache, the same flash default, and the same
composition-data binding; the patcher handles all three from one code path.
So the patch reaches all 190 BTF products.

The hard ceiling is the MCU: on a light whose MCU has no live `0xFD`
responder, no BT-side change can invent the data. **That ceiling has now been
hit.** The patch was flashed to a real SL200III Bi and its panel-changed colour
temperature was still the placeholder — because the MCU routes an `0xFD`
request and a panel change to the same frame builder, so asking cannot yield
more than the panel change already pushes. The limit is the MCU, not the chip
count, and not something a BT-side patch reaches.

## Pinned images and checksums

Recorded here because the integration no longer carries them: it used to
offer flashing from its options flow, and that was removed once hardware
testing showed the patch delivers nothing. `tools/lk8620/` still builds and
writes these images.

| chip | stock SHA-256 | patched SHA-256 |
|---|---|---|
| `LK8620` | `aa56783357eae820be9af03e61d5bea3f816bc0f6e7ea52333411cdd5d7afb0c` | `17c385076ee344754a2ec636bdbefba6e97e9600d615645d8b308038e4297f2b` |
| `LK8720` | `cc8c48fc0d039f393298d611b2848e4fb882f8935a466bce641818de4efcea6d` | `e77011c1dba6196a53aae03a7a96e754c837704b64eac6df34804566866ff4dc` |
| `LK8728B` | `ffba898f84c6bfc6d644c210b602082dbad3234dae3a8c0b06d012225ca36df3` | `8a4e0ae3081af98d1d9cf8df0f85db2357a080504e5fdf8fe8c4e5b6fba8a9d2` |

Download URLs are in
[../reverse-artifacts/firmware-coverage.json](../reverse-artifacts/firmware-coverage.json),
and `reverse-artifacts/fetch_firmware.py` retrieves the corpus.

## Restoring stock firmware (built in)

By default the patcher also **downgrades the version the light reports** — one
byte, changing the `FD 02` reply from the real version to `1`. This makes the
official Godox app your restore path: the app decides an update is available
when `deviceBleVersion < serverVersionCode` (verified in
`UpdateFirmwareItemBean.checkBtfVersionNeedUpdate`), so a light reporting `1`
against the server's `102` will always be offered the **stock** firmware as an
"update". Accepting it in the app flashes Godox's own image and undoes the
patch — no custom tooling needed to go back.

Details:

- Only the *reported* version changes (the `tmov r3, #<ver>` immediate the
  version reply is built from). The image header, the actual code, and the
  bootloader-relevant fields are untouched.
- The light keeps working normally in the meantime; the app just shows an
  update badge. Restoring is a deliberate tap, never automatic (the app
  requires confirmation before any OTA).
- This is reversible in both directions: re-flashing the patched image restores
  the readback behaviour.
- Pass `--keep-version` to suppress it and report the true version, if you would
  rather the app not show an update.

The trade-off is the persistent "update available" badge. It is on by default
because cheap, reliable reversibility is worth more than a tidy update screen
for an operation this risky.

## Risks

- **No success signal.** The app's OTA — and therefore this flasher — reports
  success on write-success, write-error, *and* timeout (see
  [firmware-api.md](firmware-api.md) §5). A light that **rejects** the image
  looks identical to one that accepted it. Always verify with `FD 02`
  afterwards; do not trust "done".
- **Bootloader acceptance — now hardware-confirmed.** Modified images (the
  readback patch and a separate debug build) were both accepted and booted by a
  real SL200III Bi, and stock was restored afterwards. The original static
  findings, which predicted this, are kept below:
  1. *There is no whole-image CRC or signature to invalidate.* Tested: the file
     carries no trailing or header checksum over its body (CRC-16/32 and sum, in
     several boundary configurations, all miss). And the app computes and sends
     no image hash. So there is nothing for the bootloader to check a modified
     image against — a whole-image gate would need an expected value that exists
     nowhere. This is the load-bearing result and it is strong, though "none
     found in the configurations tested" is not "provably none".
  2. *No version/downgrade gate.* The GATT-OTA start command carries no version,
     the firmware's own version byte (`0x66`) is compared nowhere in the OTA
     region, and the app queries the version only to discard it. So reflashing
     the same version — a patched `0x66` over a stock `0x66` — is not refused on
     version grounds. (On an older light, e.g. `0x42`, there is no downgrade
     either way.)
  3. *Integrity is per-packet only.* Each 20-byte packet's CRC-16 is checked by
     the firmware as it arrives; a corrupt or out-of-order packet is rejected
     and aborts the OTA. The flasher computes those CRCs correctly, so a clean
     BLE transfer passes. This is what actually gates acceptance.
  What remains unverified is only the bootloader's boot-time behaviour, which no
  available binary shows directly.
- **Bricking is unlikely by design, but treat it as possible.** Telink's OTA is
  dual-bank: the new image is written to the inactive bank, and the "valid"
  marker (the `KNLT` magic at offset 8) is written *last*, only after a complete
  transfer. An interrupted or packet-rejected OTA never arms the new bank, so
  the bootloader keeps running the old image — failure falls back rather than
  bricks. This matches the alternate flash bases referenced in the image, but
  the exact write base and boot-select logic were not fully traced.
- **Interruption.** There is no resume. A disconnect mid-flash abandons the
  transfer; re-run from the start.
- **What is and is not proven.** Flashing itself is hardware-proven: stock,
  patched and debug images have all been written to a real SL200III Bi, and it
  was restored to stock. What is *not* proven is any benefit — the patch
  changed no observable behaviour. Treat this as firmware tooling, not as a
  readback fix.
