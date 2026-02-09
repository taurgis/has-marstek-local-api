"""Config flow for Marstek integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import section
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import format_mac

try:
    from homeassistant.helpers.service_info.dhcp import (  # type: ignore[import-not-found]
        DhcpServiceInfo,
    )
except ImportError:
    # Fallback for older Home Assistant versions (pre-2025.1)
    try:
        from homeassistant.components.dhcp import DhcpServiceInfo
    except ImportError:
        # If DHCP service info is not available, create a minimal stub
        from dataclasses import dataclass

        @dataclass
        class DhcpServiceInfo:  # type: ignore[no-redef]
            """Fallback DHCP service info for older Home Assistant versions."""

            ip: str
            hostname: str
            macaddress: str

from .const import (
    CONF_ACTION_CHARGE_POWER,
    CONF_ACTION_DISCHARGE_POWER,
    CONF_FAILURE_THRESHOLD,
    CONF_POLL_INTERVAL_FAST,
    CONF_POLL_INTERVAL_MEDIUM,
    CONF_POLL_INTERVAL_SLOW,
    CONF_REQUEST_DELAY,
    CONF_REQUEST_TIMEOUT,
    CONF_SOCKET_LIMIT,
    DEFAULT_ACTION_CHARGE_POWER,
    DEFAULT_ACTION_DISCHARGE_POWER,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_POLL_INTERVAL_FAST,
    DEFAULT_POLL_INTERVAL_MEDIUM,
    DEFAULT_POLL_INTERVAL_SLOW,
    DEFAULT_REQUEST_DELAY,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_UDP_PORT,
    DOMAIN,
    device_default_socket_limit,
)
from .device_info import format_device_name
from .discovery import discover_devices, get_device_info
from .helpers.flow_helpers import (
    build_entry_data,
    collect_configured_macs,
    format_already_configured_text,
    get_unique_id_from_device_info,
    split_devices_by_configured,
)
from .helpers.flow_schemas import (
    build_manual_entry_schema,
    build_network_schema,
    build_polling_schema,
    build_power_schema,
)

_LOGGER = logging.getLogger(__name__)

class MarstekConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Marstek."""

    VERSION = 1
    discovered_devices: list[dict[str, Any]]
    _discovered_ip: str | None = None
    _discovered_port: int | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step - broadcast device discovery."""
        if user_input is not None and "device" in user_input:
            # User has selected a device from the discovered list
            device_index = int(user_input["device"])
            device = self.discovered_devices[device_index]

            # Use BLE-MAC as unique_id for stability (beardhatcode & mik-laj feedback)
            # BLE-MAC is more stable than WiFi MAC and ensures device history continuity
            formatted_unique_id = get_unique_id_from_device_info(device)
            if not formatted_unique_id:
                return await self.async_step_manual(
                    errors={"base": "invalid_discovery_info"}
                )

            await self.async_set_unique_id(formatted_unique_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=format_device_name(device),
                data=build_entry_data(device["ip"], DEFAULT_UDP_PORT, device),
            )

        # Start broadcast device discovery
        try:
            _LOGGER.info("Starting device discovery")

            # Execute broadcast discovery with retry mechanism
            # Uses local discovery module (workaround for pymarstek echo issues)
            devices = await self._discover_devices_with_retry()

            if not devices:
                # No devices found, offer manual entry
                return await self.async_step_manual()

            # Store discovered devices for selection
            self.discovered_devices = devices
            _LOGGER.info("Discovered %d devices", len(devices))

            # Get already configured device MACs for comparison
            configured_macs = collect_configured_macs(
                self._async_current_entries(include_ignore=False)
            )

            # Build device options, separating new and already-configured devices
            device_options, already_configured_names = split_devices_by_configured(
                devices, configured_macs
            )

            # If all discovered devices are already configured, show manual entry
            if not device_options:
                _LOGGER.info("All discovered devices are already configured")
                return await self.async_step_manual(
                    errors={"base": "all_devices_configured"}
                )

            # Build description showing already configured devices only
            # Note: The "Already configured devices:" header is embedded in the placeholder
            # value since HA config flows don't support dynamic translation lookups.
            # This is a common pattern in HA integrations for this type of dynamic content.
            already_configured_text = format_already_configured_text(
                already_configured_names
            )

            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {vol.Required("device"): vol.In(device_options)}
                ),
                description_placeholders={
                    "already_configured": already_configured_text
                },
            )

        except ConnectionError as err:
            _LOGGER.error("Cannot connect for device discovery: %s", err)
            # Connection failed, offer manual entry
            return await self.async_step_manual(errors={"base": "cannot_connect"})

        except (OSError, TimeoutError, ValueError) as err:
            _LOGGER.error("Device discovery failed: %s", err)
            # Discovery failed, offer manual entry
            return await self.async_step_manual(errors={"base": "discovery_failed"})

    async def async_step_manual(
        self,
        user_input: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle manual IP entry when discovery fails or user prefers manual setup."""
        if errors is None:
            errors = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_UDP_PORT)
            manual_entry_schema = build_manual_entry_schema(DEFAULT_UDP_PORT)

            try:
                # Validate connection by attempting to get device info
                device_info = await get_device_info(host=host, port=port)

                if not device_info:
                    return self.async_show_form(
                        step_id="manual",
                        data_schema=manual_entry_schema,
                        errors={"base": "cannot_connect"},
                    )

                # Check if device is already configured
                formatted_unique_id = get_unique_id_from_device_info(device_info)
                if not formatted_unique_id:
                    return self.async_show_form(
                        step_id="manual",
                        data_schema=manual_entry_schema,
                        errors={"base": "invalid_discovery_info"},
                    )

                await self.async_set_unique_id(formatted_unique_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=format_device_name(device_info),
                    data=build_entry_data(host, port, device_info),
                )

            except (ConnectionError, OSError, TimeoutError) as err:
                _LOGGER.error("Cannot connect to device at %s:%s: %s", host, port, err)
                return self.async_show_form(
                    step_id="manual",
                    data_schema=manual_entry_schema,
                    errors={"base": "cannot_connect"},
                )
            except ValueError as err:
                _LOGGER.error("Invalid response from device at %s:%s: %s", host, port, err)
                return self.async_show_form(
                    step_id="manual",
                    data_schema=manual_entry_schema,
                    errors={"base": "invalid_discovery_info"},
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=build_manual_entry_schema(DEFAULT_UDP_PORT),
            errors=errors,
        )

    async def _discover_devices_with_retry(
        self, max_retries: int = 2, retry_delay: float = 3.0
    ) -> list[dict[str, Any]]:
        """Device discovery retry mechanism using local discovery module."""
        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1:
                    _LOGGER.info("Device discovery, attempt %d", attempt)
                    await asyncio.sleep(retry_delay)

                devices = await discover_devices()

                if devices:
                    if attempt > 1:
                        _LOGGER.info("Device discovery retry successful")
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

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> config_entries.ConfigFlowResult:
        """Handle DHCP discovery to update IP address when it changes (mik-laj feedback)."""
        if not discovery_info.macaddress or not discovery_info.ip:
            return self.async_abort(reason="invalid_discovery_info")

        mac = format_mac(discovery_info.macaddress)
        _LOGGER.info(
            "DHCP discovery triggered: MAC=%s, IP=%s, Hostname=%s",
            mac,
            discovery_info.ip,
            discovery_info.hostname,
        )

        await self.async_set_unique_id(mac)
        self._discovered_ip = discovery_info.ip
        self._discovered_port = DEFAULT_UDP_PORT

        # Use shared discovery handler to update existing entries or confirm new ones
        return await self._async_handle_discovery_with_unique_id()

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle discovery from Scanner (integration discovery)."""
        discovered_ip = discovery_info.get("ip")
        discovered_ble_mac = discovery_info.get("ble_mac")

        if not discovered_ble_mac or not discovered_ip:
            return self.async_abort(reason="invalid_discovery_info")

        # Set unique_id using BLE-MAC
        await self.async_set_unique_id(format_mac(discovered_ble_mac))
        self._discovered_ip = discovered_ip
        self._discovered_port = int(discovery_info.get("port", DEFAULT_UDP_PORT))

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

        if user_input is not None:
            host = str(user_input.get(CONF_HOST, ""))
            port = int(user_input.get(CONF_PORT, DEFAULT_UDP_PORT))

            try:
                device_info = await get_device_info(host=host, port=port)

                if not device_info:
                    errors["base"] = "cannot_connect"
                else:
                    formatted_unique_id = get_unique_id_from_device_info(device_info)
                    if not formatted_unique_id:
                        errors["base"] = "invalid_discovery_info"
                    else:
                        if self.unique_id and self.unique_id != formatted_unique_id:
                            errors["base"] = "unique_id_mismatch"
                        else:
                            await self.async_set_unique_id(formatted_unique_id)
                            self._abort_if_unique_id_configured()

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
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=self._discovered_ip): cv.string,
                    vol.Required(CONF_PORT, default=discovered_port): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=65535)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"host": self._discovered_ip},
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle reauth when device becomes unreachable."""
        return await self.async_step_reauth_confirm()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguration of an existing entry."""
        return await self.async_step_reconfigure_confirm(user_input)

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
                reason=None,
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

    async def async_step_reconfigure_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm reconfiguration dialog."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            host = str(user_input.get(CONF_HOST, ""))
            port = int(user_input.get(CONF_PORT, DEFAULT_UDP_PORT))

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
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOST, default=str(reconfigure_entry.data.get(CONF_HOST, ""))
                    ): cv.string,
                    vol.Required(
                        CONF_PORT,
                        default=int(reconfigure_entry.data.get(CONF_PORT, DEFAULT_UDP_PORT)),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                }
            ),
            errors=errors,
            description_placeholders={
                "host": str(reconfigure_entry.data.get(CONF_HOST, ""))
            },
        )

    async def _async_handle_discovery_with_unique_id(
        self,
    ) -> config_entries.ConfigFlowResult:
        """Handle any discovery with a unique id (similar to Yeelight pattern)."""
        if not self.unique_id or not self._discovered_ip:
            return self.async_abort(reason="invalid_discovery_info")

        for entry in self._async_current_entries(include_ignore=False):
            # Check if unique_id matches
            if not self._entry_matches_unique_id(entry):
                continue

            reload = entry.state == ConfigEntryState.SETUP_RETRY
            if entry.data.get(CONF_HOST) != self._discovered_ip:
                _LOGGER.info(
                    "Discovery: Device %s IP changed from %s to %s, updating config entry",
                    entry.unique_id,
                    entry.data.get(CONF_HOST),
                    self._discovered_ip,
                )
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_HOST: self._discovered_ip}
                )
                reload = entry.state in (
                    ConfigEntryState.SETUP_RETRY,
                    ConfigEntryState.LOADED,
                )
            if reload:
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
        reason: str | None,
    ) -> tuple[config_entries.ConfigFlowResult | None, str | None]:
        """Validate host and update the entry if the device matches."""
        if not host:
            return None, "cannot_connect"

        try:
            device_info = await get_device_info(host=host, port=port)
            if not device_info:
                return None, "cannot_connect"

            formatted_unique_id = get_unique_id_from_device_info(device_info)
            if not formatted_unique_id:
                return None, "invalid_discovery_info"

            await self.async_set_unique_id(formatted_unique_id)
            self._abort_if_unique_id_mismatch()

            data_updates: dict[str, Any] = {CONF_HOST: host}
            if update_port:
                data_updates[CONF_PORT] = port

            if reason is None:
                return (
                    self.async_update_reload_and_abort(
                        entry,
                        data_updates=data_updates,
                    ),
                    None,
                )

            return (
                self.async_update_reload_and_abort(
                    entry,
                    data_updates=data_updates,
                    reason=reason,
                ),
                None,
            )
        except (OSError, TimeoutError, ValueError):
            return None, "cannot_connect"

    def _entry_matches_unique_id(self, entry: config_entries.ConfigEntry) -> bool:
        """Return True if entry matches current flow unique id."""
        if entry.unique_id and entry.unique_id == self.unique_id:
            return True

        if not self.unique_id:
            return False

        entry_mac = (
            entry.data.get("ble_mac")
            or entry.data.get("mac")
            or entry.data.get("wifi_mac")
        )
        return bool(entry_mac and format_mac(entry_mac) == self.unique_id)

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return MarstekOptionsFlow()


class MarstekOptionsFlow(config_entries.OptionsFlow):
    """Handle Marstek options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the Marstek options."""
        if user_input is not None:
            # Flatten section data for storage
            flat_data: dict[str, Any] = {}
            for section_data in user_input.values():
                if isinstance(section_data, dict):
                    flat_data.update(section_data)
            return self.async_create_entry(title="", data=flat_data)

        # Get current values from options, falling back to defaults
        current_fast = self.config_entry.options.get(
            CONF_POLL_INTERVAL_FAST, DEFAULT_POLL_INTERVAL_FAST
        )
        current_medium = self.config_entry.options.get(
            CONF_POLL_INTERVAL_MEDIUM, DEFAULT_POLL_INTERVAL_MEDIUM
        )
        current_slow = self.config_entry.options.get(
            CONF_POLL_INTERVAL_SLOW, DEFAULT_POLL_INTERVAL_SLOW
        )
        current_delay = self.config_entry.options.get(
            CONF_REQUEST_DELAY, DEFAULT_REQUEST_DELAY
        )
        current_timeout = self.config_entry.options.get(
            CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT
        )
        current_failure_threshold = self.config_entry.options.get(
            CONF_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD
        )
        current_charge_power = self.config_entry.options.get(
            CONF_ACTION_CHARGE_POWER, DEFAULT_ACTION_CHARGE_POWER
        )
        current_discharge_power = self.config_entry.options.get(
            CONF_ACTION_DISCHARGE_POWER, DEFAULT_ACTION_DISCHARGE_POWER
        )
        current_socket_limit = self.config_entry.options.get(
            CONF_SOCKET_LIMIT,
            device_default_socket_limit(self.config_entry.data.get("device_type")),
        )

        # Build schema with collapsible sections for better UX
        polling_schema = build_polling_schema(
            current_fast=current_fast,
            current_medium=current_medium,
            current_slow=current_slow,
        )

        network_schema = build_network_schema(
            current_delay=current_delay,
            current_timeout=current_timeout,
            current_failure_threshold=current_failure_threshold,
        )

        power_schema = build_power_schema(
            current_charge_power=current_charge_power,
            current_discharge_power=current_discharge_power,
            current_socket_limit=current_socket_limit,
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("polling_settings"): section(
                        polling_schema,
                        {"collapsed": False},
                    ),
                    vol.Required("network_settings"): section(
                        network_schema,
                        {"collapsed": True},
                    ),
                    vol.Required("power_settings"): section(
                        power_schema,
                        {"collapsed": True},
                    ),
                }
            ),
        )
