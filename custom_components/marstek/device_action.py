"""Device actions for Marstek integration."""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from homeassistant.components.device_automation import (  # type: ignore[attr-defined]
    InvalidDeviceAutomationConfig,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_HOST, CONF_PORT, CONF_TYPE
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.typing import ConfigType, TemplateVarsType

from .const import (
    CONF_ACTION_CHARGE_POWER,
    CONF_ACTION_DISCHARGE_POWER,
    CONF_POLL_INTERVAL_FAST,
    CONF_REQUEST_DELAY,
    CONF_REQUEST_TIMEOUT,
    DEFAULT_ACTION_CHARGE_POWER,
    DEFAULT_ACTION_DISCHARGE_POWER,
    DEFAULT_POLL_INTERVAL_FAST,
    DEFAULT_REQUEST_DELAY,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_UDP_PORT,
    DOMAIN,
)
from .helpers.device_lookup import (
    async_find_marstek_device,
    async_get_marstek_entry,
)
from .helpers.polling import polling_paused
from .helpers.udp_clients import get_udp_client_for_entry
from .mode_config import build_manual_mode_config
from .power import validate_power_for_entry
from .pymarstek import MarstekUDPClient, build_command, get_es_status, parse_es_status_response

_LOGGER = logging.getLogger(__name__)

# Action type constants
ACTION_CHARGE = "charge"
ACTION_DISCHARGE = "discharge"
ACTION_STOP = "stop"

ACTION_TYPES = {ACTION_CHARGE, ACTION_DISCHARGE, ACTION_STOP}

# Command constants from pymarstek
CMD_ES_SET_MODE = "ES.SetMode"

# Retry configuration
# Timing is calculated from configured poll interval to ensure device has time to respond
MAX_RETRY_ATTEMPTS = 8
BASE_REQUEST_TIMEOUT = 5.0  # Base timeout for UDP request

# Verification configuration
# Fast tier makes 3 calls: ES.GetMode, ES.GetStatus, EM.GetStatus
FAST_TIER_CALL_COUNT = 3
CALL_TIME_BUDGET = 5.0  # Seconds per API call
VERIFICATION_ATTEMPTS = 3
STOP_POWER_THRESHOLD = 50  # W

STOP_POWER = 0  # W

# Manual mode configuration
MANUAL_MODE_START_TIME = "00:00"
MANUAL_MODE_END_TIME = "23:59"
MANUAL_MODE_WEEK_SET = 127  # All days of week

# Power attribute for action config
ATTR_POWER = "power"

ACTION_SCHEMA = cv.DEVICE_ACTION_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_DOMAIN): vol.In((DOMAIN,)),
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Required(CONF_TYPE): vol.In(ACTION_TYPES),
        vol.Optional(ATTR_POWER): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=5000)
        ),
        vol.Optional("entity_id"): cv.entity_id,
    }
)


def _resolve_action_settings(
    action_type: str,
    action_power: int | None,
    entry: ConfigEntry,
) -> tuple[int, int]:
    """Resolve power and enable values for the action."""
    if action_type == ACTION_CHARGE:
        default_power = entry.options.get(
            CONF_ACTION_CHARGE_POWER, DEFAULT_ACTION_CHARGE_POWER
        )
        power_value = action_power if action_power is not None else abs(default_power)
        return -int(power_value), 1

    if action_type == ACTION_DISCHARGE:
        default_power = entry.options.get(
            CONF_ACTION_DISCHARGE_POWER, DEFAULT_ACTION_DISCHARGE_POWER
        )
        power_value = action_power if action_power is not None else default_power
        return int(power_value), 1

    return STOP_POWER, 0


