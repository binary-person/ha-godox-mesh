"""Plan replay of captured ATT writes.

Examples
--------
>>> write = AttWrite(1, 0.0, "0x52", "write-command", "0x0012", "0102")
>>> plan_replay([write], row_index=1, device="AA:BB", characteristic="char").payload
b'\\x01\\x02'
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from godox_mesh_bt.research.parse_capture import AttWrite

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplayPlan:
    """A raw BLE write selected for replay.

    Parameters
    ----------
    device
        Platform-specific BLE address or identifier.
    characteristic
        Characteristic UUID or handle to write.
    row_index
        One-based capture row selected for replay.
    source_handle
        ATT handle from the source capture.
    payload
        Exact captured payload bytes to replay. Length and byte values are
        preserved from the capture row.
    response
        Whether to request a GATT write response.

    Examples
    --------
    >>> ReplayPlan("AA:BB", "char", 1, "0x0012", b"\\x01").payload
    b'\\x01'
    """

    device: str
    characteristic: str
    row_index: int
    source_handle: str
    payload: bytes
    response: bool = False


def select_replay_write(writes: Iterable[AttWrite], row_index: int) -> AttWrite:
    """Select a captured ATT write by one-based row index.

    Parameters
    ----------
    writes
        Captured write rows.
    row_index
        One-based row number to select.

    Returns
    -------
    AttWrite
        Selected write with a non-empty hex payload.

    Examples
    --------
    >>> write = AttWrite(1, 0.0, "0x52", "write-command", "0x0012", "0102")
    >>> select_replay_write([write], 1).value_hex
    '0102'
    """

    logger.debug("selecting replay row %s", row_index)
    for write in writes:
        if write.index == row_index:
            if not write.value_hex:
                raise ValueError(f"capture row {row_index} has no replay payload")
            return write
    raise ValueError(f"capture row {row_index} was not found")


def plan_replay(
    writes: Iterable[AttWrite],
    *,
    row_index: int,
    device: str,
    characteristic: str,
) -> ReplayPlan:
    """Build a replay plan from a captured ATT write.

    Parameters
    ----------
    writes
        Captured write rows.
    row_index
        One-based row number to replay.
    device
        Platform-specific BLE address or identifier.
    characteristic
        Characteristic UUID or handle to write.

    Returns
    -------
    ReplayPlan
        Replay target and exact payload bytes decoded from hex.

    Examples
    --------
    >>> write = AttWrite(1, 0.0, "0x12", "write-request", "0x0012", "0102")
    >>> plan_replay([write], row_index=1, device="AA:BB", characteristic="char").response
    True
    """

    write = select_replay_write(writes, row_index)
    plan = ReplayPlan(
        device=device,
        characteristic=characteristic,
        row_index=row_index,
        source_handle=write.handle,
        payload=bytes.fromhex(write.value_hex),
        response=write.operation == "write-request",
    )
    logger.info("planned replay for row %s", row_index)
    return plan
