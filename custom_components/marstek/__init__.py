"""The Marstek integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import BAT_STATUS_KEYS, DATA_SUPPRESS_RELOADS, DEFAULT_UDP_PORT, DOMAIN, PLATFORMS
from .coordinator import MarstekDataUpdateCoordinator
from .device_info import get_device_identifier
from .firmware_profile import (
    FirmwareProfile,
    is_unsupported_venus_e2,
    resolve_firmware_profile,
)
from .helpers.device_lookup import (
    async_lookup_device_by_identifier,
    iter_device_config_entry_ids,
)
from .helpers.number_descriptions import NUMBER_ENTITIES
from .helpers.switch_descriptions import SWITCH_ENTITIES
from .helpers.udp_clients import (
    acquire_udp_client_lease,
    async_cleanup_all_udp_clients,
    async_release_udp_client_for_entry,
    bind_port_for_host,
    clear_reset_prone_owner_from_pool,
    discovery_lock,
    domain_has_udp_leases,
    get_udp_client,
    get_udp_client_for_entry,
    store_udp_client,
    udp_client_lock,
)
from .pymarstek import MarstekUDPClient
from .scanner import MarstekScanner
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class MarstekRuntimeData:
    """Runtime data for Marstek integration."""

    coordinator: MarstekDataUpdateCoordinator
    device_info: dict[str, Any]


type MarstekConfigEntry = ConfigEntry[MarstekRuntimeData]


def _issue_id_for_entry(entry: ConfigEntry) -> str:
    """Build issue id for a config entry."""
    return f"cannot_connect_{entry.entry_id}"


def _openapi_reset_issue_id(entry: ConfigEntry) -> str:
    """Build the Local API firmware-reset warning id for a config entry."""
    return f"openapi_reset_prone_{entry.entry_id}"


def _sync_openapi_reset_issue(
    hass: HomeAssistant, entry: ConfigEntry, profile: FirmwareProfile
) -> None:
    """Warn when Control firmware is known to reset Open API under polling."""
    issue_id = _openapi_reset_issue_id(entry)
    issue_registry = ir.async_get(hass)
    if not profile.openapi_reset_prone:
        if issue_registry.async_get_issue(DOMAIN, issue_id):
            issue_registry.async_delete(DOMAIN, issue_id)
        return

    firmware = (
        str(profile.firmware_version)
        if profile.firmware_version is not None
        else "unknown"
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="openapi_reset_prone",
        translation_placeholders={
            "family": profile.family.value,
            "firmware": firmware,
        },
        learn_more_url="https://github.com/taurgis/has-marstek-local-api/issues/15",
    )


def _create_connection_issue(
    hass: HomeAssistant, entry: ConfigEntry, host: str, error: str
) -> None:
    """Create a fixable connection issue for the entry."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id_for_entry(entry),
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="cannot_connect",
        translation_placeholders={"host": host, "error": error},
        data={"entry_id": entry.entry_id},
    )


def _clear_connection_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear a connection issue for the entry if present."""
    issue_registry = ir.async_get(hass)
    issue_id = _issue_id_for_entry(entry)
    if issue_registry.async_get_issue(DOMAIN, issue_id):
        issue_registry.async_delete(DOMAIN, issue_id)


def _clear_openapi_reset_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear the Local API firmware-reset warning if present."""
    issue_registry = ir.async_get(hass)
    issue_id = _openapi_reset_issue_id(entry)
    if issue_registry.async_get_issue(DOMAIN, issue_id):
        issue_registry.async_delete(DOMAIN, issue_id)


def _async_remove_unsupported_capability_entities(
    hass: HomeAssistant,
    device_info: dict[str, Any],
    profile: FirmwareProfile,
) -> None:
    """Remove registry entries for capability-gated entities the profile dropped."""
    try:
        device_identifier = get_device_identifier(device_info)
    except ValueError:
        _LOGGER.debug(
            "Skipping capability entity cleanup; device identifier is missing"
        )
        return

    registry = er.async_get(hass)
    gated_entities: tuple[
        tuple[Platform, tuple[Any, ...]],
        ...,
    ] = (
        (Platform.NUMBER, NUMBER_ENTITIES),
        (Platform.SWITCH, SWITCH_ENTITIES),
    )
    for platform, descriptions in gated_entities:
        for description in descriptions:
            if description.supported_fn(profile):
                continue
            unique_id = f"{device_identifier}_{description.key}"
            entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
            if entity_id is not None:
                registry.async_remove(entity_id)

    if not profile.openapi_reset_prone:
        return

    for key in BAT_STATUS_KEYS:
        for platform in (Platform.SENSOR, Platform.BINARY_SENSOR):
            unique_id = f"{device_identifier}_{key}"
            entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
            if entity_id is not None:
                registry.async_remove(entity_id)


