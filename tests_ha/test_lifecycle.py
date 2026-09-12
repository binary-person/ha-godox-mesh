"""Edge cases across the full config entry lifecycle.

Covers setup, reload, unload, removal, shutdown, error recovery and
concurrency — the transitions where a shared BLE connection and a shared
monotonic sequence counter are easiest to get wrong.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.godox_mesh.const import (
    CONF_MESH,
    CONF_MODEL,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    DOMAIN,
)
from custom_components.godox_mesh.store import (
    KEY_SEQUENCE_NUMBER,
    SAVE_DELAY_SECONDS,
)
from custom_components.godox_mesh._lib import GodoxController
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_ADDRESS,
    CONF_NAME,
    EVENT_HOMEASSISTANT_STOP,
    SERVICE_TURN_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from tests_ha.conftest import ADDRESS, MESH_STATE

ENTITY = "light.key_light"
BLE_PATH = "custom_components.godox_mesh.bluetooth.async_ble_device_from_address"


def _entry(**overrides) -> MockConfigEntry:
    options = overrides.pop(
        "options",
        {
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 2, CONF_NAME: "Key Light", CONF_MODEL: None}
            ]
        },
    )
    mesh = overrides.pop("mesh", dict(MESH_STATE))
    return MockConfigEntry(
        domain=DOMAIN,
        title="Key Light",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: mesh},
        options=options,
        **overrides,
    )


async def _turn_on(hass: HomeAssistant, entity: str = ENTITY) -> None:
    await hass.services.async_call(
        "light", SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity}, blocking=True
    )


# --- setup ---------------------------------------------------------------


async def test_setup_retries_when_the_light_is_out_of_range(
    hass: HomeAssistant, fake_ble
) -> None:
    """An unreachable light must schedule a retry, not fail permanently."""
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=None):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_without_options_falls_back_to_the_mesh_state_node(
    hass: HomeAssistant, fake_ble
) -> None:
    """An entry created before the node list existed must still produce a light."""
    entry = _entry(options={})
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get(ENTITY) is not None


async def test_stored_sequence_wins_when_it_is_ahead_of_the_entry(
    hass: HomeAssistant, fake_ble, hass_storage
) -> None:
    """A restart must never rewind the counter to the entry's stale value."""
    entry = _entry()
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "data": {KEY_SEQUENCE_NUMBER: 900_000},
    }
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _turn_on(hass)

    link = entry.runtime_data.link
    assert link._controller.state.sequence_number > 900_000


async def test_entry_sequence_wins_when_the_store_is_behind(
    hass: HomeAssistant, fake_ble, hass_storage
) -> None:
    """Re-importing mesh state with a higher counter must take effect."""
    entry = _entry()
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "data": {KEY_SEQUENCE_NUMBER: 10},
    }
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    link = entry.runtime_data.link
    assert link._controller.state.sequence_number >= MESH_STATE["sequence_number"]


# --- reload --------------------------------------------------------------


async def test_reload_closes_the_previous_connection(
    hass: HomeAssistant, fake_ble
) -> None:
    """A reload must not leak the old BLE connection."""
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _turn_on(hass)
        assert fake_ble[0].is_connected

        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert not fake_ble[0].is_connected


# --- unload --------------------------------------------------------------


async def test_unload_without_ever_connecting(
    hass: HomeAssistant, fake_ble
) -> None:
    """Unloading an entry that never sent a command must not raise."""
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert fake_ble == []
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_command_after_unload_is_refused(
    hass: HomeAssistant, fake_ble
) -> None:
    """A command racing an unload must fail cleanly rather than reconnect."""
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        link = entry.runtime_data.link
        await _turn_on(hass)

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError, match="shutting down"):
            await link.async_turn_on(2)


# --- removal -------------------------------------------------------------


async def test_removing_the_entry_deletes_stored_state(
    hass: HomeAssistant, fake_ble, hass_storage
) -> None:
    """Uninstalling must not leave mesh keys or counters behind."""
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _turn_on(hass)
        async_fire_time_changed(
            hass,
            dt_util.utcnow() + dt_util.dt.timedelta(seconds=SAVE_DELAY_SECONDS + 1),
        )
        await hass.async_block_till_done()
        assert f"{DOMAIN}.{entry.entry_id}" in hass_storage

        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

    assert hass_storage.get(f"{DOMAIN}.{entry.entry_id}", {}).get("data") in (None, {})


