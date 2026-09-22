"""Config and options flows for the Godox Bluetooth Mesh integration."""

from __future__ import annotations

import logging
import os
from typing import Any

import voluptuous as vol
from ._lib import MeshState
from ._lib.config_session import ConfigSession
from ._lib.provisioning import ProvisioningSession

from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_last_service_info,
    async_process_advertisements,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .ble import HomeAssistantBleakClient
from .capabilities import (
    GodoxCapabilities,
    capabilities_for_radio_id,
    known_models,
    radio_id_from_manufacturer_data,
)
from .const import (
    CONF_DEVICE_KEY,
    CONF_MESH,
    CONF_MESH_STATE_JSON,
    CONF_MODEL,
    CONF_NODE_ADDRESS,
    CONF_NUM_ELEMENTS,
    CONF_MAC,
    CONF_NODES,
    CONF_POLL_BRIGHTNESS,
    CONF_POLL_CCT,
    CONF_POLL_INTERVAL,
    CONF_RADIO_ID,
    CONF_READBACK,
    CONF_USE_XY,
    DEFAULT_NODE_ADDRESS,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PROVISIONER_ADDRESS,
    DISCOVERY_MODEL_WAIT_SECONDS,
    DOMAIN,
    ELEMENTS_PER_NODE,
    INTEGRATION_TITLE,
    GODOX_DEVICE_NAME,
    GODOX_NAME_HINTS,
    MAX_POLL_INTERVAL,
    MESH_PROVISIONING_SERVICE_UUID,
    MIN_POLL_INTERVAL,
)
from .mesh_state_input import (
    InvalidMeshState,
    mesh_state_to_dict,
    parse_mesh_state,
)

_LOGGER = logging.getLogger(__name__)

# Leave room above anything the Godox app may have already burned into the
# node's replay protection list. Sequence numbers are 24-bit and cheap; a light
# that ignores commands because of a stale counter is the single most confusing
# failure this integration has.
PROVISIONED_SEQUENCE_FLOOR = 300_000


def _model_name(
    service_info: BluetoothServiceInfoBleak, radio_id: str | None = None
) -> str:
    """The product name for a discovered device, or the best available label.

    Every Godox mesh light advertises the same device name, ``GD_LED``, so it
    identifies nothing. The model id is in the advertisement, so prefer the
    product name and keep the advertised name only as a fallback.

    Pass ``radio_id`` to name the device from a model the user *chose* rather
    than one detected from the advertisement -- the advertisement does not carry
    the model id on every Bluetooth stack, so a provisioned light is often named
    from the picker instead.
    """
    radio_id = radio_id or radio_id_from_manufacturer_data(
        service_info.manufacturer_data
    )
    if radio_id:
        name = capabilities_for_radio_id(radio_id).name
        if name:
            return name
    return service_info.name or service_info.address


def _display_name(
    service_info: BluetoothServiceInfoBleak, radio_id: str | None = None
) -> str:
    """A label that stays unique when two lights are the same model.

    Two SL200III Bis would otherwise produce two identical discovery cards, so
    the last four hex digits of the address are appended -- enough to tell them
    apart, and short enough to read. Omitted when the label already *is* the
    address, which would just repeat it.
    """
    name = _model_name(service_info, radio_id)
    if name == service_info.address:
        return name
    suffix = service_info.address.replace(":", "").replace("-", "")[-4:].upper()
    return f"{name} ({suffix})" if suffix else name


def _detected_radio_id(service_info: BluetoothServiceInfoBleak | None) -> str | None:
    """The model id a light advertises, when it is one this build knows.

    Godox puts the model id in its manufacturer data, so in the common case the
    user does not have to identify their light at all -- and this works for any
    of the mesh models, not a list someone maintains by hand. An unknown or
    absent id just leaves the picker empty.
    """
    if service_info is None:
        return None
    radio_id = radio_id_from_manufacturer_data(service_info.manufacturer_data)
    return radio_id if radio_id in known_models() else None


def _model_form_fields(suggested: str | None) -> dict:
    """Schema for the model picker, pre-filling a detected model when there is one.

    ``default=`` does NOT pre-fill a rendered form -- it only supplies a value at
    validation time when the key is absent. Populating the field the user sees
    requires ``suggested_value``.
    """
    field = (
        vol.Optional(CONF_RADIO_ID, description={"suggested_value": suggested})
        if suggested
        else vol.Optional(CONF_RADIO_ID)
    )
    return {field: _model_selector()}


