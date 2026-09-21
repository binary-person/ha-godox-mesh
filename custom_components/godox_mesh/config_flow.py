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
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .ble import HomeAssistantBleakClient
from .capabilities import (
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
    CONF_NODES,
    CONF_POLL_CCT,
    CONF_RADIO_ID,
    CONF_READBACK,
    CONF_USE_XY,
    DEFAULT_NODE_ADDRESS,
    DEFAULT_PROVISIONER_ADDRESS,
    DOMAIN,
    ELEMENTS_PER_NODE,
    INTEGRATION_TITLE,
    GODOX_DEVICE_NAME,
    GODOX_NAME_HINTS,
    MESH_PROVISIONING_SERVICE_UUID,
    PATCHED_FIRMWARE_VERSION,
)
from .mesh_state_input import (
    InvalidMeshState,
    mesh_state_to_dict,
    parse_mesh_state,
    parse_node_address,
)

_LOGGER = logging.getLogger(__name__)

# Leave room above anything the Godox app may have already burned into the
# node's replay protection list. Sequence numbers are 24-bit and cheap; a light
# that ignores commands because of a stale counter is the single most confusing
# failure this integration has.
PROVISIONED_SEQUENCE_FLOOR = 300_000


def _model_name(service_info: BluetoothServiceInfoBleak) -> str:
    """The product name for a discovered device, or the best available label.

    Every Godox mesh light advertises the same device name, ``GD_LED``, so it
    identifies nothing. The model id is in the advertisement, so prefer the
    product name and keep the advertised name only as a fallback.
    """
    radio_id = radio_id_from_manufacturer_data(service_info.manufacturer_data)
    if radio_id:
        name = capabilities_for_radio_id(radio_id).name
        if name:
            return name
    return service_info.name or service_info.address


