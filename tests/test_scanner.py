from __future__ import annotations

import pytest

from dataclasses import dataclass

from godox_mesh_bt.scanner import (
    Advertisement,
    DiscoveredDevice,
    is_likely_godox_device,
    scan,
)


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


def test_discovered_device_serializes_advertisement_data() -> None:
    device = DiscoveredDevice(
        name="Godox Bluetooth Mesh",
        address="AA:BB:CC:DD:EE:FF",
        rssi=-55,
        advertisement=Advertisement(
            local_name="Godox Bluetooth Mesh",
            service_uuids=("0000feed-0000-1000-8000-00805f9b34fb",),
            manufacturer_data={123: "010203"},
        ),
        likely_godox=True,
    )

    assert device.to_dict() == {
        "name": "Godox Bluetooth Mesh",
        "address": "AA:BB:CC:DD:EE:FF",
        "rssi": -55,
        "likely_godox": True,
        "advertisement": {
            "local_name": "Godox Bluetooth Mesh",
            "service_uuids": ["0000feed-0000-1000-8000-00805f9b34fb"],
            "manufacturer_data": {"123": "010203"},
        },
    }


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Godox Bluetooth Mesh", True),
        ("GODOX Light", True),
        ("GD_LED", True),
        ("UL60BI-Lite", True),
        ("Aputure Light", False),
        (None, False),
    ],
)
def test_matches_likely_godox_names(name: str | None, expected: bool) -> None:
    device = DiscoveredDevice(
        name=name,
        address="device-id",
        rssi=None,
        advertisement=Advertisement(),
        likely_godox=False,
    )

    assert is_likely_godox_device(device) is expected


@pytest.mark.asyncio
async def test_scan_returns_structured_devices_from_discover_results() -> None:
    async def fake_discover(*, timeout: float, return_adv: bool) -> dict[str, tuple[FakeBleDevice, FakeAdvertisementData]]:
        assert timeout == 2.5
        assert return_adv is True
        return {
            "opaque-macos-id": (
                FakeBleDevice(name=None, address="opaque-macos-id"),
                FakeAdvertisementData(
                    local_name="Godox Bluetooth Mesh",
                    service_uuids=["service-1"],
                    manufacturer_data={1: b"\x01\x02"},
                    rssi=-61,
                ),
            )
        }

    devices = await scan(timeout=2.5, discover=fake_discover)

    assert devices == [
        DiscoveredDevice(
            name="Godox Bluetooth Mesh",
            address="opaque-macos-id",
            rssi=-61,
            advertisement=Advertisement(
                local_name="Godox Bluetooth Mesh",
                service_uuids=("service-1",),
                manufacturer_data={1: "0102"},
            ),
            likely_godox=True,
        )
    ]


@pytest.mark.asyncio
async def test_scan_handles_empty_results() -> None:
    async def fake_discover(*, timeout: float, return_adv: bool) -> dict[str, tuple[FakeBleDevice, FakeAdvertisementData]]:
        return {}

    assert await scan(discover=fake_discover) == []


def test_scan_unprovisioned_returns_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """scan_unprovisioned returns a list of DiscoveredDevice."""
    import asyncio
    from godox_mesh_bt.scanner import scan_unprovisioned

    async def fake_scan(service_uuids: list[str], timeout: float) -> list[object]:
        from unittest.mock import MagicMock
        dev = MagicMock()
        dev.name = "GD_LED_UNPROV"
        dev.address = "AA:BB:CC:DD:EE:FF"
        return [dev]

    monkeypatch.setattr("godox_mesh_bt.scanner.BleakScanner.discover", fake_scan)
    results = asyncio.run(scan_unprovisioned(timeout=3.0))
    assert isinstance(results, list)


def test_scan_unprovisioned_uses_provisioning_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    """scan_unprovisioned filters on service UUID 0x1827."""
    import asyncio
    from godox_mesh_bt.scanner import scan_unprovisioned
    captured: dict[str, list[str]] = {}

    async def fake_scan(service_uuids: list[str], timeout: float) -> list[object]:
        captured["uuids"] = service_uuids
        return []

    monkeypatch.setattr("godox_mesh_bt.scanner.BleakScanner.discover", fake_scan)
    asyncio.run(scan_unprovisioned(timeout=1.0))
    assert any("1827" in str(u).lower() for u in captured.get("uuids", []))
