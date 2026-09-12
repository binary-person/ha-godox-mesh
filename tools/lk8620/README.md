# LK8620 status-readback patch toolchain

Turns a stock Godox Telink Bluetooth-mesh firmware — **LK8620, LK8720, or
LK8728B** — into one that forwards status queries to the main MCU. The chip is
auto-detected from the input SHA (or pass `--chip`). See
[../../docs/lk8620-flashing.md](../../docs/lk8620-flashing.md) for the full
procedure, the verification story, and the model inventory.

> **The patch has no demonstrated benefit — do not install it.** Live
> brightness, commanded colour temperature and battery all read back on
> **stock** firmware once the status request selects record `0xA0`. The one
> field this patch targets, colour temperature changed on the light's own dial,
> did not appear when the patch was flashed to a real SL200III Bi: that limit
> is in the MCU. See
> [../../docs/readback-hardware-findings.md](../../docs/readback-hardware-findings.md).
>
> What *is* proven here is the flashing pipeline — stock, patched and debug
> images were all written to a real light and stock was restored. Keep these
> scripts for firmware work. **Flashing can still brick a light.**

## Scripts

All commands are run **from the repository root** — several scripts resolve
paths relative to it.

### The patch pipeline

| Script | Purpose |
|---|---|
| `patch_status_readback.py [--chip C] IN OUT` | build the patched image (SHA-gated to a known stock image) |
| `verify_patch.py CHIP STOCK PATCHED` | 18 structural checks; must end `ALL PASS` |
| `ota_packets.py IMG` | print the OTA packet stream (a port of the app's `OtaPacketParser`) |
| `flash_ota.py IMG` | dry-run (default, no device) or `--flash` over BLE |

### Debugging aids

These exist for firmware investigation, not for the patch. `memread.py` needs a
light flashed with the image `patch_memread.py` builds — a debugging build that
should not be left on a light.

| Script | Purpose |
|---|---|
| `probe_readback.py` | read everything the vendor protocol reports from a live light |
| `patch_memread.py IN OUT` | build a debug image adding a RAM-read command |
| `memread.py --at ADDR` | read the BLE chip's RAM, via that debug image |
| `tc32asm.py` | self-verifying TC32 assembler used to build the trampolines |

Requires `tools/tc32/generate_sleigh.py` (fetch once — see `../tc32/README.md`)
and, for real flashing, `bleak`.

## Quick check

```bash
uv run python tools/lk8620/patch_status_readback.py stock.bin patched.bin
uv run python tools/lk8620/verify_patch.py LK8620 stock.bin patched.bin   # -> ALL PASS
uv run python tools/lk8620/flash_ota.py patched.bin                       # dry run
```

`verify_patch.py` takes the **chip first** — omitting it is an `IndexError`, not
a usage message. The same scripts cover all three chips; only the stock image
differs.
