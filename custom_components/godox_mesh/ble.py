"""Bridge the library's Bleak client factory onto Home Assistant's BLE stack.

``godox_mesh_bt`` builds its own :class:`bleak.BleakClient` and, by default,
runs a :class:`bleak.BleakScanner` to resolve an address. Neither is allowed
inside Home Assistant: the address must come from the Bluetooth manager so that
ESPHome proxies and multiple adapters keep working, and connections must go
through ``bleak-retry-connector`` for its retry and connection-slot handling.

:class:`HomeAssistantBleakClient` presents the small slice of the Bleak surface
the library actually uses, while connecting the Home Assistant way.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 20.0


class DeviceNotFound(Exception):
    """Raised when the Bluetooth manager cannot currently see the light."""


class HomeAssistantBleakClient:
    """Bleak-compatible client backed by the Home Assistant Bluetooth manager.

    The library calls ``client_factory(address)`` and then ``await
    client.connect()``. Construction therefore stays cheap and every bit of the
    real work happens in :meth:`connect`, where the address can be resolved
    against whichever adapter or proxy currently hears the device.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        *,
        use_services_cache: bool = True,
        max_attempts: int = 4,
        on_drop: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the client wrapper.

        Set ``use_services_cache=False`` when the device's GATT table is
        expected to have changed since it was last seen. Provisioning is the
        case that matters: a light on a mesh exposes the Mesh Proxy service
        (0x1827 is absent), and after a reset it exposes Mesh Provisioning
        instead. Reusing the cached table then hides the very characteristic
        provisioning has to write to, and the exchange times out waiting for a
        reply that was never solicited.

        ``max_attempts`` bounds ``bleak-retry-connector``'s own retries, which
        are all to *this one* address. The mesh link keeps it low so a node
        that will not connect is abandoned quickly and another can be tried;
        provisioning, which has no other node to fall back to, keeps the
        library default.

        ``on_drop`` is called with the address when the connection drops on its
        own -- a supervision timeout, the node going away -- but not when it is
        closed deliberately here, so the link can tell an unreliable node from
        one it chose to let go of.
        """
        self._hass = hass
        self._address = address
        self._name = name
        self._use_services_cache = use_services_cache
        self._max_attempts = max_attempts
        self._on_drop = on_drop
        self._closing = False
        self._client: BleakClientWithServiceCache | None = None
        self._dropped = False

    @property
    def address(self) -> str:
        """Return the address this client connects to."""
        return self._address

    @property
    def is_connected(self) -> bool:
        """Return whether the underlying Bleak client is connected.

        Proxy writes are sent without a GATT response, so a command written
        into a connection that has silently died would look like it succeeded.
        The disconnect callback lets that be noticed at once rather than
        whenever Bleak next updates its own state.
        """
        if self._dropped:
            return False
        return self._client is not None and self._client.is_connected

    def _on_disconnected(self, _client: Any) -> None:
        _LOGGER.debug("connection to %s dropped", self._address)
        self._dropped = True
        # Only an *unsolicited* drop signals an unreliable node; a disconnect we
        # asked for (idle close, release, shutdown) sets _closing first.
        if self._on_drop is not None and not self._closing:
            self._on_drop(self._address)

    @property
    def mtu_size(self) -> int | None:
        """Return the negotiated MTU, which the library checks before writing."""
        if self._client is None:
            return None
        return self._client.mtu_size

    def _resolve(self) -> Any:
        return bluetooth.async_ble_device_from_address(
            self._hass, self._address, connectable=True
        )

    async def connect(self, **_kwargs: Any) -> None:
        """Establish a connection through ``bleak-retry-connector``."""
        if self.is_connected:
            return
        self._dropped = False
        self._closing = False
        device = self._resolve()
        if device is None:
            raise DeviceNotFound(
                f"{self._name} ({self._address}) is not currently in range of any "
                "Bluetooth adapter or proxy"
            )
        _LOGGER.debug("establishing connection to %s", self._address)
        self._client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            self._name,
            disconnected_callback=self._on_disconnected,
            ble_device_callback=self._resolve,
            timeout=CONNECT_TIMEOUT,
            use_services_cache=self._use_services_cache,
            max_attempts=self._max_attempts,
        )

    async def disconnect(self) -> None:
        """Close the connection if one is open."""
        # Mark the close as deliberate before it happens, so the disconnect
        # callback does not report it as an unreliable node dropping.
        self._closing = True
        client, self._client = self._client, None
        self._dropped = False
        if client is not None:
            await client.disconnect()

    async def __aenter__(self) -> HomeAssistantBleakClient:
        """Connect for ``async with`` use, as the provisioning sessions expect."""
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Disconnect when leaving an ``async with`` block."""
        await self.disconnect()

    def _require(self) -> BleakClientWithServiceCache:
        if self._client is None:
            raise DeviceNotFound(f"{self._name} is not connected")
        return self._client

    async def write_gatt_char(
        self, characteristic: str, data: bytes, response: bool = False
    ) -> None:
        """Write a characteristic value."""
        await self._require().write_gatt_char(characteristic, data, response=response)

    async def read_gatt_char(self, characteristic: str) -> bytes:
        """Read a characteristic value."""
        return bytes(await self._require().read_gatt_char(characteristic))

    async def has_service(self, service_uuid: str) -> bool:
        """Return whether the connected device exposes a service."""
        client = self._require()
        return client.services.get_service(service_uuid) is not None

    async def start_notify(self, characteristic: str, callback: Any) -> None:
        """Subscribe to characteristic notifications."""
        await self._require().start_notify(characteristic, callback)

    async def stop_notify(self, characteristic: str) -> None:
        """Unsubscribe from characteristic notifications."""
        await self._require().stop_notify(characteristic)
