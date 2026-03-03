"""Config flow schemas for Marstek integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from ..const import (
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
)


def build_manual_entry_schema(default_port: int) -> vol.Schema:
    """Build the manual entry schema."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST): cv.string,
            vol.Optional(CONF_PORT, default=default_port): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
        }
    )


def build_polling_schema(
    *,
    current_fast: int,
    current_medium: int,
    current_slow: int,
) -> vol.Schema:
    """Build polling options schema."""
    return vol.Schema(
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
                    max=86400,
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
                    max=86400,
                    step=30,
                    unit_of_measurement="seconds",
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


def build_network_schema(
    *,
    current_parallel_requests: bool,
    current_delay: float,
    current_timeout: float,
    current_failure_threshold: int,
) -> vol.Schema:
    """Build network options schema."""
    return vol.Schema(
        {
            vol.Required(
                CONF_PARALLEL_API_REQUESTS,
                default=current_parallel_requests,
            ): BooleanSelector(),
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
            vol.Required(
                CONF_REQUEST_TIMEOUT,
                default=current_timeout,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=5.0,
                    max=60.0,
                    step=1.0,
                    unit_of_measurement="seconds",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_FAILURE_THRESHOLD,
                default=current_failure_threshold,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=10,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


def build_power_schema(
    *,
    current_charge_power: int,
    current_discharge_power: int,
    current_socket_limit: bool,
) -> vol.Schema:
    """Build power options schema."""
    return vol.Schema(
        {
            vol.Required(
                CONF_ACTION_CHARGE_POWER,
                default=current_charge_power,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=-5000,
                    max=0,
                    step=50,
                    unit_of_measurement="W",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_ACTION_DISCHARGE_POWER,
                default=current_discharge_power,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=5000,
                    step=50,
                    unit_of_measurement="W",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SOCKET_LIMIT,
                default=current_socket_limit,
            ): BooleanSelector(),
        }
    )
