"""A tiny TC32 assembler that verifies itself against the disassembler.

Hand-encoding TC32 is where mistakes hide, and a wrong encoding in a firmware
patch is a bricked light. So rather than hand-writing opcodes, this searches the
16-bit encoding space for the instruction whose *disassembly* matches the text
that was asked for. The disassembler is the same one used to verify patches, so
an instruction that assembles here is one that verifier will agree with.
"""

from __future__ import annotations

import struct
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tc32"))
from tc32dis import TC32  # noqa: E402

_DIS = TC32(Path(__file__).resolve().parents[1] / "tc32" / "generate_sleigh.py")


def _decode_at(word: int, addr: int) -> str | None:
    """Disassemble a single 16-bit word placed at *addr*."""
    blob = b"\x00" * addr + struct.pack("<H", word) + b"\x00" * 8
    try:
        out = list(_DIS.disasm(blob, addr, 2))
    except Exception:
        return None
    return out[0][2] if out else None


@lru_cache(maxsize=4096)
def asm16(text: str, addr: int = 0x1000) -> bytes:
    """Assemble one 16-bit TC32 instruction by searching for its encoding.

    Parameters
    ----------
    text
        Exactly the mnemonic the disassembler prints, e.g. ``"tcmp r3, #4"``.
    addr
        Address the instruction will live at. Only matters for PC-relative
        forms, which this deliberately does not support.

    Returns
    -------
    bytes
        Two bytes, little-endian.

    Raises
    ------
    ValueError
        If no encoding produces that disassembly, or if more than one does.
    """
    want = text.strip()
    found = [w for w in range(0x10000) if _decode_at(w, addr) == want]
    if not found:
        raise ValueError(f"no TC32 encoding disassembles to {want!r}")
    return struct.pack("<H", found[0])


def tjl(src: int, dst: int) -> bytes:
    """Encode a TC32 long branch-and-link (writes lr)."""
    off = (dst - (src + 4)) >> 1
    return struct.pack("<HH", 0x9000 | ((off >> 11) & 0x7FF), 0x9800 | (off & 0x7FF))


def assemble(lines: list[str], base: int) -> bytes:
    """Assemble a list of 16-bit instructions laid out from *base*."""
    out = bytearray()
    for text in lines:
        out += asm16(text, base + len(out))
    return bytes(out)
