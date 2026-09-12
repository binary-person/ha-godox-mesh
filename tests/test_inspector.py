from __future__ import annotations

from dataclasses import dataclass

import pytest

from godox_mesh_bt.inspector import (
    CharacteristicInfo,
    DescriptorInfo,
    ServiceInfo,
    inspect_device,
    render_markdown,
)


@dataclass(frozen=True)
class FakeDescriptor:
    uuid: str
    handle: int


@dataclass(frozen=True)
class FakeCharacteristic:
    uuid: str
    handle: int
    properties: list[str]
    descriptors: list[FakeDescriptor]


@dataclass(frozen=True)
class FakeService:
    uuid: str
    handle: int
    characteristics: list[FakeCharacteristic]


class FakeClient:
    def __init__(self, target: object) -> None:
        self.address = target
        self.connected = False
        self.services = [
            FakeService(
                uuid="service-1",
                handle=1,
                characteristics=[
                    FakeCharacteristic(
                        uuid="char-write",
                        handle=2,
                        properties=["write", "write-without-response"],
                        descriptors=[FakeDescriptor(uuid="desc-1", handle=3)],
                    ),
                    FakeCharacteristic(
                        uuid="char-notify",
                        handle=4,
                        properties=["notify"],
                        descriptors=[],
                    ),
                ],
            )
        ]

    async def __aenter__(self) -> FakeClient:
        self.connected = True
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.connected = False


async def no_device_resolver(address: str) -> None:
    return None


def test_service_info_serializes_nested_characteristics() -> None:
    service = ServiceInfo(
        uuid="service-1",
        handle=1,
        characteristics=(
            CharacteristicInfo(
                uuid="char-write",
                handle=2,
                properties=("write",),
                descriptors=(DescriptorInfo(uuid="desc-1", handle=3),),
            ),
        ),
    )

    assert service.to_dict() == {
        "uuid": "service-1",
        "handle": 1,
        "characteristics": [
            {
                "uuid": "char-write",
                "handle": 2,
                "properties": ["write"],
                "descriptors": [{"uuid": "desc-1", "handle": 3}],
            }
        ],
    }


@pytest.mark.asyncio
async def test_inspect_device_reads_services_from_client_factory() -> None:
    inspection = await inspect_device(
        "device-id",
        client_factory=FakeClient,
        device_resolver=no_device_resolver,
    )

    assert inspection.address == "device-id"
    assert inspection.services == (
        ServiceInfo(
            uuid="service-1",
            handle=1,
            characteristics=(
                CharacteristicInfo(
                    uuid="char-write",
                    handle=2,
                    properties=("write", "write-without-response"),
                    descriptors=(DescriptorInfo(uuid="desc-1", handle=3),),
                ),
                CharacteristicInfo(
                    uuid="char-notify",
                    handle=4,
                    properties=("notify",),
                    descriptors=(),
                ),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_inspect_device_resolves_live_ble_device_before_connecting() -> None:
    live_device = object()
    targets: list[object] = []

    class RecordingClient(FakeClient):
        def __init__(self, target: object) -> None:
            targets.append(target)
            super().__init__(target)

    async def resolver(address: str) -> object:
        assert address == "device-id"
        return live_device

    await inspect_device(
        "device-id",
        client_factory=RecordingClient,
        device_resolver=resolver,
    )

    assert targets == [live_device]


def test_inspection_identifies_writable_and_notifiable_characteristics() -> None:
    service = ServiceInfo(
        uuid="service-1",
        handle=1,
        characteristics=(
            CharacteristicInfo(uuid="read-only", handle=2, properties=("read",)),
            CharacteristicInfo(uuid="writer", handle=3, properties=("write",)),
            CharacteristicInfo(uuid="notifier", handle=4, properties=("notify",)),
        ),
    )

    assert [char.uuid for char in service.writable_characteristics()] == ["writer"]
    assert [char.uuid for char in service.notifiable_characteristics()] == ["notifier"]


def test_render_markdown_highlights_properties() -> None:
    markdown = render_markdown(
        address="device-id",
        services=(
            ServiceInfo(
                uuid="service-1",
                handle=1,
                characteristics=(
                    CharacteristicInfo(
                        uuid="writer",
                        handle=2,
                        properties=("write", "notify"),
                    ),
                ),
            ),
        ),
    )

    assert "# GATT Inspection: device-id" in markdown
    assert "- Service `service-1` (handle 1)" in markdown
    assert "Characteristic `writer` (handle 2): notify, write" in markdown
