"""Config flow for Marstek integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PORT
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

try:
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
except ImportError:
    # Fallback for older Home Assistant versions (pre-2025.1)
    try:
        from homeassistant.components.dhcp import DhcpServiceInfo  # type: ignore[assignment,no-redef]
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
    CONF_POLL_INTERVAL_FAST,
    CONF_POLL_INTERVAL_MEDIUM,
    CONF_POLL_INTERVAL_SLOW,
    CONF_REQUEST_DELAY,
    DEFAULT_POLL_INTERVAL_FAST,
    DEFAULT_POLL_INTERVAL_MEDIUM,
    DEFAULT_POLL_INTERVAL_SLOW,
    DEFAULT_REQUEST_DELAY,
    DEFAULT_UDP_PORT,
    DOMAIN,
)
from .discovery import discover_devices, get_device_info

_LOGGER = logging.getLogger(__name__)

MANUAL_ENTRY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_UDP_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
    }
)


class MarstekConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Marstek."""

    VERSION = 1
    discovered_devices: list[dict[str, Any]]
    _discovered_ip: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step - broadcast device discovery."""
        if user_input is not None and "device" in user_input:
            # User has selected a device from the discovered list
            device_index = int(user_input["device"])
            device = self.discovered_devices[device_index]

            # Check if device is already configured using host/mac
            self._async_abort_entries_match({CONF_HOST: device["ip"]})
            # Use BLE-MAC as unique_id for stability (beardhatcode & mik-laj feedback)
            # BLE-MAC is more stable than WiFi MAC and ensures device history continuity
            unique_id_mac = (
                device.get("ble_mac") or device.get("mac") or device.get("wifi_mac")
            )
            if not unique_id_mac:
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema({}),
                    errors={"base": "invalid_discovery_info"},
                )

            self._async_abort_entries_match({CONF_MAC: unique_id_mac})
            await self.async_set_unique_id(format_mac(unique_id_mac))
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"Marstek {device['device_type']} ({device['ip']})",
                data={
                    CONF_HOST: device["ip"],
                    CONF_MAC: device["mac"],
                    "device_type": device["device_type"],
                    "version": device["version"],
                    "wifi_name": device["wifi_name"],
                    "wifi_mac": device["wifi_mac"],
                    "ble_mac": device["ble_mac"],
                    "model": device["model"],  # Compatibility field
                    "firmware": device["firmware"],  # Compatibility field
                },
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
            configured_macs = set()
            for entry in self._async_current_entries(include_ignore=False):
                entry_mac = (
                    entry.data.get("ble_mac")
                    or entry.data.get(CONF_MAC)
                    or entry.data.get("wifi_mac")
                )
                if entry_mac:
                    configured_macs.add(format_mac(entry_mac))

            # Build device options, separating new and already-configured devices
            device_options = {}
            already_configured_names = []
            for i, device in enumerate(devices):
                # Build detailed device display name with all important info
                device_name = (
                    f"{device.get('device_type', 'Unknown')} "
                    f"v{device.get('version', 'Unknown')} "
                    f"({device.get('wifi_name', 'No WiFi')}) "
                    f"- {device.get('ip', 'Unknown')}"
                )
                # Check if this device is already configured
                device_mac = (
                    device.get("ble_mac")
                    or device.get("mac")
                    or device.get("wifi_mac")
                )
                is_configured = (
                    device_mac and format_mac(device_mac) in configured_macs
                )
                if is_configured:
                    already_configured_names.append(f"{device_name} (already added)")
                else:
                    device_options[str(i)] = device_name

            # If all discovered devices are already configured, show manual entry
            if not device_options:
                _LOGGER.info("All discovered devices are already configured")
                return await self.async_step_manual(
                    errors={"base": "all_devices_configured"}
                )

            # Build description showing already configured devices if any
            description_lines = [f"- {name}" for name in device_options.values()]
            if already_configured_names:
                description_lines.append("")
                description_lines.append("Already configured:")
                description_lines.extend(
                    [f"- {name}" for name in already_configured_names]
                )

            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {vol.Required("device"): vol.In(device_options)}
                ),
                description_placeholders={
                    "devices": "\n".join(description_lines)
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

            try:
                # Validate connection by attempting to get device info
                device_info = await get_device_info(host=host, port=port)

                if not device_info:
                    return self.async_show_form(
                        step_id="manual",
                        data_schema=MANUAL_ENTRY_SCHEMA,
                        errors={"base": "cannot_connect"},
                    )

                # Check if device is already configured
                unique_id_mac = (
                    device_info.get("ble_mac")
                    or device_info.get("mac")
                    or device_info.get("wifi_mac")
                )
                if not unique_id_mac:
                    return self.async_show_form(
                        step_id="manual",
                        data_schema=MANUAL_ENTRY_SCHEMA,
                        errors={"base": "invalid_discovery_info"},
                    )

                self._async_abort_entries_match({CONF_HOST: host})
                self._async_abort_entries_match({CONF_MAC: unique_id_mac})
                await self.async_set_unique_id(format_mac(unique_id_mac))
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Marstek {device_info.get('device_type', 'Device')} ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_MAC: device_info.get("mac"),
                        "device_type": device_info.get("device_type"),
                        "version": device_info.get("version"),
                        "wifi_name": device_info.get("wifi_name"),
                        "wifi_mac": device_info.get("wifi_mac"),
                        "ble_mac": device_info.get("ble_mac"),
                        "model": device_info.get("model"),
                        "firmware": device_info.get("firmware"),
                    },
                )

            except (ConnectionError, OSError, TimeoutError) as err:
                _LOGGER.error("Cannot connect to device at %s:%s: %s", host, port, err)
                return self.async_show_form(
                    step_id="manual",
                    data_schema=MANUAL_ENTRY_SCHEMA,
                    errors={"base": "cannot_connect"},
                )
            except ValueError as err:
                _LOGGER.error("Invalid response from device at %s:%s: %s", host, port, err)
                return self.async_show_form(
                    step_id="manual",
                    data_schema=MANUAL_ENTRY_SCHEMA,
                    errors={"base": "invalid_discovery_info"},
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=MANUAL_ENTRY_SCHEMA,
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
        mac = format_mac(discovery_info.macaddress)
        _LOGGER.info(
            "DHCP discovery triggered: MAC=%s, IP=%s, Hostname=%s",
            mac,
            discovery_info.ip,
            discovery_info.hostname,
        )

        # Use BLE-MAC or MAC as unique_id (beardhatcode & mik-laj feedback)
        # Try to find existing entry by MAC address
        for entry in self._async_current_entries(include_ignore=False):
            entry_mac = (
                entry.data.get("ble_mac")
                or entry.data.get("mac")
                or entry.data.get("wifi_mac")
            )
            if entry_mac and format_mac(entry_mac) == mac:
                # Found existing entry, update IP if it changed
                if entry.data.get(CONF_HOST) != discovery_info.ip:
                    _LOGGER.info(
                        "DHCP discovery: Device %s IP changed from %s to %s, updating config entry",
                        mac,
                        entry.data.get(CONF_HOST),
                        discovery_info.ip,
                    )
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={**entry.data, CONF_HOST: discovery_info.ip},
                    )
                    # Reload the entry to use new IP
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                else:
                    _LOGGER.debug(
                        "DHCP discovery: Device %s IP unchanged (%s)",
                        mac,
                        discovery_info.ip,
                    )
                return self.async_abort(reason="already_configured")

        # No existing entry found, continue with user flow
        _LOGGER.debug("DHCP discovery: No existing entry found for MAC %s", mac)
        return await self.async_step_user()

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle discovery from Scanner (integration discovery)."""
        discovered_ip = discovery_info.get("ip")
        discovered_ble_mac = discovery_info.get("ble_mac")

        if not discovered_ble_mac:
            return self.async_abort(reason="invalid_discovery_info")

        # Set unique_id using BLE-MAC
        await self.async_set_unique_id(format_mac(discovered_ble_mac))
        self._discovered_ip = discovered_ip

        # Handle discovery with unique_id (updates existing entries or creates new)
        return await self._async_handle_discovery_with_unique_id()

    async def _async_handle_discovery_with_unique_id(
        self,
    ) -> config_entries.ConfigFlowResult:
        """Handle any discovery with a unique id (similar to Yeelight pattern)."""
        for entry in self._async_current_entries(include_ignore=False):
            # Check if unique_id matches
            if entry.unique_id != self.unique_id:
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

        # No existing entry found, continue with user flow
        return await self.async_step_user()

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
            return self.async_create_entry(title="", data=user_input)

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

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL_FAST,
                        default=current_fast,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=10,
                            max=300,
                            step=5,
                            unit_of_measurement="seconds",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_POLL_INTERVAL_MEDIUM,
                        default=current_medium,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=30,
                            max=600,
                            step=10,
                            unit_of_measurement="seconds",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_POLL_INTERVAL_SLOW,
                        default=current_slow,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=60,
                            max=1800,
                            step=30,
                            unit_of_measurement="seconds",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_REQUEST_DELAY,
                        default=current_delay,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1.0,
                            max=30.0,
                            step=0.5,
                            unit_of_measurement="seconds",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )
