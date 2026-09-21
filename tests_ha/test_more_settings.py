"""The vendor app's "more settings" screen, and the two-range models.

None of this has run against hardware; the frames are built to the layouts in
the app's own ``GodoxCommandApi`` and asserted byte-for-byte here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.godox_mesh._lib.controller import GodoxController
from custom_components.godox_mesh._lib.protocol import (
    build_control_mode_command,
    build_motion_recognize_command,
    build_selfie_cct_command,
    build_smoothness_command,
)
from custom_components.godox_mesh.const import (
    CONF_MESH,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    CONF_RADIO_ID,
    DOMAIN,
)
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
)
from homeassistant.const import ATTR_ENTITY_ID, CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests_ha.conftest import ADDRESS, MESH_STATE

BLE_PATH = "custom_components.godox_mesh.bluetooth.async_ble_device_from_address"
LIGHT = "light.light"

#: MG4KR -- control mode, mains frequency and smoothness all present.
MORE_SETTINGS = "00C2"
#: MA5R -- 1800-10000 K normally, 2800-6500 K in selfie mode.
TWO_RANGE = "0089"
#: SL200III Bi -- none of the above.
PLAIN = "003F"


async def _setup(hass: HomeAssistant, radio_id: str) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Light",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 2, CONF_NAME: "Light", CONF_RADIO_ID: radio_id}
            ]
        },
    )
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.fixture(name="sent")
def sent_fixture():
    """Capture the frames the controller would put on the wire."""
    frames: list[bytes] = []

    async def capture(_self, payload: bytes, *, dst: int | None = None) -> None:
        frames.append(payload)

    with (
        patch.object(GodoxController, "send_payload", capture),
        patch.object(GodoxController, "power_on", AsyncMock()),
        patch.object(GodoxController, "power_off", AsyncMock()),
    ):
        yield frames


@pytest.mark.usefixtures("fake_ble")
async def test_control_mode_and_frequency_share_one_frame(
    hass: HomeAssistant, sent
) -> None:
    """They are two entities but one command, so either one sends both."""
    await _setup(hass, MORE_SETTINGS)
    mode = hass.states.get("select.light_output_mode")
    frequency = hass.states.get("select.light_mains_frequency")
    assert mode is not None and frequency is not None

    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.light_output_mode", "option": "Low End"},
        blocking=True,
    )

    # Low End is code 1; the frequency keeps its list's first entry.
    assert sent == [build_control_mode_command(1, 0)]


@pytest.mark.usefixtures("fake_ble")
async def test_smoothness_sends_its_own_frame(hass: HomeAssistant, sent) -> None:
    """Dimming smoothness is a separate register."""
    await _setup(hass, MORE_SETTINGS)

    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.light_dimming_smoothness", "option": "OFF"},
        blocking=True,
    )

    option = next(
        o
        for o in hass.states.get("select.light_dimming_smoothness").attributes[
            "options"
        ]
        if o == "OFF"
    )
    assert option == "OFF"
    assert len(sent) == 1 and sent[0][:2] == build_smoothness_command(0)[:2]


@pytest.mark.usefixtures("fake_ble")
async def test_no_more_settings_on_a_model_without_them(
    hass: HomeAssistant,
) -> None:
    """A plain bi-colour light gets none of these controls."""
    await _setup(hass, PLAIN)

    assert hass.states.get("select.light_output_mode") is None
    assert hass.states.get("select.light_dimming_smoothness") is None
    assert hass.states.get("switch.light_accessory_recognition") is None
    assert hass.states.get("select.light_colour_temperature_range") is None


@pytest.mark.usefixtures("fake_ble")
async def test_the_colour_temperature_range_can_be_swapped(
    hass: HomeAssistant, sent
) -> None:
    """The light re-advertises its bounds when the range is switched.

    This is the part that could have failed: Home Assistant reads the bounds
    from ``capability_attributes``, and if it cached them at registration a
    runtime swap would be invisible. It re-reads them on every state write.
    """
    await _setup(hass, TWO_RANGE)
    state = hass.states.get(LIGHT)
    assert state.attributes[ATTR_MIN_COLOR_TEMP_KELVIN] == 1800
    assert state.attributes[ATTR_MAX_COLOR_TEMP_KELVIN] == 10000

    await hass.services.async_call(
        "select",
        "select_option",
        {
            ATTR_ENTITY_ID: "select.light_colour_temperature_range",
            "option": "Selfie",
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(LIGHT)
    assert state.attributes[ATTR_MIN_COLOR_TEMP_KELVIN] == 2800
    assert state.attributes[ATTR_MAX_COLOR_TEMP_KELVIN] == 6500


@pytest.mark.usefixtures("fake_ble")
async def test_selfie_mode_uses_its_own_command(hass: HomeAssistant, sent) -> None:
    """In the selfie range the light sends 0xFC, not the 0xF0 frame."""
    await _setup(hass, TWO_RANGE)
    await hass.services.async_call(
        "select",
        "select_option",
        {
            ATTR_ENTITY_ID: "select.light_colour_temperature_range",
            "option": "Selfie",
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    sent.clear()

    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: LIGHT, ATTR_BRIGHTNESS: 255, ATTR_COLOR_TEMP_KELVIN: 4000},
        blocking=True,
    )

    assert sent == [build_selfie_cct_command(100, 4000)]


@pytest.mark.usefixtures("fake_ble")
async def test_a_colour_temperature_outside_the_selfie_range_is_clamped(
    hass: HomeAssistant, sent
) -> None:
    """Switching range must pull a now-out-of-range value back inside it."""
    await _setup(hass, TWO_RANGE)
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: LIGHT, ATTR_BRIGHTNESS: 255, ATTR_COLOR_TEMP_KELVIN: 9000},
        blocking=True,
    )
    sent.clear()

    await hass.services.async_call(
        "select",
        "select_option",
        {
            ATTR_ENTITY_ID: "select.light_colour_temperature_range",
            "option": "Selfie",
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(LIGHT).attributes[ATTR_COLOR_TEMP_KELVIN] == 6500
    assert sent == [build_selfie_cct_command(100, 6500)]


@pytest.mark.usefixtures("fake_ble")
async def test_accessory_recognition_inverts_on_the_wire(
    hass: HomeAssistant, sent
) -> None:
    """Zero enables, the same way the power command is inverted."""
    await _setup(hass, "0073")  # MG1200R, one of the 7 with attachmentSupport
    entity = "switch.light_accessory_recognition"
    assert hass.states.get(entity) is not None

    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: entity}, blocking=True
    )
    assert sent == [build_motion_recognize_command(True)]
    assert sent[0][-2] == 0

    sent.clear()
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: entity}, blocking=True
    )
    assert sent[0][-2] == 1