def _model_detection_note(detected: str | None) -> str:
    """Say so when the model came from the advertisement, rather than the user."""
    caps = capabilities_for_radio_id(detected) if detected else None
    if caps and caps.name:
        return (
            f"This light identifies itself as a **{caps.name}**, which is "
            "already selected below. Change it only if that is wrong.\n\n"
        )
    return ""


def _light_settings_fields(
    caps: GodoxCapabilities,
    *,
    readback: bool,
    poll_cct: bool,
    poll_brightness: bool,
    poll_interval: int,
    use_xy: bool | None = None,
) -> dict:
    """Schema for the per-light readback/polling settings.

    ``poll_cct`` is offered only for colour-temperature models; ``use_xy`` only
    when ``use_xy`` is not ``None`` and the model supports it (post-setup, where
    the model is known). Brightness applies to every light, so its toggle is
    always offered. Defaults pre-fill the rendered form: bool fields via
    ``default=``, the number field via ``suggested_value``.
    """
    fields: dict[Any, Any] = {
        vol.Required(CONF_READBACK, default=readback): bool,
        vol.Required(CONF_POLL_BRIGHTNESS, default=poll_brightness): bool,
    }
    if caps.supports_cct:
        fields[vol.Required(CONF_POLL_CCT, default=poll_cct)] = bool
    fields[
        vol.Required(
            CONF_POLL_INTERVAL,
            default=poll_interval,
            description={"suggested_value": poll_interval},
        )
    ] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=MIN_POLL_INTERVAL,
            max=MAX_POLL_INTERVAL,
            step=1,
            unit_of_measurement="s",
            mode=selector.NumberSelectorMode.BOX,
        )
    )
    if use_xy is not None and caps.supports_xy:
        fields[vol.Required(CONF_USE_XY, default=use_xy)] = bool
    return fields


def _light_settings_from_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Extract the per-light settings a step's form submitted, for a node dict."""
    settings: dict[str, Any] = {}
    for key in (CONF_READBACK, CONF_POLL_CCT, CONF_POLL_BRIGHTNESS, CONF_USE_XY):
        if key in user_input:
            settings[key] = bool(user_input[key])
    if CONF_POLL_INTERVAL in user_input:
        settings[CONF_POLL_INTERVAL] = int(user_input[CONF_POLL_INTERVAL])
    return settings


def _model_note(caps: GodoxCapabilities) -> str:
    """A model's known quirk, for the settings step, or empty when none.

    This is why the settings step is a *second* form: a Home Assistant form is
    static once shown, so the readback/CCT defaults and this note can only
    reflect the model once it has been picked and submitted.
    """
    if caps.note and caps.name:
        return f"\n\n**{caps.name}:** {caps.note}"
    return ""


def _looks_like_godox(service_info: BluetoothServiceInfoBleak) -> bool:
    """Return whether an advertisement looks like a Godox mesh light.

    Ordered by how much each signal is worth, and deliberately not a list of
    model names -- that only recognises models someone remembered to add, and
    there are 190 of them:

    1. Manufacturer data carrying a ``radioId`` this build knows.
    2. The device name Godox's own app filters on.
    3. A mesh service UUID plus a Godox-ish name, for a light whose model id is
       unknown to this build but which is still plainly one of theirs.
    """
    if radio_id_from_manufacturer_data(service_info.manufacturer_data) in known_models():
        return True
    name = (service_info.name or "").lower().replace(" ", "").replace("-", "")
    if name == GODOX_DEVICE_NAME.replace("_", ""):
        return True
    return any(hint in name for hint in GODOX_NAME_HINTS)




def _model_selector() -> selector.SelectSelector:
    """A searchable dropdown of every known model, keyed by radioId.

    The value stored is the radioId; the label is the product name. Leaving it
    unset is allowed and yields a safe default colour-temperature light.
    """
    options = [
        selector.SelectOptionDict(value=radio_id, label=f"{caps.name} ({radio_id})")
        for radio_id, caps in sorted(
            known_models().items(), key=lambda kv: (kv[1].name or "", kv[0])
        )
        if caps.name
    ]
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            mode=selector.SelectSelectorMode.DROPDOWN,
            custom_value=False,
        )
    )


class GodoxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Godox Bluetooth Mesh."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}
        self._address: str | None = None
        self._title: str | None = None
        self._state: MeshState | None = None
        #: The model picked on a model step, carried to the settings step that
        #: follows it (the settings form's defaults depend on it).
        self._pending_radio_id: str | None = None
        #: When joining an existing mesh: which entry, and the provisioned node
        #: waiting for its model on the join-model step.
        self._join_entry_id: str | None = None
        self._join_pending: dict[str, Any] | None = None

    def _loaded_meshes(self) -> list[ConfigEntry]:
        """Existing Godox mesh entries this light could be added to.

        Only loaded entries qualify -- joining one provisions the light over
        that entry's live proxy connection, which an unloaded entry does not
        have.
        """
        return [
            entry
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
            and getattr(entry, "runtime_data", None) is not None
        ]

    def _mesh_by_id(self, entry_id: str | None) -> ConfigEntry | None:
        return next(
            (e for e in self._loaded_meshes() if e.entry_id == entry_id), None
        )

    def _configured_addresses(self) -> set[str]:
        """Every BLE address this integration already manages.

        Each entry's primary light plus every node provisioned onto it. A
        provisioned node keeps advertising the Mesh Proxy service -- that is how
        gateway failover finds it -- so without this it is re-discovered and
        offered as a new device even though it is already one of our lights. The
        primary is the entry's unique id, but a node's MAC is stored on the entry
        rather than as a unique id, so the framework's own dedupe misses it.
        """
        addresses: set[str] = set()
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if primary := entry.data.get(CONF_ADDRESS):
                addresses.add(primary)
            for node in entry.options.get(CONF_NODES, []):
                if mac := node.get(CONF_MAC):
                    addresses.add(mac)
        return addresses

    # -- discovery ---------------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device discovered over Bluetooth."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        # A light already provisioned as a node of an existing mesh keeps
        # advertising the proxy service; its MAC is on the entry, not the entry's
        # unique id, so the check above does not catch it. Ignore it rather than
        # offer a light we already manage as a new discovery.
        if discovery_info.address in self._configured_addresses():
            return self.async_abort(reason="already_configured")

        # Driving the light needs a GATT write, so an advertisement heard only
        # by a listen-only controller is no use.
        if not discovery_info.connectable:
            return self.async_abort(reason="not_connectable")

        discovery_info = await self._async_advert_with_model(discovery_info)
        self._discovery = discovery_info
        self._address = discovery_info.address
        self._title = _display_name(discovery_info)
        self.context["title_placeholders"] = {"name": self._title or INTEGRATION_TITLE}
        return await self.async_step_setup_method()

    async def _async_advert_with_model(
        self, service_info: BluetoothServiceInfoBleak
    ) -> BluetoothServiceInfoBleak:
        """Return an advert that carries the model id, waiting briefly for one.

        These lights alternate advert packets, and only one carries the
        manufacturer data with the ``radioId``; discovery often fires on the
        proxy-service packet, which has none, so the light would be named the
        bare ``GD_LED``. Home Assistant merges packets per address, so its stored
        advert may already have it; otherwise wait a short while for one that
        does. Falls back to what triggered discovery if none arrives.
        """
        if radio_id_from_manufacturer_data(service_info.manufacturer_data):
            return service_info
        merged = async_last_service_info(
            self.hass, service_info.address, connectable=service_info.connectable
        )
        if merged is not None and radio_id_from_manufacturer_data(
            merged.manufacturer_data
        ):
            return merged
        try:
            return await async_process_advertisements(
                self.hass,
                lambda info: radio_id_from_manufacturer_data(info.manufacturer_data)
                is not None,
                {"address": service_info.address, "connectable": service_info.connectable},
                BluetoothScanningMode.ACTIVE,
                DISCOVERY_MODEL_WAIT_SECONDS,
            )
        except Exception as err:  # noqa: BLE001
            # Best-effort naming: on timeout, or if the wait cannot run, keep the
            # advertised name rather than failing the discovery. The model is
            # picked in the flow regardless.
            _LOGGER.debug("no model-carrying advert for %s: %s", service_info.address, err)
            return service_info

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow started from the UI: pick a device, then set it up."""
        # `flow_title` in strings.json is "{name}", and Home Assistant formats it
        # whenever it renders this flow -- including the device picker below,
        # which is shown before any device has been chosen. Without a value here
        # that render fails with a MISSING_VALUE translation error.
        self.context.setdefault("title_placeholders", {"name": INTEGRATION_TITLE})
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._address = address
            self._title = _display_name(self._discovered[address])
            self._discovery = self._discovered[address]
            self.context["title_placeholders"] = {
                "name": self._title or INTEGRATION_TITLE
            }
            return await self.async_step_setup_method()

        # Hide both other entries (by unique id) and any node already provisioned
        # onto a mesh -- a provisioned node still advertises, so it would
        # otherwise appear in the picker as a light to add again.
        current = self._async_current_ids(include_ignore=False)
        current |= self._configured_addresses()
        for service_info in async_discovered_service_info(self.hass, connectable=True):
            if service_info.address in current:
                continue
            self._discovered[service_info.address] = service_info

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        # Likely Godox lights first, but never hide anything: a provisioned
        # mesh node does not always advertise a recognizable name.
        ordered = sorted(
            self._discovered.values(),
            key=lambda info: (not _looks_like_godox(info), _display_name(info)),
        )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            info.address: f"{_model_name(info)} ({info.address})"
                            for info in ordered
                        }
                    )
                }
            ),
        )

    # -- choosing how to get the keys --------------------------------------

    async def async_step_setup_method(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask whether the light is already provisioned or factory reset.

        A form rather than a menu, so the choice can be changed before
        submitting -- a menu commits on the first tap, with no way back short of
        closing the dialog and starting over.
        """
        if user_input is not None:
            choice = user_input["setup_method"]
            if choice == "mesh_state":
                return await self.async_step_mesh_state()
            if choice == "join_existing":
                return await self.async_step_join_existing()
            return await self.async_step_provision()
        # Default to provisioning (the common case for a light being added);
        # "Add to an existing Godox mesh" only when one exists; pasting keys is
        # the advanced path, so it comes last.
        options = ["provision"]
        if self._loaded_meshes():
            options.append("join_existing")
        options.append("mesh_state")
        return self.async_show_form(
            step_id="setup_method",
            data_schema=vol.Schema(
                {
                    vol.Required("setup_method"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.LIST,
                            translation_key="setup_method",
                            custom_value=False,
                        )
                    )
                }
            ),
            description_placeholders={"name": self._title or ""},
        )

    async def async_step_mesh_state(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Accept the contents of an existing ``mesh_state.json``."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                state = parse_mesh_state(user_input[CONF_MESH_STATE_JSON])
            except InvalidMeshState as err:
                _LOGGER.debug("rejected pasted mesh state: %s", err)
                errors[CONF_MESH_STATE_JSON] = "invalid_mesh_state"
            else:
                return self._create_entry(state)

        return self.async_show_form(
            step_id="mesh_state",
            data_schema=vol.Schema({vol.Required(CONF_MESH_STATE_JSON): str}),
            errors=errors,
            description_placeholders={"name": self._title or ""},
        )

    # -- provisioning a factory-reset light --------------------------------

    async def async_step_provision(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Provision a factory-reset light and bind the application key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                state = await self._async_provision()
            except Exception as err:  # noqa: BLE001 - surfaced to the user
                _LOGGER.warning("provisioning %s failed: %s", self._address, err)
                errors["base"] = "provision_failed"
            else:
                return self._create_entry(state)

        return self.async_show_form(
            step_id="provision",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"name": self._title or ""},
        )

    async def _async_provision(self) -> MeshState:
        """Run PB-GATT provisioning, then push and bind the application key."""
        assert self._address is not None
        name = self._title or self._address

        def client_factory(_target: str) -> HomeAssistantBleakClient:
            # Never reuse a cached GATT table here: a light's services change
            # when it joins or leaves a mesh, and a stale table hides the
            # provisioning characteristics entirely -- which is why
            # provisioning a just-reset light can sit until it times out.
            return HomeAssistantBleakClient(
                self.hass, self._address or "", name, use_services_cache=False
            )

        net_key = os.urandom(16)
        app_key = os.urandom(16)

        session = ProvisioningSession(
            address=self._address,
            net_key=net_key,
            key_index=0,
            iv_index=0,
            unicast_address=DEFAULT_NODE_ADDRESS,
            provisioner_address=DEFAULT_PROVISIONER_ADDRESS,
            client_factory=client_factory,
        )
        state = await session.run()
        state = MeshState(
            network_key=state.network_key,
            app_key=app_key.hex(),
            device_key=state.device_key,
            device_address=self._address,
            provisioner_address=state.provisioner_address,
            node_address=state.node_address,
            sequence_number=state.sequence_number,
            iv_index=state.iv_index,
        )

        # The node now knows the network key but has no application key bound
        # to the vendor model, so nothing would respond to a light command yet.
        return await ConfigSession(
            address=self._address, state=state, client_factory=client_factory
        ).run()

    # -- joining an existing mesh ------------------------------------------

    async def async_step_join_existing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Provision this factory-reset light onto an existing Godox mesh.

        Unlike the provision step, this adds the light to a mesh already set up
        here -- same keys, same proxy connection -- rather than creating a new
        one. No new config entry results; the chosen entry gains a node.
        """
        meshes = self._loaded_meshes()
        if not meshes:
            return self.async_abort(reason="no_existing_mesh")

        errors: dict[str, str] = {}
        if user_input is not None:
            entry = self._mesh_by_id(user_input.get("mesh")) or meshes[0]
            self._join_entry_id = entry.entry_id
            nodes = list(entry.options.get(CONF_NODES, []))
            node_address = _next_free_node_address(_occupied_addresses(nodes))
            name = self._title or self._address or ""
            try:
                device_key, num_elements = await entry.runtime_data.link.async_provision_node(
                    self._address, node_address, name
                )
            except Exception as err:  # noqa: BLE001 - surfaced to the user
                _LOGGER.warning(
                    "provisioning %s onto %s failed: %s",
                    self._address,
                    entry.title,
                    err,
                )
                errors["base"] = "provision_failed"
            else:
                self._join_pending = {
                    "node_address": node_address,
                    "device_key": device_key,
                    "num_elements": num_elements,
                }
                return await self.async_step_join_model()

        # Always show the picker, even for a single mesh; a default keeps a
        # one-mesh submission valid without the user touching it.
        schema = {
            vol.Required("mesh", default=meshes[0].entry_id): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=e.entry_id, label=e.title)
                        for e in meshes
                    ],
                    mode=selector.SelectSelectorMode.LIST,
                    custom_value=False,
                )
            )
        }
        return self.async_show_form(
            step_id="join_existing",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"name": self._title or ""},
        )

    async def async_step_join_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the model of a light just joined to an existing mesh."""
        assert self._join_pending is not None
        detected = _detected_radio_id(self._discovery)
        if user_input is None:
            return self.async_show_form(
                step_id="join_model",
                data_schema=vol.Schema(_model_form_fields(detected)),
                description_placeholders={"detected": _model_detection_note(detected)},
            )
        self._pending_radio_id = user_input.get(CONF_RADIO_ID) or detected
        return await self.async_step_settings()

    def _complete_join(
        self, radio_id: str | None, settings: dict[str, Any]
    ) -> ConfigFlowResult:
        """Add the just-provisioned light to the existing mesh entry."""
        entry = self._mesh_by_id(self._join_entry_id)
        pending = self._join_pending
        assert entry is not None and pending is not None
        name = (
            _display_name(self._discovery, radio_id)
            if self._discovery is not None
            else (self._title or self._address or "")
        )
        nodes = list(entry.options.get(CONF_NODES, []))
        nodes.append(
            {
                CONF_NODE_ADDRESS: pending["node_address"],
                CONF_NAME: name,
                CONF_MODEL: None,
                CONF_RADIO_ID: radio_id,
                CONF_MAC: self._address,
                CONF_DEVICE_KEY: pending["device_key"],
                CONF_NUM_ELEMENTS: pending["num_elements"],
                **settings,
            }
        )
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_NODES: nodes}
        )
        return self.async_abort(reason="added_to_existing")

    # -- entry creation ----------------------------------------------------

    def _create_entry(self, state: MeshState) -> ConfigFlowResult:
        """Stash the mesh state, then ask which model this is."""
        self._state = state
        return self._show_model_form()

    def _show_model_form(
        self, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        detected = _detected_radio_id(self._discovery)
        return self.async_show_form(
            step_id="model",
            data_schema=vol.Schema(_model_form_fields(detected)),
            errors=errors or {},
            description_placeholders={
                "name": self._title or "",
                "detected": _model_detection_note(detected),
            },
        )

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Record the chosen model, then ask how its state should be read."""
        if user_input is None:
            return self._show_model_form()
        # Fall back to the detected model when the user leaves it as suggested,
        # matching the provision/join model steps.
        self._pending_radio_id = user_input.get(CONF_RADIO_ID) or _detected_radio_id(
            self._discovery
        )
        return await self.async_step_settings()

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set readback/polling for the light, defaulted from the picked model.

        Shared by first-time setup and by joining an existing mesh; which one is
        in progress is told by whether a join is pending.
        """
        radio_id = self._pending_radio_id
        caps = capabilities_for_radio_id(radio_id)
        if user_input is None:
            if self._join_pending is not None and self._discovery is not None:
                name = _display_name(self._discovery, radio_id)
            else:
                name = self._title or self._address or ""
            return self.async_show_form(
                step_id="settings",
                data_schema=vol.Schema(
                    _light_settings_fields(
                        caps,
                        readback=caps.readback_default,
                        poll_cct=caps.poll_cct_default,
                        poll_brightness=caps.poll_brightness_default,
                        poll_interval=DEFAULT_POLL_INTERVAL,
                    )
                ),
                description_placeholders={"name": name, "note": _model_note(caps)},
            )
        settings = _light_settings_from_input(user_input)
        if self._join_pending is not None:
            return self._complete_join(radio_id, settings)
        assert self._state is not None
        return self._finish_entry(self._state, radio_id, settings)

    def _finish_entry(
        self, state: MeshState, radio_id: str | None, settings: dict[str, Any]
    ) -> ConfigFlowResult:
        """Create the config entry from a validated mesh state and chosen model."""
        assert self._address is not None
        # Name from the model the user just picked, not the label fixed at
        # discovery: a Godox light advertises no useful name and often no model
        # id during provisioning, so without this the entry (and its primary
        # light) would keep the bare MAC even after a model was chosen.
        title = (
            _display_name(self._discovery, radio_id)
            if self._discovery is not None
            else (self._title or self._address)
        )
        return self.async_create_entry(
            title=title,
            data={
                CONF_ADDRESS: self._address,
                CONF_MESH: mesh_state_to_dict(
                    MeshState(
                        network_key=state.network_key,
                        app_key=state.app_key,
                        device_key=state.device_key,
                        device_address=state.device_address or self._address,
                        provisioner_address=state.provisioner_address,
                        node_address=state.node_address,
                        sequence_number=max(
                            state.sequence_number, PROVISIONED_SEQUENCE_FLOOR
                        ),
                        iv_index=state.iv_index,
                    )
                ),
            },
            options={
                CONF_NODES: [
                    {
                        CONF_NODE_ADDRESS: state.node_address,
                        CONF_NAME: title,
                        CONF_MODEL: None,
                        CONF_RADIO_ID: radio_id,
                        # What the light itself reported, so later nodes are
                        # allocated around its real span rather than a guess.
                        CONF_NUM_ELEMENTS: state.num_elements,
                        **settings,
                    }
                ]
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> GodoxOptionsFlow:
        """Return the options flow for managing mesh nodes."""
        return GodoxOptionsFlow()


class GodoxOptionsFlow(OptionsFlow):
    """Add and remove lights on an already-configured mesh network."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._flash_node: int | None = None
        #: Carried between the provision step and the model step that follows it.
        self._pending_provision: dict[str, Any] | None = None
        #: The node whose model the change-model step is editing.
        self._model_node_address: int | None = None
        #: The model picked on a model step, carried to the node-settings step.
        self._pending_radio_id: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the node management menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "provision_node",
                "change_model",
                "remove_node",
            ],
        )

    async def async_step_provision_node(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Provision a factory-reset light onto this mesh network.

        Mirrors the main setup flow: pick a device, then confirm its model on
        the next step so the name and colour-temperature range come out right,
        rather than asking for a name up front and dropping the model.
        """
        errors: dict[str, str] = {}
        nodes = list(self.config_entry.options.get(CONF_NODES, []))
        taken = _occupied_addresses(nodes)

        # The entry's own primary light is excluded: it is already a node here,
        # and offering it for provisioning is nonsense. Additional provisioned
        # nodes store only a unicast int, not a MAC, so they can't be excluded
        # by address -- but a provisioned light advertises the proxy service,
        # not the provisioning service filtered on below, so it won't appear.
        already_here = self.config_entry.data.get(CONF_ADDRESS)
        candidates = {
            service_info.address: service_info
            for service_info in async_discovered_service_info(
                self.hass, connectable=True
            )
            if MESH_PROVISIONING_SERVICE_UUID in service_info.service_uuids
            and service_info.address != already_here
        }
        if not candidates:
            return self.async_abort(reason="no_unprovisioned_devices")

        if user_input is not None:
            node_address = _next_free_node_address(taken)
            link = self.config_entry.runtime_data.link
            service_info = candidates[user_input[CONF_ADDRESS]]
            override = user_input.get(CONF_NAME)
            log_name = override or _display_name(service_info)
            try:
                device_key, num_elements = await link.async_provision_node(
                    user_input[CONF_ADDRESS], node_address, log_name
                )
            except Exception as err:  # noqa: BLE001 - surfaced to the user
                _LOGGER.warning(
                    "provisioning %s onto the existing network failed: %s",
                    user_input[CONF_ADDRESS],
                    err,
                )
                errors["base"] = "provision_failed"
            else:
                self._pending_provision = {
                    "node_address": node_address,
                    "device_key": device_key,
                    "num_elements": num_elements,
                    "service_info": service_info,
                    "name_override": override,
                }
                return await self.async_step_provision_model()

        return self.async_show_form(
            step_id="provision_node",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            info.address: f"{_model_name(info)} ({info.address})"
                            for info in candidates.values()
                        }
                    ),
                    vol.Optional(CONF_NAME): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "node_address": f"0x{_next_free_node_address(taken):04X}"
            },
        )

    async def async_step_provision_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the model of a light just provisioned onto the network."""
        pending = self._pending_provision
        assert pending is not None
        detected = _detected_radio_id(pending["service_info"])
        if user_input is None:
            return self.async_show_form(
                step_id="provision_model",
                data_schema=vol.Schema(_model_form_fields(detected)),
                description_placeholders={"detected": _model_detection_note(detected)},
            )
        # Fall back to the detected model when the user leaves it as suggested,
        # so accepting the auto-detected light does not depend on the frontend
        # echoing the suggested value back.
        self._pending_radio_id = user_input.get(CONF_RADIO_ID) or detected
        return await self.async_step_node_settings()

    def _complete_provision(
        self, radio_id: str | None, settings: dict[str, Any]
    ) -> ConfigFlowResult:
        """Add the just-provisioned node to this entry with its settings."""
        pending = self._pending_provision
        assert pending is not None
        service_info = pending["service_info"]
        name = pending["name_override"] or _display_name(service_info, radio_id)
        nodes = list(self.config_entry.options.get(CONF_NODES, []))
        nodes.append(
            {
                CONF_NODE_ADDRESS: pending["node_address"],
                CONF_NAME: name,
                CONF_MODEL: None,
                CONF_RADIO_ID: radio_id,
                CONF_MAC: service_info.address,
                CONF_DEVICE_KEY: pending["device_key"],
                CONF_NUM_ELEMENTS: pending["num_elements"],
                **settings,
            }
        )
        return self.async_create_entry(data={CONF_NODES: nodes})

    async def async_step_node_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set readback/polling for a node, defaulted from the picked model.

        Shared by provisioning a new node and by re-configuring an existing one;
        which is in progress is told by whether a provision is pending. ``use_xy``
        is offered only when re-configuring an xy-capable model -- provisioning
        does not know the model well enough yet to place a colour-meter control.
        """
        radio_id = self._pending_radio_id
        caps = capabilities_for_radio_id(radio_id)
        provisioning = self._pending_provision is not None
        if user_input is None:
            if provisioning:
                pending = self._pending_provision
                assert pending is not None
                name = pending["name_override"] or _display_name(
                    pending["service_info"], radio_id
                )
                readback = caps.readback_default
                poll_cct = caps.poll_cct_default
                poll_brightness = caps.poll_brightness_default
                poll_interval = DEFAULT_POLL_INTERVAL
                use_xy: bool | None = None
            else:
                node = self._model_node()
                current = next(
                    (
                        n
                        for n in self.config_entry.runtime_data.nodes
                        if n.address == self._model_node_address
                    ),
                    None,
                )
                name = node[CONF_NAME]
                readback = current.readback if current else caps.readback_default
                poll_cct = current.poll_cct if current else caps.poll_cct_default
                poll_brightness = (
                    current.poll_brightness
                    if current
                    else caps.poll_brightness_default
                )
                poll_interval = (
                    current.poll_interval if current else DEFAULT_POLL_INTERVAL
                )
                use_xy = (
                    (current.use_xy if current else False)
                    if caps.supports_xy
                    else None
                )
            return self.async_show_form(
                step_id="node_settings",
                data_schema=vol.Schema(
                    _light_settings_fields(
                        caps,
                        readback=readback,
                        poll_cct=poll_cct,
                        poll_brightness=poll_brightness,
                        poll_interval=poll_interval,
                        use_xy=use_xy,
                    )
                ),
                description_placeholders={"name": name, "note": _model_note(caps)},
            )
        settings = _light_settings_from_input(user_input)
        if provisioning:
            return self._complete_provision(radio_id, settings)
        return self._complete_set_model(radio_id, settings)

    def _model_node(self) -> dict[str, Any]:
        """The node dict the change-model flow is editing."""
        return next(
            n
            for n in self.config_entry.options.get(CONF_NODES, [])
            if n[CONF_NODE_ADDRESS] == self._model_node_address
        )

    async def async_step_change_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Correct which model a light is, without removing and re-adding it."""
        nodes = list(self.config_entry.options.get(CONF_NODES, []))
        if not nodes:
            return self.async_abort(reason="no_nodes")
        if len(nodes) == 1:
            self._model_node_address = nodes[0][CONF_NODE_ADDRESS]
            return await self.async_step_set_model()
        if user_input is not None:
            self._model_node_address = int(user_input[CONF_NODES])
            return await self.async_step_set_model()
        return self.async_show_form(
            step_id="change_model",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NODES): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            mode=selector.SelectSelectorMode.LIST,
                            options=[
                                selector.SelectOptionDict(
                                    value=str(node[CONF_NODE_ADDRESS]),
                                    label=(
                                        f"{node[CONF_NAME]} "
                                        f"(0x{node[CONF_NODE_ADDRESS]:04X})"
                                    ),
                                )
                                for node in nodes
                            ],
                        )
                    )
                }
            ),
        )

    async def async_step_set_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a node's model, then its readback/polling; re-resolves on reload."""
        node = self._model_node()
        if user_input is None:
            return self.async_show_form(
                step_id="set_model",
                data_schema=vol.Schema(_model_form_fields(node.get(CONF_RADIO_ID))),
                description_placeholders={"name": node[CONF_NAME], "detected": ""},
            )
        # No fall-back to a detected model here: this is a manual correction, and
        # leaving it blank is a deliberate "treat as a standard light".
        self._pending_radio_id = user_input.get(CONF_RADIO_ID)
        return await self.async_step_node_settings()

    def _complete_set_model(
        self, radio_id: str | None, settings: dict[str, Any]
    ) -> ConfigFlowResult:
        """Write the picked model and settings onto the node being configured."""
        nodes = list(self.config_entry.options.get(CONF_NODES, []))
        node = next(
            n for n in nodes if n[CONF_NODE_ADDRESS] == self._model_node_address
        )
        node[CONF_RADIO_ID] = radio_id
        node.update(settings)
        return self.async_create_entry(data={CONF_NODES: nodes})

    async def async_step_remove_node(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a light from this mesh network."""
        nodes = list(self.config_entry.options.get(CONF_NODES, []))

        errors: dict[str, str] = {}
        if user_input is not None:
            removed = {int(value) for value in user_input[CONF_NODES]}
            remaining = [
                node for node in nodes if node[CONF_NODE_ADDRESS] not in removed
            ]
            if not remaining:
                # An entry with no lights is a mesh connection to nothing.
                # Deleting the integration is the way to remove the last one.
                errors[CONF_NODES] = "cannot_remove_all"
            else:
                return self.async_create_entry(data={CONF_NODES: remaining})

        if not nodes:
            return self.async_abort(reason="no_nodes")

        return self.async_show_form(
            step_id="remove_node",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NODES): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                            options=[
                                selector.SelectOptionDict(
                                    value=str(node[CONF_NODE_ADDRESS]),
                                    label=(
                                        f"{node[CONF_NAME]} "
                                        f"(0x{node[CONF_NODE_ADDRESS]:04X})"
                                    ),
                                )
                                for node in nodes
                            ],
                        )
                    )
                }
            ),
        )


def _occupied_addresses(nodes: list[dict[str, Any]]) -> set[int]:
    """Every unicast address the configured nodes occupy, elements included.

    A node holds one address per element, and only its *primary* address is
    stored as the node address -- so a set of node addresses alone understates
    what is taken, and a new light can land on an existing light's element.
    Each node's element count comes from its own Provisioning Capabilities PDU
    where it is known, falling back to ``ELEMENTS_PER_NODE``.
    """
    taken: set[int] = set()
    for node in nodes:
        primary = node[CONF_NODE_ADDRESS]
        count = node.get(CONF_NUM_ELEMENTS) or ELEMENTS_PER_NODE
        taken.update(range(primary, primary + max(1, count)))
    return taken


def _next_free_node_address(
    taken: set[int], num_elements: int = ELEMENTS_PER_NODE
) -> int:
    """Return the next free primary unicast address for a new node.

    A light occupies one address per element, so the allocator must skip past
    all of them: the provisioner holds 0x0001, a two-element light 0x0002 and
    0x0003, the next light 0x0004, and so on.

    *num_elements* is how many the **new** node will need. Its real count is
    only known once provisioning reaches the Capabilities PDU, which is after
    the address has to be chosen, so this is the conservative default; the
    actual count is recorded afterwards so later allocations are exact.
    """
    span = max(1, num_elements)
    candidate = DEFAULT_NODE_ADDRESS
    while any(candidate + offset in taken for offset in range(span)):
        candidate += span
    return candidate
