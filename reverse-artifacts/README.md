# Committable research artifacts

`reverse/` is the scratch space for this work: Godox's firmware images, their
decompiled app, support bundles, live mesh keys. None of it is redistributable
and all of it is gitignored.

**This directory is the committable half.** Small, filtered, and enough to
reproduce the integration's per-model capability table from a clean checkout —
so the derivation can be checked rather than taken on trust.

| | |
|---|---|
| `product-catalogue.json` | Godox's product list, filtered to the fields this repository consumes |
| `firmware-coverage.json` | which mesh firmware image serves which model |
| `color-chips.json` | Godox's lighting-gel catalogue, filtered to the fields the colour-chip command needs |
| `firmware-inventory.md` | every firmware image Godox serves — names, sizes, SHA-256s, and what disassembling them showed |
| `firmware-measurements.json` | per-image measurements: checksums, Cortex-M vectors, CRC-table offsets |
| `refresh.py` | re-downloads the two catalogues from Godox and rebuilds them |
| `fetch_firmware.py` | downloads the firmware corpus from Godox, using the URLs and sizes in `firmware-coverage.json` |
| `analyse_firmware.py` | re-measures a local firmware corpus and rewrites `firmware-measurements.json` |
| `nocommit/` | raw API responses, gitignored — `refresh.py` writes them here |

Everything here is text: catalogues, checksums and findings. No vendor binaries,
no keys, nothing that has to stay local.

## Reproducing the capability table

From a clean checkout, using the committed artifacts:

```bash
uv run python scripts/generate_capabilities.py
```

That rewrites `custom_components/godox_mesh/capabilities_data.json`. It should
come out byte-identical; if it does not, either the artifacts or the generator
changed and the diff is the answer.

To rebuild against Godox's current data instead:

```bash
uv run python reverse-artifacts/refresh.py     # fetch + distill
uv run python scripts/generate_capabilities.py
```

## What was filtered out, and why

Godox's product API returns **40 fields per product**; the integration reads
**seven**. The committed catalogue keeps those seven plus `hasBtFirmware` and
`paVersion`, which the non-mesh-product claims in
[../docs/model-support.md](../docs/model-support.md) rest on.

Dropping the rest removes product-image URLs, timestamps, internal ids and a
range of vendor fields nothing here uses. That is partly size, but mostly
intent: this is a research input, not a copy of someone's product database.
Facts about a light — that an SL200III Bi is 2800–6500 K — are not anyone's
property; a verbatim dump of a commercial catalogue is a different thing.

The same reasoning is why firmware images are **not** here. They are Godox's
copyrighted work, a patched image is a derivative of one, and neither is
redistributed in any form. What is published instead is the *transformation*
(`tools/lk8620/`) and the *checksums* (`custom_components/godox_mesh/firmware.py`),
which let anyone confirm they fetched the same bytes without anyone shipping
them.

## The trap these files encode

`refresh.py` requests **every radioId in one call**. Godox's firmware endpoint
filters `supportRadioIds` to whatever you asked for: query model `003F` alone
and it answers `["003F"]`, making every model look like it has firmware all to
itself. Any coverage count built from per-model queries is wrong. This is why
`firmware-coverage.json` is worth committing at all — the correct answer takes
one specific request that is easy to get wrong.

## Why the inventory is here rather than in `reverse/`

`firmware-inventory.md` is the index of a 62-image corpus — 3 Bluetooth images
and 59 MCU images — that several claims in [../docs/](../docs/) rest on, such as
"52 of 59 MCU images read colour temperature from a live variable". Those claims
were uncheckable while the inventory sat in a gitignored scratch directory.

It records **checksums, not images**. That is the whole trick: anyone can
re-download the corpus from Godox's own API (the requests are in
[../docs/firmware-api.md](../docs/firmware-api.md)) and confirm byte-for-byte
that they have what was analysed, without anyone redistributing a vendor binary.

## Checking the firmware corpus

`firmware-inventory.md` describes 62 images; `firmware-measurements.json` is the
machine-readable half. Both are regenerable once you have downloaded the corpus
(the requests are in [../docs/firmware-api.md](../docs/firmware-api.md)):

```bash
uv run python reverse-artifacts/fetch_firmware.py --into reverse    # ~25 MB
uv run python reverse-artifacts/analyse_firmware.py --corpus reverse
```

Nothing here is a dead end: the images are not redistributable, but every
download URL and expected size is committed, so the corpus is reconstructible
from a clean checkout plus a network connection. Downloads are checked against
the catalogue's sizes and, where an entry exists, against the committed
SHA-256 -- which is the point of publishing checksums rather than bytes.

Every field it emits is a direct measurement of the bytes — SHA-256, the ARM
Cortex-M vector table, the offset of the vendor CRC-8 lookup table — joined with
each image's **version** from `firmware-coverage.json`. It reproduces the
historical analysis exactly on all 59 MCU images.

Versions matter as much as the checksums here: a checksum says two files are
identical, a version says which build you have and whether Godox has shipped a
newer one since. For the Bluetooth images `versionName` is the same number in
hex — `"000066"` is 0x66, decimal 102 — which is what the chip reports to a
`FD 02` query, and the reason early notes in this repository disagreed about
whether stock was "66" or "102".

Two quirks in Godox's own data, left as they are rather than smoothed over:
M300R (`008C`) publishes `versionCode: 0` despite a filename reading `V101`, and
`EC7B` has an MCU image on disk that the MCU catalogue does not list at all.

**What it does not reproduce**, deliberately: an `a_frames_strict` field the
original carried, whose definition was never recorded and could not be recovered
from the data. A re-invented heuristic that quietly disagreed with the committed
docs would be worse than an absent field. The same applies to the "52 of 59 MCU
images read colour temperature from a live variable" figure quoted in
[../docs/readback-hardware-findings.md](../docs/readback-hardware-findings.md) —
that detector was never saved, and the document now says so rather than implying
the number is checkable.

## Provenance

Each file carries `_source` and `_fetched`. Treat them as a snapshot: Godox adds
models, and a stale snapshot that looks authoritative is worse than none. Re-run
`refresh.py` and commit the diff.