async def _async_cleanup_last_entry(hass: HomeAssistant) -> None:
    """Clean up shared resources when the last entry unloads."""
    await async_cleanup_all_udp_clients(hass)

    # Stop scanner before resetting singleton to ensure clean state on reload
    scanner = MarstekScanner.async_get(hass)
    await scanner.async_unload()
    MarstekScanner.async_reset()

    # Remove domain data entirely when last entry is unloaded
    hass.data.pop(DOMAIN, None)


async def _get_or_create_udp_client(
    hass: HomeAssistant, *, port: int, host: str, owner: str
) -> MarstekUDPClient:
    """Get or create the UDP client bound to this device's Open API port.

    Firmware replies to the device listen port rather than an ephemeral
    source port, so each unique listen port needs its own socket. Entries
    that share a port reuse one client so two sockets do not steal replies
    from each other.
    """
    effective_bind_port = bind_port_for_host(host, port)
    stale_client: MarstekUDPClient | None = None
    async with discovery_lock(hass), udp_client_lock(hass):
        existing = get_udp_client(hass, effective_bind_port)
        if existing is not None:
            stale_client = acquire_udp_client_lease(
                hass, owner, effective_bind_port
            )
            udp_client = existing
        else:
            _LOGGER.debug(
                "Creating UDP client for Marstek Open API port (bind_port=%s)",
                effective_bind_port,
            )
            udp_client = MarstekUDPClient(port=port, bind_port=effective_bind_port)
            await udp_client.async_setup()
            store_udp_client(hass, effective_bind_port, udp_client)
            stale_client = acquire_udp_client_lease(
                hass, owner, effective_bind_port
            )
    if stale_client is not None:
        _LOGGER.debug(
            "Closing unused Open API UDP client after bind-port change"
        )
        await stale_client.async_cleanup()
    return udp_client


async def _async_verify_device_connection(
    hass: HomeAssistant,
    entry: ConfigEntry,
    udp_client: MarstekUDPClient,
    host: str,
    port: int,
) -> None:
    """Verify device connectivity using a lightweight API request."""
    try:
        _LOGGER.info("Attempting connection to %s:%s", host, port)
        parsed = await udp_client.fetch_es_mode(
            host,
            port,
            timeout=5.0,
        )
        if parsed is None:
            raise TimeoutError("ES.GetMode returned no usable result")
        _LOGGER.info(
            "Connection successful to device at %s - using config_entry data",
            host,
        )
    except (TimeoutError, OSError, ValueError) as ex:
        error_type = type(ex).__name__
        _LOGGER.debug(
            "Unable to connect to device at %s (%s: %s). "
            "Scanner will detect IP changes automatically. "
            "Home Assistant will retry setup periodically.",
            host,
            error_type,
            str(ex),
        )
        _create_connection_issue(hass, entry, host, str(ex))
        raise ConfigEntryNotReady(
            f"Unable to connect to device at {host} ({error_type}: {ex}). "
            "Scanner will detect IP changes and update configuration automatically. "
            "Home Assistant will retry setup periodically."
        ) from ex


def _build_device_info_dict(
    entry: ConfigEntry,
    host: str,
    port: int,
) -> dict[str, Any]:
    """Build device info dictionary from config entry data."""
    return {
        "ip": host,
        "port": port,
        "mac": entry.data.get("mac", ""),
        "device_type": entry.data.get("device_type", "Unknown"),
        "version": entry.data.get("version"),
        "wifi_name": entry.data.get("wifi_name", ""),
        "wifi_mac": entry.data.get("wifi_mac", ""),
        "ble_mac": entry.data.get("ble_mac", ""),
    }


