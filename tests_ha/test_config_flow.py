"""Tests for the Godox Bluetooth Mesh config and options flows."""

from __future__ import annotations

import json
from unittest.mock import patch

from custom_components.godox_mesh.const import (
    CONF_MAC,
    CONF_POLL_BRIGHTNESS,
    CONF_POLL_CCT,
    CONF_POLL_INTERVAL,
    CONF_RADIO_ID,
    CONF_READBACK,
    CONF_MESH,
    CONF_MESH_STATE_JSON,
    CONF_NODE_ADDRESS,
    CONF_NUM_ELEMENTS,
    CONF_NODES,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from tests_ha.conftest import ADDRESS, MESH_STATE

from pytest_homeassistant_custom_component.common import MockConfigEntry

SERVICE_INFO_PATH = (
    "custom_components.godox_mesh.config_flow.async_discovered_service_info"
)


def _service_info(
    address: str = ADDRESS,
    name: str = "GD_LED",
    connectable: bool = True,
    manufacturer_data: dict | None = None,
):
    """Build a minimal discovery payload."""
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

    manufacturer_data = manufacturer_data or {}
    device = BLEDevice(address, name, {})
    advertisement = AdvertisementData(
        local_name=name,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=["00001828-0000-1000-8000-00805f9b34fb"],
        tx_power=None,
        rssi=-60,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=["00001828-0000-1000-8000-00805f9b34fb"],
        source="local",
        device=device,
        advertisement=advertisement,
        connectable=connectable,
        time=0,
        tx_power=None,
    )


async def test_bluetooth_discovery_offers_both_setup_paths(hass: HomeAssistant) -> None:
    """A discovered light leads to the setup-method form, not straight to an entry.

    A form rather than a menu, so the choice can be changed before submitting.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "setup_method"
    options = result["data_schema"].schema["setup_method"].config["options"]
    assert set(options) == {"mesh_state", "provision"}


async def test_discovery_waits_for_the_model_id_before_naming(
    hass: HomeAssistant,
) -> None:
    """Discovery on a packet without the model id waits for one that carries it.

    These lights alternate advert packets; discovery often fires on the
    proxy-service one, which has no manufacturer data, so without the wait the
    light would be named the bare GD_LED instead of its model.
    """
    from unittest.mock import AsyncMock

    # An advert that does carry radioId 003F (SL200III Bi), returned by the wait.
    model_advert = _service_info(
        manufacturer_data={0x0211: bytes(4) + b"\x3f\x00" + bytes(2)}
    )
    with (
        patch(
            "custom_components.godox_mesh.config_flow.async_last_service_info",
            return_value=None,
        ),
        patch(
            "custom_components.godox_mesh.config_flow.async_process_advertisements",
            AsyncMock(return_value=model_advert),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_BLUETOOTH},
            data=_service_info(),  # the triggering packet: no manufacturer data
        )

    assert result["step_id"] == "setup_method"
    assert result["description_placeholders"]["name"] == "SL200IIIBi (EEFF)"


async def test_discovery_falls_back_to_the_bare_name_on_timeout(
    hass: HomeAssistant,
) -> None:
    """If no model-carrying advert arrives in time, the advertised name is kept."""
    from unittest.mock import AsyncMock

    with (
        patch(
            "custom_components.godox_mesh.config_flow.async_last_service_info",
            return_value=None,
        ),
        patch(
            "custom_components.godox_mesh.config_flow.async_process_advertisements",
            AsyncMock(side_effect=TimeoutError),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_BLUETOOTH},
            data=_service_info(),
        )

    assert result["step_id"] == "setup_method"
    assert result["description_placeholders"]["name"] == "GD_LED (EEFF)"


async def test_setup_method_choice_routes_to_the_selected_step(
    hass: HomeAssistant,
) -> None:
    """Submitting the form routes to the chosen step (form, not a menu dead-end)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )
    provision = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"setup_method": "provision"}
    )
    assert provision["step_id"] == "provision"

    # A separate device (distinct unique_id) for the other branch.
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=_service_info(address="11:22:33:44:55:66"),
    )
    mesh = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"setup_method": "mesh_state"}
    )
    assert mesh["step_id"] == "mesh_state"


