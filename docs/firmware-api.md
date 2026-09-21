# Godox's firmware distribution API


> [!NOTE]
> Paths beginning `reverse/` are a **local scratch directory, not part of this
> repository** — it holds Godox's firmware, their decompiled app and live mesh
> keys, none of which are redistributable. They are cited so each finding says
> where it came from. The publishable inputs are committed under
> [../reverse-artifacts/](../reverse-artifacts/), which also carries the script
> that rebuilds them; see [README.md](README.md#reproducing-the-inputs).

How the Godox Light app finds and downloads firmware, traced through the
Android APK and then verified against the live server. Written while looking
for a firmware image for the **SL200III Bi** to extend the analysis in
[state-readback-investigation.md](state-readback-investigation.md) section 10.

**The headline result is a negative one — for the analysis, not for the
light.** The API serves the SL200III Bi a *generic Telink BLE-SoC image* shared
by 112 different products, not model-specific firmware. The light's main-MCU
firmware — the binary that would contain the vendor protocol handler, as the
UL150Bi II image did — **is not distributed through this API at all**. See
[What this does not get you](#what-this-does-not-get-you).

To be unambiguous, since the wording above has been misread once already: the
SL200III Bi **does** get firmware updates through the app, and this API is how
it finds them. What is unobtainable here is the *main-MCU* image, which is a
limit on disassembly work only. See [What the flags do and do not
mean](#what-the-flags-do-and-do-not-mean).

Everything below is reproducible without an account. No login, no token.

## Source

| | |
|---|---|
| APK | `Godox+Light_4.1.0_APKPure.apk` (`reverse/`) |
| Version | 4.1.0, versionCode `2026071017`, flavor `online_google` |
| Decompiler | jadx 1.5.1 |
| Verified against live server | 2026-09-07 |

Paths below are relative to the jadx output root (`sources/`).

## The endpoint

`com/godox/ble/mesh/api/ApiService.java:164`:

```java
@POST("/godox/api/firmware/last/batch-list/GodoxLight")
Object getBatchFirmware(@Body List<GetBatchFirmwareBodyItem> list,
                        Continuation<? super BaseResponse<List<FirmwareInfo>>> continuation);
```

Base URL is `BuildConfig.HostName` = `https://www.godox.net/`
(`net/RetrofitManager.java:215`).

An older per-device form still exists at `ApiService.java:182`:

```java
@GET("/godox/api/firmware/{appName}/{category}/{paVersion}")
Object getFirmware2(@Path("appName") String, @Path("category") String,
                    @Path("paVersion") String, @Query("radioId") String, ...);
```

Its caller is marked `@Deprecated("不准确了，paVersion不止两个了")` — *"no longer
accurate, there are more than two paVersions"*. The server still routes it but
returns `{"code":400,"msg":"Data not found"}` for every combination tried. Treat
it as dead; use the batch endpoint.

## Authentication

There is none. `Authorization` is set unconditionally from
`Prefs.getString("token")` (`RetrofitManager.java:174`) and is an empty string
until you log in — the firmware endpoint does not look at it.

What the server *does* check is a three-header app gate. All three must be
present or you get `{"code":400,"msg":"Unauthorized APP, unsupported request"}`:

| Header | Checked? |
|---|---|
| `AppName` | **Yes.** Must be `GodoxLight` or `GodoxLight-Pad` (`constant/Key.java:69`). Arbitrary values rejected |
| `AppVersion` | Presence only. `1.0.0` is accepted |
| `SystemInfo` | Presence only. `x` is accepted |

Two headers instead of three is rejected, in every pairing. The app also sends
`MachineId` and `EquipmentName`, and appends `?lang=<tag>` via an interceptor
(`RetrofitManager.java:201`) — none of which are required. `Referer` is added
only for `file.godox.net` hosts, not for this one.

## Request

Body items are `{category, paVersion, radioId}`
(`bean/firmware/GetBatchFirmwareBodyItem.java`), built one or two per light in
`ui/firmware/vm/UpdateFirmwareViewModel.java:587`:

```java
if (lightBean.getHasBtFirmware())
    arrayList.add(new GetBatchFirmwareBodyItem("BTF", lightBean2.getPaVersion(), fdsNodeInfo.getType()));
if (lightBean.getHasMcuFirmware())
    arrayList.add(new GetBatchFirmwareBodyItem("MCUF", null, fdsNodeInfo.getType()));
```

- **`category`** — `"BTF"` = Telink BLE/mesh SoC, `"MCUF"` = main MCU.
- **`radioId`** — `fdsNodeInfo.getType()`, the 4-hex-digit model id.
- **`paVersion`** — BTF only; `null` for MCUF.

All three values come from `assets/product.json` in the APK, which is a cached
`BaseResponse` snapshot of `/godox/api/productV2/getProduct` (212 products).
For the SL200III family:

| radioId | productName | paVersion | hasBtFirmware | hasMcuFirmware |
|---|---|---|---|---|
| `003C` | SL200III | 0 | yes | no |
| `003D` | SL300III | 0 | yes | no |
| `003F` | **SL200IIIBi** | 0 | yes | no |
| `0040` | SL300IIIbi | 0 | yes | no |

So an SL200III Bi produces exactly one item, and no MCUF request at all.

The whole call, reduced to what the server actually needs:

```bash
curl -sS -X POST https://www.godox.net/godox/api/firmware/last/batch-list/GodoxLight \
  -H 'Content-Type: application/json' \
  -H 'AppName: GodoxLight' -H 'AppVersion: 4.1.0' -H 'SystemInfo: Android15' \
  -d '[{"category":"BTF","paVersion":0,"radioId":"003F"}]'
```

The batch is unordered and deduplicating: 264 items covering every product in
`product.json` collapse to 55 distinct firmware entries.

## Response

```json
{"code":200,"status":true,"msg":"Operation success","timestamp":1788754360765,
 "data":[{"appName":"GodoxLight","category":"BTF",
  "firmwareName":"LK8620_MESH_GD_v000066_20250403_beta.bin",
  "paVersion":0,"versionCode":102,"versionName":"000066",
  "downloadURL":"https://www.godox.net/godox/api/doc/download/1680457224855552",
  "versionPoint":"Known issues have been fixed(8620)",
  "created":"2026-07-02T05:38:59",
  "fileSizeBytes":148692,"fileSize":"145.21KB",
  "btf56versionEnable":false,"supportRadioIds":["003F"]}]}
```

Deserialised into `bean/FirmwareInfo.java`. The two categories return
*different shapes*, which is why the app matches them differently:

| Field | BTF | MCUF |
|---|---|---|
| `supportRadioIds` | list of models | absent |
| `radioId` | absent | scalar |
| `versionName` | set | `null` |
| `equipmentName` | absent | set |

Match rule, `UpdateFirmwareViewModel.java:619`: a BTF entry applies when
`firmwareInfo.paVersion == lightBean.paVersion` **and**
`supportRadioIds.contains(radioId)`; MCUF matches on the scalar `radioId`.

### Trap: `supportRadioIds` is filtered to your request

It is **not** the firmware's true support list. Ask for `003F` alone and you
get `["003F"]`; ask for `003C` and `003F` together and the *same* entry comes
back as `["003F","003C"]`. The list is filtered to whatever you asked for, so a
single-item response does not mean the image is model-specific.

To recover the real list, request every radioId at once. The result is only
**three** BTF images for all 212 products, keyed by `paVersion`:

| paVersion | firmwareName | Version | Size | Models |
|---|---|---|---|---|
| 0 | `LK8620_MESH_GD_v000066_20250403_beta.bin` | `000066` | 145.21 KB | **114** |
| 1 | `LK8720_MESH_GD_v000066_20250403_beta.bin` | `000066` | 145.49 KB | 62 |
| 3 | `LK8728B_MESH_GD_v000067_20260603_beta.bin` | `000067` | 145.39 KB | 14 |

(Counts from the live product list. Driving the sweep off the APK's stale
`product.json` instead gives 112 / 60 / 14 — see [Do not trust the bundled
`product.json`](#do-not-trust-the-bundled-productjson--fetch-the-live-one).)

`paVersion` selects the **BLE module variant**, not the light. LK8620 / LK8720
/ LK8728B are the chip part numbers, and `versionPoint` for the first reads
"Known issues have been fixed(8620)". `paVersion` 2 exists in `product.json`
but returns nothing.

"PA" is the app's own term and reads as *power amplifier* — the RF front end.
It is not merely a label: a device reporting PA value 1 requires an explicit
**open-PA-upgrade** command before BT OTA can start
(`UpdateFirmwareViewModel.startUpdateFirmwareChannel` → `openPaUpgrade`,
`GodoxCommandApi.java:806`), which is the vendor frame
`FD 04 FF FF FF FF FF E4`. PA-0 devices — including the SL200III Bi and the
UL150Bi II — skip that step. So `paVersion` is an RF-hardware distinction with
a real protocol consequence, not just a firmware-selection key.

The 59 MCUF entries are genuinely per-product and are named accordingly
(`TP2R_TP4R_TP8R_V139`, `LA300R_200R_150R_V112`, `MG1200R_V111(1).bin`, …).
Exactly two SL/UL-series products have MCU firmware — SL300 RF (`00B5`) and
SL200 RF (`00B6`). Neither is an SL200III Bi; see [SL200 RF is a different
light](#sl200-rf-is-a-different-light).

Both responses are kept so the difference is checkable:
`reverse/sl200iiibi-btf.json` is the single-item request for `003F`, and
`reverse/firmware-batch-list-all.json` the full 264-item sweep. The same
firmware entry appears in each with a different `supportRadioIds`.

## Download

`downloadURL` is absolute and goes to an opaque doc-id endpoint on
`www.godox.net` — not a guessable path, and not `file.godox.net` where the
app's product images live. It is fetched verbatim, with no rewriting, through
`ApiService.java:151`:

```java
@Streaming
@GET
Object downloadFile(@Url String str, Continuation<? super ResponseBody> continuation);
```

reached via `UpdateFirmwareViewModel.downloadFirmwareFile` (`:504`) →
`BaseViewModel.downloadFile` (`:37`) → `ktx/DownloadKtxKt.downloadFile` (`:15`).
Files land in `context.getCacheDir()/download`. When `firmwareName` is absent
the app synthesises `file_base_ble_<versionCode>_<created>` (`:213`).

```bash
curl -sSL https://www.godox.net/godox/api/doc/download/1680457224855552 \
  -o LK8620_MESH_GD_v000066_20250403_beta.bin
```

Serves `application/octet-stream`, `content-disposition:
attachment;filename=1680457224855552.bin`, 148,692 bytes — matching
`fileSizeBytes` exactly. No app-gate headers needed on this one.

## The image

```
sha256  aa56783357eae820be9af03e61d5bea3f816bc0f6e7ea52333411cdd5d7afb0c
size    148692
0000    5e80 0100 4101 5d01 4b4e 4c54 a002 8800   ^...A.].KNLT....
```

`KNLT` at offset 8 is `TLNK` byte-swapped: a Telink OTA image header. Dense
2-byte instruction stream from ~`0x30`, so **TC32**, Telink's proprietary
core — not the ARM Cortex-M of the UL150Bi II image. A different disassembly
target, and one most tools will not handle out of the box.

Consistent with the Telink mesh stack already vendored into the APK
(`com.telink.ble.mesh.*`).

### What the BT image is for, versus the MCU

The two binaries have near-disjoint string vocabularies, which settles the
division of labour. The BT image is entirely Bluetooth Mesh plumbing:

```
$$$telink_sig_mesh_sdk_V4.1.0.1$$$
app key decrypt error / device key decrypt error / invalid Network key index
PBGATT-IN / PBGATT-OUT / PROXY-IN / PROXY-OUT / USEROTA
segment rx timeout / rc seg block ack / tx segment busy / ttl invalid
SHA256 / prck256          GD_LED          MCU OTA
```

Company id `0x0211` appears 49 times; `GD_LED` sits at `0x21ba6`, adjacent to
the CRC8 table at `0x21bc4`.

| | LK8620 (BT) | Main MCU (UL150Bi II) |
|---|---|---|
| Total strings | 492 | 1070 |
| Mesh-stack strings | **20** | **0** |
| Light-logic strings (bright/cct/fan/effect/dmx/pwm) | **0** | logic is in code, not strings |
| Godox CRC8 table | `0x21bc4` | `0xe58c` |
| Core | TC32 | ARM Cortex-M |
| Shared across models | 114 products | per-model |

So the BT chip **validates and emits** V2/V3 frames — hence the CRC8 table —
but never interprets them: it unwraps the mesh vendor PDU and passes the
payload over UART to the MCU, which decides what it means. One generic image
can serve 114 lights precisely because it is a dumb pipe.

That is also why `product.json` must exist. Nothing on the mesh advertises a
light's capabilities, so the app ships a 226-product database instead;
colour-temp ranges, effect lists and fan gears come from that JSON, not from
the light.

Practical consequence: updating the BT firmware affects pairing, proxy
handling, key handling, segmentation and OTA reliability — **never** what the
light can do.

### MCU OTA over mesh exists on these lights

`MCU OTA` is a string in the BT firmware, and `MeshGattMcuUpgrade` is a class
in the app. The mesh path for flashing the **main MCU** is therefore present in
both halves of the stack, including on models like the SL200III Bi. It is
gated only by the server having no `MCUF` entry for the radioId — not by
hardware or firmware capability. The USB port is a second, independent route.

**Unresolved:** the BT image carries an ASCII command set — `DVRESET`,
`SLEEP`, `SWSON`, `SRCTEST`, each with a `:OK` reply — and none of those
strings appear in the UL150Bi II MCU image. It cannot currently be attributed
to the BT↔MCU UART link; it may be a factory-test channel.

## What the flags do and do not mean

Read the two flags narrowly. They describe **what the app will OTA**, nothing
more:

| Flag | Means | Does not mean |
|---|---|---|
| `hasBtFirmware: true` | the app looks up and OTAs the Telink BLE/mesh image | — |
| `hasMcuFirmware: false` | the app never requests an MCU image for this model | *not* that the model has no MCU, and *not* that its MCU firmware is never updated |

An SL200III Bi (`003F`) is `hasBtFirmware: true`, so it **does** receive
firmware updates through the app — that is the `LK8620_MESH_GD_v000066` image
above. Nothing here says otherwise. So is the SL200Bi (`00D1`), on the same
image.

The counterexample for the second flag is in this repo. The UL150Bi II is
flagged the same way:

```
001D  UL150IIBI  hasBtFirmware=True  hasMcuFirmware=False
```

and yet `reverse/1729857629339846.zip` holds `UL150Bi II V1.02.bin` — 447 KB of
ARM Cortex-M **main-MCU** firmware, distributed by Godox alongside a
release-notes PDF. Its MCU firmware plainly exists and plainly got updated. For
this family that happens out-of-band through support or service channels, not
through app OTA.

## What this does not get you

The UL150Bi II image that settled the vendor-protocol question
([state-readback-investigation.md](state-readback-investigation.md) section 10)
was the **main MCU** — ARM Cortex-M, flash base `0x08000000`, containing the
Godox CRC8 table and the complete V2/V3 sub-command dispatch. That image came
from a support bundle (`reverse/1729857629339846.zip`), **not** from this API.

This API gives the SL200III Bi only the other side of the UART link: a generic
Telink mesh/proxy image shared with 111 other products. It cannot contain
model-specific light-control logic, because 112 different lights run the same
bytes.

That said — **it is not protocol-free.** The Godox CRC8 table is embedded in
the LK8620 image at `0x21bc4`, byte-identical to
`src/godox_mesh_bt/crypto.py`. So the BLE side validates and generates V2/V3
frames too, and this image is a legitimate target for the *framing and
tunnelling* half of the protocol. It is also the firmware an SL200III Bi
actually runs. What it cannot tell you is anything model-specific.

This bears on the remaining open question in that document — why an SL200III Bi
does not report colour temperature changed on its own dial. (The request's end
byte selects a record, and record `0xA0` is live; see
[readback-hardware-findings.md](readback-hardware-findings.md).) That remaining
behaviour lives in the main MCU, which this API does not serve for the model.

### The MCU catalogue was queried directly, not inferred

The app's `hasMcuFirmware` flag is not evidence about what the server holds, so
`MCUF` was requested explicitly: for `003F` alone, with `paVersion: 0`, with
`paVersion: null`, and in a sweep of the entire radioId byte space
(`0000`–`00FF`, 256 items in one batch). All empty for `003F`. The control
(`002A`, TP2R) returned a result in the same requests, so the query form was
correct.

### Do not trust the bundled `product.json` — fetch the live one

The APK's `assets/product.json` is a **stale snapshot**: 212 products, against
**226** from the live `/godox/api/productV2/getProduct`. Fetch the live one:

```bash
curl -sS 'https://www.godox.net/godox/api/productV2/getProduct?lang=en-US' \
  -H 'AppName: GodoxLight' -H 'AppVersion: 4.1.0' -H 'SystemInfo: Android15'
```

Two things only the live data shows.

**Seven products gain `hasMcuFirmware: true`** — MA5R Plus (`00A9`), SL300 RF
(`00B5`), SL200 RF (`00B6`), MS15R (`00C4`), ParTrix S (`00C8`), LT1 (`00CF`),
AD00-02 (`EC7B`). Server and app agree on these once product data is refreshed;
a stale snapshot can show them out of step.

**radioIds are not one byte.** The live set includes `00DA`–`00DF`, `EC77`–
`EC7F`, and `10000`. A sweep of `0000`–`00FF` is therefore *not* exhaustive;
drive sweeps from the live product list instead.

### The definitive sweep

Both categories, all 226 live radioIds, 452 body items, one request
(`reverse/firmware-sweep-live-products.json`):

| | Count |
|---|---|
| BTF images | **3** (`paVersion` 0 / 1 / 3, as tabled above) |
| MCUF entries | **59** |
| SL/UL-series products with MCU firmware | **2** — SL300 RF (`00B5`), SL200 RF (`00B6`) |

For `003F` (SL200III Bi): **BTF yes** (`LK8620_MESH_GD_v000066`), **MCUF
none**. Also none for `003C`, `00D1`, or `001D`.

That last one still matters: the UL150Bi II's MCU firmware demonstrably exists
— it is in `reverse/1729857629339846.zip`, and Godox publishes it on the web
(see [Godox's other firmware channels](#godoxs-other-firmware-channels)) — yet
the API does not serve it. **The API's MCU catalogue is incomplete relative to
what Godox actually ships**, so a negative result here is not evidence that
firmware does not exist, only that this API will not hand it over.

### Things that do not work

Recorded so nobody spends the requests again. All return `200` with
`items: 0`, or are ignored:

| Attempt | Result |
|---|---|
| Empty body `[]` | 0 items |
| `category` without `radioId` | 0 items |
| `radioId` as `"*"`, `""`, or `null` | 0 items |
| `category` of `""`, `APP`, `PA`, `BTF56`, `MCU` | 0 items — only `BTF`/`MCUF` are real |
| `MCUF` with `paVersion` 0 or `null` for `003F` | 0 items |
| `appName` path segment (`Godox`, `KNOWLED`, `G3`, `GodoxPhoto`, …) | **ignored** — every value returns the identical result |

There is no unfiltered listing: both fields must match exactly.

### SL200 RF is a different light

`SL200RF_V113.bin` is a real ARM Cortex-M main-MCU image (SP `0x20019a78`,
reset `0x0800c2a1`) carrying the Godox CRC8 table at `0x44870`, so it does
contain a V2/V3 handler. It is **not** SL200III Bi firmware: different radioId
(`00B6` vs `003F`), different BLE SoC (`paVersion` 1 / LK8720 vs 0 / LK8620),
different product line — an RF-equipped variant. Treat it exactly as the
UL150Bi II image is treated in
[state-readback-investigation.md](state-readback-investigation.md): a
same-family substitute whose findings need re-verifying per model, not an
answer for this one.

Getting a genuine SL200III Bi MCU image needs a different route — the web
firmware portal below, or reading the flash off the hardware.

## Godox's other firmware channels

The `godox.net` app API is not Godox's only firmware distribution. There is a
separate, entirely unrelated **web portal on `godox.com`**, and it is where the
UL150Bi II image in this repo came from:

```
https://www.godox.com/firmware-continuous-light/
  → /static/upload/other/20241025/1729857629339846.zip     # UL150Bi II V1.02
```

That is byte-for-byte the file at `reverse/1729857629339846.zip`. So the two
channels carry **different** firmware: godox.com publishes main-MCU `.bin`
images for continuous lights; the app API publishes Telink BT images plus a
partly-overlapping MCU set.

### What the portal actually holds

Eight firmware categories exist, all `/firmware-<category>/`:
`launcher-installers`, `flash`, `continuous-light`, `audio`, `monitor`,
`control-system`, `Cameras-Printers`, `Intercom-Systems`.

Continuous Light is 3 pages, **15 files total**, and contains **no SL-series
firmware at all**: UP-C150, UL150Bi II, UL150II, SZ150R, LD75R, LD150R,
LD150RS, LE200Bi, LE300Bi, LE600Bi, LA600Bi, LA600R, LA300Bi,
LA300R/LA200R/LA150R, FL-D200.

Two link conventions: legacy
`/static/upload/other/{YYYYMMDD}/{numeric-id}.{zip,rar}` and current
`/Downloads/{Brand}_Firmware_{MODEL}_{VERSION}.zip`. The `/Downloads/`
directory returns **403** (no listing), and since the version string is part of
the filename it is not guessable — do not try.

Note the portal can be *behind* the API: LA300R/LA200R/LA150R is `V1.11` on the
web and `LA300R_200R_150R_V112` on the API.

### Update mechanism for these lights

Continuous lights do **not** use the G3 desktop updater. Per the portal's own
instructions: put a single `.bin` in the root of a FAT32 USB drive, insert it
into the fixture's USB port, and power on — some models auto-start, others need
MODE held during power-on. An SL200III Bi has that USB port, so its MCU **is**
user-flashable; there is simply no published image for it.

### Channels ruled out

| Source | Result |
|---|---|
| SLIII product page (`/product-d/SLIII.html`) | has a **"Product Firmware"** heading with **nothing under it** — the category exists and is empty for this family |
| G3 Firmware Launcher V2.0 (macOS build inspected) | manifest `Config/G3_firmware_20241219.json`, live copy at `/Downloads/G3_firmwares.json` — 69 entries, all FLASH / Control_System / AUDIO. **No continuous lights whatsoever**. The 45 MB installer was deleted after this check; the live manifest URL is enough to re-confirm it |
| `godox.cn` | does not resolve (NXDOMAIN) |
| `knowled.com` | no continuous-light firmware section |
| Resellers (MoLight, Strobepro) | Strobepro mirrors SZ150R, LD75R, LD150R, LD150RS, F200Bi, F400Bi, F600Bi, M600Bi — **no SL III**. MoLight only links back to Godox |

The G3 manifest is worth knowing about regardless: it is a plain published JSON
listing every flash/trigger firmware with `device_id`, `version`,
`release_date`, changelog text, and a `firmware_url` relative to
`base_url: /Downloads/G3_V2.0_bins`.

### Conclusion on obtaining SL200III Bi MCU firmware

Five independent published sources — the app API, the godox.com portal, the
SLIII product page, the G3 manifest, and two reseller mirrors — and none holds
a main-MCU image for `003F`. The remaining routes are a support request to
Godox for the specific model (which is how the UL150Bi II bundle came to
exist), or reading the flash off the board.

### The whole SL line, for reference

All 20 SL-series products in the live catalogue. Generated by cross-referencing
`reverse/product_live.json` with `reverse/firmware-sweep-live-products.json`:

| radioId | Product | paVer | BT image | MCU firmware |
|---|---|---|---|---|
| `000E` | SL100D | 0 | LK8620 | — |
| `000F` | SL100Bi | 0 | LK8620 | — |
| `0034` | SL300II | 0 | LK8620 | — |
| `0035` | SL300IIBi | 0 | LK8620 | — |
| `0039` | SL60IID | 0 | LK8620 | — |
| `003A` | SL60IIBi | 0 | LK8620 | — |
| `003B` | SL150III | 0 | LK8620 | — |
| `003C` | SL200III | 0 | LK8620 | — |
| `003D` | SL300III | 0 | LK8620 | — |
| `003E` | SL150IIIbi | 0 | LK8620 | — |
| `003F` | **SL200IIIBi** | 0 | LK8620 | — |
| `0040` | SL300IIIbi | 0 | LK8620 | — |
| `005C` | SL150R | 0 | LK8620 | — |
| `005D` | SL300R | 0 | LK8620 | — |
| `00A3` | SL300R | 0 | LK8620 | — |
| `00B5` | SL300 RF | 1 | LK8720 | **`SL300RF_V113.bin`** |
| `00B6` | SL200 RF | 1 | LK8720 | **`SL200RF_V113.bin`** |
| `00B7` | SL150 RF | 1 | LK8720 | — |
| `00D0` | SL300Bi | 0 | LK8620 | — |
| `00D1` | SL200Bi | 0 | LK8620 | — |

Three things this settles:

- **Bi vs daylight is irrelevant to firmware distribution.** Every pair shares
  one BT image and neither member has MCU firmware.
- **MCU availability tracks nothing systematic** — not generation, not
  `paVersion`, not even RF family: SL150 RF sits alongside SL200/SL300 RF on
  LK8720 and gets none. It is simply whichever models Godox shipped a fix for.
- **There is no SL200II.** Second-gen SL appears only as SL300II, SL300IIBi,
  SL60IID and SL60IIBi. Absence from this catalogue means "not a BLE-mesh
  product known to this app", not "does not exist".

Where Bi *does* matter is interchangeability. MCU firmware is calibrated to the
emitters:

| | Colour temp | colorChipVersion |
|---|---|---|
| SL200III | 5600 K fixed | 0 |
| SL200IIIBi | 2800–6500 K | 2 |

An MCU image is therefore **not** transferable between the two, despite the
shared BT firmware. Note also that "Bi" is not a reliable capability signal:
`SL200Bi` (`00D1`) reports 1800–10000 K, a full-colour range.

### Boundary note

Two avenues were deliberately **not** taken, because they mean fishing for
unpublished records rather than reading published ones:

- enumerating the opaque numeric IDs behind `/godox/api/doc/download/{id}`;
- probing the `/api.php/cms/...` CMS routes on godox.com for unlisted content.

Everything documented here came from public pages, a published JSON manifest, a
freely downloadable installer, and the app's own documented API with
well-formed queries.

### MCU images that pair with the SL200III Bi's own BT firmware

The most useful substitute for an unobtainable SL200III Bi MCU image is one
from a product running the **identical** BT firmware — same `paVersion`, so
the same `LK8620_MESH_GD_v000066` binary sits on the other side of the UART.
There are **16** such products with downloadable MCU firmware, all fetched to
`reverse/mcu_lk8620/`:

TP2R, TP4R, TP8R, TPC2R, TPC4R, TPC8R, MG1200R, LP400Bi, LP600Bi, LP1200Bi,
LP400R, LP600R, LP1200R, ML100R, ML80Bi, AD00-02.

All 16 are ARM Cortex-M and all 16 carry the Godox CRC8 table, so every one
contains a V2/V3 handler.

The **complete corpus is now downloaded** — all three BT images plus all 59 MCU
images, per-chip, with hashes, per-model verification and analysis in
[../reverse-artifacts/firmware-inventory.md](../reverse-artifacts/firmware-inventory.md):

| Directory | Contents |
|---|---|
| `reverse/bt-bins/` | 3 BT images (LK8620, LK8720, LK8728B) |
| `reverse/mcu_lk8620/` | 16 MCU images — same BT firmware as an SL200III Bi |
| `reverse/mcu_lk8720/` | 36 MCU images |
| `reverse/mcu_lk8728b/` | 7 MCU images |

**Capability match** to `003F`, scored across 12 `product.json` fields
(colour-temp range, colorChipVersion, rgb, rgbDisplay, modeType, effect count,
fanGear, powerType, batteryReturnType, gmVersion, paVersion, effectVersion):

| Candidate | Score | Differs on |
|---|---|---|
| **UL150Bi II** (`001D`, already in repo) | **11/12** | `colorChipVersion` only |
| ML80Bi (`0097`) | 9/12 | rgbDisplay, effect count, effectVersion |
| LP400Bi / LP600Bi / LP1200Bi | 7/12 | + powerType, batteryReturnType |
| SL200 RF (`00B6`) | 4/12 | nearly everything |

Worth recording: the UL150Bi II image is itself `paVersion` 0, so the analysis
in [state-readback-investigation.md](state-readback-investigation.md) §10 was
done against a binary that pairs with the *same* BT firmware an SL200III Bi
runs, and whose capability profile differs in one field. That is a stronger
basis than "same protocol family".

**Cleanest to disassemble** — 8 images carry a contiguous, verified 256-byte
table: MG1200R, LP400Bi, LP600Bi, LP1200Bi, LP400R, LP600R, LP1200R, and
UL150Bi II. Of those, LP400Bi and LP600Bi are byte-identical 138,624-byte
images, bi-colour 2800–6500 K with `colorChipVersion` 2 — a third the size of
the UL150Bi II binary and the easiest starting point for new work. ML80Bi is
the closest-matching *newer* image (`effectVersion` 1, 11 effects vs 7), so it
is where protocol growth in the effect sub-commands would show up.

### The CRC8 table is correct — and 9 images have a one-byte anomaly

`GODOX_CRC8_TABLE` in `src/godox_mesh_bt/crypto.py` was validated by
generating tables for all 256 polynomials in both bit orders. It is an exact
256/256 match for **reflected CRC-8, polynomial `0x8C`** (Dallas/Maxim
1-Wire). Entry 255 = `0x35` is correct.

Searching the 17 MCU images for the table gives two groups:

- **8 images** contain the full contiguous 256 bytes (listed above).
- **9 images** match entries 0–254 exactly, then have **one foreign byte**
  before the final `0x35`:

  ```
  reference    … 0a 54 d7 89 6b 35
  group B      … 0a 54 d7 89 6b 22 35 …
  ```

  The inserted byte varies by image — `0x22` (TP/TPC/ML80Bi), `0x52`
  (ML100R), `0x82` (AD00-02) — and `0x35` always follows it. No polynomial in
  either bit order yields entries 0–254 of poly-`0x8C` with any of those at
  index 255, so the 256-byte window is not a valid table and the odd byte is
  data, not table content.

**Cause unresolved.** What is established: the table is present in all 17
images, so a hit on the 255-byte prefix is the reliable detection test, and the
library's own table is not in question. Prefer a group-A image when the exact
array layout matters.

### Verifying an image is a main-MCU binary

Two cheap checks, both used above:

1. **Vector table.** First two little-endian words should be an initial SP in
   SRAM (`0x2000_0000`+) and a reset vector in flash (`0x0800_0000`+).
2. **Godox CRC8 table.** Search for the **first 255 bytes** of
   `GODOX_CRC8_TABLE` from `src/godox_mesh_bt/crypto.py` (starts
   `005ebce2613fdd83`). A hit places a V2/V3 protocol handler in the binary.
   Match the 255-byte prefix, not the full 256 — 9 of 17 images tested have a
   foreign byte before the last entry and a full-table search misses them
   entirely. See [the one-byte
   anomaly](#the-crc8-table-is-correct--and-9-images-have-a-one-byte-anomaly).

Validated against the known-good image: the UL150Bi II bin returns `0xe58c`,
matching the offset recorded independently in
[state-readback-investigation.md](state-readback-investigation.md) section 10.

| Image | Cortex-M | CRC8 table |
|---|---|---|
| UL150Bi II `V1.02` (support bundle) | yes | `0xe58c` |
| `SL200RF_V113.bin` (API, wrong model) | yes | `0x44870` |
| `SL300RF_V113.bin` (API, wrong model) | yes | `0x44884` |
| `LK8620_MESH_GD_v000066` (API, SL200III Bi) | no — TC32 | `0x21bc4` |

## Reusable facts

- `radioId` is the stable model key across both `product.json` and the firmware
  API. `003F` = SL200III Bi.
- `product.json` in the APK is a full offline product database: colour-temp
  ranges, effect lists and gears, fan gears, battery types, `paVersion`, and
  the `hasBtFirmware`/`hasMcuFirmware` flags — 212 products, all without a
  network call. Refreshed at runtime from
  `/godox/api/productV2/getProduct`.
- Everything served here is a `_beta` build.

## Follow-up: the BT image was disassembled

This document reasoned about the LK8620 image from its strings and structure,
and reached two conclusions that a full TC32 disassembly later refined. See
[bt-chip-firmware.md](bt-chip-firmware.md) for the disassembly; the corrections:

- **"A dumb pipe" is true for control, not for status.** The BT chip forwards
  `0xF0/0xF3/0xF4/0xF5/0xFE` to the MCU, but it *terminates* status request
  `0xFD` (and `0xFC`) locally and answers from a RAM cache seeded with flash
  defaults. One generic image still serves 114 lights; it simply is not purely
  a pipe. (`10% @ 2700K` is that flash default — what a factory-reset light
  returns, or what any light returns when the request's end byte is padded to
  `0xFF` rather than selecting record `0xA0`. Selecting `0xA0` returns live
  data, so the local answer is not the barrier it was taken for.)

- **The direct-GATT OTA characteristics are 128-bit.** They are
  `00010203-0405-0607-0809-0A0B0C0D1912` (service) and `…2B12` (data), both
  present in the LK8620 image (little-endian at `0x21d64` / `0x21d28`). The
  `0x7FDD/0x7FDE/0x7FDF` UUIDs are an alternative container for the *mesh*
  services, not OTA. This matches the app-side finding in the OTA section
  above.

- **The MCU flash bases are non-zero.** UL150Bi II is linked at `0x08007000`,
  the LP family at `0x0800c000`. `flash_base + file_offset` is the true
  address; the CRC8-table offsets tabled above (`0xe58c` etc.) are file
  offsets, and resolve against those bases.

The practical upshot for this document's central question — obtaining live
state from an SL200III Bi — is that **no firmware change is needed**: select
record `0xA0` and stock firmware answers with live brightness and commanded
colour temperature. The ~26-byte BLE patch that
[bt-chip-firmware.md](bt-chip-firmware.md) proposes was built and flashed, and
delivered nothing, because the one remaining gap is in the MCU image this API
does not serve.
