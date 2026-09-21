"""Polling live state, which works on stock firmware for brightness."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.godox_mesh._lib.protocol import parse_status_response
from custom_components.godox_mesh.const import (
    CONF_MESH,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    CONF_RADIO_ID,
    CONF_READBACK,
    DOMAIN,
)
from custom_components.godox_mesh.mesh import GodoxMeshLink
from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_COLOR_TEMP_KELVIN
from homeassistant.const import ATTR_ASSUMED_STATE, CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests_ha.conftest import ADDRESS, MESH_STATE

BLE_PATH = "custom_components.godox_mesh.bluetooth.async_ble_device_from_address"
ENTITY = "light.key"

# Captured from an SL200III Bi.
COMMAND_ECHO = "a06441320000000c"  # 100% / 6500K, written by a command
PANEL_WRITE = "a04d3800ffff01f0"  # 77%, written by the light's own knob


async def _setup(hass: HomeAssistant, *, readback: bool) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Key",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 2, CONF_NAME: "Key", CONF_RADIO_ID: "003F"}
            ],
            CONF_READBACK: readback,
        },
    )
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.mark.usefixtures("fake_ble")
async def test_polling_off_keeps_the_state_assumed(hass: HomeAssistant) -> None:
    """Without polling the entity is optimistic, as before."""
    await _setup(hass, readback=False)
    assert hass.states.get(ENTITY).attributes[ATTR_ASSUMED_STATE] is True


@pytest.mark.usefixtures("fake_ble")
async def test_polling_reports_live_brightness(hass: HomeAssistant) -> None:
    """A polled light shows the brightness the hardware reports."""
    status = parse_status_response(bytes.fromhex(PANEL_WRITE))
    with patch.object(
        GodoxMeshLink, "async_request_status", AsyncMock(return_value=status)
    ):
        await _setup(hass, readback=True)
        state = hass.states.get(ENTITY)
        # 77% of the 1-100 scale, converted to Home Assistant's 0-255.
        assert state.attributes[ATTR_BRIGHTNESS] == pytest.approx(196, abs=2)
        # Brightness is real, so the state is no longer assumed.
        assert state.attributes.get(ATTR_ASSUMED_STATE) is not True


@pytest.mark.usefixtures("fake_ble")
async def test_polling_accepts_cct_from_a_command_echo(hass: HomeAssistant) -> None:
    """A record written by a command reports the commanded colour temperature."""
    status = parse_status_response(bytes.fromhex(COMMAND_ECHO))
    with patch.object(
        GodoxMeshLink, "async_request_status", AsyncMock(return_value=status)
    ):
        await _setup(hass, readback=True)
        assert hass.states.get(ENTITY).attributes[ATTR_COLOR_TEMP_KELVIN] == 6500


@pytest.mark.usefixtures("fake_ble")
async def test_polling_uses_the_reported_cct(hass: HomeAssistant) -> None:
    """The colour temperature a light reports is shown, not second-guessed.

    An earlier version discarded it whenever the reply carried the 0xFF markers,
    believing those meant "panel-written, so stale". Hardware disproved that: an
    SL60II Bi sends those markers on replies whose colour temperature is live and
    exact, so the rule threw away good data.
    """
    status = parse_status_response(bytes.fromhex(PANEL_WRITE))
    with patch.object(
        GodoxMeshLink, "async_request_status", AsyncMock(return_value=status)
    ):
        await _setup(hass, readback=True)
        assert hass.states.get(ENTITY).attributes[ATTR_COLOR_TEMP_KELVIN] == status.cct


@pytest.mark.usefixtures("fake_ble")
async def test_a_model_that_reports_live_cct_has_it_used(hass: HomeAssistant) -> None:
    """Whatever the light reports is what is shown, for every model.

    Two attempts to infer from the wire format which colour temperatures were
    trustworthy were both wrong, so the heuristic was deleted. A reported value
    is used as received, whichever model sends it; a user whose light reports a
    wrong one turns colour-temperature polling off instead.
    """
    live = parse_status_response(bytes.fromhex("a03c283200000061"))  # 60% / 4000K
    with patch.object(
        GodoxMeshLink, "async_request_status", AsyncMock(return_value=live)
    ):
        await _setup(hass, readback=True)
        assert hass.states.get(ENTITY).attributes[ATTR_COLOR_TEMP_KELVIN] == 4000


@pytest.mark.usefixtures("fake_ble")
async def test_a_light_that_does_not_answer_stays_usable(hass: HomeAssistant) -> None:
    """A failed poll must not break the entity."""
    from homeassistant.exceptions import HomeAssistantError

    with patch.object(
        GodoxMeshLink,
        "async_request_status",
        AsyncMock(side_effect=HomeAssistantError("no answer")),
    ):
        await _setup(hass, readback=True)
        assert hass.states.get(ENTITY) is not None


@pytest.mark.usefixtures("fake_ble")
async def test_a_node_that_does_not_answer_keeps_the_connection(
    hass: HomeAssistant,
) -> None:
    """Polling an off node must not tear down the shared proxy connection.

    The command fails at the mesh level -- the node did not reply -- while the
    proxy connection is still up. Dropping it would make every poll of an off
    light re-open the connection.
    """
    from custom_components.godox_mesh._lib import GodoxController

    # Fail the request itself, so it flows through the link's own error handling
    # rather than being short-circuited at the link method.
    with patch.object(
        GodoxController,
        "request_status",
        AsyncMock(side_effect=TimeoutError("node did not answer")),
    ):
        entry = await _setup(hass, readback=True)

    # The immediate poll failed, but the proxy connection is still held.
    assert entry.runtime_data.link._controller.is_connected


@pytest.mark.usefixtures("fake_ble")
async def test_colour_temperature_polling_can_be_turned_off(
    hass: HomeAssistant,
) -> None:
    """A user whose light reports a wrong colour temperature can opt out.

    Brightness keeps updating; only the colour temperature is left alone. This
    is the escape hatch for a light that reports a colour temperature which is
    not its real setting -- the SL200III Bi does, after its own dial is used.
    """
    from custom_components.godox_mesh.const import CONF_POLL_CCT
    from homeassistant.components.light import ATTR_BRIGHTNESS

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Key",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 2, CONF_NAME: "Key", CONF_RADIO_ID: "003F"}
            ],
            CONF_READBACK: True,
            CONF_POLL_CCT: False,
        },
    )
    entry.add_to_hass(hass)
    # a command echo, whose colour temperature would normally be used
    status = parse_status_response(bytes.fromhex(COMMAND_ECHO))
    assert status.cct == 6500
    with (
        patch(BLE_PATH, return_value=object()),
        patch.object(
            GodoxMeshLink, "async_request_status", AsyncMock(return_value=status)
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    # brightness still tracks the light ...
    assert state.attributes[ATTR_BRIGHTNESS] is not None
    # ... but the reported colour temperature was not applied
    assert state.attributes[ATTR_COLOR_TEMP_KELVIN] != 6500


async def _setup_nodes(hass: HomeAssistant, nodes: list[dict], **entry_opts):
    """Set up an entry with explicit node dicts and optional entry-wide options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Key",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={CONF_NODES: nodes, **entry_opts},
    )
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.mark.usefixtures("fake_ble")
async def test_a_node_setting_overrides_the_entry_wide_fallback(
    hass: HomeAssistant,
) -> None:
    """An explicit per-node readback:false beats a legacy entry-wide readback:true."""
    from custom_components.godox_mesh.const import CONF_POLL_INTERVAL  # noqa: F401

    status = parse_status_response(bytes.fromhex(PANEL_WRITE))
    with patch.object(
        GodoxMeshLink, "async_request_status", AsyncMock(return_value=status)
    ):
        await _setup_nodes(
            hass,
            [
                {
                    CONF_NODE_ADDRESS: 2,
                    CONF_NAME: "Key",
                    CONF_RADIO_ID: "003F",
                    CONF_READBACK: False,
                }
            ],
            **{CONF_READBACK: True},
        )
    assert hass.states.get(ENTITY).attributes[ATTR_ASSUMED_STATE] is True