async def test_pasted_mesh_state_creates_an_entry(hass: HomeAssistant) -> None:
    """The paste-JSON path produces a usable entry with one node."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"setup_method": "mesh_state"}
    )
    assert result["step_id"] == "mesh_state"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MESH_STATE_JSON: json.dumps(MESH_STATE)}
    )
    assert result["step_id"] == "model"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_RADIO_ID: "003F"}
    )
    # Picking the model leads to a second, settings step whose defaults come
    # from that model. Accepting them unchanged carries them onto the node.
    assert result["step_id"] == "settings"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == ADDRESS
    assert result["data"][CONF_MESH]["network_key"] == MESH_STATE["network_key"]
    # num_elements is recorded from the light's own Provisioning Capabilities
    # PDU, so the allocator can place later lights around this one's real span
    # rather than assuming every model occupies two addresses.
    assert result["options"][CONF_NODES] == [
        {
            CONF_NODE_ADDRESS: 2,
            # The advertised name is the same on every Godox light, so the label
            # carries the last four of the address. The name follows the model
            # the user picked, so "GD_LED" resolves to the product name.
            CONF_NAME: "SL200IIIBi (EEFF)",
            "model": None,
            CONF_RADIO_ID: "003F",
            CONF_NUM_ELEMENTS: 2,
            # Settings default from the picked model: 003F reports panel colour
            # temperature stale, so its verified defaults are readback on,
            # colour-temperature polling off.
            CONF_READBACK: True,
            CONF_POLL_CCT: False,
            # Brightness readback defaults on for every model.
            CONF_POLL_BRIGHTNESS: True,
            CONF_POLL_INTERVAL: 10,
        }
    ]


async def test_pasted_mesh_state_rejects_garbage(hass: HomeAssistant) -> None:
    """A bad paste re-shows the form with a field-level error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"setup_method": "mesh_state"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MESH_STATE_JSON: "nonsense"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MESH_STATE_JSON: "invalid_mesh_state"}


async def test_sequence_number_is_floored_to_survive_the_godox_app(
    hass: HomeAssistant,
) -> None:
    """A low counter is raised past anything the vendor app is likely to have used."""
    low = dict(MESH_STATE, sequence_number=5)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"setup_method": "mesh_state"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MESH_STATE_JSON: json.dumps(low)}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["data"][CONF_MESH]["sequence_number"] >= 300_000


