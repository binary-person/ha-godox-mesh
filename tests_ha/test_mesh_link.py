"""Tests for connection lifecycle and sequence-number durability."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.godox_mesh.const import (
    IDLE_DISCONNECT_SECONDS,
    SEQUENCE_BLOCK_SIZE,
)
from custom_components.godox_mesh.store import KEY_SEQUENCE_NUMBER, SAVE_DELAY_SECONDS
from custom_components.godox_mesh._lib import GodoxController
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from tests_ha.conftest import MESH_STATE

ENTITY = "light.key_light"


async def _turn_on(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "light", SERVICE_TURN_ON, {ATTR_ENTITY_ID: ENTITY}, blocking=True
    )


async def _flush_storage(hass: HomeAssistant) -> None:
    """Advance past the coalescing window so the delayed save actually runs."""
    async_fire_time_changed(
        hass, dt_util.utcnow() + dt_util.dt.timedelta(seconds=SAVE_DELAY_SECONDS + 1)
    )
    await hass.async_block_till_done()


async def test_connection_is_opened_lazily(
    hass: HomeAssistant, setup_entry, fake_ble
) -> None:
    """Setting up an entry must not tie up a BLE connection slot."""
    assert fake_ble == []

    await _turn_on(hass)

    assert len(fake_ble) == 1
    assert fake_ble[0].is_connected


async def test_connection_is_reused_across_commands(
    hass: HomeAssistant, setup_entry, fake_ble
) -> None:
    """Repeated commands must not re-run the proxy handshake each time."""
    await _turn_on(hass)
    await hass.services.async_call(
        "light", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY}, blocking=True
    )
    await _turn_on(hass)

    assert len(fake_ble) == 1


async def test_idle_connection_is_released(
    hass: HomeAssistant, setup_entry, fake_ble
) -> None:
    """The adapter's connection slot is given back once the light goes quiet."""
    await _turn_on(hass)
    assert fake_ble[0].is_connected

    async_fire_time_changed(
        hass, dt_util.utcnow() + dt_util.dt.timedelta(seconds=IDLE_DISCONNECT_SECONDS + 5)
    )
    await hass.async_block_till_done()

    assert not fake_ble[0].is_connected


async def test_reconnects_after_an_idle_disconnect(
    hass: HomeAssistant, setup_entry, fake_ble
) -> None:
    """A command after the idle window opens a fresh connection."""
    await _turn_on(hass)
    async_fire_time_changed(
        hass, dt_util.utcnow() + dt_util.dt.timedelta(seconds=IDLE_DISCONNECT_SECONDS + 5)
    )
    await hass.async_block_till_done()

    await _turn_on(hass)

    assert len(fake_ble) == 2
    assert fake_ble[1].is_connected


async def test_a_failed_command_surfaces_and_drops_the_connection(
    hass: HomeAssistant, setup_entry, fake_ble
) -> None:
    """A transport error must not leave a half-dead connection behind."""
    with patch.object(
        GodoxController, "power_on", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        with pytest.raises(HomeAssistantError, match="boom"):
            await _turn_on(hass)

    assert not fake_ble[0].is_connected

    # The next command starts from a clean handshake.
    await _turn_on(hass)
    assert len(fake_ble) == 2


async def test_sequence_number_is_reserved_ahead_of_use(
    hass: HomeAssistant, setup_entry, hass_storage
) -> None:
    """The stored counter must always lead what has actually been transmitted."""
    await _turn_on(hass)
    await _flush_storage(hass)

    key = next(k for k in hass_storage if k.startswith("godox_mesh."))
    stored = hass_storage[key]["data"][KEY_SEQUENCE_NUMBER]

    assert stored >= MESH_STATE["sequence_number"] + 1
    assert stored % SEQUENCE_BLOCK_SIZE == 0


async def test_sequence_number_survives_a_reload(
    hass: HomeAssistant, setup_entry, hass_storage
) -> None:
    """After a restart the counter resumes above the reserved mark, never below."""
    await _turn_on(hass)
    await _flush_storage(hass)

    key = next(k for k in hass_storage if k.startswith("godox_mesh."))
    before = hass_storage[key]["data"][KEY_SEQUENCE_NUMBER]

    with patch(
        "custom_components.godox_mesh.bluetooth.async_ble_device_from_address",
        return_value=object(),
    ):
        assert await hass.config_entries.async_reload(setup_entry.entry_id)
        await hass.async_block_till_done()
        await _turn_on(hass)
        await _flush_storage(hass)

    after = hass_storage[key]["data"][KEY_SEQUENCE_NUMBER]
    assert after >= before


async def test_unload_closes_the_connection(
    hass: HomeAssistant, setup_entry, fake_ble
) -> None:
    """Removing the integration must release the BLE connection."""
    await _turn_on(hass)
    assert fake_ble[0].is_connected

    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()

    assert not fake_ble[0].is_connected
