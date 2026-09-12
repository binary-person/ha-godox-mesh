# Reading state back from a Godox mesh light

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

**Superseded in part.** This began as an investigation into whether these
lights can be read at all, and concluded twice that they cannot. Both
conclusions were wrong. Firmware analysis eventually found a status request —
V2 sub-command `0xFD`, answered on vendor opcode `0x0211F1` — which a real
light does respond to.

**The protocol as now understood is documented in
[protocol.md](protocol.md).** Read that first; this file is kept as the record
of how it was found, what was ruled out along the way, and which traps are
worth not repeating.

**This document's central conclusion was wrong, and the correction is in
[readback-hardware-findings.md](readback-hardware-findings.md).** What follows
is kept as the record of how the investigation went, including the wrong turn.

The short version of the correction: the reply was "a constant" because the
request was built with its end byte padded to `0xFF`. That byte *selects which
record the light reports*. Ask for record `0xA0` and stock firmware returns
live brightness — including changes made on the light's own panel — and live
colour temperature for anything commanded over the mesh. Only panel-changed
colour temperature is missing, on some models, and a BLE-firmware patch was
later flashed to a real light and did not recover it.

The short version of where it landed:

| Question | Answer |
|---|---|
| Do the standard SIG models work? | They answer, but are wired to nothing — a shadow state |
| Does the vendor model answer commands? | No. Commands are fire-and-forget |
| Is there a status request? | **Yes** — `0xFD`, replying on `0x0211F1` |
| Does the reply carry live values? | **Yes**, when the end byte selects record `0xA0` — see [readback-hardware-findings.md](readback-hardware-findings.md) |
| Can you poll usefully today? | **Yes, on stock firmware.** Brightness and commanded colour temperature both read back |

The two wrong turns are worth naming, because both came from stopping at the
first negative result:

1. *"The light never responds."* It responds readily — only the vendor model's
   **command** opcode is silent. Nothing had been sent that asks a question.
2. *"The standard models prove there is no readback."* They proved only that
   *those* models are stubs.

## 1. Test setup

| | |
|---|---|
| Device | Godox SL200III Bi |
| Host | macOS, `bleak` over CoreBluetooth |
| Address | CoreBluetooth UUID, **not** a MAC (macOS-specific; Home Assistant sees real MACs) |
| Provisioning | This library's own CLI (`godox-mesh provision`) |
| Advertised name | `GD_LED` |
| Advertised service | Mesh Proxy `0x1828` |
| Proxy service data | `00` + 8 bytes = `k3(net_key)` (Network ID), verified to match |

Advertising is **bursty**. Three separate 15-second scans saw nothing; a scan
immediately after power-cycling caught it at once. Do not conclude a node is
absent from one short scan.

---

## 2. Where it landed

| Path | Drives the LED? | Answers? | State meaningful? |
|---|---|---|---|
| Godox vendor model, command opcode `0x0211F0` | **Yes** | No — fire and forget | n/a |
| Godox vendor model, **status request `0xFD`** | n/a | **Yes**, on `0x0211F1` | live on the firmware analysed; constant on an SL200III Bi |
| Standard SIG models (OnOff, Lightness, CTL) | **No** | Yes | **No — shadow state** |

The light also pushes unsolicited notifications when its own controls are used;
see [protocol.md](protocol.md).

## 3. The vendor model

