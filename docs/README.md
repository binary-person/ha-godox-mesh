# How this was worked out

These documents are the research the integration is built on: the wire protocol,
the firmware that speaks it, Godox's own APIs, and which lights it all applies
to.

Several conclusions here were later corrected by hardware testing. Where that
happened the original analysis is kept and marked as superseded rather than
edited away — the mechanism it describes is usually still accurate even where
the conclusion drawn from it was not.

## Start here

| | |
|---|---|
| [protocol.md](protocol.md) | The wire protocol: framing, CRC, every command and reply. **Read first.** |
| [model-support.md](model-support.md) | Which of Godox's 226 products this reaches (190), what each gets, and why the other 36 are unreachable. |
| [readback-hardware-findings.md](readback-hardware-findings.md) | What two real lights actually do. **Supersedes the readback conclusions in every other document here.** |

## Going deeper

| | |
|---|---|
| [bt-chip-firmware.md](bt-chip-firmware.md) | The Telink mesh chip's firmware, disassembled: how it handles status, and where its limits really are. |
| [firmware-api.md](firmware-api.md) | Godox's public firmware and product APIs — how to look up any model, and the filtering trap that makes the answers lie. |
| [ota-login-gate.md](ota-login-gate.md) | Why OTA writes are silently discarded on a bare connection. Short, and the answer to "the flash reported success and nothing happened". |
| [lk8620-flashing.md](lk8620-flashing.md) | Building and writing a patched image. Hardware-proven — and the patch is **not worth installing**; the document says why. |
| [state-readback-investigation.md](state-readback-investigation.md) | The original investigation. Kept for the record; **its central conclusion is wrong.** |

## Corrections

Four conclusions in these documents were superseded. Each is left in place with
a correction banner, because the reasoning that led to them is still instructive
and the mechanisms they describe are largely accurate.

1. **"Readback is impossible on stock firmware."** Wrong, and not because of the
   firmware: the status request was built with its end byte padded to `0xFF`.
   That byte *selects which record the light reports*. Record `0xA0` is live.
   Selecting it correctly, readback works on stock firmware.
2. **"A 26-byte firmware patch fixes it."** Built, verified, flashed to a real
   light — and it changed nothing. The remaining limit is in the light's MCU,
   which no Bluetooth-side patch reaches.
3. **"The wire format tells you which colour temperatures are trustworthy."**
   Two heuristics were tried and both were contradicted by a second light. No
   such signal exists; the integration reports what it is given, with a
   per-entry switch to stop polling colour temperature.
4. **"The `0xF3` handler compares against 1,3,4,5,6,7,8."** Attributed to the
   mesh firmware; that comparison chain is not in that image, which forwards
   `0xF3` to the MCU rather than decoding it. The conclusion it supported holds
   on other evidence — see [protocol.md](protocol.md).

The common thread: static analysis produced a coherent account that went
unchallenged until it met hardware. Where a claim here has not been tested
against a real light, it says so.

## Reproducing the inputs

The inputs split in two. The ones that can be published are committed under
[../reverse-artifacts/](../reverse-artifacts/), so the capability table
regenerates from a clean checkout with no downloads at all:

```bash
uv run python scripts/generate_capabilities.py   # should be byte-identical
```

Everything else — firmware images, the decompiled app, mesh keys — is vendor
material that is not redistributable, and lives only in a local `reverse/`
scratch directory. To rebuild those, fetch from Godox's own public endpoints.

**Product and firmware catalogues.** `reverse-artifacts/refresh.py` fetches
both, and handles the trap documented in [firmware-api.md](firmware-api.md):
`supportRadioIds` is *filtered to whatever you asked for*, so a per-model query
reports each model supporting only itself and any coverage count built that way
is wrong.

**The Android app.** Fetch `Godox Light` 4.1.0 and decompile with jadx 1.5.1
(`jadx -d reverse/jadx-out <apk>`); unpack resources separately to read
`resources.arsc`, which holds the effect names.

**Firmware images.** Downloaded from Godox's own endpoint by URL and SHA-256,
both pinned in `custom_components/godox_mesh/firmware.py`.

To refresh the committed artifacts against Godox's current data:

```bash
uv run python reverse-artifacts/refresh.py       # raw -> nocommit/, then distilled
uv run python scripts/generate_capabilities.py
```

## What is deliberately absent

- **Vendor firmware, stock or patched.** Godox's binaries are their copyrighted
  work and are not redistributed here in any form — a patched image is a
  derivative of one. The repository ships the *transformation* (a script) and
  the *checksums*, and downloads the original from Godox at run time.
- **The decompiled app.** Same reason. Findings are described; the decompilation
  is not republished.
- **`mesh_state.json`.** Contains live network, application and device keys for
  a real mesh network. Never commit one.
- **`tools/tc32/generate_sleigh.py`.** Third-party; fetched, not vendored.

What *is* published is in [../reverse-artifacts/](../reverse-artifacts/): the
filtered catalogues, the script that rebuilds them, and the checksums.

Checksums *are* published, which is the useful half: they let anyone confirm
they fetched the same bytes this analysis was done on, without anyone
redistributing anything.
