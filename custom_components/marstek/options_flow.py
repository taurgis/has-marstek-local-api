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
    CONF_FAILURE_THRESHOLD,
    CONF_PARALLEL_API_REQUESTS,
    CONF_POLL_INTERVAL_FAST,
    CONF_POLL_INTERVAL_MEDIUM,
    CONF_POLL_INTERVAL_SLOW,
    CONF_REQUEST_DELAY,
    CONF_REQUEST_TIMEOUT,
    CONF_SOCKET_LIMIT,
    DEFAULT_ACTION_CHARGE_POWER,
    DEFAULT_ACTION_DISCHARGE_POWER,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_PARALLEL_API_REQUESTS,
    DEFAULT_POLL_INTERVAL_FAST,
    DEFAULT_POLL_INTERVAL_MEDIUM,
    DEFAULT_POLL_INTERVAL_SLOW,
    DEFAULT_REQUEST_DELAY,
    DEFAULT_REQUEST_TIMEOUT,
    device_default_socket_limit,
)
from .helpers.flow_schemas import (
    build_network_schema,
    build_polling_schema,
    build_power_schema,
)


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
        current_parallel_requests = self.config_entry.options.get(
            CONF_PARALLEL_API_REQUESTS,
            DEFAULT_PARALLEL_API_REQUESTS,
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
            current_parallel_requests=current_parallel_requests,
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
