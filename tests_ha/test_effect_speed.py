"""Effect speed: a separate control, because the light platform has no concept of it."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.godox_mesh.const import (
    CONF_MESH,
    CONF_NODE_ADDRESS,
    CONF_NODES,
    CONF_RADIO_ID,
    DOMAIN,
)
from custom_components.godox_mesh.mesh import GodoxMeshLink
from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_EFFECT
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import ATTR_ENTITY_ID, CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests_ha.conftest import ADDRESS, MESH_STATE

BLE_PATH = "custom_components.godox_mesh.bluetooth.async_ble_device_from_address"
LIGHT = "light.light"
SPEED = "number.light_effect_speed"

# SL200III Bi: older effect generation, Flash and Lightning have two speed
# steps and the rest have one.
WITH_SPEED = "003F"
# ES45: no effects at all, so nothing to set a speed for.
WITHOUT_SPEED = "0009"
# TP2R: newer effect generation, whose speed is a continuous 0-100 value.
NEW_GENERATION = "002A"


async def _setup(hass: HomeAssistant, radio_id: str) -> MockConfigEntry:
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
    return entry


@pytest.mark.usefixtures("fake_ble")
async def test_model_without_effects_gets_no_control(hass: HomeAssistant) -> None:
    """A model with no effects has no speed to set."""
    await _setup(hass, WITHOUT_SPEED)
    assert hass.states.get(SPEED) is None


@pytest.mark.usefixtures("fake_ble")
async def test_new_generation_model_gets_a_continuous_control(
    hass: HomeAssistant,
) -> None:
    """The newer effect frame carries a 0-100 speed, not a step index.

    These models report ``gear: 0`` for every effect, so deriving the maximum
    from ``gear`` gave zero and the control was never created -- on 122 of the
    177 models that have effects.
    """
    await _setup(hass, NEW_GENERATION)

    state = hass.states.get(SPEED)
    assert state is not None
    assert float(state.attributes["max"]) == 100


@pytest.mark.usefixtures("fake_ble")
async def test_multi_speed_model_gets_a_bounded_control(hass: HomeAssistant) -> None:
    """The maximum is gear-1 of the fastest effect the model ships."""
    await _setup(hass, WITH_SPEED)

    state = hass.states.get(SPEED)
    assert state is not None
    assert float(state.attributes["min"]) == 0
    assert float(state.attributes["max"]) == 1


@pytest.mark.usefixtures("fake_ble")
async def test_the_selected_speed_reaches_the_effect_command(
    hass: HomeAssistant,
) -> None:
    """Setting speed then running a two-speed effect sends that speed."""
    await _setup(hass, WITH_SPEED)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: SPEED, ATTR_VALUE: 1},
        blocking=True,
    )

    with patch.object(GodoxMeshLink, "async_set_effect", new=AsyncMock()) as set_effect:
        await hass.services.async_call(
            "light",
            "turn_on",
            {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Lightning", ATTR_BRIGHTNESS: 255},
            blocking=True,
        )

    assert set_effect.await_args.kwargs["speed"] == 1


@pytest.mark.usefixtures("fake_ble")
async def test_speed_is_clamped_to_what_the_effect_supports(
    hass: HomeAssistant,
) -> None:
    """Speed 1 on a single-speed effect must go out as 0, not 1."""
    await _setup(hass, WITH_SPEED)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: SPEED, ATTR_VALUE: 1},
        blocking=True,
    )

    with patch.object(GodoxMeshLink, "async_set_effect", new=AsyncMock()) as set_effect:
        await hass.services.async_call(
            "light",
            "turn_on",
            {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Candle", ATTR_BRIGHTNESS: 255},
            blocking=True,
        )

    # Candle is a one-gear effect on this model.
    assert set_effect.await_args.kwargs["speed"] == 0


@pytest.mark.usefixtures("fake_ble")
async def test_changing_speed_re_sends_the_running_effect(
    hass: HomeAssistant,
) -> None:
    """Changing the speed of a running effect applies at once, like the tint control."""
    await _setup(hass, WITH_SPEED)

    # A two-speed effect is running (light on).
    with patch.object(GodoxMeshLink, "async_set_effect", new=AsyncMock()):
        await hass.services.async_call(
            "light", "turn_on",
            {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Lightning", ATTR_BRIGHTNESS: 255},
            blocking=True,
        )
    await hass.async_block_till_done()

    # Changing the speed re-sends the effect with the new speed, without the
    # user having to pick the effect again.
    with patch.object(GodoxMeshLink, "async_set_effect", new=AsyncMock()) as set_effect:
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: SPEED, ATTR_VALUE: 1},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert set_effect.await_count == 1
    assert set_effect.await_args.kwargs["speed"] == 1
    assert set_effect.await_args.kwargs["effect"] == 3  # Lightning


@pytest.mark.usefixtures("fake_ble")
async def test_changing_speed_with_no_effect_running_sends_nothing(
    hass: HomeAssistant,
) -> None:
    """With no effect running, setting the speed only records it for next time."""
    await _setup(hass, WITH_SPEED)  # light off, no effect

    with patch.object(GodoxMeshLink, "async_set_effect", new=AsyncMock()) as set_effect:
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: SPEED, ATTR_VALUE: 1},
            blocking=True,
        )
        await hass.async_block_till_done()

    set_effect.assert_not_awaited()


@pytest.mark.usefixtures("fake_ble")
async def test_the_control_says_which_effects_respond_to_it(
    hass: HomeAssistant,
) -> None:
    """Most effects ignore the speed, so the entity must say which do not.

    Without this the slider looks broken: on an SL200III Bi it changes nothing
    for five of the seven effects, because those accept a single speed.
    """
    await _setup(hass, WITH_SPEED)

    attributes = hass.states.get(SPEED).attributes

    assert attributes["applies_to_effects"] == ["Flash Light", "Lightning"]
    assert attributes["speeds_per_effect"] == {"Flash Light": 2, "Lightning": 2}
    assert attributes["ignored_by_other_effects"] is True


@pytest.mark.usefixtures("fake_ble")
async def test_the_slider_range_follows_the_running_effect(
    hass: HomeAssistant,
) -> None:
    """Choosing an effect must re-bound the speed control to that effect.

    This is how a user learns an effect has no speeds: the slider collapses.
    Attributes alone are invisible outside Developer Tools.
    """
    await _setup(hass, WITH_SPEED)

    # Lightning takes two speeds on this model, Candle one.
    with patch.object(GodoxMeshLink, "async_set_effect", new=AsyncMock()):
        await hass.services.async_call(
            "light", "turn_on",
            {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Lightning", ATTR_BRIGHTNESS: 255},
            blocking=True,
        )
    await hass.async_block_till_done()
    assert float(hass.states.get(SPEED).attributes["max"]) == 1

    with patch.object(GodoxMeshLink, "async_set_effect", new=AsyncMock()):
        await hass.services.async_call(
            "light", "turn_on",
            {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Candle", ATTR_BRIGHTNESS: 255},
            blocking=True,
        )
    await hass.async_block_till_done()
    assert float(hass.states.get(SPEED).attributes["max"]) == 0, (
        "a single-speed effect should collapse the slider, so it is visibly inert"
    )


@pytest.mark.usefixtures("fake_ble")
async def test_the_effect_list_says_which_effects_have_speeds(
    hass: HomeAssistant,
) -> None:
    """The count must be visible before choosing, not only after.

    The slider's range follows the running effect, which only helps once you
    have picked one. Annotating the list lets you see it while choosing.
    """
    from homeassistant.components.light import ATTR_EFFECT_LIST

    await _setup(hass, WITH_SPEED)
    effects = hass.states.get(LIGHT).attributes[ATTR_EFFECT_LIST]

    assert "Lightning (2 speeds)" in effects
    assert "Candle" in effects, "single-speed effects stay plain"


@pytest.mark.usefixtures("fake_ble")
async def test_an_annotated_effect_still_resolves_to_its_symbol(
    hass: HomeAssistant,
) -> None:
    """Selecting the annotated label must send the right effect."""
    await _setup(hass, WITH_SPEED)

    with patch.object(GodoxMeshLink, "async_set_effect", new=AsyncMock()) as sent:
        await hass.services.async_call(
            "light", "turn_on",
            {
                ATTR_ENTITY_ID: LIGHT,
                ATTR_EFFECT: "Lightning (2 speeds)",
                ATTR_BRIGHTNESS: 255,
            },
            blocking=True,
        )

    # Lightning is catalogue id 4, so wire symbol 3.
    assert sent.await_args.kwargs["effect"] == 3


@pytest.mark.usefixtures("fake_ble")
async def test_a_bare_name_still_works_for_existing_automations(
    hass: HomeAssistant,
) -> None:
    """An automation written before the annotation must not break."""
    await _setup(hass, WITH_SPEED)

    with patch.object(GodoxMeshLink, "async_set_effect", new=AsyncMock()) as sent:
        await hass.services.async_call(
            "light", "turn_on",
            {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Lightning", ATTR_BRIGHTNESS: 255},
            blocking=True,
        )

    assert sent.await_args.kwargs["effect"] == 3


@pytest.mark.usefixtures("fake_ble")
async def test_a_state_saved_before_annotation_restores(hass: HomeAssistant) -> None:
    """An entity restored from an older state must not lose its effect."""
    from homeassistant.components.light import ATTR_EFFECT as EFFECT_ATTR
    from homeassistant.const import STATE_ON
    from homeassistant.core import State

    from pytest_homeassistant_custom_component.common import mock_restore_cache

    mock_restore_cache(hass, [State(LIGHT, STATE_ON, {EFFECT_ATTR: "Lightning"})])
    await _setup(hass, WITH_SPEED)

    assert hass.states.get(LIGHT).attributes[EFFECT_ATTR] == "Lightning (2 speeds)"


@pytest.mark.usefixtures("fake_ble")
async def test_shrinking_the_range_brings_the_value_with_it(
    hass: HomeAssistant,
) -> None:
    """Switching to a slower-range effect must not leave the value past the end.

    Home Assistant has no way to declare that this number belongs to that
    effect, so the relationship is maintained by hand -- and a hand-built one
    has to remember cases the declarative kind would get for free. This is one:
    set speed 1, choose a single-speed effect, and the slider showed 1 out of a
    maximum of 0.
    """
    from homeassistant.components.number import (
        ATTR_VALUE,
        DOMAIN as NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
    )

    await _setup(hass, WITH_SPEED)
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: SPEED, ATTR_VALUE: 1},
        blocking=True,
    )

    with patch.object(GodoxMeshLink, "async_set_effect", new=AsyncMock()):
        await hass.services.async_call(
            "light", "turn_on",
            {ATTR_ENTITY_ID: LIGHT, ATTR_EFFECT: "Candle", ATTR_BRIGHTNESS: 255},
            blocking=True,
        )
    await hass.async_block_till_done()

    state = hass.states.get(SPEED)
    assert float(state.state) <= float(state.attributes["max"])


@pytest.mark.usefixtures("fake_ble")
async def test_the_bound_survives_a_restart(hass: HomeAssistant) -> None:
    """A restored effect must re-bind the slider, not just the light.

    The light restores its effect from state; the speed control has no state of
    its own to tell it which effect that was, so the light has to publish it.
    """
    from homeassistant.components.light import ATTR_EFFECT as EFFECT_ATTR
    from homeassistant.const import STATE_ON
    from homeassistant.core import State

    from pytest_homeassistant_custom_component.common import mock_restore_cache

    mock_restore_cache(hass, [State(LIGHT, STATE_ON, {EFFECT_ATTR: "Candle"})])
    await _setup(hass, WITH_SPEED)

    assert float(hass.states.get(SPEED).attributes["max"]) == 0


@pytest.mark.usefixtures("fake_ble")
async def test_a_saved_speed_is_restored_without_crashing(hass: HomeAssistant) -> None:
    """Restoring a saved speed must not read the _attr_ backing field this class never sets.

    ``native_max_value`` is a property here (it follows the running effect), so
    ``_attr_native_max_value`` is never assigned -- and Home Assistant's
    NumberEntity exposes that name as a property whose internals reference a
    name-mangled private, so a bare read of it raises AttributeError. The entity
    then fails to add. Every earlier restore test cached a *light* state, not a
    speed one, so ``_restore`` returned early and this line never ran; caching a
    speed state is what exercises it.
    """
    from homeassistant.core import State

    from pytest_homeassistant_custom_component.common import mock_restore_cache

    mock_restore_cache(hass, [State(SPEED, "1")])
    await _setup(hass, WITH_SPEED)

    state = hass.states.get(SPEED)
    assert state is not None  # the entity added; before the fix it raised on restore
    assert state.state == "1"
