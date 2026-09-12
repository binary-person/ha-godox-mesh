"""Status readback against the record selector the hardware actually answers.

Verified on an SL200III Bi: a status request must select the A0 record with the
end byte. A request with the padding end byte (0xFF) gets no reply at all, which
is what made an earlier investigation wrongly conclude readback was impossible.
"""

from __future__ import annotations

from godox_mesh_bt.protocol import (
    build_status_ack,
    build_status_request,
    parse_status_response,
)


def test_status_request_selects_the_a0_record() -> None:
    """The end byte selects which record the light reports."""
    assert build_status_request().hex() == "fd01ffffffffa095"


def test_status_ack_stops_retransmission() -> None:
    """The light retransmits a reply until acknowledged."""
    assert build_status_ack(0xA0).hex() == "e0a0ffffffffffa7"


def test_command_echo_frame_reports_trustworthy_cct() -> None:
    """A reply written by a command echo carries both brightness and CCT.

    Captured from hardware after commanding 100% / 6500K.
    """
    status = parse_status_response(bytes.fromhex("a06441320000000c"))
    assert status.brightness == 100
    assert status.cct == 6500


def test_panel_written_frame_still_reports_brightness() -> None:
    """A record written by a physical panel change carries live brightness.

    Captured from an SL200III Bi after turning the light's own knob to 77%.
    """
    status = parse_status_response(bytes.fromhex("a04d3800ffff01f0"))
    assert status.brightness == 77


def test_panel_frames_across_a_sweep_all_report_live_brightness() -> None:
    """Every frame captured while the knob was turned tracks the real value."""
    captured = {
        "a01f3800ffff01b0": 31,
        "a02e3800ffff016a": 46,
        "a03f3800ffff0106": 63,
        "a04d3800ffff01f0": 77,
        "a00a3800ffff0100": 10,
        "a0053800ffff0124": 5,
    }
    for frame, brightness in captured.items():
        assert parse_status_response(bytes.fromhex(frame)).brightness == brightness


def test_colour_temperature_is_reported_as_received() -> None:
    """Whatever the light sends is parsed and returned, with no guessing.

    Two attempts at inferring "this colour temperature is stale" from the wire
    format both failed against hardware: keying off the 0xFF markers at bytes
    4-5 discarded good data on an SL60II Bi, and trusting nothing but command
    echoes required a source tag whose meaning is only known for two models.

    A wrong guess here is worse than no guess: it silently drops a correct value
    and the user has no setting that puts it back. Reporting what arrived leaves
    a wrong value visible, and the entry's colour-temperature option can switch
    it off. These frames are all real captures.
    """
    captured = {
        "a06441320000000c": 6500,  # SL200III Bi, commanded
        "a0141e320000006e": 3000,  # SL200III Bi, commanded
        "a04d3800ffff01f0": 5600,  # SL200III Bi, panel -- not its real setting
        "a0191f32ffff0358": 3100,  # SL60II Bi, commanded
        "a01f4132ffff03dd": 6500,  # SL60II Bi, panel -- accurate
    }
    for frame, cct in captured.items():
        assert parse_status_response(bytes.fromhex(frame)).cct == cct, frame


def test_brightness_is_accurate_on_every_captured_frame() -> None:
    """Brightness tracked the light on both models, commanded or panel-set."""
    assert parse_status_response(bytes.fromhex("a04d3800ffff01f0")).brightness == 77
    assert parse_status_response(bytes.fromhex("a01f4132ffff03dd")).brightness == 31
