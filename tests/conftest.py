from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-hw",
        action="store_true",
        default=False,
        help="run hardware tests",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "hardware: tests that require a powered-on Bluetooth fixture",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Handle hardware tests
    if config.getoption("--run-hw") or os.environ.get("GODOX_MESH_BT_HARDWARE") == "1":
        return

    skip_hardware = pytest.mark.skip(
        reason="use --run-hw or set GODOX_MESH_BT_HARDWARE=1 to run hardware tests",
    )
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip_hardware)


@dataclass(frozen=True)
class FakeBleDevice:
    name: str | None
    address: str


@dataclass(frozen=True)
class FakeAdvertisementData:
    local_name: str | None = None
    service_uuids: list[str] | None = None
    manufacturer_data: dict[int, bytes] | None = None
    rssi: int | None = None


class FakeBleakClient:
    def __init__(self, target: Any) -> None:
        self.address = target
        self.is_connected = False
        self.writes: list[tuple[str, bytes, bool]] = []
        self.started_notifications: list[tuple[str, Callable[[str, bytearray], None]]] = []
        self.stopped_notifications: list[str] = []

    async def connect(self) -> None:
        self.is_connected = True

    async def disconnect(self) -> None:
        self.is_connected = False

    async def write_gatt_char(self, characteristic: str, data: bytes, *, response: bool) -> None:
        self.writes.append((characteristic, data, response))

    async def start_notify(
        self,
        characteristic: str,
        callback: Callable[[str, bytearray], None],
    ) -> None:
        self.started_notifications.append((characteristic, callback))

    async def stop_notify(self, characteristic: str) -> None:
        self.stopped_notifications.append(characteristic)


@pytest.fixture
def fake_client_factory() -> tuple[Callable[[Any], FakeBleakClient], list[FakeBleakClient]]:
    clients: list[FakeBleakClient] = []

    def factory(target: Any) -> FakeBleakClient:
        client = FakeBleakClient(target)
        clients.append(client)
        return client

    return factory, clients


@pytest.fixture
def no_device_resolver() -> Callable[[str], Any]:
    async def resolver(address: str) -> None:
        return None

    return resolver
