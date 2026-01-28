"""Repair flows for Marstek integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import format_mac

from .const import DEFAULT_UDP_PORT, DOMAIN
from .discovery import get_device_info


class CannotConnectRepairFlow(RepairsFlow):
    """Handler for cannot connect repair flow."""

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        """Handle the first step of the repair flow."""
        # Get entry_id from the issue data (set by RepairsFlowManager)
        entry_id_raw = self.data.get("entry_id") if self.data else None
        if not entry_id_raw or not isinstance(entry_id_raw, str):
            return self.async_abort(reason="missing_config")
        
        entry_id: str = entry_id_raw

        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return self.async_abort(reason="entry_not_found")

        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input.get(CONF_HOST)
            port_raw = user_input.get(CONF_PORT, DEFAULT_UDP_PORT)
            port = int(port_raw) if port_raw else DEFAULT_UDP_PORT

            if not host:
                errors["base"] = "cannot_connect"
            else:
                try:
                    device_info = await get_device_info(host=host, port=port)
                    if device_info:
                        unique_id_mac = (
                            device_info.get("ble_mac")
                            or device_info.get("mac")
                            or device_info.get("wifi_mac")
                        )
                        if not unique_id_mac:
                            errors["base"] = "invalid_discovery_info"
                        elif format_mac(unique_id_mac) != entry.unique_id:
                            errors["base"] = "unique_id_mismatch"
                        else:
                            # Update the config entry with the new host/port
                            self.hass.config_entries.async_update_entry(
                                entry,
                                data={**entry.data, CONF_HOST: host, CONF_PORT: port},
                            )
                            # Delete the issue since it's resolved
                            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
                            # Reload the entry to reconnect
                            await self.hass.config_entries.async_reload(entry.entry_id)
                            return self.async_create_entry(data={})
                    else:
                        errors["base"] = "cannot_connect"
                except (OSError, TimeoutError, ValueError):
                    errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOST, default=entry.data.get(CONF_HOST, "")
                    ): cv.string,
                    vol.Required(
                        CONF_PORT,
                        default=entry.data.get(CONF_PORT, DEFAULT_UDP_PORT),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                }
            ),
            errors=errors,
            description_placeholders={
                "host": entry.data.get(CONF_HOST, "unknown"),
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create flow."""
    return CannotConnectRepairFlow()
