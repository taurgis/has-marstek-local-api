"""Services for Marstek devices."""

from __future__ import annotations

import asyncio
import logging
from datetime import time
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from .pymarstek import (
    MAX_PASSIVE_DURATION,
    MAX_POWER_VALUE,
    MAX_TIME_SLOTS,
    MarstekUDPClient,
    build_command,
)

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import (
    API_MODE_MANUAL,
    API_MODE_PASSIVE,
    CMD_ES_SET_MODE,
    CONF_SOCKET_LIMIT,
    DATA_UDP_CLIENT,
    DEFAULT_UDP_PORT,
    DOMAIN,
    WEEKDAY_MAP,
    WEEKDAYS_ALL,
    device_default_socket_limit,
    get_device_power_limits,
)

if TYPE_CHECKING:
    from . import MarstekConfigEntry

_LOGGER = logging.getLogger(__name__)

# Retry configuration
MAX_RETRY_ATTEMPTS = 3
RETRY_TIMEOUT = 5.0
RETRY_DELAY = 1.0

# Service names
SERVICE_SET_PASSIVE_MODE = "set_passive_mode"
SERVICE_SET_MANUAL_SCHEDULE = "set_manual_schedule"
SERVICE_SET_MANUAL_SCHEDULES = "set_manual_schedules"
SERVICE_CLEAR_MANUAL_SCHEDULES = "clear_manual_schedules"
SERVICE_REQUEST_DATA_SYNC = "request_data_sync"

# Service schemas
ATTR_DEVICE_ID = "device_id"
ATTR_POWER = "power"
ATTR_DURATION = "duration"
ATTR_SCHEDULE_SLOT = "schedule_slot"
ATTR_START_TIME = "start_time"
ATTR_END_TIME = "end_time"
ATTR_DAYS = "days"
ATTR_ENABLE = "enable"
ATTR_SCHEDULES = "schedules"

SERVICE_SET_PASSIVE_MODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_POWER): vol.All(
            vol.Coerce(int), vol.Range(min=-MAX_POWER_VALUE, max=MAX_POWER_VALUE)
        ),
        vol.Optional(ATTR_DURATION, default=3600): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_PASSIVE_DURATION)
        ),
    }
)

SERVICE_SET_MANUAL_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_SCHEDULE_SLOT, default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_TIME_SLOTS - 1)
        ),
        vol.Required(ATTR_START_TIME): cv.time,
        vol.Required(ATTR_END_TIME): cv.time,
        vol.Required(ATTR_POWER): vol.All(
            vol.Coerce(int), vol.Range(min=-MAX_POWER_VALUE, max=MAX_POWER_VALUE)
        ),
        vol.Optional(ATTR_DAYS, default=["mon", "tue", "wed", "thu", "fri", "sat", "sun"]): vol.All(
            cv.ensure_list,
            [vol.In(WEEKDAY_MAP.keys())],
        ),
        vol.Optional(ATTR_ENABLE, default=True): cv.boolean,
    }
)

SCHEDULE_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SCHEDULE_SLOT): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_TIME_SLOTS - 1)
        ),
        vol.Required(ATTR_START_TIME): cv.string,
        vol.Required(ATTR_END_TIME): cv.string,
        vol.Optional(ATTR_POWER, default=0): vol.All(
            vol.Coerce(int), vol.Range(min=-MAX_POWER_VALUE, max=MAX_POWER_VALUE)
        ),
        vol.Optional(ATTR_DAYS, default=["mon", "tue", "wed", "thu", "fri", "sat", "sun"]): vol.All(
            cv.ensure_list,
            [vol.In(WEEKDAY_MAP.keys())],
        ),
        vol.Optional(ATTR_ENABLE, default=True): cv.boolean,
    }
)

SERVICE_CLEAR_MANUAL_SCHEDULES_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
    }
)

SERVICE_SET_MANUAL_SCHEDULES_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_SCHEDULES): vol.All(
            cv.ensure_list,
            [SCHEDULE_ITEM_SCHEMA],
        ),
    }
)

SERVICE_REQUEST_DATA_SYNC_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
    }
)


def _calculate_week_set(days: list[str]) -> int:
    """Calculate week_set bitmask from list of day names."""
    week_set = 0
    for day in days:
        day_lower = day.lower()
        if day_lower in WEEKDAY_MAP:
            week_set |= WEEKDAY_MAP[day_lower]
    return week_set