async def test_removing_the_entry_allows_rediscovery(
    hass: HomeAssistant, fake_ble
) -> None:
    """The light must be addable again without restarting Home Assistant."""
    entry = _entry()
    entry.add_to_hass(hass)
    with (
        patch(BLE_PATH, return_value=object()),
        patch(
            "custom_components.godox_mesh.bluetooth.async_rediscover_address"
        ) as rediscover,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

    rediscover.assert_called_once_with(hass, ADDRESS)


# --- shutdown ------------------------------------------------------------


async def test_home_assistant_shutdown_closes_the_connection(
    hass: HomeAssistant, fake_ble
) -> None:
    """A clean shutdown must release the connection and flush the counter."""
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _turn_on(hass)
        assert fake_ble[0].is_connected

        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()

    assert not fake_ble[0].is_connected


# --- error recovery ------------------------------------------------------


async def test_light_going_out_of_range_surfaces_a_clear_error(
    hass: HomeAssistant, fake_ble
) -> None:
    """A command to a light that has left must say so, not time out silently."""
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with patch(
        "custom_components.godox_mesh.mesh.GodoxController.connect",
        AsyncMock(side_effect=RuntimeError("not in range")),
    ):
        with pytest.raises(HomeAssistantError, match="not in range"):
            await _turn_on(hass)


async def test_recovers_on_the_next_command_after_a_failure(
    hass: HomeAssistant, fake_ble
) -> None:
    """One bad command must not wedge the light permanently."""
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            GodoxController, "power_on", AsyncMock(side_effect=RuntimeError("boom"))
        ):
            with pytest.raises(HomeAssistantError):
                await _turn_on(hass)

        await _turn_on(hass)

    assert hass.states.get(ENTITY).state == "on"


# --- concurrency ---------------------------------------------------------


async def test_concurrent_commands_are_serialized(
    hass: HomeAssistant, fake_ble
) -> None:
    """Overlapping commands must not interleave on the shared counter.

    Driven through the mesh link directly: the light platform sets
    PARALLEL_UPDATES = 1, so going via the service call would only prove that
    Home Assistant serializes, not that the link does.
    """
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        link = entry.runtime_data.link

        overlap = 0
        peak = 0

        async def tracked(self, **kwargs):
            nonlocal overlap, peak
            overlap += 1
            peak = max(peak, overlap)
            # Yield so a second command can interleave if nothing prevents it.
            await asyncio.sleep(0)
            overlap -= 1

        with patch.object(GodoxController, "set_params", tracked):
            await asyncio.gather(
                *(
                    link.async_set_light(node, brightness_pct=50, kelvin=4000)
                    for node in (2, 3, 4, 5)
                )
            )

    assert peak == 1


async def test_sequence_numbers_are_never_reused_under_concurrency(
    hass: HomeAssistant, fake_ble
) -> None:
    """Every concurrent command must consume a distinct sequence number."""
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        link = entry.runtime_data.link

        seen: list[int] = []
        real = GodoxController.set_params

        async def record(self, **kwargs):
            seen.append(self.state.sequence_number)
            await asyncio.sleep(0)
            await real(self, **kwargs)

        with patch.object(GodoxController, "set_params", record):
            await asyncio.gather(
                *(
                    link.async_set_light(2, brightness_pct=50, kelvin=4000)
                    for _ in range(10)
                )
            )

    assert len(seen) == len(set(seen)) == 10
    assert seen == sorted(seen)


async def test_idle_timer_reschedules_when_a_command_beat_it_to_the_lock(
    hass: HomeAssistant, fake_ble
) -> None:
    """The timer must not close a connection a command has just used.

    The race is narrow and not reachable through the service layer, because a
    command cancels the pending timer before it takes the lock. This drives the
    already-fired timer callback directly with the activity counter it captured
    at scheduling time.
    """
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        link = entry.runtime_data.link
        await _turn_on(hass)
        assert link._controller.is_connected

        stale = link._activity - 1
        await link._async_idle_disconnect(stale, None)
        connected = link._controller.is_connected

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert connected, "a freshly used connection was closed"


async def test_idle_timer_closes_a_genuinely_idle_connection(
    hass: HomeAssistant, fake_ble
) -> None:
    """The counterpart: when nothing has run since scheduling, it does close."""
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        link = entry.runtime_data.link
        await _turn_on(hass)

        await link._async_idle_disconnect(link._activity, None)
        connected = link._controller.is_connected

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert not connected


# --- topology edge cases -------------------------------------------------


async def test_removing_the_proxy_node_leaves_the_others_working(
    hass: HomeAssistant, fake_ble
) -> None:
    """The BLE connection is addressed by MAC, not by the node behind it.

    Node 2 is the light Home Assistant physically connects to. Removing it
    while keeping node 3 must still leave node 3 controllable through that
    same connection.
    """
    entry = _entry(
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 2, CONF_NAME: "Key Light", CONF_MODEL: None},
                {CONF_NODE_ADDRESS: 3, CONF_NAME: "Fill Light", CONF_MODEL: None},
            ]
        }
    )
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "remove_node"}
        )
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_NODES: ["2"]}
        )
        await hass.async_block_till_done()

        assert hass.states.get("light.key_light") is None
        await _turn_on(hass, "light.fill_light")

    assert hass.states.get("light.fill_light").state == "on"