async def _async_setup_coordinator(
    hass: HomeAssistant,
    entry: MarstekConfigEntry,
    udp_client: MarstekUDPClient,
    device_info: dict[str, Any],
) -> MarstekDataUpdateCoordinator:
    """Create and refresh the coordinator, raising ConfigEntryNotReady on failure."""
    coordinator = MarstekDataUpdateCoordinator(
        hass,
        entry,
        udp_client,
        device_info["ip"],
        device_info.get("port", DEFAULT_UDP_PORT),
        is_initial_setup=True,
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        failures = getattr(coordinator, "consecutive_failures", 0)
        last_attempt = getattr(coordinator, "last_update_attempt_time", None)

        _LOGGER.debug(
            "Initial data fetch failed for %s after %d attempt(s) (last attempt: %s): %s",
            device_info["ip"],
            failures,
            last_attempt.isoformat() if last_attempt else "unknown",
            err,
        )
        _create_connection_issue(hass, entry, device_info["ip"], str(err))
        raise ConfigEntryNotReady(
            f"Initial data fetch failed for {device_info['ip']}: {err}. "
            "The device responded to connection check but failed to return status data. "
            "This may be temporary - Home Assistant will retry automatically."
        ) from err

    coordinator.finish_initial_setup()
    return coordinator


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Marstek component."""
    # Register services
    await async_setup_services(hass)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: MarstekConfigEntry) -> bool:
    """Set up Marstek from a config entry."""
    _LOGGER.info("Setting up Marstek config entry: %s", entry.title)

    if is_unsupported_venus_e2(entry.data.get("device_type")):
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="unsupported_device",
        )

    await async_setup_services(hass)

    stored_ip = entry.data[CONF_HOST]
    stored_port = int(entry.data.get(CONF_PORT, DEFAULT_UDP_PORT))
    # One UDP client per Open API port; devices on the same port share it
    udp_client = await _get_or_create_udp_client(
        hass, port=stored_port, host=stored_ip, owner=entry.entry_id
    )

    try:
        return await _async_setup_entry_with_client(
            hass, entry, udp_client, stored_ip, stored_port
        )
    except ConfigEntryNotReady:
        raise
    except BaseException:
        await async_release_udp_client_for_entry(hass, entry)
        raise


async def _async_setup_entry_with_client(
    hass: HomeAssistant,
    entry: MarstekConfigEntry,
    udp_client: MarstekUDPClient,
    stored_ip: str,
    stored_port: int,
) -> bool:
    """Finish setup after the UDP client lease is held."""
    stored_ble_mac = entry.data.get("ble_mac")
    stored_wifi_mac = entry.data.get("wifi_mac")

    _LOGGER.info(
        "Starting setup: attempting to connect to device at IP %s (BLE-MAC: %s)",
        stored_ip,
        stored_ble_mac or stored_wifi_mac or "unknown",
    )

    device_info_dict = _build_device_info_dict(entry, stored_ip, stored_port)
    profile = resolve_firmware_profile(
        device_info_dict.get("device_type"),
        device_info_dict.get("version"),
    )
    _sync_openapi_reset_issue(hass, entry, profile)
    udp_client.set_openapi_reset_prone(
        stored_ip, profile.openapi_reset_prone, owner=entry.entry_id
    )
    udp_client.set_openapi_retransmit_safe(
        stored_ip, profile.openapi_wifi_retransmit_safe
    )

    # Scanner starts after the pooled client exists so an immediate scan can
    # pause this listener instead of racing a probe on a missing socket.
    # It still starts before the first unicast probe (needed for IP recovery
    # on ConfigEntryNotReady).
    scanner = MarstekScanner.async_get(hass)
    await scanner.async_setup()

    # Try to connect with stored IP (mik-laj feedback)
    # If we have an IP address in the configuration, we should always connect to that IP
    # Discovery is handled by Scanner, not here
    await _async_verify_device_connection(
        hass,
        entry,
        udp_client,
        stored_ip,
        stored_port,
    )

    # Create coordinator in __init__.py (mik-laj feedback)
    # Use is_initial_setup=True for faster API request delays during first data fetch
    coordinator = await _async_setup_coordinator(
        hass,
        entry,
        udp_client,
        device_info_dict,
    )

    # Drop stale capability-gated registry entries before platforms re-add
    # the entities the current firmware profile still supports.
    _async_remove_unsupported_capability_entities(
        hass, device_info_dict, coordinator.profile
    )

    # Clear any prior connection issue after successful setup
    _clear_connection_issue(hass, entry)
    _sync_openapi_reset_issue(hass, entry, coordinator.profile)

    # Store coordinator and device_info in runtime_data.
    # UDP clients are pooled per Open API bind port in hass.data.
    entry.runtime_data = MarstekRuntimeData(
        coordinator=coordinator,
        device_info=device_info_dict,
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _entry_coordinator(entry: ConfigEntry) -> MarstekDataUpdateCoordinator | None:
    """Return the runtime coordinator for a config entry, if setup finished."""
    runtime_data = getattr(entry, "runtime_data", None)
    coordinator = getattr(runtime_data, "coordinator", None)
    if isinstance(coordinator, MarstekDataUpdateCoordinator):
        return coordinator
    return None


def _clear_entry_reset_prone_flag(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Clear the UDP reset-prone mark using every IP this entry may have used."""
    coordinator = _entry_coordinator(entry)
    if coordinator is not None:
        coordinator.clear_openapi_reset_mark()
    host = entry.data.get(CONF_HOST)
    udp_client = get_udp_client_for_entry(hass, entry)
    if isinstance(host, str) and udp_client is not None:
        udp_client.clear_openapi_reset_prone(host, owner=entry.entry_id)
        udp_client.set_openapi_retransmit_safe(host, False)
    clear_reset_prone_owner_from_pool(hass, entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MarstekConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Marstek config entry: %s", entry.title)

    coordinator = _entry_coordinator(entry)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    if coordinator is not None:
        coordinator.clear_openapi_reset_mark()
    else:
        _clear_entry_reset_prone_flag(hass, entry)

    # Clear any repair issues tied to this entry
    _clear_connection_issue(hass, entry)
    _clear_openapi_reset_issue(hass, entry)

    await async_release_udp_client_for_entry(hass, entry)
    if not domain_has_udp_leases(hass):
        remaining_entries = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id
            and e.state
            in (
                ConfigEntryState.LOADED,
                ConfigEntryState.SETUP_RETRY,
                ConfigEntryState.SETUP_IN_PROGRESS,
            )
        ]
        if not remaining_entries:
            await _async_cleanup_last_entry(hass)

    return True


async def async_remove_entry(hass: HomeAssistant, entry: MarstekConfigEntry) -> None:
    """Remove a config entry and clean up stale devices."""
    from homeassistant.helpers.device_registry import format_mac

    _clear_entry_reset_prone_flag(hass, entry)

    # Clear any remaining repair issues
    _clear_connection_issue(hass, entry)
    _clear_openapi_reset_issue(hass, entry)

    await async_release_udp_client_for_entry(hass, entry)
    if not domain_has_udp_leases(hass):
        remaining_active = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id
            and e.state
            in (
                ConfigEntryState.LOADED,
                ConfigEntryState.SETUP_RETRY,
                ConfigEntryState.SETUP_IN_PROGRESS,
            )
        ]
        if not remaining_active:
            await _async_cleanup_last_entry(hass)

    device_identifier_raw = (
        entry.data.get("ble_mac")
        or entry.data.get("mac")
        or entry.data.get("wifi_mac")
    )
    if not device_identifier_raw:
        return

    # Use format_mac for consistency with build_device_info
    device_identifier = format_mac(device_identifier_raw)

    device_registry = dr.async_get(hass)
    device = async_lookup_device_by_identifier(
        device_registry,
        (DOMAIN, device_identifier),
        config_entry_id=entry.entry_id,
    )
    if not device:
        return

    remaining_entries = set(iter_device_config_entry_ids(device)) - {entry.entry_id}
    if not remaining_entries:
        _LOGGER.info("Removing stale device registry entry: %s", device.name)
        device_registry.async_remove_device(device.id)


async def _async_update_listener(hass: HomeAssistant, entry: MarstekConfigEntry) -> None:
    """Handle options updates by reloading the entry."""
    suppress = hass.data.get(DOMAIN, {}).get(DATA_SUPPRESS_RELOADS)
    if suppress and entry.entry_id in suppress:
        suppress.discard(entry.entry_id)
        _LOGGER.debug(
            "Skipping reload for entry %s (metadata-only update)", entry.entry_id
        )
        return
    await hass.config_entries.async_reload(entry.entry_id)