def _display_name(service_info: BluetoothServiceInfoBleak) -> str:
    """A label that stays unique when two lights are the same model.

    Two SL200III Bis would otherwise produce two identical discovery cards, so
    the last four hex digits of the address are appended -- enough to tell them
    apart, and short enough to read. Omitted when the label already *is* the
    address, which would just repeat it.
    """
    name = _model_name(service_info)
    if name == service_info.address:
        return name
    suffix = service_info.address.replace(":", "").replace("-", "")[-4:].upper()
    return f"{name} ({suffix})" if suffix else name


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

    # -- discovery ---------------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device discovered over Bluetooth."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        # Driving the light needs a GATT write, so an advertisement heard only
        # by a listen-only controller is no use.
        if not discovery_info.connectable:
            return self.async_abort(reason="not_connectable")

        self._discovery = discovery_info
        self._address = discovery_info.address
        self._title = _display_name(discovery_info)
        self.context["title_placeholders"] = {"name": self._title or INTEGRATION_TITLE}
        return await self.async_step_setup_method()

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

        current = self._async_current_ids(include_ignore=False)
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
        """Ask whether the light is already provisioned or factory reset."""
        return self.async_show_menu(
            step_id="setup_method",
            menu_options=["mesh_state", "provision"],
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

    # -- entry creation ----------------------------------------------------

    def _create_entry(self, state: MeshState) -> ConfigFlowResult:
        """Stash the mesh state, then ask which model this is."""
        self._state = state
        return self._show_model_form()

    def _detected_radio_id(self) -> str | None:
        """The model id this light advertises, when it is one we know.

        Godox puts the model id in its manufacturer data, so in the common case
        the user does not have to identify their light at all -- and this works
        for any of the 190 mesh models, not a list someone maintains by hand.
        An unknown or absent id just leaves the dropdown empty.
        """
        if self._discovery is None:
            return None
        radio_id = radio_id_from_manufacturer_data(self._discovery.manufacturer_data)
        return radio_id if radio_id in known_models() else None

    def _show_model_form(
        self, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        detected = self._detected_radio_id()
        # `default=` does NOT pre-fill a rendered form -- it only supplies a
        # value at validation time when the key is absent. Pre-populating the
        # field the user sees requires `suggested_value`. Getting this wrong
        # made detection look broken: it worked, and its answer was dropped
        # before the form was drawn.
        field = (
            vol.Optional(CONF_RADIO_ID, description={"suggested_value": detected})
            if detected
            else vol.Optional(CONF_RADIO_ID)
        )
        # Say so when the model came from the advertisement, rather than
        # presenting a pre-filled dropdown as though the user chose it.
        caps = capabilities_for_radio_id(detected) if detected else None
        note = (
            f"This light identifies itself as a **{caps.name}**, which is "
            "already selected below. Change it only if that is wrong.\n\n"
            if caps and caps.name
            else ""
        )
        return self.async_show_form(
            step_id="model",
            data_schema=vol.Schema({field: _model_selector()}),
            errors=errors or {},
            description_placeholders={"name": self._title or "", "detected": note},
        )

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Record the chosen model, then create the entry with the right controls."""
        if user_input is None:
            return self._show_model_form()
        assert self._state is not None
        return self._finish_entry(self._state, user_input.get(CONF_RADIO_ID))

    def _finish_entry(
        self, state: MeshState, radio_id: str | None
    ) -> ConfigFlowResult:
        """Create the config entry from a validated mesh state and chosen model."""
        assert self._address is not None
        title = self._title or self._address
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

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the node management menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "provision_node",
                "add_node",
                "remove_node",
                "settings",
            ],
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Toggle entry-wide settings, including polling for live state.

        Polling works on stock firmware — brightness is genuinely reported — so
        this is not gated on the firmware patch. The light is still asked for
        its version, only to tell the user which of the two levels they get.
        """
        # Only offered when a node on this entry can actually do xy; asking
        # about a mode the hardware lacks is worse than not asking.
        xy_capable = any(
            node.capabilities.supports_xy
            for node in self.config_entry.runtime_data.nodes
        )
        if user_input is not None:
            options = {
                **self.config_entry.options,
                CONF_READBACK: user_input[CONF_READBACK],
                CONF_POLL_CCT: user_input[CONF_POLL_CCT],
            }
            if xy_capable:
                options[CONF_USE_XY] = user_input[CONF_USE_XY]
            return self.async_create_entry(data=options)
        _patched, detail = await self._async_detect_patch()
        placeholders = {"firmware": detail}

        schema: dict[Any, Any] = {
            vol.Required(
                CONF_READBACK,
                default=self.config_entry.options.get(CONF_READBACK, False),
            ): bool,
            vol.Required(
                CONF_POLL_CCT,
                default=self.config_entry.options.get(CONF_POLL_CCT, True),
            ): bool,
        }
        if xy_capable:
            schema[
                vol.Required(
                    CONF_USE_XY,
                    default=self.config_entry.options.get(CONF_USE_XY, False),
                )
            ] = bool

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(schema),
            description_placeholders=placeholders,
        )

    async def _async_detect_patch(self) -> tuple[bool | None, str]:
        """Ask the first node its firmware version to detect the readback patch.

        Returns
        -------
        tuple[bool | None, str]
            ``(True, ...)`` if patched, ``(False, ...)`` if clearly stock,
            ``(None, ...)`` if the light could not be reached — in which case the
            user is trusted rather than blocked.
        """
        link = self.config_entry.runtime_data.link
        nodes = self.config_entry.options.get(CONF_NODES) or []
        if not nodes:
            return None, "no nodes to query"
        node_address = nodes[0][CONF_NODE_ADDRESS]
        try:
            version = await link.async_request_version(node_address)
        except Exception as err:  # noqa: BLE001 - unreachable light is not fatal
            _LOGGER.debug("could not read firmware version: %s", err)
            return None, "could not be reached"
        if version == PATCHED_FIRMWARE_VERSION:
            return True, "patched firmware detected"
        return False, f"stock firmware (reports version {version})"

    # -- flashing the readback firmware patch ------------------------------



    async def async_step_provision_node(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Provision a factory-reset light onto this mesh network."""
        errors: dict[str, str] = {}
        nodes = list(self.config_entry.options.get(CONF_NODES, []))
        taken = _occupied_addresses(nodes)

        candidates = {
            service_info.address: service_info
            for service_info in async_discovered_service_info(
                self.hass, connectable=True
            )
            if MESH_PROVISIONING_SERVICE_UUID in service_info.service_uuids
        }
        if not candidates:
            return self.async_abort(reason="no_unprovisioned_devices")

        if user_input is not None:
            node_address = _next_free_node_address(taken)
            link = self.config_entry.runtime_data.link
            try:
                device_key, num_elements = await link.async_provision_node(
                    user_input[CONF_ADDRESS], node_address, user_input[CONF_NAME]
                )
            except Exception as err:  # noqa: BLE001 - surfaced to the user
                _LOGGER.warning(
                    "provisioning %s onto the existing network failed: %s",
                    user_input[CONF_ADDRESS],
                    err,
                )
                errors["base"] = "provision_failed"
            else:
                nodes.append(
                    {
                        CONF_NODE_ADDRESS: node_address,
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_MODEL: None,
                        CONF_DEVICE_KEY: device_key,
                        CONF_NUM_ELEMENTS: num_elements,
                    }
                )
                return self.async_create_entry(data={CONF_NODES: nodes})

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
                    vol.Required(CONF_NAME): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "node_address": f"0x{_next_free_node_address(taken):04X}"
            },
        )

    async def async_step_add_node(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add another light that shares this mesh network."""
        errors: dict[str, str] = {}
        nodes = list(self.config_entry.options.get(CONF_NODES, []))

        if user_input is not None:
            try:
                address = parse_node_address(user_input[CONF_NODE_ADDRESS])
            except InvalidMeshState as err:
                _LOGGER.debug("rejected node address: %s", err)
                errors[CONF_NODE_ADDRESS] = "invalid_node_address"
            else:
                if any(node[CONF_NODE_ADDRESS] == address for node in nodes):
                    errors[CONF_NODE_ADDRESS] = "node_exists"
                else:
                    nodes.append(
                        {
                            CONF_NODE_ADDRESS: address,
                            CONF_NAME: user_input[CONF_NAME],
                            CONF_MODEL: user_input.get(CONF_MODEL) or None,
                            CONF_RADIO_ID: user_input.get(CONF_RADIO_ID) or None,
                        }
                    )
                    return self.async_create_entry(data={CONF_NODES: nodes})

        return self.async_show_form(
            step_id="add_node",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_NODE_ADDRESS): str,
                    vol.Optional(CONF_RADIO_ID): _model_selector(),
                }
            ),
            errors=errors,
        )

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
