from __future__ import annotations

import pytest

from godox_mesh_bt.research.parse_capture import AttWrite
from godox_mesh_bt.research.replay_capture import ReplayPlan, plan_replay, select_replay_write


WRITES = [
    AttWrite(
        index=1,
        timestamp=1.0,
        opcode="0x12",
        operation="write-request",
        handle="0x001e",
        value_hex="",
    ),
    AttWrite(
        index=2,
        timestamp=2.0,
        opcode="0x52",
        operation="write-command",
        handle="0x0020",
        value_hex="aabbcc",
    ),
]


def test_select_replay_write_returns_non_empty_payload_row() -> None:
    assert select_replay_write(WRITES, 2) == WRITES[1]


def test_select_replay_write_rejects_missing_row() -> None:
    with pytest.raises(ValueError, match="capture row 99 was not found"):
        select_replay_write(WRITES, 99)


def test_select_replay_write_rejects_empty_payload_row() -> None:
    with pytest.raises(ValueError, match="capture row 1 has no replay payload"):
        select_replay_write(WRITES, 1)


def test_plan_replay_maps_capture_row_to_raw_write() -> None:
    assert plan_replay(
        WRITES,
        row_index=2,
        device="device-id",
        characteristic="00002add-0000-1000-8000-00805f9b34fb",
    ) == ReplayPlan(
        device="device-id",
        characteristic="00002add-0000-1000-8000-00805f9b34fb",
        row_index=2,
        source_handle="0x0020",
        payload=b"\xaa\xbb\xcc",
        response=False,
    )