From this repository plus [ndricjaho/godox-mesh-ha](https://github.com/ndricjaho/godox-mesh-ha)'s
`PROTOCOL.md`, which came from decompiling `com.godox.agm.GodoxCommandApi`.

```
Company ID          0x0211  (Telink Semiconductor, SIG-assigned)
Model ID            0x0000
Full vendor model   0x02110000
Access opcode       0x00F011  -> wire bytes F0 11 02   (the ONLY outgoing opcode)
```

**V2 framing — fixed 8 bytes** (on/off, HSI, CCT):
```
[0] sub-command   [1..5] five data bytes (0xFF padded)   [6] end byte   [7] CRC8 over [0..6]
```

**V3 framing — variable length** (effects):
```
[0] sub-command   [1] length = len(data)+3   [2..N+1] data   [N+2] CRC8
```

CRC8 is a 256-entry table lifted from `com.godox.agm.CRC8Util`; this library's
`crypto.checksum()` already matches it (V2 commands work on hardware).

**V2 sub-commands:**

| Byte | Meaning | Notes |
|---|---|---|
| `0xFE` | On/Off | **Polarity reversed**: `0x00` = on, `0x01` = off |
| `0xF1` | HSI colour | also collides with reply-opcode `0xF1` at the outer layer |
| `0xF0` | CCT | what this library uses |
| `0xF2` | RGBW | untraced |
| `0xF3` | `changeLightFX` | untraced |
| `0xF4` | `changeLightCard` | untraced |
| `0xF5` | `changeElectricFan` | other product line |
| `0xFC` | `changeBrightnessOffset` | deprecated, no effect |

**There is no GET, status, query or telemetry sub-command.** `0xF1` exists in
the app as the *expected reply* opcode for a request-response exchange, but
nothing ever sends such a request and the light never answers on it —
confirmed independently on a TL120 and on the SL200III here.

---

## 4. Everything queried, with raw replies

Decrypted with the app key (models) or device key (Config Server).

### Answered

| Query | Opcode | Raw reply | Reading |
|---|---|---|---|
| Generic OnOff Get | `8201` | `820401` | ON |
| Generic Level Get | `8205` | `8208ff7f` | `0x7fff` |
| Light Lightness Get | `824B` | `824effff` | 65535 |
| Light CTL Get | `825D` | `8260ffff204e` | lightness 65535, temp 20000 K |
| Light CTL Temperature Range Get | `8262` | `8263002003204e` | 800–20000 K |
| Generic Power OnOff Get | `8211` | `821201` | OnPowerUp = 1 |
| Generic Default Transition Get | `820D` | `821041` | default transition |
| Config Default TTL Get | `800C` | `800e0a` | TTL 10 |
| Config Relay Get | `8026` | `80280105` | relay enabled |
| Config Beacon Get | `8009` | `800b01` | beacon enabled |
| Health Attention Get | `8004` | `800700` | timer 0 |
| Health Period Get | `8034` | `803700` | divisor 0 |
| Health Fault Get | `8031` + `1102` | `05001102` | company `0x0211`, no faults |

### Silent — no reply at all

`Sensor Descriptor Get 8230` · `Sensor Get 8231` · `Sensor Cadence Get 8234` ·
**`Generic Battery Get 8223`** · `Generic Power Level Get 8215` ·
`Scene Get 8241` · `Scene Register Get 8244` · `Light HSL Get 826D` ·
`Light Lightness Range Get 824D` · `Light CTL Default Get 8263` ·
`Light LC Mode Get 8291`

**Sensor Server and Generic Battery Server are the two standard models that
carry real hardware readings, and neither is implemented.** They are also the
only models that could not have been shadowed, because nothing else writes
them.

Note the tell in what *did* answer: CTL Temperature Range reports the
specification's full 800–20000 K, not this light's actual 2800–6500 K. Even
the range is boilerplate.

---

## 5. Proof the standard models are a shadow

**Vendor writes do not update model state:**

```
vendor set_params(brightness=20)  ->  Light Lightness Get still reads 65535
vendor set_params(cct=3000)       ->  Light CTL Get still reads temp 20000
vendor power_off()                ->  Generic OnOff Get still reads ON
```

**Standard writes do update model state, exactly:**

```
Light Lightness Set 0x8000        ->  reads back 32768
Light CTL Set 0x8000 / 3000K      ->  reads back (32768, 3000)
Generic OnOff Set OFF             ->  reads back OFF, lightness 0
```

**But standard writes do not move the LED.** Five isolated steps, ~8 s apart,
light watched throughout:

| Step | Command | LED |
|---|---|---|
| 1 | standard Generic OnOff Set OFF | no change |
| 2 | standard Generic OnOff Set ON | no change |
| 3 | standard Light Lightness Set 10% | no change |
| 4 | standard Light CTL Set 100% / 2800 K | no change |
| 5 | **vendor** 20% / 6500 K | changed — dimmer, cooler |
| — | **vendor** restore 100% / 5600 K | changed — brighter, warmer |

---

## 6. Listening for unsolicited traffic

Roughly 75 seconds across four windows: passive after connecting, after a
known-good V2 brightness command, after a vendor probe, and while the light's
own physical controls were being operated.

**Zero frames.**

Qualify this: firmware analysis (section 10) later showed the light *does* emit
status frames when its own controls are operated. It was never confirmed that
the physical controls were actually touched during the window above, so this
should be read as "nothing arrived unprompted while commands were being sent",
not as proof that the light never publishes.

(The Secure Network Beacon is consumed inside `GodoxController.connect()`
before a caller can attach a listener, so it is outside that count. Register
extra notification callbacks *after* `connect()` returns.)

---

## 7. How to decode inbound PDUs

All primitives already exist in `godox_mesh_bt.crypto`; no new code needed.

```python
nid, enc_key, priv_key = k2(bytes.fromhex(state.network_key))

deobf = deobfuscate(pdu[1:], priv_key, state.iv_index)   # strip the proxy SAR byte
seq = int.from_bytes(deobf[2:5], "big")
src = int.from_bytes(deobf[5:7], "big")
dst, encrypted_access = decrypt_network_pdu(deobf, enc_key, state.iv_index)

# model messages:
decrypt_access_pdu(encrypted_access, app_key, iv_index, seq, src, dst)
# Config Server messages:
decrypt_device_key_pdu(encrypted_access, device_key, iv_index, seq, src, dst)
```

For replies to reach you at all, the proxy filter must whitelist the
provisioner address. `GodoxController.connect()` already does this.

---

## 8. Two mistakes worth not repeating

**`0xA6` is not a query.** This library has `parse_battery_power_response`,
which parses a `0xA6` *reply*. That was read here as evidence of a battery
request. It is not — a response parser implies nothing about a request, `0xA6`
appears nowhere in the app's sub-command table, and V3 framing carries
**effects** with sub-commands passed dynamically.

Sending `a6040142` (sub-command `0xA6`, valid CRC) **set an unwanted effect on
a real light**, which needed a re-provision to clear. In this protocol a
well-formed frame in the effect space is a command, not a question.

**Do not sweep the sub-command space.** There are no acknowledgements, so every
probe is a valid command to *something* and nothing tells you what you just
did. A single accidental frame already set an effect; 512 would be reckless.

---

## 9. Rebuilding the probe

The throwaway scripts used here are not kept — the investigation is closed and
a script that can misconfigure a light is a poor thing to ship. Everything
needed to rewrite one is above; it is perhaps forty lines.

A probe needs four things:

1. **Connect and set up the proxy filter.** `GodoxController.connect()` does
   both, including the beacon echo. Register your notification callback
   *afterwards*, or the controller will have consumed the beacon already.

   ```python
   controller = GodoxController(address, state_path)
   await controller.connect()
   await controller._client.start_notify(your_callback)
   ```

2. **Send access payloads.** Standard models and Config Server messages are
   just their opcode plus parameters, wrapped by `pack_proxy_network_pdu`
   (app key) or `pack_proxy_device_key_pdu` (device key). Advance the sequence
   number after every write via `controller._advance_state()` — a reused
   sequence number is silently dropped by the node's replay list.

3. **Decode replies** with the recipe in section 7.

4. **Power-cycle the light first**, to catch the advertising burst and to free
   its single proxy connection.

Do not sweep the vendor sub-command space; see section 8.
## 10. Evidence from the firmware itself

A firmware image for the **UL150Bi II** (`V1.02`, 447 KB) settles the vendor
protocol question from the inside. Different model from the SL200III probed
above, same protocol family.

This image came from a Godox support bundle (`reverse/1729857629339846.zip`).
The equivalent for an SL200III Bi **cannot be obtained** — Godox's firmware API
serves that model only a generic Telink BLE-SoC image shared with 111 other
products, and no SL- or UL-series product is flagged as having distributable
MCU firmware at all. See [firmware-api.md](firmware-api.md), which also
explains why the constant-status behaviour below is not reachable that way.

The image is ARM Cortex-M, flash base `0x08000000` — the light's **main MCU**,
not the Telink BLE chip. The Godox CRC8 table is embedded at file `0xe58c`,
byte-identical to the one in this library, which places the V2/V3 protocol
handler in this binary. The framing is a UART link between the BLE module and
the main MCU, tunnelled over the mesh vendor model.

### The receive dispatch is complete and contains no query

The V2 validator sits at `0x08003c5a`:

```
cmp   r0, #7            ; frame index 7
movs  r1, #7
bl    0x080008e4        ; CRC8 over bytes 0..6
ldrb  r1, [r1, #7]
cmp   r0, r1            ; compare with byte 7
```

`0x080008e4` is `crc = table[crc ^ byte]`, matching this library exactly. The
sub-command dispatch immediately after handles precisely:

| Sub-command | Handler |
|---|---|
| `0xE0` | acknowledgement (see below) |
| `0xF0` | CCT — clamps brightness ≤ `0x64` and temperature `0x1c`–`0x41`, i.e. 100 % and 2800–6500 K |
| `0xF3` `0xF4` `0xF5` `0xFA` `0xFD` | effects and other setters |
| `0xFE` | on/off |

`0xE0`, `0xFA` and `0xFD` do not appear in the app-derived table in section 3.
`0xA6` is tested nowhere — its single appearance in the image is `movs r0,#0xa6`
as a screen-drawing coordinate, which is further confirmation that reading it
as a battery query was wrong.

**No sub-command causes the firmware to answer.** There is no GET.

### The light does push status upstream, unprompted

Three outbound frame builders exist. Two are fixed notifications:

```
movs r0,#0xb0 ; strb [buf]       ; byte0 = 0xB0
movs r0,#0xa0 ; strb [buf,#1]    ; byte1 = 0xA0   (the other builder: 0xA2)
movs r0,#0xff ; strb [buf,#2..6]
bl   CRC8(buf,7) ; strb [buf,#7]
loop 8x: TX byte over UART
```

They are gated on retry counters in RAM — `0x200000be` for `B0 A0`, set to
**3**, and `0x200000b8` for `B0 A2`, set to **20**. The frame is retransmitted
on a timer until the host acknowledges with `E0`, whose handler clears the
counter:

```
ldrb r0,[rx+1] ; cmp r0,#0xa2 ; bne skip
movs r0,#0 ; strb r0,[0x200000b8]     ; clear the retry counter
ldrb [rx+3] -> 0x200000aa             ; the ack carries two bytes back
ldrb [rx+2] -> 0x200000ab
```

The counters are set only from **local UI code** — panel interaction and mode
changes — never from the receive dispatch. Confirmed by scanning the whole
dispatch range: the only reference to either counter there is the `0xE0` clear.

### What this settles, and what it does not

**Polling is impossible.** The complete receive dispatch is enumerated above
and contains no query of any kind. Nothing an external client sends can make
the light report. This is now firmware evidence rather than inference from the
app decompile.

**Passive listening is unresolved.** The light genuinely emits status frames
when its own controls are used — that is more than section 6 concluded. What
is unknown is whether the Telink BLE module bridges those UART frames onto the
mesh as vendor messages. That firmware was not available.

Section 6 recorded zero unsolicited frames including a window for operating the
light's physical controls, but it was never confirmed that the controls were
actually touched during it. That window should not be treated as a negative
result.

**How to settle it:** connect, listen, and physically change the light with its
own knob or buttons — nothing sent from the client. If `B0 A0` or `B0 A2`
appears as a vendor message, passive state tracking is possible for
locally-made changes. If nothing arrives, the BLE module does not bridge them
and the matter is closed.

Note this would only ever report changes made *at the light*. Commands sent
over the mesh would still need optimistic state, so `assumed_state` stays
correct either way.

## 11. What is actually left

Ordered by cost.

1. **Check whether the Godox app displays any device state at all.** One
   minute of work. If the app shows no battery, no runtime, no status, then
   there is nothing to find and the question is closed for good.

2. **Segmentation reassembly.** `Config Composition Data Get` (`8008`) would
   enumerate every element and model on the node — ndricjaho reports roughly 13
   — which would prove no model was missed. Its reply spans multiple segments
   and this library cannot reassemble them, so it currently cannot be read.
   Short single-segment Status messages decode fine, which is why everything in
   section 4 worked. This is the one real gap in coverage.

3. **Capture the app.** HCI snoop on Android, then `godox-parse-capture` in
   this repo. This is how the vendor protocol was reverse-engineered in the
   first place. Only worth doing if step 1 says the app shows something.

4. **Try a different model.** `parse_battery_power_response` came from
   somewhere. Battery-capable models — the UL60Bi Lite, for instance — may
   expose something the mains-powered SL200III does not. Nothing here rules
   that out; the finding above is specific to hardware tested.

---

## 12. Sources

- Bench probing of an SL200III Bi, recorded above.
- [ndricjaho/godox-mesh-ha](https://github.com/ndricjaho/godox-mesh-ha) —
  independent work on Godox **TL-series RGB** lights, via HCI snoop plus
  `apktool` decompile of the Godox Android app. Reached the same conclusion by
  a different method on different hardware: "No real status feedback… the
  standard SIG models are present for certification but don't control the
  light."

Same conclusion, two models, two methods. Treat the absence of telemetry as
settled for Telink-based Godox lights generally, not just this one.
