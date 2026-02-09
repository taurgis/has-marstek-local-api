"""Services for Marstek devices."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from .const import API_MODE_PASSIVE, DATA_UDP_CLIENT, DEFAULT_UDP_PORT, DOMAIN
from .helpers.service_helpers import (
    ATTR_DAYS,
    ATTR_DEVICE_ID,
    ATTR_DURATION,
    ATTR_ENABLE,
    ATTR_END_TIME,
    ATTR_POWER,
    ATTR_SCHEDULE_SLOT,
    ATTR_SCHEDULES,
    ATTR_START_TIME,
    DEFAULT_SCHEDULE_DAYS,
    SERVICE_CLEAR_MANUAL_SCHEDULES_SCHEMA,
    SERVICE_REQUEST_DATA_SYNC_SCHEMA,
    SERVICE_SET_MANUAL_SCHEDULE_SCHEMA,
    SERVICE_SET_MANUAL_SCHEDULES_SCHEMA,
    SERVICE_SET_PASSIVE_MODE_SCHEMA,
    build_manual_schedule_config,
)
from .helpers.service_retry import send_mode_command_with_retries
from .mode_config import build_manual_mode_config
from .power import validate_power_for_entry
from .pymarstek import MAX_TIME_SLOTS, MarstekUDPClient

if TYPE_CHECKING:
    from . import MarstekConfigEntry

_LOGGER = logging.getLogger(__name__)

# Service names
SERVICE_SET_PASSIVE_MODE = "set_passive_mode"
SERVICE_SET_MANUAL_SCHEDULE = "set_manual_schedule"
SERVICE_SET_MANUAL_SCHEDULES = "set_manual_schedules"
SERVICE_CLEAR_MANUAL_SCHEDULES = "clear_manual_schedules"
SERVICE_REQUEST_DATA_SYNC = "request_data_sync"


def _get_device_id_from_call(call: ServiceCall) -> str:
    """Extract device_id from service call data."""
    device_id = call.data.get(ATTR_DEVICE_ID)
    if not device_id:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_device_specified",
        )
    return str(device_id)


def _get_entry_and_client_from_device_id(
    hass: HomeAssistant, device_id: str
) -> tuple[MarstekConfigEntry, MarstekUDPClient, str, int]:
    """Get config entry and UDP client from device ID."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)

    if not device:
        raise ServiceValidationError(
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

    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="no_config_entry",
        translation_placeholders={"device_id": device_id},
    )


def _power_error(
    requested: int, min_power: int, max_power: int
) -> ServiceValidationError:
    """Build a power validation error for service calls."""
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="power_out_of_range",
        translation_placeholders={
            "requested": str(requested),
            "min": str(min_power),
            "max": str(max_power),
        },
    )


def _validate_power_for_device(power: int, entry: MarstekConfigEntry) -> None:
    """Validate requested power against device limits."""
    validate_power_for_entry(entry, power, _power_error)


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

    await send_mode_command_with_retries(
        udp_client,
        host,
        port,
        config,
        logger=_LOGGER,
    )

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

    # Normalize times to HH:MM and validate range
    config, start_time_str, end_time_str = build_manual_schedule_config(
        schedule_slot=schedule_slot,
        start_time_raw=start_time,
        end_time_raw=end_time,
        power=power,
        days=days,
        enable=enable,
    )

    await send_mode_command_with_retries(
        udp_client,
        host,
        port,
        config,
        logger=_LOGGER,
    )

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

    _LOGGER.info(
        "Clearing %d manual schedule slots for device %s...",
        MAX_TIME_SLOTS,
        device_id,
    )

    # Pause polling once for all 10 commands
    await udp_client.pause_polling(host)

    try:
        # Clear all 10 schedule slots by setting them to disabled
        for slot in range(MAX_TIME_SLOTS):
            config = build_manual_mode_config(
                power=0,
                enable=False,
                time_num=slot,
                start_time="00:00",
                end_time="00:00",
                week_set=0,
            )

            await send_mode_command_with_retries(
                udp_client,
                host,
                port,
                config,
                pause_polling=False,
                logger=_LOGGER,
            )
            _LOGGER.debug(
                "Cleared manual schedule slot %d/%d for device %s",
                slot + 1,
                MAX_TIME_SLOTS,
                device_id,
            )
    finally:
        await udp_client.resume_polling(host)

    # Refresh coordinator
    await entry.runtime_data.coordinator.async_request_refresh()

    _LOGGER.info("Cleared all manual schedules for device %s", device_id)


async def async_set_manual_schedules(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle set_manual_schedules service call (batch configuration).

    Polling is paused once for all schedule commands to avoid race conditions.
    """
    device_id = _get_device_id_from_call(call)
    schedules = call.data[ATTR_SCHEDULES]

    entry, udp_client, host, port = _get_entry_and_client_from_device_id(hass, device_id)
    # Pause polling once for all schedule commands
    await udp_client.pause_polling(host)

    try:
        for schedule in schedules:
            schedule_slot = schedule[ATTR_SCHEDULE_SLOT]
            start_time_raw = schedule[ATTR_START_TIME]
            end_time_raw = schedule[ATTR_END_TIME]
            power = schedule.get(ATTR_POWER, 0)
            days = schedule.get(ATTR_DAYS, list(DEFAULT_SCHEDULE_DAYS))
            enable = schedule.get(ATTR_ENABLE, True)
            config, start_time_str, end_time_str = build_manual_schedule_config(
                schedule_slot=schedule_slot,
                start_time_raw=start_time_raw,
                end_time_raw=end_time_raw,
                power=power,
                days=days,
                enable=enable,
            )
            _validate_power_for_device(power, entry)
            await send_mode_command_with_retries(
                udp_client,
                host,
                port,
                config,
                pause_polling=False,
                logger=_LOGGER,
            )

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
