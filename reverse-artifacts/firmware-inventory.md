# Godox firmware corpus — inventory and analysis

Every firmware image Godox's app API serves, downloaded and analysed. Companion
to [../docs/firmware-api.md](../docs/firmware-api.md), which documents the API
itself. Generated 2026-09-07.

> [!NOTE]
> This inventory is committed; **the images it inventories are not**. They are
> Godox's copyrighted work. Directory names below (`bt-bins/`, `mcu_lk8620/`
> and so on) refer to a local `reverse/` scratch directory. The checksums are
> the point: they let anyone who downloads the same images from Godox confirm
> they have the same bytes this analysis was done on. See
> [README.md](README.md).
>
> **The SHA-256 values in the tables below are truncated for width.** The full
> hashes, plus each image's version, are in
> [firmware-measurements.json](firmware-measurements.json) — use that to verify,
> not these.

| | |
|---|---|
| BT (`BTF`) images | **3** — one per BLE chip, in `bt-bins/` |
| MCU (`MCUF`) images | **59** across `mcu_lk8620/`, `mcu_lk8720/`, `mcu_lk8728b/` |
| Products covered | 190 radioIds hold BT firmware; 59 of them also have MCU firmware |
| Machine-readable | [`firmware-measurements.json`](firmware-measurements.json) — regenerate with `analyse_firmware.py` |

## BT firmware: one image per chip, verified per model

Each of the **190** radioIds with BT firmware was queried **individually**
(not via the deduplicating batch), 190 single-item requests. Within each
`paVersion` group every radioId returned the identical `firmwareName`,
`downloadURL` and `fileSizeBytes`. Zero anomalies. The three groups are
**disjoint** — no radioId maps to more than one BT image.

| paVersion | Chip | Version | Size | Models | sha256 |
|---|---|---|---|---|---|
| 0 | **LK8620** | `000066` | 148,692 | 114 | `aa56783357eae820be9af03e61d5bea3…` |
| 1 | **LK8720** | `000066` | 148,980 | 62 | `cc8c48fc0d039f393298d611b2848e4f…` |
| 3 | **LK8728B** | `000067` | 148,884 | 14 | `ffba898f84c6bfc6d644c210b602082d…` |

All three: `KNLT` magic at offset 8 (Telink OTA header, `TLNK` byte-swapped),
SDK string `telink_sig_mesh_sdk_V4.1.0.1`, 49 occurrences of company id
`0x0211`, 2 of `GD_LED`, the full contiguous Godox CRC8 table, and **zero**
light-control strings.

They are the same program built per chip, not unrelated firmware: printable-
string Jaccard similarity is **0.925–0.944** (184–186 of ~190 strings shared).
Byte-level identity is low (~20% even after shifting) because the code is
relocated, so compare these by strings and structure, not by bytes.

### The A0–A6 default reply table is in all three

Byte-identical in every BT image — the same seven length-prefixed frames at
different offsets:

```
08 a2 0a bd fb e7 4f 01 eb
08 a1 0a 22 01 45 ff 01 a5
08 a3 0a 02 00 ff ff 01 7e
08 a0 0a 1b 32 ff ff 01 f9
08 a0 0a 1b 32 ff ff 01 f9
08 a0 0a 1b 32 ff ff 01 f9
08 a4 0a 01 00 14 14 01 d3
```

| Chip | CRC8 table | A0 default frame offsets |
|---|---|---|
| LK8620 | `0x21bc4` | 7 frames |
| LK8720 | `0x21ce4` | 7 frames |
| LK8728B | `0x21c84` | 7 frames |

This matters for [../docs/bt-chip-firmware.md](../docs/bt-chip-firmware.md):
the stale-cache defaults are **not** an LK8620 quirk. The identical table is
present in LK8720 and LK8728B, so the behaviour spans **all 190 products**
that run any of these three images, not just the LK8620 population.

Conversely, **no MCU image contains this table.** All 59 were scanned; the
only candidate hits were ARM Thumb code patterns (`a3 41 08 a3 41 08 …`) and
one incrementing lookup table in ML150 RF. It is exclusively a BT-chip
structure.

## MCU firmware

| Chip | Images | Cortex-M | CRC8 table (255-prefix) | contiguous 256 |
|---|---|---|---|---|
| LK8620 | 16 | 16/16 | 16/16 | 7/16 |
| LK8720 | 36 | 36/36 | 36/36 | 8/36 |
| LK8728B | 7 | 7/7 | 7/7 | 0/7 |

**Every one of the 59 is ARM Cortex-M and every one carries the Godox CRC8
table**, so all 59 contain a V2/V3 protocol handler. Only 15 have it as a
contiguous 256-byte array; the rest need the 255-byte-prefix search (see
firmware-api.md).

