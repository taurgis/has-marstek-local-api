"""Options flow for the Marstek integration.

Polling cadence, network behaviour and power defaults are per-entry
settings, so they live apart from the config flow that discovers and
adopts a device.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import section

from .const import (
    CONF_ACTION_CHARGE_POWER,
    CONF_ACTION_DISCHARGE_POWER,
    CONF_CLAMP_NEGATIVE_OFFGRID_POWER,
    CONF_FAILURE_THRESHOLD,
    CONF_PARALLEL_API_REQUESTS,
    CONF_PLUG_ENERGY_IN,
    CONF_PLUG_ENERGY_OUT,
    CONF_POLL_INTERVAL_FAST,
    CONF_POLL_INTERVAL_MEDIUM,
    CONF_POLL_INTERVAL_SLOW,
    CONF_REQUEST_DELAY,
    CONF_REQUEST_TIMEOUT,
    CONF_SHOW_CAPACITY_DASHBOARD,
    CONF_SOCKET_LIMIT,
    DEFAULT_ACTION_CHARGE_POWER,
    DEFAULT_ACTION_DISCHARGE_POWER,
    DEFAULT_CLAMP_NEGATIVE_OFFGRID_POWER,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_PARALLEL_API_REQUESTS,
    DEFAULT_POLL_INTERVAL_FAST,
    DEFAULT_POLL_INTERVAL_MEDIUM,
    DEFAULT_POLL_INTERVAL_SLOW,
    DEFAULT_REQUEST_DELAY,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_SHOW_CAPACITY_DASHBOARD,
    device_default_socket_limit,
)
from .helpers.flow_schemas import (
    build_dashboard_schema,
    build_network_schema,
    build_plug_meter_schema,
    build_polling_schema,
    build_power_schema,
)


class MarstekOptionsFlow(config_entries.OptionsFlow):
    """Handle Marstek options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the Marstek options."""
        values: dict[str, Any] = dict(self.config_entry.options)
        errors: dict[str, str] = {}
        if user_input is not None:
            # Flatten section data for storage
            flat_data: dict[str, Any] = {}
            for section_data in user_input.values():
                if isinstance(section_data, dict):
                    flat_data.update(section_data)
            error = _plug_meter_error(flat_data)
            if error is None:
                return self.async_create_entry(title="", data=flat_data)
            errors["base"] = error
            # An emptied meter field must stay empty when the form comes back.
            values = {**values, CONF_PLUG_ENERGY_IN: None, CONF_PLUG_ENERGY_OUT: None, **flat_data}
        return self.async_show_form(step_id="init", data_schema=self._schema(values), errors=errors)

    def _schema(self, values: dict[str, Any]) -> vol.Schema:
        """Build the sectioned form from stored or just-submitted values."""
        socket_default = device_default_socket_limit(self.config_entry.data.get("device_type"))
        sections = {
            "polling_settings": build_polling_schema(
                current_fast=values.get(CONF_POLL_INTERVAL_FAST, DEFAULT_POLL_INTERVAL_FAST),
                current_medium=values.get(CONF_POLL_INTERVAL_MEDIUM, DEFAULT_POLL_INTERVAL_MEDIUM),
                current_slow=values.get(CONF_POLL_INTERVAL_SLOW, DEFAULT_POLL_INTERVAL_SLOW),
            ),
            "network_settings": build_network_schema(
                current_parallel_requests=values.get(
                    CONF_PARALLEL_API_REQUESTS, DEFAULT_PARALLEL_API_REQUESTS
                ),
                current_delay=values.get(CONF_REQUEST_DELAY, DEFAULT_REQUEST_DELAY),
                current_timeout=values.get(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT),
                current_failure_threshold=values.get(
                    CONF_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD
                ),
            ),
            "power_settings": build_power_schema(
                current_charge_power=values.get(
                    CONF_ACTION_CHARGE_POWER, DEFAULT_ACTION_CHARGE_POWER
                ),
                current_discharge_power=values.get(
                    CONF_ACTION_DISCHARGE_POWER, DEFAULT_ACTION_DISCHARGE_POWER
                ),
                current_socket_limit=values.get(CONF_SOCKET_LIMIT, socket_default),
                current_clamp_negative_offgrid_power=values.get(
                    CONF_CLAMP_NEGATIVE_OFFGRID_POWER, DEFAULT_CLAMP_NEGATIVE_OFFGRID_POWER
                ),
            ),
            "dashboard_settings": build_dashboard_schema(
                show_capacity_dashboard=bool(
                    values.get(CONF_SHOW_CAPACITY_DASHBOARD, DEFAULT_SHOW_CAPACITY_DASHBOARD)
                ),
            ),
            "plug_meter_settings": build_plug_meter_schema(
                energy_in=values.get(CONF_PLUG_ENERGY_IN),
                energy_out=values.get(CONF_PLUG_ENERGY_OUT),
            ),
        }
        # Only polling is open by default; the rest are occasional settings.
        return vol.Schema(
            {
                vol.Required(key): section(schema, {"collapsed": key != "polling_settings"})
                for key, schema in sections.items()
            }
        )


def _plug_meter_error(options: dict[str, Any]) -> str | None:
    """Return why the linked meter cannot work, or None when it can."""
    energy_in = options.get(CONF_PLUG_ENERGY_IN)
    energy_out = options.get(CONF_PLUG_ENERGY_OUT)
    if bool(energy_in) != bool(energy_out):
        return "plug_meter_incomplete"
    if energy_in and energy_in == energy_out:
        return "plug_meter_same_entity"
    return None
