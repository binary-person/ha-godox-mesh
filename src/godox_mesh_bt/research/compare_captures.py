"""Compare two parsed ATT write tables.

Examples
--------
>>> row = AttWrite(1, 0.0, "0x52", "write-command", "0x0012", "0102")
>>> compare_packet_tables([row], [row]).row_count_match
True
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from godox_mesh_bt.research.parse_capture import AttWrite

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RowComparison:
    """Comparison result for one paired capture row.

    Parameters
    ----------
    index
        One-based paired row index.
    handle_match
        Whether the ATT handles match.
    operation_match
        Whether the write operation names match.
    payload_length_match
        Whether the byte payload lengths match.
    first_payload_length
        Byte length of the first row payload.
    second_payload_length
        Byte length of the second row payload.
    timing_gap_ms
        Difference between inter-row timing gaps in milliseconds, if available.

    Examples
    --------
    >>> RowComparison(1, True, True, True, 2, 2, None).payload_length_match
    True
    """

    index: int
    handle_match: bool
    operation_match: bool
    payload_length_match: bool
    first_payload_length: int
    second_payload_length: int
    timing_gap_ms: float | None


@dataclass(frozen=True)
class PacketTableDiff:
    """Summary comparison of two ATT write tables.

    Parameters
    ----------
    first_count
        Number of rows in the first table.
    second_count
        Number of rows in the second table.
    row_count_match
        Whether both tables contain the same number of rows.
    handle_sequence_match
        Whether paired rows have matching handle values.
    operation_sequence_match
        Whether paired rows have matching write operations.
    payload_length_sequence_match
        Whether paired rows have matching byte payload lengths.
    rows
        Per-row comparison results.

    Examples
    --------
    >>> PacketTableDiff(1, 1, True, True, True, True, ()).row_count_match
    True
    """

    first_count: int
    second_count: int
    row_count_match: bool
    handle_sequence_match: bool
    operation_sequence_match: bool
    payload_length_sequence_match: bool
    rows: tuple[RowComparison, ...]


def compare_packet_tables(
    first: Sequence[AttWrite],
    second: Sequence[AttWrite],
) -> PacketTableDiff:
    """Compare two sequences of parsed ATT write rows.

    Parameters
    ----------
    first
        First capture table.
    second
        Second capture table.

    Returns
    -------
    PacketTableDiff
        Row count, handle, operation, payload length, and timing comparison.

    Examples
    --------
    >>> row = AttWrite(1, 0.0, "0x52", "write-command", "0x0012", "0102")
    >>> compare_packet_tables([row], [row]).payload_length_sequence_match
    True
    """

    first_count = len(first)
    second_count = len(second)
    row_count_match = first_count == second_count
    logger.debug("comparing packet tables: %d vs %d rows", first_count, second_count)

    paired = zip(first, second)
    rows: list[RowComparison] = []
    handle_sequence_match = True
    operation_sequence_match = True
    payload_length_sequence_match = True

    for idx, (a, b) in enumerate(paired, start=1):
        first_len = len(a.value_hex) // 2
        second_len = len(b.value_hex) // 2
        handle_match = a.handle == b.handle
        operation_match = a.operation == b.operation
        payload_length_match = first_len == second_len

        if not handle_match:
            handle_sequence_match = False
        if not operation_match:
            operation_sequence_match = False
        if not payload_length_match:
            payload_length_sequence_match = False

        timing_gap_ms: float | None = None
        if idx > 1:
            first_gap = (a.timestamp - first[idx - 2].timestamp) * 1000
            second_gap = (b.timestamp - second[idx - 2].timestamp) * 1000
            timing_gap_ms = first_gap - second_gap

        rows.append(
            RowComparison(
                index=idx,
                handle_match=handle_match,
                operation_match=operation_match,
                payload_length_match=payload_length_match,
                first_payload_length=first_len,
                second_payload_length=second_len,
                timing_gap_ms=timing_gap_ms,
            )
        )

    diff = PacketTableDiff(
        first_count=first_count,
        second_count=second_count,
        row_count_match=row_count_match,
        handle_sequence_match=handle_sequence_match,
        operation_sequence_match=operation_sequence_match,
        payload_length_sequence_match=payload_length_sequence_match,
        rows=tuple(rows),
    )
    logger.info("packet table comparison complete: row_count_match=%s", diff.row_count_match)
    return diff