@pytest.mark.usefixtures("fake_ble")
async def test_readback_is_per_node(hass: HomeAssistant) -> None:
    """One node can poll while a sibling on the same mesh does not."""
    status = parse_status_response(bytes.fromhex(PANEL_WRITE))
    with patch.object(
        GodoxMeshLink, "async_request_status", AsyncMock(return_value=status)
    ):
        await _setup_nodes(
            hass,
            [
                {
                    CONF_NODE_ADDRESS: 2,
                    CONF_NAME: "Key",
                    CONF_RADIO_ID: "003F",
                    CONF_READBACK: True,
                },
                {
                    CONF_NODE_ADDRESS: 4,
                    CONF_NAME: "Fill",
                    CONF_RADIO_ID: "003F",
                    CONF_READBACK: False,
                },
            ],
        )
    assert hass.states.get("light.key").attributes.get(ATTR_ASSUMED_STATE) is not True
    assert hass.states.get("light.fill").attributes[ATTR_ASSUMED_STATE] is True


@pytest.mark.usefixtures("fake_ble")
async def test_the_poll_interval_triggers_a_repeat_poll(hass: HomeAssistant) -> None:
    """The per-light timer polls again after its interval elapses."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    from custom_components.godox_mesh.const import CONF_POLL_INTERVAL

    status = parse_status_response(bytes.fromhex(PANEL_WRITE))
    poll = AsyncMock(return_value=status)
    with patch.object(GodoxMeshLink, "async_request_status", poll):
        await _setup_nodes(
            hass,
            [
                {
                    CONF_NODE_ADDRESS: 2,
                    CONF_NAME: "Key",
                    CONF_RADIO_ID: "003F",
                    CONF_READBACK: True,
                    CONF_POLL_INTERVAL: 10,
                }
            ],
        )
        after_setup = poll.await_count  # the immediate poll on add
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
        await hass.async_block_till_done()
        assert poll.await_count > after_setup