def _get_device_id_from_call(call: ServiceCall) -> str:
    """Extract device_id from service call data."""
    device_id = call.data.get(ATTR_DEVICE_ID)
    if not device_id:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="no_device_specified",
        )
    return device_id


def _get_entry_and_client_from_device_id(
    hass: HomeAssistant, device_id: str
) -> tuple[MarstekConfigEntry, MarstekUDPClient, str, int]:
    """Get config entry and UDP client from device ID."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)

    if not device:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="invalid_device",
            translation_placeholders={"device_id": device_id},
        )

    # Find the Marstek config entry for this device
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN and entry.state == ConfigEntryState.LOADED:
            host = entry.data.get(CONF_HOST)
            port = entry.data.get(CONF_PORT, DEFAULT_UDP_PORT)
            # Get shared UDP client from hass.data
            udp_client = hass.data.get(DOMAIN, {}).get(DATA_UDP_CLIENT)
            if host and udp_client:
                return entry, udp_client, host, int(port)

    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="no_config_entry",
        translation_placeholders={"device_id": device_id},
    )


def _get_power_limits(entry: MarstekConfigEntry) -> tuple[int, int]:
    """Get power limits for a device based on model and socket limit option."""
    device_type = entry.data.get("device_type")
    socket_limit = entry.options.get(
        CONF_SOCKET_LIMIT,
        device_default_socket_limit(device_type),
    )
    return get_device_power_limits(device_type, socket_limit=socket_limit)


def _validate_power_for_device(power: int, entry: MarstekConfigEntry) -> None:
    """Validate requested power against device limits."""
    min_power, max_power = _get_power_limits(entry)
    if power < min_power or power > max_power:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="power_out_of_range",
            translation_placeholders={
                "requested": power,
                "min": min_power,
                "max": max_power,
            },
        )


async def _send_mode_command(
    udp_client: MarstekUDPClient,
    host: str,
    port: int,
    config: dict[str, Any],
    *,
    pause_polling: bool = True,
) -> None:
    """Send a mode command with retries.
    
    Args:
        udp_client: UDP client for communication
        host: Device IP address
        port: Device port
        config: Mode configuration payload
        pause_polling: Whether to pause/resume polling (set False for batch ops)
    """
    command = build_command(CMD_ES_SET_MODE, {"id": 0, "config": config})

    # Pause polling while sending command (unless caller handles it)
    if pause_polling:
        await udp_client.pause_polling(host)

    try:
        success = False
        last_error: str | None = None

        for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
            try:
                await udp_client.send_request(
                    command,
                    host,
                    port,
                    timeout=RETRY_TIMEOUT,
                )
                _LOGGER.info(
                    "Successfully sent mode command (attempt %d/%d)",
                    attempt,
                    MAX_RETRY_ATTEMPTS,
                )
                success = True
                break
            except (TimeoutError, OSError, ValueError) as err:
                last_error = str(err)
                _LOGGER.warning(
                    "Failed to send mode command (attempt %d/%d): %s",
                    attempt,
                    MAX_RETRY_ATTEMPTS,
                    err,
                )
                if attempt < MAX_RETRY_ATTEMPTS:
                    await asyncio.sleep(RETRY_DELAY)

        if not success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": last_error or "Unknown error"},
            )

    finally:
        if pause_polling:
            await udp_client.resume_polling(host)


async def async_set_passive_mode(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle set_passive_mode service call."""
    device_id = _get_device_id_from_call(call)
    power = call.data[ATTR_POWER]
    duration = call.data[ATTR_DURATION]

    entry, udp_client, host, port = _get_entry_and_client_from_device_id(hass, device_id)

    _validate_power_for_device(power, entry)

    config = {
        "mode": API_MODE_PASSIVE,
        "passive_cfg": {
            "power": power,
            "cd_time": duration,
        },
    }

    await _send_mode_command(udp_client, host, port, config)

    # Refresh coordinator
    await entry.runtime_data.coordinator.async_request_refresh()

    _LOGGER.info(
        "Set passive mode: power=%dW, duration=%ds for device %s",
        power,
        duration,
        device_id,
    )


