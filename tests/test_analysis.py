from __future__ import annotations

import pytest

from godox_mesh_bt.research.compare_captures import (
    PacketTableDiff,
    RowComparison,
    compare_packet_tables,
)
from godox_mesh_bt.research.parse_capture import AttWrite


def _write(index: int, handle: str, operation: str, value_hex: str, timestamp: float) -> AttWrite:
    return AttWrite(
        index=index,
        timestamp=timestamp,
        opcode="0x52",
        operation=operation,
        handle=handle,
        value_hex=value_hex,
    )


def test_compare_identical_tables() -> None:
    first = [
        _write(1, "0x0020", "write-command", "aabb", 1.0),
        _write(2, "0x0020", "write-command", "ccdd", 2.0),
    ]
    second = [
        _write(1, "0x0020", "write-command", "aabb", 1.0),
        _write(2, "0x0020", "write-command", "ccdd", 2.0),
    ]

    diff = compare_packet_tables(first, second)

    assert diff == PacketTableDiff(
        first_count=2,
        second_count=2,
        row_count_match=True,
        handle_sequence_match=True,
        operation_sequence_match=True,
        payload_length_sequence_match=True,
        rows=(
            RowComparison(
                index=1,
                handle_match=True,
                operation_match=True,
                payload_length_match=True,
                first_payload_length=2,
                second_payload_length=2,
                timing_gap_ms=None,
            ),
            RowComparison(
                index=2,
                handle_match=True,
                operation_match=True,
                payload_length_match=True,
                first_payload_length=2,
                second_payload_length=2,
                timing_gap_ms=0.0,
            ),
        ),
    )


def test_compare_different_row_counts() -> None:
    first = [_write(1, "0x0020", "write-command", "aabb", 1.0)]
    second = [
        _write(1, "0x0020", "write-command", "aabb", 1.0),
        _write(2, "0x0020", "write-command", "ccdd", 2.0),
    ]

    diff = compare_packet_tables(first, second)

    assert diff.row_count_match is False
    assert diff.first_count == 1
    assert diff.second_count == 2


def test_compare_different_handles() -> None:
    first = [_write(1, "0x0020", "write-command", "aabb", 1.0)]
    second = [_write(1, "0x001e", "write-command", "aabb", 1.0)]

    diff = compare_packet_tables(first, second)

    assert diff.handle_sequence_match is False
    assert diff.rows[0].handle_match is False


def test_compare_different_operations() -> None:
    first = [_write(1, "0x0020", "write-command", "aabb", 1.0)]
    second = [_write(1, "0x0020", "write-request", "aabb", 1.0)]

    diff = compare_packet_tables(first, second)

    assert diff.operation_sequence_match is False
    assert diff.rows[0].operation_match is False


def test_compare_different_payload_lengths() -> None:
    first = [_write(1, "0x0020", "write-command", "aabb", 1.0)]
    second = [_write(1, "0x0020", "write-command", "aabbccdd", 1.0)]

    diff = compare_packet_tables(first, second)

    assert diff.payload_length_sequence_match is False
    assert diff.rows[0].payload_length_match is False
    assert diff.rows[0].first_payload_length == 2
    assert diff.rows[0].second_payload_length == 4


def test_compare_empty_tables() -> None:
    diff = compare_packet_tables([], [])

    assert diff.first_count == 0
    assert diff.second_count == 0
    assert diff.row_count_match is True
    assert diff.rows == ()


def test_compare_timing_gaps() -> None:
    first = [
        _write(1, "0x0020", "write-command", "aabb", 1.0),
        _write(2, "0x0020", "write-command", "ccdd", 1.5),
    ]
    second = [
        _write(1, "0x0020", "write-command", "aabb", 10.0),
        _write(2, "0x0020", "write-command", "ccdd", 10.7),
    ]

    diff = compare_packet_tables(first, second)

    assert diff.rows[0].timing_gap_ms is None
    assert diff.rows[1].timing_gap_ms == pytest.approx(-200.0)
