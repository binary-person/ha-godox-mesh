"""The colour-block effects send the vendor app's full header, blocks or not."""

from __future__ import annotations

import pytest
from godox_mesh_bt.protocol import (
    ColorBlock,
    build_fx_color_block_command,
    build_fx_command,
)


@pytest.mark.parametrize(
    ("symbol", "name", "data_bytes"),
    [(0, "RGB Fade", 17), (1, "RGB Flow", 13), (2, "RGB Chase", 13)],
)
def test_header_is_sent_at_full_width_with_no_blocks(symbol, name, data_bytes):
    """The app builds the whole header, then sends it even if the list is empty.

    Deriving these from the flat FX_V3 table emitted six or seven data bytes
    instead, which the light would not parse.
    """
    frame = build_fx_command(symbol, 80, effect_version=1)
    # V3 framing is [cmd][len][...data...][crc], so three bytes of overhead.
    assert len(frame) - 3 == data_bytes, f"{name} header is the wrong width"


def test_chase_puts_its_mode_where_flow_pads():
    """Chase carries a mode byte in the position Flow leaves as 0xFF."""
    flow = build_fx_command(1, 80, effect_version=1)
    chase = build_fx_command(2, 80, effect_version=1, mode=3)
    # [cmd, len, brightness, tenths, selector, speed, direction, length, here]
    assert flow[8] == 0xFF
    assert chase[8] == 3


def test_colour_blocks_append_four_bytes_each():
    """Each block is option, value (two bytes for hue), saturation."""
    blocks = (
        ColorBlock(option=1, value=240, saturation=100),
        ColorBlock(option=0, value=56, saturation=50),
    )
    frame = build_fx_color_block_command(1, 80, blocks=blocks)

    assert frame[-9:-1] == bytes([1, 0x00, 0xF0, 100, 0, 56, 0xFF, 50])
    # The block count is the last header byte, before the blocks themselves.
    assert frame[-10] == 2


def test_an_unused_block_is_all_padding():
    """Option 2 marks a block unused; the app sends four 0xFF bytes."""
    assert ColorBlock(option=2).to_bytes() == b"\xff\xff\xff\xff"
