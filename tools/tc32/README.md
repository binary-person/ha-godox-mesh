# TC32 disassembler

A small disassembler for Telink's TC32 core (the ISA used by the LK8620 and
other TLSR825x BLE-mesh SoCs). Written for the analysis in
[../../docs/bt-chip-firmware.md](../../docs/bt-chip-firmware.md), because
standard ARM Thumb disassemblers produce garbage on TC32 — the mnemonics and
encodings differ (`tand` is `0x0000/0xffc0` where Thumb's AND is `0x4000`).

## Opcode table

`tc32dis.py` parses its instruction table at runtime from `generate_sleigh.py`,
which is **not vendored here** — it belongs to
[trust1995/Ghidra_TELink_TC32](https://github.com/trust1995/Ghidra_TELink_TC32),
where Ryan Govostes recovered the table from Telink's own `tc32-elf-objdump`.
Fetch it next to the script:

```bash
curl -sL https://raw.githubusercontent.com/trust1995/Ghidra_TELink_TC32/master/generate_sleigh.py \
  -o generate_sleigh.py
```

Four register-offset load/store encodings (`0x1000/0x1400/0x1800/0x1c00`,
mask `0xfe00`) are missing from that recovered table and are added by
`tc32dis.py` itself (`EXTRA`), deduced by symmetry and verified against the
CRC8 routine where `0x1c8a` must be `crc = table[crc ^ byte]`.

## Use

```bash
python tc32dis.py <image.bin> <start-offset> [length]
# e.g.  python tc32dis.py LK8620_MESH_GD_v000066_20250403_beta.bin 0x02ec0 0x20
```

Coverage on the LK8620 image is ~99.8% of the code region; the remainder is
literal-pool data interleaved with code, not missing instructions.

## Limits

Disassembly only — no p-code, no decompilation, no assembler. The table is not
a complete ISA model; it was built for reading control flow and data access in
one specific firmware family, and it is accurate enough for that. Verify
anything load-bearing by hand.
