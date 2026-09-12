"""A TC32 disassembler.

Telink's TC32 core is Thumb-*like* but uses its own instruction encodings, so
an ARM Thumb disassembler produces nonsense. The opcode table below is parsed
out of generate_sleigh.py in trust1995/Ghidra_TELink_TC32, where Ryan Govostes
recovered it from Telink's own tc32-elf-objdump binary.

Each pattern is (width, value, mask, assembler-format), and the format strings
use binutils arm-dis.c control codes: %0-2r is a register in bits 0..2, %0-7W a
word-scaled immediate, %0-10B a branch displacement, and so on.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

COND = ["eq","ne","cs","cc","mi","pl","vs","vc","hi","ls","ge","lt","gt","le","","nv"]
REG = [f"r{i}" for i in range(8)] + ["r8","r9","r10","r11","r12","sp","lr","pc"]


def load_table(path: Path) -> list[tuple[int, int, int, str]]:
    body = re.search(r"^insns = \[(.*?)^\]", path.read_text(), re.S | re.M).group(1)
    out = []
    for w, v, m, fmt in re.findall(
        r"\(\s*(16|32)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*'((?:[^'\\]|\\.)*)'",
        body,
    ):
        out.append((int(w), int(v, 16), int(m, 16), fmt))
    out.extend(EXTRA)
    # Longer masks first so specific encodings win over general ones.
    out.sort(key=lambda r: (-bin(r[2]).count("1"), r[0]))
    return out


# Encodings absent from the recovered table. The register-offset load/store
# block is 0x1000-0x1fff in steps of 0x200; the table carries the halfword and
# sign-extended forms but not the word/byte ones. Confirmed against the CRC8
# routine, where 0x1c8a must be `crc = table[crc ^ byte]`: it decodes under
# 0x1c00/0xfe00 as tloadrb r2, [r1, r2], with r1 the table base.
EXTRA: list[tuple[int, int, int, str]] = [
    (16, 0x1000, 0xFE00, "tstorer%c %0-2r, [%3-5r, %6-8r]"),
    (16, 0x1400, 0xFE00, "tstorerb%c %0-2r, [%3-5r, %6-8r]"),
    (16, 0x1800, 0xFE00, "tloadr%c %0-2r, [%3-5r, %6-8r]"),
    (16, 0x1C00, 0xFE00, "tloadrb%c %0-2r, [%3-5r, %6-8r]"),
]


def _bits(word: int, lo: int, hi: int) -> int:
    return (word >> lo) & ((1 << (hi - lo + 1)) - 1)


def _signed(value: int, width: int) -> int:
    return value - (1 << width) if value & (1 << (width - 1)) else value


def _reglist(word: int, kind: str) -> str:
    regs = [f"r{i}" for i in range(8) if word & (1 << i)]
    if kind == "N" and word & 0x100:      # push variant includes lr
        regs.append("lr")
    if kind == "O" and word & 0x100:      # pop variant includes pc
        regs.append("pc")
    return "{" + ", ".join(regs) + "}"


def render(fmt: str, word: int, addr: int, width: int) -> str:
    fmt = fmt.replace("\\\\t", " ").replace("\\t", " ")
    out = fmt

    # ranged fields: %<lo>-<hi><code>
    def ranged(mo: re.Match) -> str:
        lo, hi, code = int(mo.group(1)), int(mo.group(2)), mo.group(3)
        raw = _bits(word, lo, hi)
        n = hi - lo + 1
        if code == "r":
            return REG[raw] if raw < 16 else f"r{raw}"
        if code == "c":
            return COND[raw] if raw < 16 else ""
        if code in "dx":
            return str(raw)
        if code == "W":
            return str(raw * 4)
        if code == "H":
            return str(raw * 2)
        if code == "a":
            return f"0x{((addr + 4) & ~3) + raw * 4:05x}"
        if code == "B":
            # branch displacement, signed, doubled before applying to PC
            return f"0x{addr + 4 + _signed(raw, n) * 2:05x}"
        return str(raw)

    out = re.sub(r"%(\d+)-(\d+)([A-Za-z])", ranged, out)

    # whole-word codes
    if "%B" in out and width == 32:
        hi, lo = word >> 16, word & 0xFFFF
        off = ((hi & 0x7FF) << 11) | (lo & 0x7FF)
        out = out.replace("%B", f"0x{addr + 4 + _signed(off, 22) * 2:05x}")
    # Hi-register forms: D = bits0-2 | bit7<<3, S = bits3-5 | bit6<<3
    out = out.replace("%D", REG[_bits(word, 0, 2) | (_bits(word, 7, 7) << 3)])
    out = out.replace("%S", REG[_bits(word, 3, 5) | (_bits(word, 6, 6) << 3)])
    for k in "NOM":
        if f"%{k}" in out:
            out = out.replace(f"%{k}", _reglist(word, k))
    out = out.replace("%C", "").replace("%c", "")
    out = re.sub(r"%[xXs]", "", out)
    out = re.sub(r";.*$", "", out)
    return " ".join(out.split())


class TC32:
    def __init__(self, table_path: Path) -> None:
        self.table = load_table(table_path)

    def decode(self, data: bytes, off: int, addr: int):
        if off + 2 > len(data):
            return None, 0
        hw = struct.unpack_from("<H", data, off)[0]
        if off + 4 <= len(data):
            word32 = (hw << 16) | struct.unpack_from("<H", data, off + 2)[0]
            for w, v, m, fmt in self.table:
                if w == 32 and (word32 & m) == v:
                    return render(fmt, word32, addr, 32), 4
        for w, v, m, fmt in self.table:
            if w == 16 and (hw & m) == v:
                return render(fmt, hw, addr, 16), 2
        return f".hword 0x{hw:04x}", 2

    def disasm(self, data: bytes, start: int, length: int, base: int = 0):
        off, end = start, min(start + length, len(data))
        while off < end:
            text, size = self.decode(data, off, base + off)
            yield base + off, data[off:off+size].hex(), text or "?"
            off += size or 2


if __name__ == "__main__":
    import sys
    here = Path(__file__).parent
    dis = TC32(here / "generate_sleigh.py")
    img = Path(sys.argv[1]).read_bytes()
    start = int(sys.argv[2], 0)
    length = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x80
    for addr, raw, text in dis.disasm(img, start, length):
        print(f"  {addr:06x}  {raw:<8}  {text}")