async def test_two_mesh_networks_do_not_share_state(
    hass: HomeAssistant, fake_ble
) -> None:
    """Separately provisioned lights get separate entries and counters."""
    first = _entry()
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Other Light",
        unique_id="11:22:33:44:55:66",
        data={
            CONF_ADDRESS: "11:22:33:44:55:66",
            CONF_MESH: dict(MESH_STATE, network_key="aa" * 16, sequence_number=7),
        },
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 2, CONF_NAME: "Other Light", CONF_MODEL: None}
            ]
        },
    )
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        # Setting up the first entry initialises the component, which loads
        # every entry of the domain.
        assert await hass.config_entries.async_setup(first.entry_id)
        await hass.async_block_till_done()
        assert second.state is ConfigEntryState.LOADED

        await _turn_on(hass, "light.key_light")
        await _turn_on(hass, "light.other_light")

    assert first.runtime_data.link is not second.runtime_data.link
    # Two networks, two connections, two independent counters.
    assert len(fake_ble) == 2
    assert (
        first.runtime_data.link._controller.state.network_key
        != second.runtime_data.link._controller.state.network_key
    )


async def test_unload_waits_for_an_in_flight_command(
    hass: HomeAssistant, fake_ble
) -> None:
    """Unloading mid-command must not tear the connection out from under it."""
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        link = entry.runtime_data.link

        entered = asyncio.Event()
        release = asyncio.Event()
        finished = False

        async def slow(self, **kwargs):
            nonlocal finished
            entered.set()
            await release.wait()
            finished = True

        with patch.object(GodoxController, "set_params", slow):
            command = asyncio.create_task(
                link.async_set_light(2, brightness_pct=50, kelvin=4000)
            )
            await entered.wait()
            shutdown = asyncio.create_task(link.async_shutdown())
            await asyncio.sleep(0)

            assert not shutdown.done(), "shutdown did not wait for the command"
            release.set()
            await command
            await shutdown

    assert finished


async def test_a_corrupt_store_falls_back_to_the_entry(
    hass: HomeAssistant, fake_ble, hass_storage
) -> None:
    """Unreadable stored state must not brick setup."""
    entry = _entry()
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {"version": 1, "data": {}}

    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get(ENTITY) is not None
    link = entry.runtime_data.link
    assert link._controller.state.sequence_number >= MESH_STATE["sequence_number"]


async def test_entity_state_is_restored_across_a_restart(
    hass: HomeAssistant, fake_ble
) -> None:
    """An optimistically-tracked light must remember what it was last told."""
    from homeassistant.const import STATE_ON
    from homeassistant.core import State
    from pytest_homeassistant_custom_component.common import mock_restore_cache

    mock_restore_cache(
        hass,
        [
            State(
                ENTITY,
                STATE_ON,
                {"brightness": 128, "color_temp_kelvin": 3200},
            )
        ],
    )
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state.state == STATE_ON
    assert state.attributes["brightness"] == 128
    assert state.attributes["color_temp_kelvin"] == 3200


async def test_one_entry_per_light_survives_unplugging_the_other(
    hass: HomeAssistant, fake_ble, monkeypatch
) -> None:
    """Separate entries have no shared gateway, so they cannot take each other down.

    This is the simple alternative to one mesh network with many nodes: it
    costs one connection slot per light, but removes the single point of
    failure entirely.
    """
    from custom_components.godox_mesh.ble import DeviceNotFound

    other = "11:22:33:44:55:66"
    first = _entry()
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Other Light",
        unique_id=other,
        data={
            CONF_ADDRESS: other,
            CONF_MESH: dict(MESH_STATE, network_key="aa" * 16),
        },
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 2, CONF_NAME: "Other Light", CONF_MODEL: None}
            ]
        },
    )
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(first.entry_id)
        await hass.async_block_till_done()

    # The first light is unplugged; the second is untouched.
    from tests_ha.conftest import FakeBleakClient

    async def connect(self, **kwargs):
        if getattr(self, "target", None) == ADDRESS:
            raise DeviceNotFound(f"{ADDRESS} is not in range")
        self.is_connected = True

    monkeypatch.setattr(FakeBleakClient, "connect", connect, raising=False)

    with pytest.raises(HomeAssistantError):
        await first.runtime_data.link.async_turn_on(2)

    # The other light is completely unaffected — no shared gateway.
    await second.runtime_data.link.async_turn_on(2)
    assert second.runtime_data.link._controller.is_connected
