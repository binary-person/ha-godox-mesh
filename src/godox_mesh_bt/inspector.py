"""Inspect BLE GATT services and render them for humans.

Examples
--------
>>> service = ServiceInfo("180f", 1, (CharacteristicInfo("2a19", 2, ("read",)),))
>>> "2a19" in render_markdown(address="AA:BB", services=[service])
True
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from bleak import BleakClient

from godox_mesh_bt.client import DeviceResolver, _resolve_ble_device

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DescriptorInfo:
    """GATT descriptor metadata.

    Parameters
    ----------
    uuid
        Descriptor UUID string.
    handle
        Platform GATT handle, if available.

    Examples
    --------
    >>> DescriptorInfo("2902", 3).to_dict()
    {'uuid': '2902', 'handle': 3}
    """

    uuid: str
    handle: int | None

    def to_dict(self) -> dict[str, object]:
        """Convert descriptor metadata to JSON-serializable values.

        Returns
        -------
        dict[str, object]
            Descriptor UUID and handle.

        Examples
        --------
        >>> DescriptorInfo("2902", None).to_dict()["handle"] is None
        True
        """

        return {
            "uuid": self.uuid,
            "handle": self.handle,
        }


@dataclass(frozen=True)
class CharacteristicInfo:
    """GATT characteristic metadata.

    Parameters
    ----------
    uuid
        Characteristic UUID string.
    handle
        Platform GATT handle, if available.
    properties
        Characteristic properties such as ``read``, ``write``, or ``notify``.
    descriptors
        Descriptor metadata attached to this characteristic.

    Examples
    --------
    >>> CharacteristicInfo("2a19", 2, ("read",)).to_dict()["properties"]
    ['read']
    """

    uuid: str
    handle: int | None
    properties: tuple[str, ...]
    descriptors: tuple[DescriptorInfo, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Convert characteristic metadata to JSON-serializable values.

        Returns
        -------
        dict[str, object]
            Characteristic UUID, handle, properties, and descriptors.

        Examples
        --------
        >>> CharacteristicInfo("2a19", 2, ("read",), (DescriptorInfo("2902", 3),)).to_dict()["descriptors"][0]["uuid"]
        '2902'
        """

        return {
            "uuid": self.uuid,
            "handle": self.handle,
            "properties": list(self.properties),
            "descriptors": [descriptor.to_dict() for descriptor in self.descriptors],
        }


