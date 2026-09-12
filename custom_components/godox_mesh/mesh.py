"""The shared Bluetooth Mesh proxy link behind every Godox light entity.

A Bluetooth Mesh network is addressed through any one of its nodes: connect to a
single light over the Mesh Proxy service and every other node on the same
network is reachable through it. This integration therefore keeps exactly one
connection per config entry and fans commands out by unicast address, rather
than connecting to each light separately.

Two invariants matter here:

* **One writer at a time.** Every PDU consumes the network's single sequence
  counter, so commands are serialized through a lock.
* **Sequence numbers never repeat.** Each node keeps a replay protection list
  and silently drops anything at or below the number it last saw, so the
  reserved high-water mark is persisted ahead of use.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from functools import partial
from collections.abc import Awaitable, Callable
from typing import TypeVar

from ._lib import GodoxController, MeshState, SequenceReserver
from ._lib.protocol import StatusResponse
from ._lib.config_session import ConfigSession
from ._lib.provisioning import ProvisioningSession

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from .ble import DeviceNotFound, HomeAssistantBleakClient
from .gateway import async_select_gateway
from .const import (
    BEACON_WAIT_TIMEOUT,
    IDLE_DISCONNECT_SECONDS,
    PROXY_CONFIG_ACK_TIMEOUT,
    SEQUENCE_BLOCK_SIZE,
)
from .store import GodoxSequenceStore

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")


class GodoxMeshLink:
    """Own the proxy connection and the sequence counter for one mesh network."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        state: MeshState,
        store: GodoxSequenceStore,
        sequence_number: int,
    ) -> None:
        """Initialize the link.

        Parameters
        ----------
        sequence_number
            High-water mark recovered from :class:`GodoxSequenceStore`, which
            is at or above anything a previous run can have transmitted.
        """
        self._hass = hass
        self._address = address
        self._name = name
        self._store = store
        self._lock = asyncio.Lock()
        self._cancel_disconnect: Callable[[], None] | None = None
        self._gateway: str | None = None
        # Bumped after every command so a timer that fired while a command was
        # waiting on the lock can tell that the link is not idle after all.
        self._activity = 0
        self._shutdown = False

        self._reserver = SequenceReserver(
            sequence_number,
            block_size=SEQUENCE_BLOCK_SIZE,
            persist=store.async_set_sequence_number,
        )
        self._controller = GodoxController(
            address,
            state=dataclasses.replace(state, sequence_number=sequence_number),
            client_factory=self._build_client,
            state_writer=self._on_sequence_advanced,
            beacon_wait_timeout=BEACON_WAIT_TIMEOUT,
            proxy_config_ack_timeout=PROXY_CONFIG_ACK_TIMEOUT,
        )

    def _build_client(self, _target: str) -> HomeAssistantBleakClient:
        """Build a transport aimed at whichever node can currently be reached.

        Called afresh by the proxy client on every reconnect, which is what
        makes losing the configured light survivable: the network is entered
        through some other node instead.
        """
        self._gateway = async_select_gateway(
            self._hass,
            network_key=self._controller.state.network_key,
            preferred=self._address,
            current=self._gateway,
        )
        return HomeAssistantBleakClient(self._hass, self._gateway, self._name)

    @property
    def gateway_address(self) -> str | None:
        """Return the node currently being used to enter the mesh."""
        return self._gateway

    @callback
    def _on_sequence_advanced(self, state: MeshState) -> None:
        """Track the counter the controller owns and reserve ahead of it."""
        self._reserver.observe(state.sequence_number)

    @property
    def address(self) -> str:
        """Return the BLE address of the proxy node."""
        return self._address

    @property
    def proxy_node_address(self) -> int:
        """Return the unicast address of the node holding the BLE connection."""
        return self._controller.state.node_address

    async def async_set_light(
        self,
        node_address: int,
        *,
        brightness_pct: int,
        kelvin: int,
        min_kelvin: int | None = None,
        max_kelvin: int | None = None,
    ) -> None:
        """Turn a node on at a given brightness and colour temperature."""
        await self._async_run(
            lambda: self._controller.set_params(
                brightness=brightness_pct,
                cct=kelvin,
                dst=node_address,
                min_kelvin=min_kelvin,
                max_kelvin=max_kelvin,
            )
        )

    async def async_set_effect(
        self, node_address: int, *, effect: int, brightness_pct: int, speed: int = 0
    ) -> None:
        """Run a lighting effect on a node, at the given speed."""
        await self._async_run(
            lambda: self._controller.set_effect(
                effect, brightness=brightness_pct, speed=speed, dst=node_address
            )
        )

    async def async_set_fan_mode(self, node_address: int, mode: int) -> None:
        """Set a node's fan or cooling mode."""
        await self._async_run(
            lambda: self._controller.set_fan_mode(mode, dst=node_address)
        )

    async def async_turn_on(self, node_address: int) -> None:
        """Send the power-on command to a node."""
        await self._async_run(lambda: self._controller.power_on(dst=node_address))

    async def async_turn_off(self, node_address: int) -> None:
        """Send the power-off command to a node."""
        await self._async_run(lambda: self._controller.power_off(dst=node_address))

    async def async_request_status(self, node_address: int) -> StatusResponse:
        """Query a node's live state.

        Brightness is live on stock firmware. Colour temperature is live on
        most lights, but a few report a placeholder after a change made on the
        light's own controls; the entry's colour-temperature polling option
        exists to switch it off in that case.
        """
        return await self._async_run(
            lambda: self._controller.request_status(dst=node_address)
        )

    async def async_request_battery(self, node_address: int) -> int:
        """Query a node's battery charge percentage.

        Works on stock firmware. Only meaningful on a battery-powered model;
        a mains light answers a constant 100 %. Raises if the light does not
        answer at all.
        """
        battery = await self._async_run(
            lambda: self._controller.request_battery(dst=node_address)
        )
        return battery.power_percent

    async def async_request_version(self, node_address: int) -> int:
        """Return a node's reported BLE firmware version.

        Answered from an immediate by the BLE chip, so it is reliable on stock
        firmware too. The readback patch makes a light report version 1.
        """
        return await self._async_run(
            lambda: self._controller.request_version(dst=node_address)
        )


    async def async_provision_node(
        self, target_address: str, node_address: int, name: str
    ) -> tuple[str, int]:
        """Provision a factory-reset light onto *this* mesh network.

        Reuses the network's existing network and application keys so the new
        light joins the network this entry already drives, rather than forming
        an isolated one of its own.

        Parameters
        ----------
        target_address
            BLE address of the factory-reset light, currently advertising the
            Mesh Provisioning Service.
        node_address
            Unicast address to assign to the new node.
        name
            Display name, used for connection logging.

        Returns
        -------
        tuple[str, int]
            The new node's device key -- needed to re-bind the application key
            later, and unrecoverable afterwards -- and how many elements the
            light reported, which the address allocator needs so the next light
            is not placed on top of this one's elements.
        """
        async with self._lock:
            if self._shutdown:
                raise HomeAssistantError(f"{self._name} is shutting down")

            state = self._controller.state

            def client_factory(_target: str) -> HomeAssistantBleakClient:
                # Same reason as the config flow's provisioning path: the
                # light's GATT table changes as it joins the mesh, so a cached
                # one hides the provisioning characteristics.
                return HomeAssistantBleakClient(
                    self._hass, target_address, name, use_services_cache=False
                )

            _LOGGER.debug(
                "provisioning %s onto this network as 0x%04x",
                target_address,
                node_address,
            )
            provisioned = await ProvisioningSession(
                address=target_address,
                net_key=bytes.fromhex(state.network_key),
                key_index=0,
                iv_index=state.iv_index,
                unicast_address=node_address,
                provisioner_address=state.provisioner_address,
                client_factory=client_factory,
            ).run()

            # The node now holds the network key but has no application key
            # bound to its vendor model, so it would ignore every light
            # command. Bind using the node's own device key, continuing the
            # network's sequence counter so nothing is replayed.
            bind_state = dataclasses.replace(
                state,
                device_key=provisioned.device_key,
                node_address=node_address,
            )
            final = await ConfigSession(
                address=target_address,
                state=bind_state,
                client_factory=client_factory,
            ).run()

            self._adopt_sequence(final.sequence_number)
            _LOGGER.info("provisioned %s as node 0x%04x", target_address, node_address)
            return provisioned.device_key, provisioned.num_elements

    def _adopt_sequence(self, sequence_number: int) -> None:
        """Take on sequence numbers consumed outside the controller."""
        if sequence_number <= self._controller.state.sequence_number:
            return
        self._controller.state = dataclasses.replace(
            self._controller.state, sequence_number=sequence_number
        )
        self._reserver.observe(sequence_number - 1)

    async def _async_run(self, operation: Callable[[], Awaitable[_T]]) -> _T:
        """Serialize one operation onto the shared connection and return its result."""
        async with self._lock:
            if self._shutdown:
                raise HomeAssistantError(f"{self._name} is shutting down")
            self._cancel_idle_disconnect()
            try:
                await self._async_connect()
                result = await operation()
            except DeviceNotFound as err:
                await self._async_close()
                raise HomeAssistantError(str(err)) from err
            except Exception as err:
                # The connection is in an unknown state; drop it so the next
                # command starts from a clean handshake.
                await self._async_close()
                raise HomeAssistantError(
                    f"Error sending command to {self._name}: {err}"
                ) from err
            self._activity += 1
            self._schedule_idle_disconnect()
            return result

    async def _async_connect(self) -> None:
        if self._controller.is_connected:
            return
        _LOGGER.debug("opening mesh proxy connection to %s", self._address)
        await self._controller.connect()

    def _cancel_idle_disconnect(self) -> None:
        if self._cancel_disconnect is not None:
            self._cancel_disconnect()
            self._cancel_disconnect = None

    def _schedule_idle_disconnect(self) -> None:
        self._cancel_idle_disconnect()
        self._cancel_disconnect = async_call_later(
            self._hass,
            IDLE_DISCONNECT_SECONDS,
            partial(self._async_idle_disconnect, self._activity),
        )

    async def _async_idle_disconnect(self, scheduled_at: int, _now: object) -> None:
        # Cancel rather than just dropping the handle. In normal operation this
        # timer has already fired and cancelling is a no-op, but it keeps the
        # bookkeeping honest if the callback is ever invoked another way.
        self._cancel_idle_disconnect()
        async with self._lock:
            if self._activity != scheduled_at:
                # A command ran while this timer was waiting for the lock, so
                # the link is not idle after all.
                self._schedule_idle_disconnect()
                return
            _LOGGER.debug("closing idle mesh proxy connection to %s", self._address)
            await self._async_close()

    async def _async_close(self) -> None:
        try:
            await self._controller.disconnect()
        except Exception as err:  # noqa: BLE001 - teardown must not raise
            _LOGGER.debug("error closing connection to %s: %s", self._address, err)

    async def async_release(self) -> None:
        """Drop the mesh connection without shutting the link down.

        These lights accept one Bluetooth connection at a time, so an operation
        that needs its own connection — flashing firmware over the OTA service —
        must have the mesh link let go first. The next command reconnects
        normally.
        """
        self._cancel_idle_disconnect()
        async with self._lock:
            await self._async_close()

    async def async_shutdown(self) -> None:
        """Close the connection and flush the sequence number."""
        self._cancel_idle_disconnect()
        async with self._lock:
            self._shutdown = True
            await self._async_close()
        await self._store.async_flush()
