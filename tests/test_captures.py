from __future__ import annotations

import json
from pathlib import Path

from godox_mesh_bt.research.parse_capture import (
    ActionLabel,
    AttWrite,
    extract_att_writes,
    render_csv,
    render_markdown,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "tshark-att-writes.json"


def test_extract_att_write_packets_from_tshark_json() -> None:
    packets = json.loads(FIXTURE_PATH.read_text())

    assert extract_att_writes(packets) == [
        AttWrite(
            index=1,
            timestamp=1777760400.1,
            opcode="0x52",
            operation="write-command",
            handle="0x0016",
            value_hex="aabb0132",
        ),
        AttWrite(
            index=2,
            timestamp=1777760401.25,
            opcode="0x12",
            operation="write-request",
            handle="0x002c",
            value_hex="aabb15e0",
        ),
    ]


def test_render_markdown_packet_table() -> None:
    writes = [
        AttWrite(
            index=1,
            timestamp=1777760400.1,
            opcode="0x52",
            operation="write-command",
            handle="0x0016",
            value_hex="aabb0132",
            action="brightness 50",
        )
    ]

    assert render_markdown(writes) == (
        "| # | Time | Operation | Handle | Value | Action |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| 1 | 1777760400.100000 | write-command | `0x0016` | `aabb0132` | brightness 50 |\n"
    )


def test_render_csv_packet_table() -> None:
    writes = [
        AttWrite(
            index=1,
            timestamp=1777760400.1,
            opcode="0x52",
            operation="write-command",
            handle="0x0016",
            value_hex="aabb0132",
            action="brightness 50",
        )
    ]

    assert render_csv(writes) == (
        "index,timestamp,operation,handle,value_hex,action\r\n"
        "1,1777760400.100000,write-command,0x0016,aabb0132,brightness 50\r\n"
    )


def test_extract_att_writes_applies_manual_action_labels_by_index() -> None:
    packets = json.loads(FIXTURE_PATH.read_text())

    writes = extract_att_writes(
        packets,
        labels=[
            ActionLabel(index=1, action="brightness 50"),
            ActionLabel(index=2, action="cct 5600"),
        ],
    )

    assert [write.action for write in writes] == ["brightness 50", "cct 5600"]


def test_extract_att_writes_accepts_iso_timestamps_and_proxy_payloads() -> None:
    packets = [
        {
            "_source": {
                "layers": {
                    "frame": {
                        "frame.time_epoch": "2026-05-02T23:38:35.220145000Z",
                        "frame.time_relative": "62.293890000",
                    },
                    "btatt": {
                        "btatt.opcode": "0x52",
                        "btatt.handle": "0x0020",
                    },
                    "btmproxy": {
                        "btmproxy.data": "38:59:ff:29",
                    },
                }
            }
        }
    ]

    assert extract_att_writes(packets) == [
        AttWrite(
            index=1,
            timestamp=62.29389,
            opcode="0x52",
            operation="write-command",
            handle="0x0020",
            value_hex="3859ff29",
        )
    ]