def _calculate_action_timing(entry: ConfigEntry) -> tuple[float, float]:
    """Calculate request timeout and poll cycle time for action verification."""
    poll_interval = entry.options.get(
        CONF_POLL_INTERVAL_FAST, DEFAULT_POLL_INTERVAL_FAST
    )
    request_delay = entry.options.get(
        CONF_REQUEST_DELAY, DEFAULT_REQUEST_DELAY
    )
    request_timeout = entry.options.get(
        CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT
    )

    poll_cycle_time = poll_interval + (
        FAST_TIER_CALL_COUNT * (request_delay + CALL_TIME_BUDGET)
    )
    return float(request_timeout), float(poll_cycle_time)


async def _send_action_command(
    udp_client: MarstekUDPClient,
    host: str,
    port: int,
    command: str,
    *,
    request_timeout: float,
    attempt_idx: int,
) -> None:
    """Send ES.SetMode command with polling pause and logging."""
    async with polling_paused(udp_client, host):
        try:
            await udp_client.send_request(
                command,
                host,
                port,
                timeout=request_timeout,
                quiet_on_timeout=True,
            )
        except (TimeoutError, OSError, ValueError) as err:
            _LOGGER.debug(
                "ES.SetMode send attempt %d/%d failed for %s: %s",
                attempt_idx,
                MAX_RETRY_ATTEMPTS,
                host,
                err,
            )


async def _verify_action_command(
    hass: HomeAssistant,
    udp_client: MarstekUDPClient,
    host: str,
    port: int,
    enable: int,
    power: int,
    *,
    request_timeout: float,
    attempt_idx: int,
) -> bool:
    """Verify ES.SetMode outcome with polling pause and logging."""
    async with polling_paused(udp_client, host):
        try:
            return await _verify_es_mode_quick(
                hass,
                host,
                port,
                enable,
                power,
                udp_client,
                request_timeout=request_timeout,
            )
        except (TimeoutError, OSError, ValueError) as err:
            _LOGGER.debug(
                "ES.SetMode verification attempt %d/%d failed for %s: %s",
                attempt_idx,
                MAX_RETRY_ATTEMPTS,
                host,
                err,
            )
            return False


async def async_validate_action_config(
    hass: HomeAssistant, config: ConfigType
) -> ConfigType:
    """Validate config for device actions."""
    device_id: str = config[CONF_DEVICE_ID]
    action_type: str = config[CONF_TYPE]

    entry = _require_entry_from_device_id(hass, device_id, require_loaded=False)

    action_power = config.get(ATTR_POWER)
    power, enable = _resolve_action_settings(action_type, action_power, entry)
    if enable:
        _validate_action_power_config(entry, power)

    return config