async def test_non_connectable_advertisement_is_rejected(hass: HomeAssistant) -> None:
    """A light heard only by a listen-only adapter cannot be driven."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=_service_info(connectable=False),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_connectable"


async def test_duplicate_discovery_aborts(hass: HomeAssistant) -> None:
    """An already-configured address is not offered again."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS, data={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_a_provisioned_node_is_not_offered_as_a_new_discovery(
    hass: HomeAssistant,
) -> None:
    """A light already provisioned as a node must not show up as discoverable.

    A provisioned node keeps advertising the proxy service, and its MAC lives on
    the entry (not as the entry's unique id), so the framework's own dedupe would
    otherwise re-offer a light we already manage.
    """
    node_mac = "11:22:33:44:55:66"
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 4, CONF_NAME: "Fill", CONF_MAC: node_mac}
            ]
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=_service_info(address=node_mac),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_the_user_picker_hides_a_provisioned_node(hass: HomeAssistant) -> None:
    """The manual picker also omits a light already provisioned onto a mesh."""
    node_mac = "11:22:33:44:55:66"
    other = "99:99:99:99:99:99"
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_MESH: dict(MESH_STATE)},
        options={
            CONF_NODES: [
                {CONF_NODE_ADDRESS: 4, CONF_NAME: "Fill", CONF_MAC: node_mac}
            ]
        },
    )
    entry.add_to_hass(hass)

    with patch(
        SERVICE_INFO_PATH,
        return_value=[_service_info(address=node_mac), _service_info(address=other)],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    options = list(result["data_schema"].schema[CONF_ADDRESS].container)
    assert node_mac not in options  # already a node
    assert other in options  # a genuine candidate is still offered


async def test_user_flow_lists_discovered_devices(hass: HomeAssistant) -> None:
    """The manual path picks from what the Bluetooth manager can see."""
    with patch(SERVICE_INFO_PATH, return_value=[_service_info()]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_flow_aborts_without_devices(hass: HomeAssistant) -> None:
    """Nothing in range means nothing to configure."""
    with patch(SERVICE_INFO_PATH, return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_provisioning_flow_binds_the_app_key_and_creates_an_entry(
    hass: HomeAssistant,
) -> None:
    """Provisioning must push the app key before the entry is created.

    A node that has been provisioned but not had the application key bound to
    its vendor model accepts the connection and ignores every light command,
    which is indistinguishable from a broken integration.
    """
    from custom_components.godox_mesh._lib import MeshState

    provisioned = MeshState(
        network_key="00" * 16,
        app_key="00" * 16,
        device_key="aa" * 16,
        provisioner_address=1,
        node_address=2,
        sequence_number=1,
        iv_index=0,
    )
    order: list[str] = []

    async def fake_provision(self):
        order.append("provision")
        return provisioned

    async def fake_bind(self):
        order.append("bind")
        return self._state.next_sequence(2)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"setup_method": "provision"}
    )
    assert result["step_id"] == "provision"

    with (
        patch(
            "custom_components.godox_mesh.config_flow.ProvisioningSession.run",
            fake_provision,
        ),
        patch(
            "custom_components.godox_mesh.config_flow.ConfigSession.run", fake_bind
        ),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert order == ["provision", "bind"]
    assert result["step_id"] == "model"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "settings"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    mesh = result["data"][CONF_MESH]
    # A freshly generated application key, not the provisioning placeholder.
    assert mesh["app_key"] != "00" * 16
    assert len(mesh["app_key"]) == 32
    assert mesh["device_key"] == "aa" * 16


async def test_provisioning_failure_is_reported_not_swallowed(
    hass: HomeAssistant,
) -> None:
    """A failed provisioning must leave the user on the form with an error."""
    async def boom(self):
        raise RuntimeError("device did not respond")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"setup_method": "provision"}
    )

    with patch(
        "custom_components.godox_mesh.config_flow.ProvisioningSession.run", boom
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "provision_failed"}


async def test_discovery_is_not_filtered_by_name(hass: HomeAssistant) -> None:
    """Discovery must match on service UUID alone.

    A local_name matcher fails silently: a Godox model advertising some other
    name would never be discovered and nothing would say why. Noise in the
    picker is recoverable; an invisible device is not.
    """
    import json
    from pathlib import Path

    manifest = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "custom_components/godox_mesh/manifest.json"
        ).read_text()
    )
    for matcher in manifest["bluetooth"]:
        assert "local_name" not in matcher, matcher
        assert matcher["service_uuid"].startswith(("00001827", "00001828"))


async def test_user_step_sorts_likely_godox_first_without_hiding_others(
    hass: HomeAssistant,
) -> None:
    """Godox lights float to the top; everything else stays selectable."""
    devices = [
        _service_info("11:11:11:11:11:11", name="Some Mesh Bulb"),
        _service_info("22:22:22:22:22:22", name="GD_LED"),
        _service_info("33:33:33:33:33:33", name="Another Node"),
    ]
    with patch(SERVICE_INFO_PATH, return_value=devices):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.FORM
    options = list(result["data_schema"].schema[CONF_ADDRESS].container)
    assert options[0] == "22:22:22:22:22:22", "the Godox light should be first"
    # Nothing is filtered out — an unrecognised name is still selectable.
    assert set(options) == {
        "11:11:11:11:11:11",
        "22:22:22:22:22:22",
        "33:33:33:33:33:33",
    }


