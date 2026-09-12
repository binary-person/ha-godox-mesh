"""BLE client wrappers for raw GATT and Mesh Proxy traffic.

Examples
--------
>>> MESH_PROXY_DATA_IN_UUID
'00002add-0000-1000-8000-00805f9b34fb'
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from bleak import BleakClient, BleakScanner

logger = logging.getLogger(__name__)


NotificationCallback = Callable[[bytes], None]
DeviceResolver = Callable[[str], Awaitable[Any | None]]

MESH_PROXY_DATA_IN_UUID = "00002add-0000-1000-8000-00805f9b34fb"
MESH_PROXY_DATA_OUT_UUID = "00002ade-0000-1000-8000-00805f9b34fb"


class ProxyClient:
    """Small BLE wrapper for Bluetooth Mesh Proxy characteristics.

    Parameters
    ----------
    address
        Platform-specific BLE address or identifier.
    client_factory
        Optional Bleak-compatible client factory for tests or custom transports.

    Examples
    --------
    >>> client = ProxyClient("AA:BB", client_factory=lambda address: object())
    >>> client.address
    'AA:BB'
    """

    def __init__(
        self,
        address: str,
        *,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.address = address
        self._client_factory = client_factory or BleakClient
        self._client: Any | None = None
        self._callbacks: list[NotificationCallback] = []
        self._notifications_started = False

    @property
    def is_connected(self) -> bool:
        """Return whether the underlying BLE client is connected.

        Returns
        -------
        bool
            ``True`` when a client exists and reports ``is_connected``.

        Examples
        --------
        >>> ProxyClient("AA:BB", client_factory=lambda address: object()).is_connected
        False
        """

        return bool(self._client and getattr(self._client, "is_connected", False))

    async def connect(self) -> None:
        """Connect the proxy client if it is not already connected.

        Returns
        -------
        None
            The underlying BLE connection is opened in place. Reconnecting
            after a dropped link resets notification state, so callers must
            re-register any notification callbacks afterwards.

        Examples
        --------
        >>> import asyncio
        >>> class FakeClient:
        ...     is_connected = False
        ...     async def connect(self): self.is_connected = True
        >>> proxy = ProxyClient("AA:BB", client_factory=lambda address: FakeClient())
        >>> asyncio.run(proxy.connect())
        >>> proxy.is_connected
        True
        """

        if self.is_connected:
            return
        logger.info("connecting proxy client to %s", self.address)
        # A previous link may have dropped without disconnect() ever running —
        # the light lost power, a proxy rebooted, the device went out of range.
        # The replacement client carries none of the old session's state, so
        # notification bookkeeping has to start over with it or the resubscribe
        # below would be skipped and the device's beacon never seen again.
        self._notifications_started = False
        self._callbacks.clear()
        self._client = self._client_factory(self.address)
        await self._client.connect()
        mtu = getattr(self._client, "mtu_size", None)
        logger.info("proxy client connected (MTU=%s)", mtu)

    async def disconnect(self) -> None:
        """Disconnect the proxy client and clear notification callbacks.

        Returns
        -------
        None
            Connection state is updated on the underlying BLE client.

        Examples
        --------
        >>> import asyncio
        >>> class FakeClient:
        ...     is_connected = True
        ...     async def connect(self): pass
        ...     async def disconnect(self): self.is_connected = False
        >>> proxy = ProxyClient("AA:BB", client_factory=lambda address: FakeClient())
        >>> asyncio.run(proxy.connect())
        >>> asyncio.run(proxy.disconnect())
        >>> proxy.is_connected
        False
        """

        if self._client is None:
            return
        logger.info("disconnecting proxy client from %s", self.address)
        await self._client.disconnect()
        self._notifications_started = False
        self._callbacks.clear()
        logger.info("proxy client disconnected")

    async def write_proxy(self, data: bytes) -> None:
        """Write a complete Mesh Proxy PDU to Data In.

        Parameters
        ----------
        data
            Raw proxy PDU bytes. The first byte carries SAR/type; the remaining
            bytes carry the mesh PDU body.

        Returns
        -------
        None
            The byte payload is written without GATT response.

        Examples
        --------
        >>> import asyncio
        >>> class FakeClient:
        ...     is_connected = True
        ...     mtu_size = 23
        ...     writes = []
        ...     async def connect(self): pass
        ...     async def write_gatt_char(self, uuid, data, response):
        ...         self.writes.append((uuid, bytes(data), response))
        >>> fake = FakeClient()
        >>> proxy = ProxyClient("AA:BB", client_factory=lambda address: fake)
        >>> asyncio.run(proxy.connect())
        >>> asyncio.run(proxy.write_proxy(b"\\x00\\x01"))
        >>> fake.writes[0][1]
        b'\\x00\\x01'
        """

        if not self.is_connected or self._client is None:
            raise RuntimeError("proxy client is not connected")
        mtu = getattr(self._client, "mtu_size", None)
        max_write = (mtu - 3) if (mtu is not None and isinstance(mtu, int)) else None
        if max_write is not None and len(data) > max_write:
            logger.warning(
                "write %d bytes exceeds MTU max payload %d — may be silently dropped",
                len(data),
                max_write,
            )
        logger.info("writing %d proxy byte(s) [hex: %s]", len(data), data.hex())
        await self._client.write_gatt_char(MESH_PROXY_DATA_IN_UUID, data, response=False)

    async def start_notify(self, callback: NotificationCallback) -> None:
        """Start Mesh Proxy Data Out notifications.

        Parameters
        ----------
        callback
            Function called with each notification payload as immutable ``bytes``.

        Returns
        -------
        None
            Notification subscription is started once per connected session.

        Examples
        --------
        >>> import asyncio
        >>> seen = []
        >>> class FakeClient:
        ...     is_connected = True
        ...     async def connect(self): pass
        ...     async def start_notify(self, uuid, callback): callback(uuid, bytearray(b"\\x01"))
        >>> proxy = ProxyClient("AA:BB", client_factory=lambda address: FakeClient())
        >>> asyncio.run(proxy.connect())
        >>> asyncio.run(proxy.start_notify(seen.append))
        >>> seen
        [b'\\x01']
        """

        if not self.is_connected or self._client is None:
            raise RuntimeError("proxy client is not connected")

        if callback not in self._callbacks:
            self._callbacks.append(callback)

        if self._notifications_started:
            return

        logger.info("starting proxy notifications on %s", MESH_PROXY_DATA_OUT_UUID)

        def bleak_callback(_characteristic: Any, data: bytearray) -> None:
            pdu = bytes(data)
            for cb in list(self._callbacks):
                cb(pdu)

        await self._client.start_notify(MESH_PROXY_DATA_OUT_UUID, bleak_callback)
        self._notifications_started = True

    async def stop_notify(self, callback: NotificationCallback | None = None) -> None:
        """Stop Mesh Proxy notifications.

        Parameters
        ----------
        callback
            Optional callback to unregister. When omitted, all callbacks are
            cleared and GATT notifications are stopped.

        Returns
        -------
        None
            Notification subscription is stopped when no callbacks remain.

        Examples
        --------
        >>> import asyncio
        >>> class FakeClient:
        ...     is_connected = True
        ...     async def connect(self): pass
        ...     async def stop_notify(self, uuid): self.stopped = uuid
        >>> fake = FakeClient()
        >>> proxy = ProxyClient("AA:BB", client_factory=lambda address: fake)
        >>> asyncio.run(proxy.connect())
        >>> asyncio.run(proxy.stop_notify())
        >>> fake.stopped == MESH_PROXY_DATA_OUT_UUID
        True
        """

        if not self.is_connected or self._client is None:
            raise RuntimeError("proxy client is not connected")

        if callback is not None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)
            if self._callbacks:
                return

        logger.info("stopping proxy notifications on %s", MESH_PROXY_DATA_OUT_UUID)
        await self._client.stop_notify(MESH_PROXY_DATA_OUT_UUID)
        self._notifications_started = False
        self._callbacks.clear()


class GodoxMeshClient:
    """Generic BLE client for raw Godox GATT operations.

    Parameters
    ----------
    address
        Platform-specific BLE address or identifier.
    client_factory
        Bleak-compatible client factory.
    device_resolver
        Optional coroutine that resolves the address to a platform BLE object.

    Examples
    --------
    >>> GodoxMeshClient("AA:BB").address
    'AA:BB'
    """

    def __init__(
        self,
        address: str,
        *,
        client_factory: Callable[[str], Any] = BleakClient,
        device_resolver: DeviceResolver | None = None,
    ) -> None:
        self.address = address
        self._client_factory = client_factory
        self._device_resolver = device_resolver or _resolve_ble_device
        self._client: Any | None = None

    @property
    def is_connected(self) -> bool:
        """Return whether the underlying BLE client is connected.

        Returns
        -------
        bool
            ``True`` when a client exists and reports ``is_connected``.

        Examples
        --------
        >>> GodoxMeshClient("AA:BB").is_connected
        False
        """

        return bool(self._client and getattr(self._client, "is_connected", False))

    async def __aenter__(self) -> GodoxMeshClient:
        """Connect and return this client for async context manager use.

        Returns
        -------
        GodoxMeshClient
            Connected client instance.

        Examples
        --------
        >>> GodoxMeshClient("AA:BB").address
        'AA:BB'
        """

        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Disconnect when leaving an async context manager.

        Parameters
        ----------
        exc_type
            Exception type raised inside the context, if any.
        exc
            Exception value raised inside the context, if any.
        tb
            Traceback raised inside the context, if any.

        Returns
        -------
        None
            The BLE connection is closed.
        """

        await self.disconnect()

    async def connect(self) -> None:
        """Resolve and connect to the BLE device.

        Returns
        -------
        None
            The underlying BLE connection is opened in place.

        Examples
        --------
        >>> import asyncio
        >>> class FakeClient:
        ...     is_connected = False
        ...     async def connect(self): self.is_connected = True
        >>> async def resolver(address): return address
        >>> client = GodoxMeshClient("AA:BB", client_factory=lambda target: FakeClient(), device_resolver=resolver)
        >>> asyncio.run(client.connect())
        >>> client.is_connected
        True
        """

        if self.is_connected:
            return
        target = await self._device_resolver(self.address)
        logger.debug("connecting UL60Bi client to %s", target or self.address)
        self._client = self._client_factory(target or self.address)
        await self._client.connect()

    async def disconnect(self) -> None:
        """Disconnect from the BLE device.

        Returns
        -------
        None
            Connection state is updated on the underlying BLE client.

        Examples
        --------
        >>> import asyncio
        >>> class FakeClient:
        ...     is_connected = True
        ...     async def connect(self): pass
        ...     async def disconnect(self): self.is_connected = False
        >>> async def resolver(address): return address
        >>> client = GodoxMeshClient("AA:BB", client_factory=lambda target: FakeClient(), device_resolver=resolver)
        >>> asyncio.run(client.connect())
        >>> asyncio.run(client.disconnect())
        >>> client.is_connected
        False
        """

        if self._client is None:
            return
        logger.debug("disconnecting UL60Bi client from %s", self.address)
        await self._client.disconnect()

    async def write_raw(
        self,
        characteristic: str,
        payload: bytes,
        *,
        response: bool,
    ) -> None:
        """Write raw bytes to a GATT characteristic.

        Parameters
        ----------
        characteristic
            Characteristic UUID or handle accepted by Bleak.
        payload
            Exact byte payload to send; length and byte order are preserved.
        response
            Whether to request a GATT write response.

        Returns
        -------
        None
            Payload is written to the connected device.

        Examples
        --------
        >>> import asyncio
        >>> class FakeClient:
        ...     is_connected = True
        ...     writes = []
        ...     async def connect(self): pass
        ...     async def write_gatt_char(self, characteristic, payload, response):
        ...         self.writes.append((characteristic, bytes(payload), response))
        >>> fake = FakeClient()
        >>> async def resolver(address): return address
        >>> client = GodoxMeshClient("AA:BB", client_factory=lambda target: fake, device_resolver=resolver)
        >>> asyncio.run(client.connect())
        >>> asyncio.run(client.write_raw("char", b"\\x01\\x02", response=True))
        >>> fake.writes
        [('char', b'\\x01\\x02', True)]
        """

        client = self._require_client()
        logger.debug("writing raw payload to %s", characteristic)
        await client.write_gatt_char(characteristic, payload, response=response)

    async def start_notify(
        self,
        characteristic: str,
        callback: NotificationCallback,
    ) -> None:
        """Start notifications for a raw GATT characteristic.

        Parameters
        ----------
        characteristic
            Characteristic UUID or handle accepted by Bleak.
        callback
            Function called with each notification payload as immutable ``bytes``.

        Returns
        -------
        None
            Notification subscription is started on the connected device.

        Examples
        --------
        >>> import asyncio
        >>> seen = []
        >>> class FakeClient:
        ...     is_connected = True
        ...     async def connect(self): pass
        ...     async def start_notify(self, characteristic, callback):
        ...         callback(characteristic, bytearray(b"\\x03"))
        >>> async def resolver(address): return address
        >>> client = GodoxMeshClient("AA:BB", client_factory=lambda target: FakeClient(), device_resolver=resolver)
        >>> asyncio.run(client.connect())
        >>> asyncio.run(client.start_notify("char", seen.append))
        >>> seen
        [b'\\x03']
        """

        client = self._require_client()
        logger.debug("starting notifications on %s", characteristic)

        def bleak_callback(_characteristic: Any, data: bytearray) -> None:
            callback(bytes(data))

        await client.start_notify(characteristic, bleak_callback)

    async def stop_notify(self, characteristic: str) -> None:
        """Stop notifications for a raw GATT characteristic.

        Parameters
        ----------
        characteristic
            Characteristic UUID or handle accepted by Bleak.

        Returns
        -------
        None
            Notification subscription is stopped on the connected device.

        Examples
        --------
        >>> import asyncio
        >>> class FakeClient:
        ...     is_connected = True
        ...     async def connect(self): pass
        ...     async def stop_notify(self, characteristic): self.stopped = characteristic
        >>> fake = FakeClient()
        >>> async def resolver(address): return address
        >>> client = GodoxMeshClient("AA:BB", client_factory=lambda target: fake, device_resolver=resolver)
        >>> asyncio.run(client.connect())
        >>> asyncio.run(client.stop_notify("char"))
        >>> fake.stopped
        'char'
        """

        client = self._require_client()
        await client.stop_notify(characteristic)

    def _require_client(self) -> Any:
        if not self.is_connected or self._client is None:
            raise RuntimeError("client is not connected")
        return self._client


async def _resolve_ble_device(address: str) -> Any | None:
    return await BleakScanner.find_device_by_address(address, timeout=10.0)