async def async_get_actions(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """List device actions for a Marstek device."""
    device = async_find_marstek_device(hass, device_id)
    if not device:
        return []

    if not any(ident[0] == DOMAIN for ident in device.identifiers):
        return []

    base_action = {
        CONF_DEVICE_ID: device_id,
        CONF_DOMAIN: DOMAIN,
    }

    return [{CONF_TYPE: action_type} | base_action for action_type in ACTION_TYPES]


async def async_call_action_from_config(
    hass: HomeAssistant,
    config: ConfigType,
    variables: TemplateVarsType,
    context: Context | None,
) -> None:
    """Execute a device action."""
    action_type: str = config[CONF_TYPE]
    device_id: str = config[CONF_DEVICE_ID]

    host_port = await _get_host_from_device(hass, device_id)
    if not host_port:
        raise InvalidDeviceAutomationConfig(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device_id": device_id},
        )

    host, port = host_port

    entry = _require_entry_from_device_id(hass, device_id)

    # Get power from action config or fall back to options
    action_power = config.get(ATTR_POWER)
    power, enable = _resolve_action_settings(action_type, action_power, entry)

    # Validate power against device limits (including socket limit)
    if enable:
        _validate_action_power_runtime(entry, power)

    command = _build_set_mode_command(power, enable)

    # Get the UDP client bound to this device's Open API port
    udp_client = get_udp_client_for_entry(hass, entry)
    if not udp_client:
        raise InvalidDeviceAutomationConfig(
            translation_domain=DOMAIN,
            translation_key="config_invalid",
            translation_placeholders={"device_id": device_id},
        )

    # Calculate timeouts based on configured poll interval and request delays
    # Device needs time to complete a full poll cycle before we can verify the mode change
    # Worst case: poll_interval + (number_of_calls * (delay_between_calls + time_per_call))
    request_timeout, poll_cycle_time = _calculate_action_timing(entry)
    # Verification delay is the full poll cycle time
    verification_delay = poll_cycle_time
    # Backoff between retry attempts also uses poll cycle time
    backoff_base = poll_cycle_time

    _LOGGER.debug(
        "Action timing for %s: poll_cycle=%.1fs, verification_delay=%.1fs",
        host,
        poll_cycle_time,
        verification_delay,
    )

    for attempt_idx in range(1, MAX_RETRY_ATTEMPTS + 1):
        # Step 1: Send command (pause only during send)
        await _send_action_command(
            udp_client,
            host,
            port,
            command,
            request_timeout=request_timeout,
            attempt_idx=attempt_idx,
        )

        # Step 2: Wait for device to settle (polling is ACTIVE during this wait)
        await asyncio.sleep(verification_delay)

        # Step 3: Quick verification checks (pause only during checks)
        if await _verify_action_command(
            hass,
            udp_client,
            host,
            port,
            enable,
            power,
            request_timeout=request_timeout,
            attempt_idx=attempt_idx,
        ):
            _LOGGER.info(
                "ES.SetMode action '%s' confirmed after attempt %d/%d for device %s",
                action_type,
                attempt_idx,
                MAX_RETRY_ATTEMPTS,
                host,
            )
            return

        if attempt_idx < MAX_RETRY_ATTEMPTS:
            # Backoff is based on poll cycle time with small jitter
            jitter = 0.30 * attempt_idx
            delay = backoff_base + jitter
            _LOGGER.warning(
                "ES.SetMode action '%s' not confirmed on attempt %d/%d for "
                "device %s, retrying in %.2fs",
                action_type,
                attempt_idx,
                MAX_RETRY_ATTEMPTS,
                host,
                delay,
            )
            # Polling is ACTIVE during backoff
            await asyncio.sleep(delay)

    raise TimeoutError(
        f"ES.SetMode action '{action_type}' not confirmed after "
        f"{MAX_RETRY_ATTEMPTS} attempts for device {host}"
    )


async def async_get_action_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """List action capabilities."""
    action_type = config.get(CONF_TYPE)

    # Charge and discharge actions have power parameter, stop does not
    if action_type in (ACTION_CHARGE, ACTION_DISCHARGE):
        return {
            "extra_fields": vol.Schema(
                {
                    vol.Optional(ATTR_POWER): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=5000)
                    ),
                }
            )
        }

    return {"extra_fields": vol.Schema({})}


def _config_power_error(
    requested: int,
    min_power: int,
    max_power: int,
) -> InvalidDeviceAutomationConfig:
    """Build a power validation error for config validation."""
    return InvalidDeviceAutomationConfig(
        translation_domain=DOMAIN,
        translation_key="power_out_of_range",
        translation_placeholders={
            "requested": str(requested),
            "min": str(min_power),
            "max": str(max_power),
        },
    )