async def async_set_manual_schedule(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle set_manual_schedule service call."""
    device_id = _get_device_id_from_call(call)
    schedule_slot = call.data[ATTR_SCHEDULE_SLOT]
    start_time = call.data[ATTR_START_TIME]
    end_time = call.data[ATTR_END_TIME]
    power = call.data[ATTR_POWER]
    days = call.data[ATTR_DAYS]
    enable = call.data[ATTR_ENABLE]

    entry, udp_client, host, port = _get_entry_and_client_from_device_id(hass, device_id)

    _validate_power_for_device(power, entry)

    # Format times as HH:MM
    start_time_str = start_time.strftime("%H:%M")
    end_time_str = end_time.strftime("%H:%M")

    _validate_time_range(start_time, end_time)

    # Calculate week_set bitmask
    week_set = _calculate_week_set(days)

    config = {
        "mode": API_MODE_MANUAL,
        "manual_cfg": {
            "time_num": schedule_slot,
            "start_time": start_time_str,
            "end_time": end_time_str,
            "week_set": week_set,
            "power": power,
            "enable": 1 if enable else 0,
        },
    }

    await _send_mode_command(udp_client, host, port, config)

    # Refresh coordinator
    await entry.runtime_data.coordinator.async_request_refresh()

    _LOGGER.info(
        "Set manual schedule slot %d: %s-%s, power=%dW, days=%s, enabled=%s for device %s",
        schedule_slot,
        start_time_str,
        end_time_str,
        power,
        days,
        enable,
        device_id,
    )


async def async_clear_manual_schedules(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle clear_manual_schedules service call.
    
    Note: This clears all 10 schedule slots sequentially. Each slot requires
    a separate API call to the device due to protocol limitations.
    Polling is paused once for all 10 commands to avoid race conditions.
    """
    device_id = _get_device_id_from_call(call)

    entry, udp_client, host, port = _get_entry_and_client_from_device_id(hass, device_id)
    min_power, max_power = _get_power_limits(entry)

    _LOGGER.info("Clearing 10 manual schedule slots for device %s...", device_id)
    
    # Pause polling once for all 10 commands
    await udp_client.pause_polling(host)
    
    try:
        # Clear all 10 schedule slots by setting them to disabled
        for slot in range(10):
            config = {
                "mode": API_MODE_MANUAL,
                "manual_cfg": {
                    "time_num": slot,
                    "start_time": "00:00",
                    "end_time": "00:00",
                    "week_set": 0,
                    "power": 0,
                    "enable": 0,
                },
            }

            await _send_mode_command(udp_client, host, port, config, pause_polling=False)
            _LOGGER.debug("Cleared manual schedule slot %d/10 for device %s", slot + 1, device_id)
    finally:
        await udp_client.resume_polling(host)

    # Refresh coordinator
    await entry.runtime_data.coordinator.async_request_refresh()

    _LOGGER.info("Cleared all manual schedules for device %s", device_id)


def _parse_time_string(time_str: str) -> str:
    """Parse time string to HH:MM format."""
    # Handle both "HH:MM" and "HH:MM:SS" formats
    parts = time_str.split(":")
    if len(parts) >= 2:
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    raise ValueError(f"Invalid time format: {time_str}")


def _time_to_minutes(value: time | str) -> int:
    """Convert time or time string to minutes since midnight."""
    if isinstance(value, time):
        return value.hour * 60 + value.minute

    parts = value.split(":")
    if len(parts) >= 2:
        return int(parts[0]) * 60 + int(parts[1])

    raise ValueError(f"Invalid time format: {value}")


def _validate_time_range(start: time | str, end: time | str) -> None:
    """Validate that end time is after start time."""
    if _time_to_minutes(end) <= _time_to_minutes(start):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="invalid_time_range",
            translation_placeholders={"start": str(start), "end": str(end)},
        )


