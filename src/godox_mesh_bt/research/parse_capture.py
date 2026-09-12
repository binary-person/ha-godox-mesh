"""Parse ATT write rows from tshark JSON captures.

Examples
--------
>>> packet = {"_source": {"layers": {"btatt": {"btatt.opcode": "0x52", "btatt.handle": "0x0012", "btatt.value": "01:02"}}}}
>>> extract_att_writes([packet])[0].value_hex
'0102'
"""

from __future__ import annotations

import json
import logging
import sys
import csv
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from io import StringIO
from typing import Any

logger = logging.getLogger(__name__)


WRITE_COMMAND_OPCODE = "0x52"
WRITE_REQUEST_OPCODE = "0x12"


@dataclass(frozen=True)
class ActionLabel:
    """Human label for a captured write row.

    Parameters
    ----------
    index
        One-based write-row index.
    action
        Human-readable action name.

    Examples
    --------
    >>> ActionLabel(1, "power on").action
    'power on'
    """

    index: int
    action: str


@dataclass(frozen=True)
class AttWrite:
    """One ATT write extracted from a capture.

    Parameters
    ----------
    index
        One-based write-row index.
    timestamp
        Packet timestamp in seconds.
    opcode
        ATT opcode as a lowercase hex string.
    operation
        Write operation name.
    handle
        ATT handle string.
    value_hex
        Exact write payload bytes encoded as lowercase hexadecimal text. Each
        pair of hex characters represents one original byte.
    action
        Optional human label.

    Examples
    --------
    >>> AttWrite(1, 0.0, "0x52", "write-command", "0x0012", "0102").value_hex
    '0102'
    """

    index: int
    timestamp: float
    opcode: str
    operation: str
    handle: str
    value_hex: str
    action: str = ""


def extract_att_writes(
    packets: Sequence[Mapping[str, Any]],
    *,
    labels: Sequence[ActionLabel] = (),
) -> list[AttWrite]:
    """Extract ATT write commands and requests from tshark JSON packets.

    Parameters
    ----------
    packets
        Sequence of tshark packet dictionaries.
    labels
        Optional row labels keyed by one-based write index.

    Returns
    -------
    list[AttWrite]
        ATT writes with byte payloads preserved as hex strings.

    Examples
    --------
    >>> packet = {"_source": {"layers": {"btatt": {"btatt.opcode": "0x52", "btatt.handle": "0x0012", "btatt.value": "01:02"}}}}
    >>> extract_att_writes([packet])[0].operation
    'write-command'
    """

    labels_by_index = {label.index: label.action for label in labels}
    writes: list[AttWrite] = []
    logger.debug("extracting ATT writes from %d packet(s)", len(packets))

    for packet in packets:
        layers = _layers(packet)
        btatt = layers.get("btatt")
        if not isinstance(btatt, Mapping):
            continue

        opcode = str(btatt.get("btatt.opcode", "")).lower()
        operation = _write_operation(opcode)
        if operation is None:
            continue

        index = len(writes) + 1
        writes.append(
            AttWrite(
                index=index,
                timestamp=_timestamp(layers),
                opcode=opcode,
                operation=operation,
                handle=str(btatt.get("btatt.handle", "")),
                value_hex=_packet_value_hex(layers),
                action=labels_by_index.get(index, ""),
            )
        )

    logger.info("extracted %d ATT write(s)", len(writes))
    return writes


def render_markdown(writes: Iterable[AttWrite]) -> str:
    """Render ATT writes as a Markdown table.

    Parameters
    ----------
    writes
        ATT write rows to render.

    Returns
    -------
    str
        Markdown table ending with a newline.

    Examples
    --------
    >>> row = AttWrite(1, 0.0, "0x52", "write-command", "0x0012", "0102")
    >>> "`0102`" in render_markdown([row])
    True
    """

    lines = [
        "| # | Time | Operation | Handle | Value | Action |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for write in writes:
        lines.append(
            f"| {write.index} | {write.timestamp:.6f} | {write.operation} | "
            f"`{write.handle}` | `{write.value_hex}` | {write.action} |"
        )
    return "\n".join(lines) + "\n"


def render_csv(writes: Iterable[AttWrite]) -> str:
    """Render ATT writes as CSV.

    Parameters
    ----------
    writes
        ATT write rows to render.

    Returns
    -------
    str
        CSV text with a header row.

    Examples
    --------
    >>> row = AttWrite(1, 0.0, "0x52", "write-command", "0x0012", "0102")
    >>> render_csv([row]).splitlines()[1]
    '1,0.000000,write-command,0x0012,0102,'
    """

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["index", "timestamp", "operation", "handle", "value_hex", "action"])
    for write in writes:
        writer.writerow(
            [
                write.index,
                f"{write.timestamp:.6f}",
                write.operation,
                write.handle,
                write.value_hex,
                write.action,
            ]
        )
    return buffer.getvalue()


def _layers(packet: Mapping[str, Any]) -> Mapping[str, Any]:
    source = packet.get("_source", {})
    if not isinstance(source, Mapping):
        return {}
    layers = source.get("layers", {})
    if not isinstance(layers, Mapping):
        return {}
    return layers


def _timestamp(layers: Mapping[str, Any]) -> float:
    frame = layers.get("frame", {})
    if not isinstance(frame, Mapping):
        return 0.0
    return _first_float(
        frame.get("frame.time_epoch"),
        frame.get("frame.time_relative"),
        default=0.0,
    )


def _packet_value_hex(layers: Mapping[str, Any]) -> str:
    btatt = layers.get("btatt", {})
    if isinstance(btatt, Mapping) and btatt.get("btatt.value"):
        return _normalize_hex(str(btatt["btatt.value"]))

    btmproxy = layers.get("btmproxy", {})
    if isinstance(btmproxy, Mapping) and btmproxy.get("btmproxy.data"):
        return _normalize_hex(str(btmproxy["btmproxy.data"]))

    btmesh = layers.get("btmesh", {})
    if isinstance(btmesh, Mapping):
        network_pdu = btmesh.get("Network PDU", {})
        if isinstance(network_pdu, Mapping):
            parts = [
                network_pdu.get("btmesh.obfuscated"),
                network_pdu.get("btmesh.encrypted"),
            ]
            return _normalize_hex(":".join(str(part) for part in parts if part))

    return ""


def _write_operation(opcode: str) -> str | None:
    if opcode == WRITE_COMMAND_OPCODE:
        return "write-command"
    if opcode == WRITE_REQUEST_OPCODE:
        return "write-request"
    return None


def _normalize_hex(value: str) -> str:
    return value.replace(":", "").replace(" ", "").lower()


def _first_float(*values: object, default: float) -> float:
    for value in values:
        try:
            return float(str(value))
        except (TypeError, ValueError):
            continue
    return default


def main() -> None:
    """Run the capture parser CLI.

    Returns
    -------
    None
        Parsed rows are printed to stdout; errors exit the process.

    Examples
    --------
    >>> main.__name__
    'main'
    """

    if len(sys.argv) < 2:
        print("Usage: parse-capture <tshark-json-file> [--format markdown|csv]", file=sys.stderr)
        sys.exit(1)
    path = sys.argv[1]
    fmt = "markdown"
    if "--format" in sys.argv:
        idx = sys.argv.index("--format")
        if idx + 1 < len(sys.argv):
            fmt = sys.argv[idx + 1]
    packets = json.loads(open(path).read())  # noqa: WPS515
    writes = extract_att_writes(packets)
    if fmt == "csv":
        print(render_csv(writes), end="")
    else:
        print(render_markdown(writes), end="")


if __name__ == "__main__":
    main()