@dataclass(frozen=True)
class ServiceInfo:
    """GATT service metadata.

    Parameters
    ----------
    uuid
        Service UUID string.
    handle
        Platform GATT handle, if available.
    characteristics
        Characteristic metadata exposed by this service.

    Examples
    --------
    >>> ServiceInfo("180f", 1).to_dict()["uuid"]
    '180f'
    """

    uuid: str
    handle: int | None
    characteristics: tuple[CharacteristicInfo, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Convert service metadata to JSON-serializable values.

        Returns
        -------
        dict[str, object]
            Service UUID, handle, and nested characteristics.

        Examples
        --------
        >>> ServiceInfo("180f", 1).to_dict()["characteristics"]
        []
        """

        return {
            "uuid": self.uuid,
            "handle": self.handle,
            "characteristics": [
                characteristic.to_dict()
                for characteristic in self.characteristics
            ],
        }

    def writable_characteristics(self) -> tuple[CharacteristicInfo, ...]:
        """Return characteristics that support write operations.

        Returns
        -------
        tuple[CharacteristicInfo, ...]
            Characteristics with ``write`` or ``write-without-response``.

        Examples
        --------
        >>> service = ServiceInfo("svc", 1, (CharacteristicInfo("c", 2, ("write",)),))
        >>> service.writable_characteristics()[0].uuid
        'c'
        """

        return tuple(
            characteristic
            for characteristic in self.characteristics
            if {"write", "write-without-response"} & set(characteristic.properties)
        )

    def notifiable_characteristics(self) -> tuple[CharacteristicInfo, ...]:
        """Return characteristics that support notifications.

        Returns
        -------
        tuple[CharacteristicInfo, ...]
            Characteristics with ``notify`` or ``indicate``.

        Examples
        --------
        >>> service = ServiceInfo("svc", 1, (CharacteristicInfo("c", 2, ("notify",)),))
        >>> service.notifiable_characteristics()[0].uuid
        'c'
        """

        return tuple(
            characteristic
            for characteristic in self.characteristics
            if {"notify", "indicate"} & set(characteristic.properties)
        )


@dataclass(frozen=True)
class InspectionResult:
    """Complete GATT inspection result for one BLE device.

    Parameters
    ----------
    address
        Platform-specific BLE address or identifier inspected.
    services
        Services discovered on the device.

    Examples
    --------
    >>> InspectionResult("AA:BB").to_dict()
    {'address': 'AA:BB', 'services': []}
    """

    address: str
    services: tuple[ServiceInfo, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        """Convert inspection results to JSON-serializable values.

        Returns
        -------
        dict[str, object]
            Address and service metadata.

        Examples
        --------
        >>> InspectionResult("AA:BB").to_dict()["address"]
        'AA:BB'
        """

        return {
            "address": self.address,
            "services": [service.to_dict() for service in self.services],
        }

    def to_markdown(self) -> str:
        """Render inspection results as Markdown.

        Returns
        -------
        str
            Markdown report for the inspected device.

        Examples
        --------
        >>> InspectionResult("AA:BB").to_markdown().splitlines()[0]
        '# GATT Inspection: AA:BB'
        """

        return render_markdown(address=self.address, services=self.services)


async def inspect_device(
    address: str,
    *,
    client_factory: Any = BleakClient,
    device_resolver: DeviceResolver | None = None,
) -> InspectionResult:
    """Inspect services on a BLE device.

    Parameters
    ----------
    address
        Platform-specific BLE address or identifier.
    client_factory
        Bleak-compatible async context manager factory.
    device_resolver
        Optional resolver that maps an address to a platform BLE device object.

    Returns
    -------
    InspectionResult
        GATT services, characteristics, descriptors, and properties.

    Examples
    --------
    >>> import asyncio
    >>> class Client:
    ...     services = []
    ...     async def __aenter__(self): return self
    ...     async def __aexit__(self, exc_type, exc, tb): return None
    >>> def factory(address): return Client()
    >>> async def resolver(address): return address
    >>> asyncio.run(inspect_device("AA:BB", client_factory=factory, device_resolver=resolver)).services
    ()
    """

    resolver = device_resolver or _resolve_ble_device
    target = await resolver(address)
    logger.debug("inspecting device %s resolved to %s", address, target or address)
    async with client_factory(target or address) as client:
        services = getattr(client, "services")
        return InspectionResult(
            address=address,
            services=tuple(_service_info(service) for service in services),
        )


def render_markdown(*, address: str, services: Iterable[ServiceInfo]) -> str:
    """Render GATT service metadata as Markdown.

    Parameters
    ----------
    address
        Platform-specific BLE address or identifier.
    services
        Service metadata to render.

    Returns
    -------
    str
        Markdown report ending with a newline.

    Examples
    --------
    >>> render_markdown(address="AA:BB", services=[]).splitlines()[0]
    '# GATT Inspection: AA:BB'
    """

    lines = [f"# GATT Inspection: {address}", ""]
    for service in services:
        lines.append(f"- Service `{service.uuid}` (handle {service.handle})")
        for characteristic in service.characteristics:
            properties = ", ".join(sorted(characteristic.properties)) or "none"
            lines.append(
                f"  - Characteristic `{characteristic.uuid}` "
                f"(handle {characteristic.handle}): {properties}"
            )
            for descriptor in characteristic.descriptors:
                lines.append(
                    f"    - Descriptor `{descriptor.uuid}` "
                    f"(handle {descriptor.handle})"
                )
    lines.append("")
    logger.info("rendered inspection markdown for %s", address)
    return "\n".join(lines)


def _service_info(service: Any) -> ServiceInfo:
    return ServiceInfo(
        uuid=str(getattr(service, "uuid")),
        handle=_optional_int(getattr(service, "handle", None)),
        characteristics=tuple(
            _characteristic_info(characteristic)
            for characteristic in getattr(service, "characteristics", ())
        ),
    )


def _characteristic_info(characteristic: Any) -> CharacteristicInfo:
    return CharacteristicInfo(
        uuid=str(getattr(characteristic, "uuid")),
        handle=_optional_int(getattr(characteristic, "handle", None)),
        properties=tuple(getattr(characteristic, "properties", ())),
        descriptors=tuple(
            _descriptor_info(descriptor)
            for descriptor in getattr(characteristic, "descriptors", ())
        ),
    )


def _descriptor_info(descriptor: Any) -> DescriptorInfo:
    return DescriptorInfo(
        uuid=str(getattr(descriptor, "uuid")),
        handle=_optional_int(getattr(descriptor, "handle", None)),
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