### Full listing

| Chip | radioId | Product | Size | SP | Reset | CRC8 @ | 256? |
|---|---|---|---|---|---|---|---|
| LK8620 | `002A` | TP2R | 172,796 | `0x200042e0` | `0x080102c5` | `0x2a058` | no |
| LK8620 | `002B` | TP4R | 172,796 | `0x200042e0` | `0x080102c5` | `0x2a058` | no |
| LK8620 | `0045` | TP8R | 172,796 | `0x200042e0` | `0x080102c5` | `0x2a058` | no |
| LK8620 | `0058` | TPC2R | 171,792 | `0x200042f8` | `0x080102c5` | `0x29c50` | no |
| LK8620 | `0059` | TPC4R | 171,792 | `0x200042f8` | `0x080102c5` | `0x29c50` | no |
| LK8620 | `005A` | TPC8R | 171,792 | `0x200042f8` | `0x080102c5` | `0x29c50` | no |
| LK8620 | `0073` | MG1200R | 1,308,692 | `0x20016ac0` | `0x0800c2cd` | `0x45000` | yes |
| LK8620 | `007A` | LP400Bi | 138,624 | `0x20005f00` | `0x0800c185` | `0x21118` | yes |
| LK8620 | `007B` | LP600Bi | 138,624 | `0x20005f00` | `0x0800c185` | `0x21118` | yes |
| LK8620 | `007C` | LP1200Bi | 140,740 | `0x20005f00` | `0x0800c185` | `0x21912` | yes |
| LK8620 | `007D` | LP400R | 185,736 | `0x20005f58` | `0x0800c185` | `0x2b882` | yes |
| LK8620 | `007E` | LP600R | 185,736 | `0x20005f58` | `0x0800c185` | `0x2b882` | yes |
| LK8620 | `007F` | LP1200R | 185,884 | `0x20005f58` | `0x0800c185` | `0x2b91a` | yes |
| LK8620 | `008F` | ML100R | 281,852 | `0x2000f9f8` | `0x08005169` | `0x44992` | no |
| LK8620 | `0097` | ML80Bi | 409,184 | `0x20010948` | `0x08004165` | `0x63aec` | no |
| LK8620 | `EC7B` | AD00-02 | 171,384 | `0x200072d0` | `0x0800424d` | `0x29aa2` | no |
| LK8720 | `0074` | LA300R | 386,832 | `0x20009860` | `0x08008169` | `0x5e2e4` | no |
| LK8720 | `0075` | LA150R | 386,832 | `0x20009860` | `0x08008169` | `0x5e2e4` | no |
| LK8720 | `0078` | MS60R | 350,720 | `0x2000d1e0` | `0x08004169` | `0x55605` | no |
| LK8720 | `0079` | LA300Bi | 293,892 | `0x200083f0` | `0x08008169` | `0x47878` | no |
| LK8720 | `0084` | MS60Bi | 279,296 | `0x2000c5d0` | `0x08004169` | `0x43fac` | no |
| LK8720 | `0085` | LA200R | 386,832 | `0x20009860` | `0x08008169` | `0x5e2e4` | no |
| LK8720 | `0088` | MG2400R | 1,309,692 | `0x20016b00` | `0x0800c2cd` | `0x457bc` | yes |
| LK8720 | `008A` | RS60R | 336,000 | `0x2000d0b0` | `0x08004169` | `0x51d16` | no |
| LK8720 | `008B` | RS60Bi | 270,736 | `0x2000c510` | `0x08004169` | `0x41e95` | no |
| LK8720 | `008C` | M300R | 923,320 | `0x20015100` | `0x0800c2cd` | `0x3f9c0` | yes |
| LK8720 | `0090` | ML150_RF | 863,360 | `0x20010178` | `0x08004165` | `0xd292f` | no |
| LK8720 | `0094` | LA600R | 574,020 | `0x200131d8` | `0x08008169` | `0x8be16` | no |
| LK8720 | `0095` | LA600Bi | 420,620 | `0x20012d98` | `0x08008169` | `0x6674f` | no |
| LK8720 | `00A0` | MG6K | 738,064 | `0x20016c00` | `0x0800c2dd` | `0x41422` | yes |
| LK8720 | `00A4` | ML100Bi(新版) | 228,280 | `0x20007a78` | `0x08004285` | `0x3790d` | no |
| LK8720 | `00A5` | ML150Bi | 417,204 | `0x20010038` | `0x08004165` | `0x65ab9` | no |
| LK8720 | `00A8` | LP800Bi | 122,944 | `0x20005dd8` | `0x080041b5` | `0x1db72` | yes |
| LK8720 | `00AD` | LE300Bi | 418,748 | `0x20012d68` | `0x08008169` | `0x65ffb` | no |
| LK8720 | `00AE` | LE200Bi | 418,748 | `0x20012d68` | `0x08008169` | `0x65ffb` | no |
| LK8720 | `00AF` | LE150Bi | 418,964 | `0x20012d80` | `0x08008169` | `0x660d5` | no |
| LK8720 | `00B3` | RS100BI | 281,876 | `0x20011b80` | `0x08004199` | `0x4488f` | no |
| LK8720 | `00B4` | RS100R | 319,980 | `0x20012798` | `0x08004199` | `0x4dc9c` | no |
| LK8720 | `00B5` | SL300_RF | 910,916 | `0x20019a78` | `0x0800c2a1` | `0x44884` | yes |
| LK8720 | `00B6` | SL200_RF | 910,904 | `0x20019a78` | `0x0800c2a1` | `0x44870` | yes |
| LK8720 | `00B8` | LE600Bi | 418,632 | `0x20012d58` | `0x08008169` | `0x65f86` | no |
| LK8720 | `00BB` | MG4K | 737,064 | `0x20016bf0` | `0x0800c2dd` | `0x41032` | yes |
| LK8720 | `00BD` | OP200R | 755,444 | `0x200143c0` | `0x08008165` | `0xb8209` | no |
| LK8720 | `00BE` | DL625Bi | 423,480 | `0x20012d78` | `0x08008169` | `0x6726b` | no |
| LK8720 | `00BF` | DL330Bi | 296,276 | `0x2000d3e8` | `0x08008169` | `0x481b4` | no |
| LK8720 | `00C2` | MG4KR | 1,314,260 | `0x20016d40` | `0x0800c2cd` | `0x46958` | yes |
| LK8720 | `00C5` | LE300R | 611,816 | `0x20013c10` | `0x08008169` | `0x95162` | no |
| LK8720 | `00C6` | LE200R | 611,816 | `0x20013c10` | `0x08008169` | `0x95162` | no |
| LK8720 | `00C8` | ParTrix_S | 500,024 | `0x20018848` | `0x0800c2a1` | `0x79d25` | no |
| LK8720 | `00CC` | OP120Bi | 735,276 | `0x20012968` | `0x08009165` | `0xb33bc` | no |
| LK8720 | `00CD` | OP200Bi | 735,276 | `0x20012968` | `0x08009165` | `0xb33bc` | no |
| LK8720 | `00D2` | LE200D | 243,228 | `0x20007b50` | `0x0800428d` | `0x3b424` | no |
| LK8728B | `0089` | MA5R | 231,704 | `0x20007dd0` | `0x08004285` | `0x38619` | no |
| LK8728B | `00A1` | C30R | 234,796 | `0x20007c50` | `0x08004285` | `0x39165` | no |
| LK8728B | `00A6` | ML40Bi | 225,508 | `0x20007f58` | `0x08004285` | `0x36de7` | no |
| LK8728B | `00A7` | ML40R | 240,444 | `0x20007fd0` | `0x08004285` | `0x3a7d7` | no |
| LK8728B | `00A9` | MA5R_Plus | 238,616 | `0x20007cb0` | `0x08004285` | `0x3a0a5` | no |
| LK8728B | `00C4` | MS15R | 362,480 | `0x2000e178` | `0x08004269` | `0x58378` | no |
| LK8728B | `00CF` | LT1 | 42,516 | `0x20006398` | `0x08008185` | `0xa49e` | no |

### Shared and near-shared binaries

53 distinct binaries across 59 files. Byte-identical groups:

- 002A TP2R, 002B TP4R, 0045 TP8R
- 0058 TPC2R, 0059 TPC4R, 005A TPC8R
- 0074 LA300R, 0075 LA150R, 0085 LA200R

Beware the near-miss: equal size **and** equal CRC8 offset does not mean
equal firmware. LE300Bi/LE200Bi share both yet differ in 119,768 bytes.
The genuinely near-identical pair is **OP120Bi / OP200Bi — 3 differing
bytes**, two of which are the radioId itself (`0xcc`→`0xcd` at `0x000daa`
and `0x018c04`) plus `'1'`→`'2'` at `0x03bf88`. That is a model identity
baked in as a single byte, and the cleanest available example of how Godox
derives sibling firmware.

## Reproducing

Manifests come from `firmware-sweep-live-products.json` (the all-radioId
sweep) cross-referenced with `product_live.json`. Downloads need no auth —
only the `AppName`/`AppVersion`/`SystemInfo` header trio on the API call;
the `doc/download/{id}` URLs need nothing at all.

