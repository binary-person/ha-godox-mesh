"""Tests for connection lifecycle and sequence-number durability."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.godox_mesh.const import (
    IDLE_DISCONNECT_SECONDS,
    MAX_CONSECUTIVE_FAILURES,
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


async def test_a_failed_command_keeps_a_live_connection(
    hass: HomeAssistant, setup_entry, fake_ble
) -> None:
    """A command that fails while the proxy is still up keeps the connection.

    The error is still surfaced, but the connection is not torn down -- a node
    that did not answer (an off light being polled, most often) must not cost
    the shared proxy, or every poll would re-open it. The next command reuses
    the held connection.
    """
    with patch.object(
        GodoxController, "power_on", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        with pytest.raises(HomeAssistantError, match="boom"):
            await _turn_on(hass)

    assert fake_ble[0].is_connected

    # The next command reuses the same connection rather than opening a new one.
    await _turn_on(hass)
    assert len(fake_ble) == 1


async def test_a_failed_command_drops_a_broken_connection(
    hass: HomeAssistant, setup_entry, fake_ble
) -> None:
    """When the connection itself broke, it is dropped so the next reconnects."""

    def break_then_raise(*_args, **_kwargs):
        # Model a mid-command disconnect: the proxy link is gone, not just the
        # one command failing.
        fake_ble[0].is_connected = False
        raise RuntimeError("boom")

    with patch.object(
        GodoxController, "power_on", AsyncMock(side_effect=break_then_raise)
    ):
        with pytest.raises(HomeAssistantError, match="boom"):
            await _turn_on(hass)

    assert not fake_ble[0].is_connected

    # The next command starts from a clean handshake on a fresh connection.
    await _turn_on(hass)
    assert len(fake_ble) == 2


async def test_a_run_of_failures_forces_a_reconnect(
    hass: HomeAssistant, setup_entry, fake_ble
) -> None:
    """Repeated failures on a still-'connected' link force a clean reconnect.

    A single failure with the connection still up is tolerated (an off node not
    answering), but a run of them with nothing succeeding means the connection
    has most likely wedged silently, so it is dropped and the next command
    reconnects rather than failing forever into a dead link.
    """
    with patch.object(
        GodoxController, "power_on", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        for _ in range(MAX_CONSECUTIVE_FAILURES):
            with pytest.raises(HomeAssistantError, match="boom"):
                await _turn_on(hass)
        # The connection never reported a disconnect, yet the run of failures
        # dropped it anyway.
        assert not fake_ble[0].is_connected

    # The next command opens a fresh connection instead of reusing the dead one.
    await _turn_on(hass)
    assert len(fake_ble) == 2
    assert fake_ble[1].is_connected


async def test_one_bad_node_among_healthy_ones_keeps_the_connection(
    hass: HomeAssistant, setup_entry, fake_ble
) -> None:
    """A sibling succeeding between failures must reset the run, sparing the link.

    One off light polled among healthy ones must never accumulate enough
    consecutive failures to force a reconnect -- the healthy traffic resets the
    count each time.
    """
    call = {"n": 0}

    async def fail_every_other(*_args, **_kwargs):
        call["n"] += 1
        if call["n"] % 2 == 1:
            raise RuntimeError("node did not answer")

    with patch.object(GodoxController, "power_on", AsyncMock(side_effect=fail_every_other)):
        for _ in range(MAX_CONSECUTIVE_FAILURES * 2):
            try:
                await _turn_on(hass)
            except HomeAssistantError:
                pass

    # Interleaved successes kept the run below the threshold, so the original
    # connection was never dropped.
    assert len(fake_ble) == 1
    assert fake_ble[0].is_connected


async def test_a_quick_drop_deprioritises_the_gateway(
    hass: HomeAssistant, setup_entry, fake_ble
) -> None:
    """A node that drops soon after connecting is marked as one that will not hold."""
    link = setup_entry.runtime_data.link
    await _turn_on(hass)  # opens a connection, records when it opened
    gateway = link.gateway_address
    link._on_gateway_drop(gateway)  # drops right after connecting
    assert gateway in link._dropped_at


async def test_a_drop_after_a_long_hold_is_treated_as_a_blip(
    hass: HomeAssistant, setup_entry, fake_ble
) -> None:
    """A node that held a useful session and then blips is not deprioritised."""
    import time

    link = setup_entry.runtime_data.link
    await _turn_on(hass)
    gateway = link.gateway_address
    link._connected_at = time.monotonic() - 999  # held for a long time
    link._on_gateway_drop(gateway)
    assert gateway not in link._dropped_at


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