def _runtime_power_error(
    requested: int,
    min_power: int,
    max_power: int,
) -> HomeAssistantError:
    """Build a power validation error for runtime action execution."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="power_out_of_range",
        translation_placeholders={
            "requested": str(requested),
            "min": str(min_power),
            "max": str(max_power),
        },
    )


def _validate_action_power_config(entry: ConfigEntry, power: int) -> None:
    """Validate power during config validation (raises InvalidDeviceAutomationConfig)."""
    validate_power_for_entry(entry, power, _config_power_error)


def _validate_action_power_runtime(entry: ConfigEntry, power: int) -> None:
    """Validate power during runtime execution (raises HomeAssistantError)."""
    validate_power_for_entry(entry, power, _runtime_power_error)


def _build_set_mode_command(power: int, enable: int) -> str:
    """Build ES.SetMode command with manual configuration."""
    payload = {
        "id": 0,
        "config": build_manual_mode_config(
            power=power,
            enable=enable == 1,
            time_num=0,
            start_time=MANUAL_MODE_START_TIME,
            end_time=MANUAL_MODE_END_TIME,
            week_set=MANUAL_MODE_WEEK_SET,
        ),
    }
    return build_command(CMD_ES_SET_MODE, payload)


async def _verify_es_mode_quick(
    hass: HomeAssistant,
    host: str,
    port: int,
    enable: int,
    power: int,
    udp_client: MarstekUDPClient,
    *,
    request_timeout: float,
) -> bool:
    """Verify that ES mode matches expected state with quick retries.

    Performs quick verification checks (no long delay - caller handles that).

    Rules:
    - Mode should be "Manual" (if present)
    - enable=0 (stop): battery_power should be near zero (< 50W)
    - enable=1 and power<0 (charge): battery_power should be negative
    - enable=1 and power>0 (discharge): battery_power should be positive
    """
    # Quick retries for network issues (short delay between checks)
    for _ in range(VERIFICATION_ATTEMPTS):
        try:
            response = await udp_client.send_request(
                get_es_status(0),
                host,
                port,
                timeout=request_timeout,
                quiet_on_timeout=True,
            )
        except (TimeoutError, OSError, ValueError):
            await asyncio.sleep(1.0)
            continue

        result = response.get("result") if isinstance(response, dict) else None
        if not isinstance(result, dict) or "error" in response:
            await asyncio.sleep(1.0)
            continue

        mode = result.get("mode")
        parsed = parse_es_status_response(response)
        battery_power = parsed.get("battery_power")

        if mode is not None and mode != "Manual":
            await asyncio.sleep(1.0)
            continue

        if not isinstance(battery_power, (int, float)):
            await asyncio.sleep(1.0)
            continue

        if enable == 0 and abs(battery_power) < STOP_POWER_THRESHOLD:
            return True
        if enable == 1 and power < 0 and battery_power < 0:
            return True
        if enable == 1 and power > 0 and battery_power > 0:
            return True

        await asyncio.sleep(1.0)

    return False


async def _get_host_from_device(
    hass: HomeAssistant, device_id: str
) -> tuple[str, int] | None:
    """Resolve device IP address from device registry and config entries."""
    device = async_find_marstek_device(hass, device_id)
    if not device:
        return None

    entry = async_get_marstek_entry(hass, device, require_loaded=False)
    if entry:
        host = entry.data.get(CONF_HOST)
        port = entry.data.get(CONF_PORT, DEFAULT_UDP_PORT)
        if host:
            return host, int(port)

    # Fallback to identifier if it looks like an IP address
    for domain, identifier in device.identifiers:
        if domain == DOMAIN:
            if "." in identifier:
                return identifier, DEFAULT_UDP_PORT
            try:
                _ = format_mac(identifier)
            except (ValueError, TypeError):
                continue

    return None


def _get_entry_from_device_id(
    hass: HomeAssistant, device_id: str, *, require_loaded: bool = True
) -> ConfigEntry | None:
    """Get config entry for a device ID."""
    device = async_find_marstek_device(hass, device_id)
    if not device:
        return None
    return async_get_marstek_entry(hass, device, require_loaded=require_loaded)


def _require_entry_from_device_id(
    hass: HomeAssistant, device_id: str, *, require_loaded: bool = True
) -> ConfigEntry:
    """Get the config entry for a device ID, or raise for the automation UI."""
    entry = _get_entry_from_device_id(hass, device_id, require_loaded=require_loaded)
    if not entry:
        raise InvalidDeviceAutomationConfig(
            translation_domain=DOMAIN,
            translation_key="no_config_entry",
            translation_placeholders={"device_id": device_id},
        )
    return entry