async def async_set_manual_schedules(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle set_manual_schedules service call (batch configuration).
    
    Polling is paused once for all schedule commands to avoid race conditions.
    """
    device_id = _get_device_id_from_call(call)
    schedules = call.data[ATTR_SCHEDULES]

    entry, udp_client, host, port = _get_entry_and_client_from_device_id(hass, device_id)
    min_power, max_power = _get_power_limits(entry)

    # Pause polling once for all schedule commands
    await udp_client.pause_polling(host)
    
    try:
        for schedule in schedules:
            schedule_slot = schedule[ATTR_SCHEDULE_SLOT]
            start_time_raw = schedule[ATTR_START_TIME]
            end_time_raw = schedule[ATTR_END_TIME]
            _validate_time_range(start_time_raw, end_time_raw)

            start_time_str = _parse_time_string(start_time_raw)
            end_time_str = _parse_time_string(end_time_raw)
            power = schedule.get(ATTR_POWER, 0)
            if power < min_power or power > max_power:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="power_out_of_range",
                    translation_placeholders={
                        "requested": power,
                        "min": min_power,
                        "max": max_power,
                    },
                )
            days = schedule.get(ATTR_DAYS, ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
            enable = schedule.get(ATTR_ENABLE, True)

            # Calculate week_set bitmask
            week_set = _calculate_week_set(days)

            config = {
                "mode": API_MODE_MANUAL,
                "manual_cfg": {
                    "time_num": schedule_slot,
                    "start_time": start_time_str,
                    "end_time": end_time_str,
                    "week_set": week_set,
                    "power": power,
                    "enable": 1 if enable else 0,
                },
            }

            await _send_mode_command(udp_client, host, port, config, pause_polling=False)

            _LOGGER.debug(
                "Set manual schedule slot %d: %s-%s, power=%dW, days=%s, enabled=%s for device %s",
                schedule_slot,
                start_time_str,
                end_time_str,
                power,
                days,
                enable,
                device_id,
            )
    finally:
        await udp_client.resume_polling(host)

    # Refresh coordinator
    await entry.runtime_data.coordinator.async_request_refresh()

    _LOGGER.info(
        "Set %d manual schedules for device %s",
        len(schedules),
        device_id,
    )


async def async_request_data_sync(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle request_data_sync service call."""
    device_id = call.data.get(ATTR_DEVICE_ID)

    if device_id:
        # Refresh specific device
        entry, _, _, _ = _get_entry_and_client_from_device_id(hass, device_id)
        await entry.runtime_data.coordinator.async_request_refresh()
        _LOGGER.info("Requested data sync for device %s", device_id)
    else:
        # Refresh all Marstek devices
        refreshed = 0
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state == ConfigEntryState.LOADED and hasattr(entry, "runtime_data"):
                await entry.runtime_data.coordinator.async_request_refresh()
                refreshed += 1
        _LOGGER.info("Requested data sync for %d Marstek devices", refreshed)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up Marstek services.
    
    Services are registered once globally (idempotent registration).
    """
    async def handle_set_passive_mode(call: ServiceCall) -> None:
        """Handle the set_passive_mode service call."""
        await async_set_passive_mode(hass, call)

    async def handle_set_manual_schedule(call: ServiceCall) -> None:
        """Handle the set_manual_schedule service call."""
        await async_set_manual_schedule(hass, call)

    async def handle_clear_manual_schedules(call: ServiceCall) -> None:
        """Handle the clear_manual_schedules service call."""
        await async_clear_manual_schedules(hass, call)

    async def handle_set_manual_schedules(call: ServiceCall) -> None:
        """Handle the set_manual_schedules service call."""
        await async_set_manual_schedules(hass, call)

    async def handle_request_data_sync(call: ServiceCall) -> None:
        """Handle the request_data_sync service call."""
        await async_request_data_sync(hass, call)

    if not hass.services.has_service(DOMAIN, SERVICE_SET_PASSIVE_MODE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_PASSIVE_MODE,
            handle_set_passive_mode,
            schema=SERVICE_SET_PASSIVE_MODE_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SET_MANUAL_SCHEDULE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_MANUAL_SCHEDULE,
            handle_set_manual_schedule,
            schema=SERVICE_SET_MANUAL_SCHEDULE_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_CLEAR_MANUAL_SCHEDULES):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CLEAR_MANUAL_SCHEDULES,
            handle_clear_manual_schedules,
            schema=SERVICE_CLEAR_MANUAL_SCHEDULES_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SET_MANUAL_SCHEDULES):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_MANUAL_SCHEDULES,
            handle_set_manual_schedules,
            schema=SERVICE_SET_MANUAL_SCHEDULES_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_REQUEST_DATA_SYNC):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REQUEST_DATA_SYNC,
            handle_request_data_sync,
            schema=SERVICE_REQUEST_DATA_SYNC_SCHEMA,
        )


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload Marstek services."""
    for service_name in (
        SERVICE_SET_PASSIVE_MODE,
        SERVICE_SET_MANUAL_SCHEDULE,
        SERVICE_SET_MANUAL_SCHEDULES,
        SERVICE_CLEAR_MANUAL_SCHEDULES,
        SERVICE_REQUEST_DATA_SYNC,
    ):
        if hass.services.has_service(DOMAIN, service_name):
            hass.services.async_remove(DOMAIN, service_name)
