"""Config flow for Marstek integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from . import MarstekConfigEntry
from .const import DEFAULT_UDP_PORT, DOMAIN
from .device_info import format_device_name
from .discovery import discover_devices, get_device_info
from .firmware_profile import is_unsupported_venus_e2
from .helpers.broadcast import async_broadcast_addresses
from .helpers.flow_helpers import (
    async_apply_entry_update,
    build_entry_data,
    collect_configured_macs,
    format_already_configured_text,
    formatted_mac_or_none,
    get_unique_id_from_device_info,
    identities_overlap,
    identity_macs_from_entry,
    identity_macs_from_mapping,
    metadata_from_device_info,
    split_devices_by_configured,
)
from .helpers.flow_schemas import (
    build_host_port_schema,
    build_manual_entry_schema,
)
from .helpers.ports import discovery_scan_ports
from .helpers.udp_clients import (
    async_paused_udp_receivers,
    bind_port_for_host,
    discovery_lock,
    get_udp_client,
    transfer_reset_prone_mark_for_entry,
)
from .options_flow import MarstekOptionsFlow

_LOGGER = logging.getLogger(__name__)

_MANUAL_DEVICE_OPTION = "__manual__"


class MarstekConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Marstek."""

    VERSION = 1
    discovered_devices: list[dict[str, Any]]
    _discovered_ip: str | None = None
    _discovered_port: int | None = None
    _discovered_metadata: dict[str, Any] | None = None
    _discovered_identity_macs: set[str] | None = None

    @staticmethod
    def async_get_options_flow(
        config_entry: MarstekConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow.

        The annotation is the integration's own typed entry alias on purpose:
        Home Assistant's runtime-data rule checks this signature once
        strict-typing is claimed.
        https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/runtime-data
        """
        return MarstekOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step - broadcast device discovery."""
        if user_input is not None and "device" in user_input:
            return await self._async_create_entry_from_selection(str(user_input["device"]))

        _LOGGER.debug("Starting device discovery")
        try:
            # Execute broadcast discovery with retry mechanism
            # Uses local discovery module (workaround for pymarstek echo issues)
            discovered = await self._discover_devices_with_retry()
        except ConnectionError as err:
            _LOGGER.error("Cannot connect for device discovery: %s", err)
            return await self.async_step_manual(errors={"base": "cannot_connect"})
        except (OSError, TimeoutError, ValueError) as err:
            _LOGGER.error("Device discovery failed: %s", err)
            return await self.async_step_manual(errors={"base": "discovery_failed"})

        devices = [
            device
            for device in discovered
            if not is_unsupported_venus_e2(device.get("device_type"))
        ]
        if not devices:
            # No devices found, offer manual entry
            return await self.async_step_manual()

        # Store discovered devices for selection
        self.discovered_devices = devices
        _LOGGER.debug("Discovered %d devices", len(devices))

        # Build device options, separating new and already-configured devices
        configured_macs = collect_configured_macs(self._async_current_entries(include_ignore=False))
        device_options, already_configured_names = split_devices_by_configured(
            devices, configured_macs
        )

        # If all discovered devices are already configured, show manual entry
        if not device_options:
            _LOGGER.debug("All discovered devices are already configured")
            return await self.async_step_manual(errors={"base": "all_devices_configured"})

        device_options[_MANUAL_DEVICE_OPTION] = "Enter IP/port manually"

        # Build description showing already configured devices only
        # Note: The "Already configured devices:" header is embedded in the placeholder
        # value since HA config flows don't support dynamic translation lookups.
        # This is a common pattern in HA integrations for this type of dynamic content.
        already_configured_text = format_already_configured_text(already_configured_names)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("device"): vol.In(device_options)}),
            description_placeholders={"already_configured": already_configured_text},
        )

    async def async_step_manual(
        self,
        user_input: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle manual IP entry when discovery fails or the user prefers it."""
        errors = dict(errors or {})
        form_port = DEFAULT_UDP_PORT

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = int(user_input.get(CONF_PORT, DEFAULT_UDP_PORT))
            form_port = port
            device_info: dict[str, Any] | None = None

            try:
                # Validate connection by attempting to get device info
                device_info = await self._async_get_device_info(host, port)
            except (ConnectionError, OSError, TimeoutError) as err:
                _LOGGER.error("Cannot connect to device at %s:%s: %s", host, port, err)
                errors["base"] = "cannot_connect"
            except ValueError as err:
                _LOGGER.error("Invalid response from device at %s:%s: %s", host, port, err)
                errors["base"] = "invalid_discovery_info"

            if not errors:
                if not device_info:
                    errors["base"] = "cannot_connect"
                elif not (formatted_unique_id := get_unique_id_from_device_info(device_info)):
                    errors["base"] = "invalid_discovery_info"
                elif is_unsupported_venus_e2(device_info.get("device_type")):
                    errors["base"] = "unsupported_device"
                else:
                    self._discovered_identity_macs = identity_macs_from_mapping(device_info)
                    await self.async_set_unique_id(formatted_unique_id)
                    self._abort_if_identity_configured()

                    return self.async_create_entry(
                        title=format_device_name(device_info),
                        data=build_entry_data(host, port, device_info),
                    )

        return self.async_show_form(
            step_id="manual",
            data_schema=build_manual_entry_schema(form_port),
            errors=errors,
        )

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> config_entries.ConfigFlowResult:
        """Handle DHCP discovery to update IP address when it changes (mik-laj feedback)."""
        if not discovery_info.macaddress or not discovery_info.ip:
            return self.async_abort(reason="invalid_discovery_info")

        mac = format_mac(discovery_info.macaddress)
        _LOGGER.debug(
            "DHCP discovery triggered: MAC=%s, IP=%s, Hostname=%s",
            mac,
            discovery_info.ip,
            discovery_info.hostname,
        )

        await self.async_set_unique_id(mac)
        self._discovered_ip = discovery_info.ip
        self._discovered_port = None
        self._discovered_metadata = {}
        self._discovered_identity_macs = {mac}

        # Use shared discovery handler to update existing entries or confirm new ones
        return await self._async_handle_discovery_with_unique_id()

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle discovery from Scanner (integration discovery)."""
        discovered_ip = discovery_info.get("ip")
        # One identity check, not two: get_unique_id_from_device_info returns
        # the first MAC identity_macs_from_mapping would collect, so a None
        # here is exactly an empty identity set.
        preferred_unique_id = get_unique_id_from_device_info(discovery_info)

        if preferred_unique_id is None or not discovered_ip:
            return self.async_abort(reason="invalid_discovery_info")

        if is_unsupported_venus_e2(discovery_info.get("device_type")):
            return self.async_abort(reason="unsupported_device")

        await self.async_set_unique_id(preferred_unique_id)
        self._discovered_ip = discovered_ip
        self._discovered_metadata = metadata_from_device_info(discovery_info)
        self._discovered_identity_macs = identity_macs_from_mapping(discovery_info)
        discovered_port = discovery_info.get("port")
        try:
            self._discovered_port = int(discovered_port) if discovered_port is not None else None
        except (TypeError, ValueError):
            self._discovered_port = None

        # Handle discovery with unique_id (updates existing entries or creates new)
        return await self._async_handle_discovery_with_unique_id()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm discovery and create entry."""
        errors: dict[str, str] = {}

        if self._discovered_ip is None:
            return await self.async_step_manual(errors={"base": "invalid_discovery_info"})

        discovered_port = self._discovered_port or DEFAULT_UDP_PORT
        form_host = self._discovered_ip
        form_port = discovered_port

        if user_input is not None:
            host = str(user_input.get(CONF_HOST, ""))
            port = int(user_input.get(CONF_PORT, DEFAULT_UDP_PORT))
            form_host = host
            form_port = port

            try:
                device_info = await self._async_get_device_info(host, port)

                if not device_info:
                    errors["base"] = "cannot_connect"
                else:
                    formatted_unique_id = get_unique_id_from_device_info(device_info)
                    if not formatted_unique_id:
                        errors["base"] = "invalid_discovery_info"
                    elif is_unsupported_venus_e2(device_info.get("device_type")):
                        errors["base"] = "unsupported_device"
                    else:
                        device_macs = identity_macs_from_mapping(device_info)
                        flow_mac = formatted_mac_or_none(self.unique_id)
                        if (
                            self.unique_id
                            and formatted_unique_id != self.unique_id
                            and (flow_mac is None or flow_mac not in device_macs)
                        ):
                            errors["base"] = "unique_id_mismatch"
                        else:
                            self._discovered_identity_macs = device_macs
                            await self.async_set_unique_id(formatted_unique_id)
                            self._abort_if_identity_configured()

                            return self.async_create_entry(
                                title=f"Marstek {device_info.get('device_type', 'Device')}",
                                data=build_entry_data(host, port, device_info),
                            )

            except (ConnectionError, OSError, TimeoutError):
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "invalid_discovery_info"

        return self.async_show_form(
            step_id="confirm",
            data_schema=build_host_port_schema(default_host=form_host, default_port=form_port),
            errors=errors,
            description_placeholders={"host": self._discovered_ip},
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle reauth when device becomes unreachable."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm reauth dialog."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            host = str(user_input.get(CONF_HOST, ""))
            port = int(reauth_entry.data.get(CONF_PORT, DEFAULT_UDP_PORT))

            result, error = await self._async_handle_host_update(
                reauth_entry,
                host,
                port,
                update_port=False,
                reason="reauth_successful",
            )
            if result is not None:
                return result
            if error:
                errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOST, default=str(reauth_entry.data.get(CONF_HOST, ""))
                    ): cv.string
                }
            ),
            errors=errors,
            description_placeholders={"host": str(reauth_entry.data.get(CONF_HOST, ""))},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguration of an existing entry."""
        return await self.async_step_reconfigure_confirm(user_input)

    async def async_step_reconfigure_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm reconfiguration dialog."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()
        form_host = str(reconfigure_entry.data.get(CONF_HOST, ""))
        form_port = int(reconfigure_entry.data.get(CONF_PORT, DEFAULT_UDP_PORT))

        if user_input is not None:
            host = str(user_input.get(CONF_HOST, ""))
            port = int(user_input.get(CONF_PORT, DEFAULT_UDP_PORT))
            form_host = host
            form_port = port

            result, error = await self._async_handle_host_update(
                reconfigure_entry,
                host,
                port,
                update_port=True,
                reason="reconfigure_successful",
            )
            if result is not None:
                return result
            if error:
                errors["base"] = error

        return self.async_show_form(
            step_id="reconfigure_confirm",
            data_schema=build_host_port_schema(default_host=form_host, default_port=form_port),
            errors=errors,
            description_placeholders={"host": str(reconfigure_entry.data.get(CONF_HOST, ""))},
        )

    async def _async_create_entry_from_selection(
        self, selected_device: str
    ) -> config_entries.ConfigFlowResult:
        """Create an entry for a device picked from the discovery list."""
        if selected_device == _MANUAL_DEVICE_OPTION:
            return await self.async_step_manual()

        device = self.discovered_devices[int(selected_device)]

        # Use BLE-MAC as unique_id for stability (beardhatcode & mik-laj feedback)
        # BLE-MAC is more stable than WiFi MAC and ensures device history continuity
        formatted_unique_id = get_unique_id_from_device_info(device)
        if not formatted_unique_id:
            return await self.async_step_manual(errors={"base": "invalid_discovery_info"})

        # No Venus E2 check here: the discovery sweep in async_step_user already
        # drops those before they reach self.discovered_devices.
        self._discovered_identity_macs = identity_macs_from_mapping(device)
        await self.async_set_unique_id(formatted_unique_id)
        self._abort_if_identity_configured()

        return self.async_create_entry(
            title=format_device_name(device),
            data=build_entry_data(
                device["ip"],
                int(device.get("port", DEFAULT_UDP_PORT)),
                device,
            ),
        )

    async def _discover_devices_with_retry(
        self, max_retries: int = 2, retry_delay: float = 3.0
    ) -> list[dict[str, Any]]:
        """Device discovery retry mechanism using local discovery module."""
        scan_ports = self._build_discovery_ports()

        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1:
                    _LOGGER.debug("Device discovery, attempt %d", attempt)
                    await asyncio.sleep(retry_delay)

                devices = await self._async_discover_devices(scan_ports)

                if devices:
                    if attempt > 1:
                        _LOGGER.debug("Device discovery retry successful")
                    return devices
                _LOGGER.warning("Attempt %d found no devices", attempt)

            except (OSError, TimeoutError, ValueError) as error:
                _LOGGER.error("Device discovery failed, attempt %d: %s", attempt, error)

                if attempt == max_retries:
                    _LOGGER.error(
                        "Device discovery failed after %d retries: %s",
                        max_retries,
                        error,
                    )
                    raise

        return []

    def _build_discovery_ports(self) -> list[int]:
        """Build UDP ports to probe during initial config flow discovery."""
        return discovery_scan_ports(self._async_current_entries(include_ignore=False))

    async def _async_get_device_info(self, host: str, port: int) -> dict[str, Any] | None:
        """Unicast GetDevice on the pooled client when one already owns this port.

        Firmware replies to the listen port. A second ``SO_REUSEPORT`` bind
        never sees that reply — Linux hashes it onto the coordinator socket
        even if that listener is paused. Pause only for broadcast discovery,
        which must bind its own sockets. Hold the discovery lock so a
        temporary unpooled socket cannot race a broadcast bind.
        """
        async with discovery_lock(self.hass):
            udp_client = get_udp_client(self.hass, bind_port_for_host(host, port))
            return await get_device_info(host=host, port=port, udp_client=udp_client)

    async def _async_discover_devices(self, scan_ports: list[int]) -> list[dict[str, Any]]:
        """Broadcast discovery while pooled listeners are paused."""
        broadcast_addresses = await async_broadcast_addresses(self.hass)
        async with async_paused_udp_receivers(self.hass):
            return await discover_devices(ports=scan_ports, broadcast_addresses=broadcast_addresses)

    async def _async_handle_discovery_with_unique_id(
        self,
    ) -> config_entries.ConfigFlowResult:
        """Handle any discovery with a unique id (similar to Yeelight pattern)."""
        if not self.unique_id or not self._discovered_ip:
            return self.async_abort(reason="invalid_discovery_info")

        for entry in self._async_current_entries(include_ignore=False):
            # Check if unique_id matches
            if not self._entry_matches_flow_identity(entry):
                continue

            discovered_port = self._discovered_port
            current_port = int(entry.data.get(CONF_PORT, DEFAULT_UDP_PORT))
            updates: dict[str, Any] = {}

            if entry.data.get(CONF_HOST) != self._discovered_ip:
                _LOGGER.debug(
                    "Discovery: Device %s IP changed from %s to %s, updating config entry",
                    entry.unique_id,
                    entry.data.get(CONF_HOST),
                    self._discovered_ip,
                )
                updates[CONF_HOST] = self._discovered_ip

            if discovered_port is not None and current_port != discovered_port:
                _LOGGER.debug(
                    "Discovery: Device %s port changed from %s to %s, updating config entry",
                    entry.unique_id,
                    current_port,
                    discovered_port,
                )
                updates[CONF_PORT] = discovered_port

            for key, value in (self._discovered_metadata or {}).items():
                if entry.data.get(key) != value:
                    updates[key] = value

            if updates:
                old_host = entry.data.get(CONF_HOST)
                new_host = updates.get(CONF_HOST, old_host)
                new_port = updates.get(CONF_PORT)
                if isinstance(old_host, str) and isinstance(new_host, str):
                    transfer_reset_prone_mark_for_entry(
                        self.hass,
                        entry,
                        old_host,
                        new_host,
                        new_port=new_port if isinstance(new_port, int) else None,
                    )
                async_apply_entry_update(self.hass, entry, {**entry.data, **updates})
            elif entry.state is ConfigEntryState.SETUP_RETRY:
                # Nothing to write, but the device is answering again, so
                # stop waiting out Home Assistant's setup-retry backoff.
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_abort(reason="already_configured")

        # No existing entry found, confirm discovery
        return await self.async_step_confirm()

    async def _async_handle_host_update(
        self,
        entry: config_entries.ConfigEntry,
        host: str,
        port: int,
        *,
        update_port: bool,
        reason: str,
    ) -> tuple[config_entries.ConfigFlowResult | None, str | None]:
        """Validate host and update the entry if the device matches."""
        if not host:
            return None, "cannot_connect"

        try:
            device_info = await self._async_get_device_info(host, port)
            if not device_info:
                return None, "cannot_connect"

            formatted_unique_id = get_unique_id_from_device_info(device_info)
            if not formatted_unique_id:
                return None, "invalid_discovery_info"

            device_macs = identity_macs_from_mapping(device_info)
            if not identities_overlap(identity_macs_from_entry(entry), device_macs):
                return None, "unique_id_mismatch"

            data_updates: dict[str, Any] = {
                CONF_HOST: host,
                **metadata_from_device_info(device_info),
            }
            if update_port:
                data_updates[CONF_PORT] = port

            old_host = entry.data.get(CONF_HOST)
            if isinstance(old_host, str):
                transfer_reset_prone_mark_for_entry(
                    self.hass,
                    entry,
                    old_host,
                    host,
                    new_port=port if update_port else None,
                )

            # The entry's own update listener owns the reload when it runs;
            # async_update_reload_and_abort would schedule a second one, which
            # Home Assistant reports as deprecated and stops doing in 2026.12.
            async_apply_entry_update(self.hass, entry, {**entry.data, **data_updates})
            return self.async_abort(reason=reason), None
        except (OSError, TimeoutError, ValueError):
            return None, "cannot_connect"

    def _entry_matches_flow_identity(self, entry: config_entries.ConfigEntry) -> bool:
        """Return True if entry shares any stable MAC with this flow.

        Wider than the unique id alone: the entry's BLE, Wi-Fi and legacy MACs
        are all compared against everything this flow has learned, so the same
        hardware is recognised whichever field the firmware filled in.
        """
        entry_macs = identity_macs_from_entry(entry)
        discovered = set(self._discovered_identity_macs or ())
        unique_id_mac = formatted_mac_or_none(self.unique_id)
        if unique_id_mac is not None:
            discovered.add(unique_id_mac)
        if self._discovered_metadata:
            discovered.update(identity_macs_from_mapping(self._discovered_metadata))
        return identities_overlap(entry_macs, discovered)

    def _abort_if_identity_configured(self) -> None:
        """Abort when this hardware is already configured.

        Unique IDs stay as originally assigned. Match BLE, Wi-Fi, and stored
        MAC identities so a later discovery view cannot create a second entry.
        """
        self._abort_if_unique_id_configured()
        for entry in self._async_current_entries(include_ignore=False):
            if self._entry_matches_flow_identity(entry):
                raise AbortFlow("already_configured")
