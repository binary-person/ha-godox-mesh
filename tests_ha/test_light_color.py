"""Colour control end to end, for the models whose catalogue says they have it.

These assert the *bytes* rather than a mock call, because the whole point of
this path is that it matches what the vendor app sends. The expected frames are
built from the same helpers the app's own logic was read into, so a change to
the framing breaks here rather than silently on a light nobody can test.

None of this has been exercised against hardware: neither light available while
it was written is a full-colour model. See docs/model-support.md.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.godox_mesh._lib.controller import GodoxController
from custom_components.godox_mesh._lib.protocol import (
    build_hsi_command,
    build_rgb_wide_command,
    build_rgbw_command,
    build_v2_command,
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
    ATTR_HS_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_XY_COLOR,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_ADDRESS,
    CONF_NAME,
    SERVICE_TURN_ON,
)
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests_ha.conftest import ADDRESS, MESH_STATE

BLE_PATH = "custom_components.godox_mesh.bluetooth.async_ble_device_from_address"
ENTITY = "light.light"
NODE = 2

#: SL200 RF -- cct + hsi + rgb + xy, 1800-10000 K, tint +/-100, rgbDisplay 1,
#: brightness in tenths. Chosen because it exercises every branch at once.
COLOUR_MODEL = "00B6"
#: SL200III Bi -- colour temperature only, whole-percent brightness, no tint.
BICOLOUR_MODEL = "003F"


async def _setup(
    hass: HomeAssistant, radio_id: str, options: dict | None = None
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Light",
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: NODE, CONF_NAME: "Light", CONF_RADIO_ID: radio_id}
            ],
            **(options or {}),
        },
    )
    entry.add_to_hass(hass)
    with patch(BLE_PATH, return_value=object()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.fixture(name="sent")
def sent_fixture():
    """Capture the Godox frames the controller would put on the wire."""
    frames: list[bytes] = []

    async def capture(_self, payload: bytes, *, dst: int | None = None) -> None:
        frames.append(payload)

    with (
        patch.object(GodoxController, "send_payload", capture),
        patch.object(GodoxController, "power_on", AsyncMock()),
        patch.object(GodoxController, "power_off", AsyncMock()),
    ):
        yield frames


async def _turn_on(hass: HomeAssistant, **attrs) -> None:
    await hass.services.async_call(
        "light",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY, **attrs},
        blocking=True,
    )


@pytest.mark.usefixtures("fake_ble")
async def test_hue_and_saturation_send_an_hsi_frame(
    hass: HomeAssistant, sent
) -> None:
    """Setting hs_color sends 0xF1, in the units Home Assistant already uses."""
    await _setup(hass, COLOUR_MODEL)
    await _turn_on(hass, **{ATTR_HS_COLOR: (240, 100), ATTR_BRIGHTNESS: 255})

    assert sent == [build_hsi_command(100, 240, 100)]
    state = hass.states.get(ENTITY)
    assert state.attributes["color_mode"] == "hs"
    assert state.attributes[ATTR_HS_COLOR] == (240, 100)


@pytest.mark.usefixtures("fake_ble")
async def test_rgbw_is_rescaled_for_a_wide_channel_model(
    hass: HomeAssistant, sent
) -> None:
    """rgbDisplay 1 takes 0-1000 channels in a V3 frame, not 0-255 in a V2 one."""
    await _setup(hass, COLOUR_MODEL)
    await _turn_on(hass, **{ATTR_RGBW_COLOR: (255, 0, 0, 0), ATTR_BRIGHTNESS: 255})

    assert sent == [build_rgb_wide_command(100, 1000, 0, 0)]
    assert hass.states.get(ENTITY).attributes["color_mode"] == "rgbw"


@pytest.mark.usefixtures("fake_ble")
async def test_colour_temperature_carries_the_tint(
    hass: HomeAssistant, sent
) -> None:
    """The tint number rides the colour-temperature frame, as the app does it."""
    await _setup(hass, COLOUR_MODEL)
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.light_green_magenta", "value": -20},
        blocking=True,
    )
    sent.clear()
    await _turn_on(hass, **{ATTR_COLOR_TEMP_KELVIN: 5600, ATTR_BRIGHTNESS: 255})

    # -20 -> 30 in the legacy centred field, 0xEC as the signed one.
    assert sent == [build_v2_command(0xF0, 0, bytes([100, 56, 30, 0, 0xEC]))]


@pytest.mark.usefixtures("fake_ble")
async def test_tenths_of_a_percent_reach_the_end_byte(
    hass: HomeAssistant, sent
) -> None:
    """A luminance-1000 model gets the fractional brightness the wire can carry.

    Home Assistant's 0-255 is finer than whole percent, so rounding every value
    up to the next percent -- which is what this integration did everywhere --
    threw away precision the protocol has a field for.
    """
    await _setup(hass, COLOUR_MODEL)
    await _turn_on(hass, **{ATTR_BRIGHTNESS: 128, ATTR_COLOR_TEMP_KELVIN: 5600})

    # 128/255 -> 50.2 %, so 50 in the data byte and 2 in the end byte.
    frame = sent[-1]
    assert frame[1] == 50
    assert frame[6] == 2


@pytest.mark.usefixtures("fake_ble")
async def test_whole_percent_model_still_rounds_up(
    hass: HomeAssistant, sent
) -> None:
    """A luminance-100 model keeps the old behaviour, tenths byte at zero."""
    await _setup(hass, BICOLOUR_MODEL)
    await _turn_on(hass, **{ATTR_BRIGHTNESS: 128, ATTR_COLOR_TEMP_KELVIN: 5600})

    frame = sent[-1]
    assert frame[1] == 51
    assert frame[6] == 0


@pytest.mark.usefixtures("fake_ble")
async def test_byte_per_channel_model_uses_the_v2_frame(
    hass: HomeAssistant, sent
) -> None:
    """rgbDisplay 0 keeps 0-255 channels and the eight-byte 0xF2 frame."""
    import dataclasses

    from custom_components.godox_mesh.capabilities import capabilities_for_radio_id

    narrow = dataclasses.replace(
        capabilities_for_radio_id(COLOUR_MODEL), rgb_display=0
    )
    # models.py imported the lookup by name, so that is the binding to replace.
    with patch(
        "custom_components.godox_mesh.models.capabilities_for_radio_id",
        return_value=narrow,
    ):
        await _setup(hass, COLOUR_MODEL)
        await _turn_on(
            hass, **{ATTR_RGBW_COLOR: (255, 0, 0, 10), ATTR_BRIGHTNESS: 255}
        )

    assert sent == [build_rgbw_command(100, 255, 0, 0, 10)]


@pytest.mark.usefixtures("fake_ble")
async def test_gel_selection_sends_a_colour_chip_frame(
    hass: HomeAssistant, sent
) -> None:
    """Picking a gel names it by brand and number, not by colour.

    The SL200 RF is a colourChipVersion 2 model, so it takes the wide V3 frame
    with the reference type in it.
    """
    from custom_components.godox_mesh._lib.protocol import build_color_chip_command

    await _setup(hass, COLOUR_MODEL)
    gel = hass.states.get("select.light_gel")
    assert gel is not None, "a model whose catalogue lists gels should get the control"

    option = gel.attributes["options"][0]
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.light_gel", "option": option},
        blocking=True,
    )

    # Brightness defaults to 100 until the light has been turned on.
    assert sent == [
        build_color_chip_command(100.0, brand=1, number=0, version=2, sub_brand=2)
    ]


@pytest.mark.usefixtures("fake_ble")
async def test_no_gel_control_when_the_model_has_none(hass: HomeAssistant) -> None:
    """A model whose catalogue does not list gels gets no picker."""
    await _setup(hass, BICOLOUR_MODEL)
    assert hass.states.get("select.light_gel") is None


@pytest.mark.usefixtures("fake_ble")
async def test_xy_option_replaces_the_other_colour_modes(
    hass: HomeAssistant, sent
) -> None:
    """With the option on, xy is the *only* colour mode the light advertises.

    It has to replace rather than join. Home Assistant resolves a colour
    wheel's hs_color against RGB, RGBW, RGBWW and only then XY, so a light
    advertising any of those alongside XY would never reach its xy command.
    """
    await _setup(hass, COLOUR_MODEL, options={"use_xy": True})
    state = hass.states.get(ENTITY)

    assert set(state.attributes["supported_color_modes"]) == {
        "color_temp",
        "xy",
    }


@pytest.mark.usefixtures("fake_ble")
async def test_the_colour_wheel_reaches_the_xy_command(
    hass: HomeAssistant, sent
) -> None:
    """A dashboard colour wheel sends hs_color; it must land on the xy frame.

    Home Assistant converts hs -> xy itself when XY is the only colour mode,
    which is what makes the swap invisible to the user.
    """
    from custom_components.godox_mesh._lib.protocol import build_xy_command
    from homeassistant.util import color as color_util

    await _setup(hass, COLOUR_MODEL, options={"use_xy": True})
    await _turn_on(hass, **{ATTR_HS_COLOR: (120, 80), ATTR_BRIGHTNESS: 255})

    x, y = color_util.color_hs_to_xy(120, 80)
    assert sent == [build_xy_command(100.0, x, y)]
    assert hass.states.get(ENTITY).attributes["color_mode"] == "xy"


@pytest.mark.usefixtures("fake_ble")
async def test_the_coordinate_sliders_send_the_pair(
    hass: HomeAssistant, sent
) -> None:
    """Moving one slider sends both coordinates, because they share a frame."""
    from custom_components.godox_mesh._lib.protocol import build_xy_command

    await _setup(hass, COLOUR_MODEL, options={"use_xy": True})
    await _turn_on(hass, **{ATTR_BRIGHTNESS: 255})
    sent.clear()

    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.light_colour_x", "value": 0.45},
        blocking=True,
    )
    await hass.async_block_till_done()

    # y keeps its neutral default; both go out together.
    assert sent == [build_xy_command(100.0, 0.45, 0.3290)]
    assert float(hass.states.get("number.light_colour_x").state) == 0.45


@pytest.mark.usefixtures("fake_ble")
async def test_sliders_follow_the_light(hass: HomeAssistant, sent) -> None:
    """Setting a colour on the light must move both sliders."""
    await _setup(hass, COLOUR_MODEL, options={"use_xy": True})
    await _turn_on(hass, **{ATTR_XY_COLOR: (0.5, 0.4), ATTR_BRIGHTNESS: 255})
    await hass.async_block_till_done()

    assert float(hass.states.get("number.light_colour_x").state) == 0.5
    assert float(hass.states.get("number.light_colour_y").state) == 0.4


@pytest.mark.usefixtures("fake_ble")
async def test_no_xy_controls_when_the_option_is_off(hass: HomeAssistant) -> None:
    """The default keeps hue/saturation and offers no coordinate sliders."""
    await _setup(hass, COLOUR_MODEL)

    assert hass.states.get("number.light_colour_x") is None
    assert "hs" in hass.states.get(ENTITY).attributes["supported_color_modes"]


@pytest.mark.usefixtures("fake_ble")
async def test_no_xy_option_for_a_model_without_it(hass: HomeAssistant) -> None:
    """Turning the option on cannot invent xy on hardware that lacks it."""
    await _setup(hass, BICOLOUR_MODEL, options={"use_xy": True})

    assert hass.states.get("number.light_colour_x") is None
    assert hass.states.get(ENTITY).attributes["supported_color_modes"] == [
        "color_temp"
    ]
