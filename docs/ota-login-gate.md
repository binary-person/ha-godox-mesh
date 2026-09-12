# Why OTA writes are silently discarded (and how to flash successfully)

Flashing a Godox mesh light over its Telink OTA characteristic fails **silently**
unless the connection has already carried a mesh **Network PDU**. The whole
packet stream is accepted at the GATT layer, the light never reboots, and
nothing reports an error.

This was found the hard way and then fixed and verified on real hardware.

## The symptom

A first flash attempt against an SL200III Bi wrote all 9294 data packets with no
GATT error, sent the end command, and printed success. The light:

- still reported BLE firmware version `0x42` (unchanged),
- still held its previous RAM status cache byte-for-byte (so it never rebooted),
- worked normally on its original firmware.

The Godox app has the same blind spot: it reports OTA success on write-success,
write-error *and* timeout, so a rejected image is indistinguishable from an
accepted one. Anything built from the app's flow inherits that.

## The cause

The OTA characteristic's write callback is gated on a RAM flag. From the LK8620
image (`0x21f20` is its attribute-table entry, write callback `0x077a4`):

```
077a4  tpush {r4, r5, r6, r7, lr}
...
077b0  tjl  0x0d954         ; -> returns the byte at RAM 0x848804
077b4  tcmp r0, #0
077b6  tjeq.n 0x077ec       ; zero -> return immediately, write discarded
```

and the check itself is three instructions:

```
0d954  tloadr  r3, [pc, #4]   ; = 0x848804
0d956  tloadrb r0, [r3, #0]
0d958  tjex    lr
```

That flag is set by the write callback of the **Mesh Proxy Data In**
characteristic (`0x2ADD`, attribute entry `0x220eb`, callback `0x07259`). Its
parser at `0x0715c` switches on the Proxy PDU type field (`byte & 0x3F`):

| Proxy PDU type | Branch | Sets the OTA gate? |
|---|---|---|
| **0 — Network PDU** | `0x0718e` | **yes**, `0x848804 = 1` |
| 1 — Mesh Beacon | `0x071c6` | no |
| 2 — Proxy Configuration | `0x07208` | no |

The flag is cleared again on disconnect (`0x0db8c`).

So a proxy *handshake* alone is not enough: a mesh beacon and proxy-filter
configuration are types 1 and 2. It takes a real Network PDU — any ordinary mesh
message — to open the gate.

This is exactly what the Godox app does, and why it works: `MeshController`
starts the GATT OTA from `onProxyLoginSuccess()`, so a mesh session is already
live on that connection.

## The fix

Flash on a mesh-logged-in connection:

1. connect and complete the proxy handshake,
2. send one real mesh message (a status request will do),
3. write the OTA stream to the OTA characteristic **on that same connection**.

In this repository:

- `tools/lk8620/flash_ota.py` does this, and is the version that works.
- The integration routes flashing through `GodoxMeshLink.async_flash_firmware`,
  which sends a Network PDU and then calls
  `flash.async_write_ota_stream` on the live link — it deliberately does **not**
  open a bare connection of its own.

## Verified on hardware

Flashing the stock `v000066` image to an SL200III Bi that was running `0x42`:

| | Before | After |
|---|---|---|
| Reported BLE version | 66 (`0x42`) | **102 (`0x66`)** |
| `A0` status cache | `a0141e320000006e` (last commanded) | `a00a1b32ffff01f9` (the flash default) |
| Mesh provisioning | intact | intact |

The version changed and the RAM cache reset to the firmware's boot-time default,
which together prove the light actually rebooted into the written image. Control
and readback behaved identically afterwards (three commanded values read back
exactly), and panel behaviour was unchanged — live brightness, placeholder
colour temperature (see
[readback-hardware-findings.md](readback-hardware-findings.md)).

## Lessons worth keeping

**Never trust an OTA that cannot fail.** Verify by reading something back that
must change — the reported version, and ideally a RAM value that resets on
reboot. This repository's version probe (`request_version`) exists for that, and
it is what caught the silent failure.

**Flash a known-good stock image before a modified one.** Doing so separated two
questions that would otherwise have been confounded: "is my OTA implementation
correct?" and "is my patched image correct?". Had the patched image been flashed
first, it would have failed identically and the absent behaviour change would
have been misread as "the patch does not work".