async def test_model_selection_stores_radio_id_for_correct_controls(
    hass: HomeAssistant,
) -> None:
    """Picking a model records its radioId, which drives the entity's controls."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"setup_method": "mesh_state"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MESH_STATE_JSON: json.dumps(MESH_STATE)}
    )
    assert result["step_id"] == "model"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_RADIO_ID: "00B6"}  # SL200 RF, 1800-10000 K
    )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_NODES][0][CONF_RADIO_ID] == "00B6"


async def test_model_selection_may_be_left_unset(hass: HomeAssistant) -> None:
    """A user who does not know the model gets a working default light."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"setup_method": "mesh_state"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MESH_STATE_JSON: json.dumps(MESH_STATE)}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "settings"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_NODES][0][CONF_RADIO_ID] is None


async def test_settings_step_surfaces_the_models_known_quirk(
    hass: HomeAssistant,
) -> None:
    """The settings step shows the picked model's note, so its defaults make sense."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_service_info()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"setup_method": "mesh_state"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MESH_STATE_JSON: json.dumps(MESH_STATE)}
    )
    # 003F reports panel colour temperature stale; the note explains it.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_RADIO_ID: "003F"}
    )
    assert result["step_id"] == "settings"
    note = result["description_placeholders"]["note"]
    assert "SL200IIIBi" in note
    assert "stale" in note

    # A model without a recorded note leaves the placeholder empty.
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=_service_info(address="11:22:33:44:55:66"),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"setup_method": "mesh_state"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MESH_STATE_JSON: json.dumps(MESH_STATE)}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_RADIO_ID: "00B6"}  # no note recorded
    )
    assert result["description_placeholders"]["note"] == ""


def test_node_address_allocator_advances_by_element_count() -> None:
    """A node occupies two unicast addresses, so the allocator strides by two.

    Otherwise a second light's primary address collides with the first light's
    element 1 (Light CTL Temperature).
    """
    from custom_components.godox_mesh.config_flow import _next_free_node_address

    # first light at 0x0002 uses 0x0002 (element 0) and 0x0003 (element 1)
    assert _next_free_node_address({2}) == 4
    assert _next_free_node_address({2, 4}) == 6
    # a gap that is too small for both elements is skipped
    assert _next_free_node_address({2, 6}) == 4


async def test_configure_light_persists_per_node_settings(
    hass: HomeAssistant, mock_config_entry, fake_ble
) -> None:
    """Configure a light writes readback/poll settings onto that node.

    Replaces the old entry-wide "Live state" step: settings are per-light now,
    reached via Configure -> Configure a light.
    """
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.godox_mesh.bluetooth.async_ble_device_from_address",
        return_value=object(),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(
            mock_config_entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "change_model"}
        )
        # One node, so it goes straight to the model picker, then its settings.
        assert result["step_id"] == "set_model"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_RADIO_ID: "003F"}
        )
        assert result["step_id"] == "node_settings"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_READBACK: True, CONF_POLL_CCT: False, CONF_POLL_INTERVAL: 60},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    node = mock_config_entry.options[CONF_NODES][0]
    assert node[CONF_READBACK] is True
    assert node[CONF_POLL_CCT] is False
    assert node[CONF_POLL_INTERVAL] == 60


async def test_setup_uses_the_models_verified_defaults(hass: HomeAssistant) -> None:
    """A detected model's verified defaults pre-fill the settings at setup.

    003F (SL200III Bi) reports panel colour temperature stale, so its default is
    poll_cct off; readback on. Submitting the model step without touching the
    settings must land those defaults on the node.
    """
    # Manufacturer data carrying radioId 003F (payload offsets 4/5).
    payload = bytes(4) + b"\x3f\x00" + bytes(2)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=_service_info(manufacturer_data={0x0211: payload}),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"setup_method": "mesh_state"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MESH_STATE_JSON: json.dumps(MESH_STATE)}
    )
    # Model detected from the advert, so the model step accepts it unchanged and
    # the settings step then shows and applies that model's verified defaults.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "settings"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    node = result["options"][CONF_NODES][0]
    assert node[CONF_RADIO_ID] == "003F"
    assert node[CONF_READBACK] is True
    assert node[CONF_POLL_CCT] is False
