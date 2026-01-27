"""The Marstek integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from .pymarstek import MarstekUDPClient, get_es_mode

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DATA_UDP_CLIENT, DEFAULT_UDP_PORT, DOMAIN, PLATFORMS
from .coordinator import MarstekDataUpdateCoordinator
from .scanner import MarstekScanner
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class MarstekRuntimeData:
    """Runtime data for Marstek integration."""

    coordinator: MarstekDataUpdateCoordinator
    device_info: dict[str, Any]


type MarstekConfigEntry = ConfigEntry[MarstekRuntimeData]


def _get_shared_udp_client(hass: HomeAssistant) -> MarstekUDPClient | None:
    """Get the shared UDP client if it exists."""
    return hass.data.get(DOMAIN, {}).get(DATA_UDP_CLIENT)


async def _get_or_create_shared_udp_client(hass: HomeAssistant) -> MarstekUDPClient:
    """Get existing shared UDP client or create a new one.
    
    All Marstek config entries share a single UDP client to avoid port conflicts.
    Multiple sockets bound to the same port with SO_REUSEADDR causes response
    routing issues where responses go to the wrong socket.
    """
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    
    if DATA_UDP_CLIENT not in hass.data[DOMAIN]:
        _LOGGER.debug("Creating shared UDP client for Marstek integration")
        udp_client = MarstekUDPClient()
        await udp_client.async_setup()
        hass.data[DOMAIN][DATA_UDP_CLIENT] = udp_client
    
    return hass.data[DOMAIN][DATA_UDP_CLIENT]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Marstek component."""
    # Initialize scanner (only once, regardless of number of config entries)
    # Scanner will detect IP changes and update config entries via config flow
    scanner = MarstekScanner.async_get(hass)
    await scanner.async_setup()

    # Register services
    await async_setup_services(hass)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: MarstekConfigEntry) -> bool:
    """Set up Marstek from a config entry."""
    _LOGGER.info("Setting up Marstek config entry: %s", entry.title)

    # Use shared UDP client to avoid port conflicts between multiple devices
    udp_client = await _get_or_create_shared_udp_client(hass)

    stored_ip = entry.data[CONF_HOST]
    stored_port = entry.data.get(CONF_PORT, DEFAULT_UDP_PORT)
    # Only use BLE-MAC for device identification (user feedback)
    stored_ble_mac = entry.data.get("ble_mac")

    _LOGGER.info(
        "Starting setup: attempting to connect to device at IP %s (BLE-MAC: %s)",
        stored_ip,
        stored_ble_mac or "unknown",
    )

    # Try to connect with stored IP (mik-laj feedback)
    # If we have an IP address in the configuration, we should always connect to that IP
    # Discovery is handled by Scanner, not here
    try:
        _LOGGER.info("Attempting connection to %s:%s", stored_ip, stored_port)
        await udp_client.send_request(
            get_es_mode(0),
            stored_ip,
            stored_port,
            timeout=5.0,  # Increased timeout for initial connection
        )
        # Connection successful - device is at the configured IP
        # Use device info from config_entry (saved during config flow)
        _LOGGER.info(
            "Connection successful to device at %s - using config_entry data",
            stored_ip,
        )
    except (TimeoutError, OSError, ValueError) as ex:
        # Connection failed - device IP may have changed
        # Scanner will detect IP changes and update config entry via config flow
        # Raise ConfigEntryNotReady to allow Home Assistant to retry after Scanner updates IP
        # Note: Don't cleanup shared UDP client - other entries may be using it
        error_type = type(ex).__name__
        _LOGGER.warning(
            "Unable to connect to device at %s (%s: %s). "
            "Scanner will detect IP changes automatically. "
            "Home Assistant will retry setup periodically.",
            stored_ip,
            error_type,
            str(ex),
        )
        raise ConfigEntryNotReady(
            f"Unable to connect to device at {stored_ip} ({error_type}: {ex}). "
            "Scanner will detect IP changes and update configuration automatically. "
            "Home Assistant will retry setup periodically."
        ) from ex

    # Use device info from config_entry (saved during config flow)
    device_info_dict = {
        "ip": stored_ip,
        "port": stored_port,
        "mac": entry.data.get("mac", ""),
        "device_type": entry.data.get("device_type", "Unknown"),
        "version": entry.data.get("version", 0),
        "wifi_name": entry.data.get("wifi_name", ""),
        "wifi_mac": entry.data.get("wifi_mac", ""),
        "ble_mac": entry.data.get("ble_mac", ""),
    }

    # Create coordinator in __init__.py (mik-laj feedback)
    # Use is_initial_setup=True for faster API request delays during first data fetch
    coordinator = MarstekDataUpdateCoordinator(
        hass,
        entry,
        udp_client,
        device_info_dict["ip"],
        device_info_dict.get("port", stored_port),
        is_initial_setup=True,
    )
    
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        # First refresh failed - raise ConfigEntryNotReady with clear message
        # Note: Don't cleanup shared UDP client - other entries may be using it
        
        # Get details from coordinator tracking if available
        failures = getattr(coordinator, "consecutive_failures", 0)
        last_attempt = getattr(coordinator, "last_update_attempt_time", None)
        
        _LOGGER.warning(
            "Initial data fetch failed for %s after %d attempt(s) (last attempt: %s): %s",
            stored_ip,
            failures,
            last_attempt.isoformat() if last_attempt else "unknown",
            err,
        )
        raise ConfigEntryNotReady(
            f"Initial data fetch failed for {stored_ip}: {err}. "
            "The device responded to connection check but failed to return status data. "
            "This may be temporary - Home Assistant will retry automatically."
        ) from err
    
    # Reset initial setup flag - subsequent polls use normal configured delays
    coordinator.finish_initial_setup()

    # Store coordinator and device_info in runtime_data
    # Note: UDP client is shared via hass.data[DOMAIN][DATA_UDP_CLIENT]
    entry.runtime_data = MarstekRuntimeData(
        coordinator=coordinator,
        device_info=device_info_dict,
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: MarstekConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Marstek config entry: %s", entry.title)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Check if this is the last LOADED config entry
    # (unloaded entries still exist in registry with NOT_LOADED state)
    remaining_entries = [
        e for e in hass.config_entries.async_entries(DOMAIN) 
        if e.entry_id != entry.entry_id and e.state == ConfigEntryState.LOADED
    ]
    
    if not remaining_entries:
        # Last entry - cleanup shared UDP client and remove domain data
        udp_client = _get_shared_udp_client(hass)
        if udp_client:
            await udp_client.async_cleanup()
        
        # Reset scanner singleton to ensure clean state on reload
        MarstekScanner.async_reset()
        
        # Remove domain data entirely when last entry is unloaded
        hass.data.pop(DOMAIN, None)
        
        await async_unload_services(hass)

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: MarstekConfigEntry) -> None:
    """Handle options updates by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)
