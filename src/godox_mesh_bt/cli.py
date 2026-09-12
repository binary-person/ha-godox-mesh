"""Command-line interface for Godox Bluetooth Mesh tools.

Examples
--------
>>> async def fake_scan(**kwargs):
...     return []
>>> main(["scan"], scan_fn=fake_scan)
[]
0
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import json
import sys
from collections.abc import Callable, Coroutine, Sequence
from typing import Any

from godox_mesh_bt.client import GodoxMeshClient
from godox_mesh_bt.controller import REQUEST_OPCODE, GodoxController
from godox_mesh_bt.crypto import build_vendor_access_payload, pack_proxy_network_pdu
from godox_mesh_bt.logging_utils import configure_logging
from godox_mesh_bt.inspector import InspectionResult, inspect_device
from godox_mesh_bt.protocol import build_v2_command, validate_brightness, validate_cct
from godox_mesh_bt.provisioning import ProvisioningSession
from godox_mesh_bt.scanner import DiscoveredDevice, scan, scan_unprovisioned
from godox_mesh_bt.state import MeshState, import_mesh_state, resolve_mesh_state_path, save_default_mesh_state

logger = logging.getLogger(__name__)


ScanFn = Callable[..., Coroutine[Any, Any, list[DiscoveredDevice]]]
InspectFn = Callable[[str], Coroutine[Any, Any, InspectionResult]]


class MeshStateNotFoundError(FileNotFoundError):
    """Raised when a control command cannot find mesh state.

    Examples
    --------
    >>> issubclass(MeshStateNotFoundError, FileNotFoundError)
    True
    """

    pass


def main(
    argv: Sequence[str] | None = None,
    *,
    scan_fn: ScanFn = scan,
    inspect_fn: InspectFn = inspect_device,
    client_factory: Callable[[str], Any] = GodoxMeshClient,
) -> int:
    """Run the CLI and return a process-style exit code.

    Parameters
    ----------
    argv
        Command-line arguments without the program name.
    scan_fn
        Async scanning function used by scan, setup, and auto-device selection.
    inspect_fn
        Async inspection function used by the ``inspect`` command.
    client_factory
        Client class used by the ``raw`` command.

    Returns
    -------
    int
        Zero on success, non-zero on command or setup errors.

    Examples
    --------
    >>> async def fake_scan(**kwargs):
    ...     return []
    >>> main(["scan"], scan_fn=fake_scan)
    []
    0
    """

    parser = _build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    logger.debug("dispatching command %s", args.command)

    # Fail fast: validate brightness and cct before any Bluetooth or state work
    if args.command == "set":
        try:
            if args.brightness is not None:
                validate_brightness(int(args.brightness))
            if args.cct is not None:
                validate_cct(args.cct)
        except ValueError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2

    try:
        if args.command == "scan":
            return _scan(args, scan_fn)
        if args.command == "inspect":
            return _inspect(args, inspect_fn, scan_fn)
        if args.command == "setup":
            return _setup(args, scan_fn)
        if args.command == "on":
            return _power_on(args, scan_fn)
        if args.command == "off":
            return _power_off(args, scan_fn)
        if args.command == "set":
            return _set(args, scan_fn)
        if args.command == "raw":
            return _raw(args, scan_fn, client_factory)
        if args.command == "rebind":
            return _cmd_rebind(args, scan_fn)
        if args.command == "provision":
            return _cmd_provision(args)
    except MeshStateNotFoundError as error:
        print(str(error), file=sys.stderr)
        return 2

    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    verbose_parent = argparse.ArgumentParser(add_help=False)
    verbose_parent.add_argument("-v", "--verbose", action="count", default=0)
    subcommand_verbose_parent = argparse.ArgumentParser(add_help=False)
    subcommand_verbose_parent.add_argument("-v", "--verbose", action="count", default=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(prog="godox-mesh", parents=[verbose_parent])
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", parents=[subcommand_verbose_parent])
    scan_parser.add_argument("--timeout", type=float, default=5.0)

    inspect_parser = subparsers.add_parser("inspect", parents=[subcommand_verbose_parent])
    inspect_parser.add_argument("device", nargs="?")
    inspect_parser.add_argument("--format", choices=["json", "markdown"], default="json")

    setup_parser = subparsers.add_parser("setup", parents=[subcommand_verbose_parent])
    setup_parser.add_argument("--import", dest="import_path", help="Import mesh state JSON or telink_shared.xml")
    setup_parser.add_argument("--timeout", type=float, default=5.0)

    raw_parser = subparsers.add_parser("raw", parents=[subcommand_verbose_parent])
    raw_parser.add_argument("--device")
    raw_parser.add_argument("--char", required=True)
    raw_parser.add_argument("--hex", required=True)
    raw_parser.add_argument("--response", action="store_true")

    on_parser = subparsers.add_parser("on", parents=[subcommand_verbose_parent])
    on_parser.add_argument("--device")
    on_parser.add_argument("--state", help="Path to mesh state JSON")
    on_parser.add_argument("--dry-run", action="store_true")
    on_parser.add_argument(
        "--seq-bump",
        type=int,
        default=0,
        metavar="N",
        help="Add N to sequence number before connecting (use if device ignores commands due to RPL)",
    )
    _add_mesh_overrides(on_parser)

    off_parser = subparsers.add_parser("off", parents=[subcommand_verbose_parent])
    off_parser.add_argument("--device")
    off_parser.add_argument("--state", help="Path to mesh state JSON")
    off_parser.add_argument("--dry-run", action="store_true")
    off_parser.add_argument(
        "--seq-bump",
        type=int,
        default=0,
        metavar="N",
        help="Add N to sequence number before connecting (use if device ignores commands due to RPL)",
    )
    _add_mesh_overrides(off_parser)

    set_parser = subparsers.add_parser("set", parents=[subcommand_verbose_parent])
    set_parser.add_argument("--device")
    set_parser.add_argument("--state", help="Path to mesh state JSON")
    set_parser.add_argument("--brightness", type=float, help="Brightness percent (0-100)")
    set_parser.add_argument("--cct", type=int, help="CCT in Kelvin; the accepted range is per-model (1800-10000 across the range)")
    set_parser.add_argument("--dry-run", action="store_true")
    set_parser.add_argument(
        "--seq-bump",
        type=int,
        default=0,
        metavar="N",
        help="Add N to sequence number before connecting (use if device ignores commands due to RPL)",
    )
    _add_mesh_overrides(set_parser)

    rebind_parser = subparsers.add_parser(
        "rebind",
        parents=[subcommand_verbose_parent],
        help="Re-send Config App Key Add + Config Model App Bind (use after factory reset)",
    )
    rebind_parser.add_argument("--device")
    rebind_parser.add_argument("--state", help="Path to mesh state JSON")
    rebind_parser.add_argument(
        "--seq-bump",
        type=int,
        default=0,
        metavar="N",
        help="Add N to sequence number before connecting (use if device ignores commands due to RPL)",
    )

    provision_parser = subparsers.add_parser(
        "provision",
        parents=[subcommand_verbose_parent],
        help="Provision a factory-reset Godox light",
    )
    provision_parser.add_argument("--address", help="BLE address (skip scan if provided)")
    provision_parser.add_argument("--net-key", dest="net_key", help="Network key hex (default: random)")
    provision_parser.add_argument("--app-key", dest="app_key", help="App key hex (default: random)")
    provision_parser.add_argument("--node-addr", dest="node_addr", type=int, default=2)
    provision_parser.add_argument("--output", default="mesh_state.json")
    provision_parser.add_argument("--timeout", type=float, default=10.0)

    return parser


def _add_mesh_overrides(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--network-key")
    parser.add_argument("--app-key")
    parser.add_argument("--provisioner-address", type=_parse_int_auto)
    parser.add_argument("--node-address", type=_parse_int_auto)
    parser.add_argument("--sequence-number", type=_parse_int_auto)
    parser.add_argument("--iv-index", type=_parse_int_auto)

def _parse_int_auto(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid integer: {value}") from error



def _scan(args: argparse.Namespace, scan_fn: ScanFn) -> int:
    devices = asyncio.run(scan_fn(timeout=args.timeout))
    logger.info("scan returned %d devices", len(devices))
    print(json.dumps([device.to_dict() for device in devices], indent=2))
    return 0


def _inspect(args: argparse.Namespace, inspect_fn: InspectFn, scan_fn: ScanFn) -> int:
    async def _do_inspect():
        device = await _ensure_device(args, scan_fn)
        inspection = await inspect_fn(device)
        if args.format == "markdown":
            print(inspection.to_markdown(), end="")
        else:
            print(json.dumps(inspection.to_dict(), indent=2))

    asyncio.run(_do_inspect())
    return 0


def _setup(args: argparse.Namespace, scan_fn: ScanFn) -> int:
    if args.import_path:
        state = import_mesh_state(args.import_path)
        saved_path = save_default_mesh_state(state)
        print(f"Saved mesh state to {saved_path}")
        return 0

    async def _scan_for_setup() -> int:
        print("No mesh state found.", file=sys.stderr)
        print(
            "Run `godox-mesh setup --import <mesh-state-or-telink-shared.xml>` "
            "to import an existing app mesh, or factory-reset the light before library provisioning.",
            file=sys.stderr,
        )
        devices = await scan_fn(timeout=args.timeout)
        godox_devices = [device for device in devices if device.likely_godox]
        if len(godox_devices) == 1:
            device = godox_devices[0]
            print(f"Found likely Godox device: {device.name} ({device.address})", file=sys.stderr)
        elif len(godox_devices) > 1:
            print("Found multiple likely Godox devices; pass --device to control commands.", file=sys.stderr)
        return 2

    return asyncio.run(_scan_for_setup())


async def _ensure_device(args: argparse.Namespace, scan_fn: ScanFn) -> str:
    if args.device:
        return args.device

    print("No device specified, scanning...", file=sys.stderr)
    devices = await scan_fn(timeout=5.0)
    godox_devices = [d for d in devices if d.likely_godox]
    if not godox_devices:
        raise ValueError("No Godox devices found. Please specify --device.")

    selected = godox_devices[0]
    print(f"Found {selected.name} ({selected.address})", file=sys.stderr)
    args.device = selected.address
    return selected.address


async def _get_controller(args: argparse.Namespace, scan_fn: ScanFn) -> GodoxController:
    state_path = resolve_mesh_state_path(args.state)
    if state_path is None:
        raise MeshStateNotFoundError(_missing_state_message())
    if not state_path.exists():
        raise MeshStateNotFoundError(_missing_state_message())

    # Use device_address cached in state as a fast-path (skip scan).
    if not args.device:
        cached = MeshState.load(state_path).device_address
        if cached:
            args.device = cached

    device = await _ensure_device(args, scan_fn)

    # After scan, persist the discovered address for future fast-path use.
    if not getattr(args, "dry_run", False):
        state = MeshState.load(state_path)
        if state.device_address != device:
            dataclasses.replace(state, device_address=device).save(state_path)
            logger.debug("device_address %s saved to state", device)

    seq_bump = getattr(args, "seq_bump", 0)
    if seq_bump > 0:
        bumped = MeshState.load(state_path).next_sequence(seq_bump)
        bumped.save(state_path)
        logger.info("sequence number bumped by %d to %d", seq_bump, bumped.sequence_number)

    controller = GodoxController(device, state_path)

    controller.state = _apply_mesh_overrides(controller.state, args)

    print(f"Using controller for {controller.address} with sequence {controller.state.sequence_number}", file=sys.stderr)
    return controller


def _missing_state_message() -> str:
    return (
        "No mesh state found. Run `godox-mesh setup --import <mesh-state-or-telink-shared.xml>` "
        "or pass --state. Already-provisioned lights cannot reveal mesh keys over BLE."
    )


def _apply_mesh_overrides(state: MeshState, args: argparse.Namespace) -> MeshState:
    node_address = state.node_address if args.node_address is None else args.node_address
    if args.node_address is None and not _is_unicast_address(node_address):
        logger.warning(
            "mesh state node address 0x%04x is not unicast; defaulting to 0x0002",
            node_address,
        )
        node_address = 0x0002

    return MeshState(
        network_key=state.network_key if args.network_key is None else args.network_key,
        app_key=state.app_key if args.app_key is None else args.app_key,
        device_key=state.device_key,
        device_address=state.device_address,
        provisioner_address=state.provisioner_address
        if args.provisioner_address is None
        else args.provisioner_address,
        node_address=node_address,
        sequence_number=state.sequence_number if args.sequence_number is None else args.sequence_number,
        iv_index=state.iv_index if args.iv_index is None else args.iv_index,
    )


def _is_unicast_address(address: int) -> bool:
    return 0x0001 <= address <= 0x7FFF


async def _run_control_command(
    args: argparse.Namespace,
    scan_fn: ScanFn,
    action: Callable[[GodoxController], Coroutine[Any, Any, None]],
) -> None:
    async with await _get_controller(args, scan_fn) as controller:
        await action(controller)


def _power_on(args: argparse.Namespace, scan_fn: ScanFn) -> int:
    if args.dry_run:
        return _dry_run_control(args, scan_fn)
    asyncio.run(_run_control_command(args, scan_fn, lambda ctrl: ctrl.power_on()))
    return 0


def _power_off(args: argparse.Namespace, scan_fn: ScanFn) -> int:
    if args.dry_run:
        return _dry_run_control(args, scan_fn)
    asyncio.run(_run_control_command(args, scan_fn, lambda ctrl: ctrl.power_off()))
    return 0


def _set(args: argparse.Namespace, scan_fn: ScanFn) -> int:
    if args.dry_run:
        return _dry_run_control(args, scan_fn)

    async def _action(ctrl: GodoxController) -> None:
        if args.brightness is not None or args.cct is not None:
            await ctrl.set_params(brightness=args.brightness, cct=args.cct)

    asyncio.run(_run_control_command(args, scan_fn, _action))
    return 0


def _dry_run_control(args: argparse.Namespace, scan_fn: ScanFn) -> int:
    async def _plan() -> dict[str, object]:
        controller = await _get_controller(args, scan_fn)
        model, end_byte, data = _control_v2_parts(args)
        godox_payload = build_v2_command(model, end_byte, data)
        access_payload = build_vendor_access_payload(REQUEST_OPCODE, godox_payload)
        state = controller.state
        proxy_config_sequences = [state.sequence_number, state.sequence_number + 1]
        command_sequence = state.sequence_number + 2
        proxy_pdu = pack_proxy_network_pdu(
            access_payload,
            bytes.fromhex(state.network_key),
            bytes.fromhex(state.app_key),
            iv_index=state.iv_index,
            seq=command_sequence,
            src=state.provisioner_address,
            dst=state.node_address,
            ttl=10,
        )
        return {
            "ble_device": controller.address,
            "mesh_src": f"0x{state.provisioner_address:04x}",
            "mesh_dst": f"0x{state.node_address:04x}",
            "proxy_config_sequences": proxy_config_sequences,
            "sequence": command_sequence,
            "iv_index": state.iv_index,
            "vendor_opcode": REQUEST_OPCODE,
            "godox_v2_payload_hex": godox_payload.hex(),
            "proxy_pdu_hex": proxy_pdu.hex(),
        }

    print(json.dumps(asyncio.run(_plan()), indent=2))
    return 0


def _control_v2_parts(args: argparse.Namespace) -> tuple[int, int, bytes]:
    if args.command == "on":
        return 0xFE, 0xFF, bytes([0x00])
    if args.command == "off":
        return 0xFE, 0xFF, bytes([0x01])

    final_brightness = args.brightness if args.brightness is not None else 100.0
    final_cct = args.cct if args.cct is not None else 5600
    validate_brightness(int(final_brightness))
    validate_cct(final_cct)
    percent = int(final_brightness)
    brightness_point = int(round((final_brightness - percent) * 10))
    brightness_point = max(0, min(9, brightness_point))
    temp = final_cct // 100
    return 0xF0, brightness_point, bytes([percent, temp, 50, 0, 0])


def _raw(args: argparse.Namespace, scan_fn: ScanFn, client_factory: Callable[[str], Any]) -> int:
    try:
        payload = bytes.fromhex(args.hex)
    except ValueError:
        print("invalid hex payload", file=sys.stderr)
        return 2

    async def write() -> int:
        device = await _ensure_device(args, scan_fn)
        logger.debug("writing raw payload to %s / %s", device, args.char)
        async with client_factory(device) as client:
            await client.write_raw(args.char, payload, response=args.response)
        return 0

    return asyncio.run(write())


def _cmd_rebind(args: argparse.Namespace, scan_fn: ScanFn) -> int:
    async def _do_rebind() -> None:
        device = await _ensure_device(args, scan_fn)
        state_path = resolve_mesh_state_path(args.state)
        if state_path is None:
            raise MeshStateNotFoundError(_missing_state_message())
        if not state_path.exists():
            raise MeshStateNotFoundError(_missing_state_message())

        seq_bump = getattr(args, "seq_bump", 0)
        if seq_bump > 0:
            from godox_mesh_bt.state import MeshState as _MeshState
            bumped = _MeshState.load(state_path).next_sequence(seq_bump)
            bumped.save(state_path)
            logger.info("sequence number bumped by %d to %d", seq_bump, bumped.sequence_number)

        print(f"Using controller for {device}", file=sys.stderr)
        async with GodoxController(device, state_path) as controller:
            await controller.rebind()

    asyncio.run(_do_rebind())
    return 0


def _cmd_provision(args: argparse.Namespace) -> int:
    import dataclasses
    import os

    net_key = bytes.fromhex(args.net_key) if args.net_key else os.urandom(16)
    app_key = bytes.fromhex(args.app_key) if args.app_key else os.urandom(16)

    async def _run() -> None:
        address = args.address
        if not address:
            print("Scanning for unprovisioned devices...")
            devices = await scan_unprovisioned(timeout=args.timeout)
            if not devices:
                print("No unprovisioned devices found. Factory-reset the light and try again.")
                return
            device = devices[0]
            print(f"Found {device.name} ({device.address})")
            address = device.address

        print(f"Provisioning {address}...")
        session = ProvisioningSession(
            address=address,
            net_key=net_key,
            key_index=0,
            iv_index=0,
            unicast_address=args.node_addr,
        )
        state = await session.run()
        state = dataclasses.replace(state, app_key=app_key.hex())
        state.save(args.output)
        print(f"✓ Provisioned! State saved to {args.output}")
        print(f"  Device key: {state.device_key}")
        print(f"  Node address: {state.node_address:#06x}")
        print()
        print("Next: run 'godox-mesh rebind' to push the app key to the device.")

    asyncio.run(_run())
    return 0


def sync_main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI synchronously; thin wrapper around :func:`main`.

    Exists so tests can monkeypatch module-level names (e.g.
    ``godox_mesh_bt.cli.ProvisioningSession``) before dispatching.
    """
    return main(argv)


def run() -> None:
    """Run the CLI and raise :class:`SystemExit`.

    Returns
    -------
    None
        This function exits the process by raising ``SystemExit``.

    Examples
    --------
    >>> run.__name__
    'run'
    """

    raise SystemExit(main())
