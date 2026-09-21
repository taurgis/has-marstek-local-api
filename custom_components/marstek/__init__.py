"""The Marstek integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import (
    BAT_STATUS_KEYS,
    DEFAULT_UDP_PORT,
    DOMAIN,
    EM_STATUS_KEYS,
    PLATFORMS,
)
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
from .helpers.domain_data import MARSTEK_DATA, peek_domain_data
from .helpers.flow_helpers import get_unique_id_from_device_info
from .helpers.number_descriptions import NUMBER_ENTITIES
from .helpers.switch_descriptions import SWITCH_ENTITIES
from .helpers.udp_clients import (
    ACTIVE_ENTRY_STATES,
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


@dataclass(frozen=True, kw_only=True, slots=True)
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


def _meter_channel_issue_id(entry: ConfigEntry) -> str:
    """Build the shared meter-channel warning id for a config entry."""
    return f"shared_meter_udp_channel_{entry.entry_id}"


def _sync_meter_channel_issue(
    hass: HomeAssistant, entry: ConfigEntry, profile: FirmwareProfile
) -> None:
    """Warn when Open API polling can cost the device its own meter samples."""
    issue_id = _meter_channel_issue_id(entry)
    issue_registry = ir.async_get(hass)
    if not profile.shared_meter_udp_channel:
        if issue_registry.async_get_issue(DOMAIN, issue_id):
            issue_registry.async_delete(DOMAIN, issue_id)
        return

    firmware = str(profile.firmware_version) if profile.firmware_version is not None else "unknown"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="shared_meter_udp_channel",
        translation_placeholders={
            "family": profile.family.value,
            "firmware": firmware,
        },
        learn_more_url="https://github.com/taurgis/has-marstek-local-api/issues/82",
    )


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

    firmware = str(profile.firmware_version) if profile.firmware_version is not None else "unknown"
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


def _clear_meter_channel_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear the shared meter-channel warning if present."""
    issue_registry = ir.async_get(hass)
    issue_id = _meter_channel_issue_id(entry)
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
        _LOGGER.debug("Skipping capability entity cleanup; device identifier is missing")
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

    def _remove_keys(keys: frozenset[str]) -> None:
        for key in keys:
            for platform in (Platform.SENSOR, Platform.BINARY_SENSOR):
                unique_id = f"{device_identifier}_{key}"
                entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
                if entity_id is not None:
                    registry.async_remove(entity_id)

    # Firmware that is not an Open API meter client never answers
    # EM.GetStatus, so the sensor platform stops adding these. Without the
    # removal an upgrading install keeps the registry entry, and HA restores
    # it as permanently unavailable instead of dropping it.
    if not profile.supports_em_status:
        _remove_keys(EM_STATUS_KEYS)

    if not profile.openapi_reset_prone:
        return

    _remove_keys(BAT_STATUS_KEYS)


async def _async_cleanup_last_entry(hass: HomeAssistant) -> None:
    """Clean up shared resources when the last entry unloads."""
    await async_cleanup_all_udp_clients(hass)

    # Stop scanner before resetting singleton to ensure clean state on reload
    scanner = MarstekScanner.async_get(hass)
    await scanner.async_unload()
    MarstekScanner.async_reset()

    # Remove domain data entirely when last entry is unloaded
    hass.data.pop(MARSTEK_DATA, None)


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
            stale_client = acquire_udp_client_lease(hass, owner, effective_bind_port)
            udp_client = existing
        else:
            _LOGGER.debug(
                "Creating UDP client for Marstek Open API port (bind_port=%s)",
                effective_bind_port,
            )
            udp_client = MarstekUDPClient(port=port, bind_port=effective_bind_port)
            await udp_client.async_setup()
            store_udp_client(hass, effective_bind_port, udp_client)
            stale_client = acquire_udp_client_lease(hass, owner, effective_bind_port)
    if stale_client is not None:
        _LOGGER.debug("Closing unused Open API UDP client after bind-port change")
        await stale_client.async_cleanup()
    return udp_client


def _connection_not_ready(
    hass: HomeAssistant, entry: ConfigEntry, host: str, err: Exception
) -> ConfigEntryNotReady:
    """Build the setup-retry error for a device that did not answer."""
    error_type = type(err).__name__
    _LOGGER.debug(
        "Unable to connect to device at %s (%s: %s). "
        "Scanner will detect IP changes automatically. "
        "Home Assistant will retry setup periodically.",
        host,
        error_type,
        err,
    )
    _create_connection_issue(hass, entry, host, str(err))
    return ConfigEntryNotReady(
        translation_domain=DOMAIN,
        translation_key="setup_cannot_connect",
        translation_placeholders={"host": host, "error": f"{error_type}: {err}"},
    )


async def _async_verify_device_connection(
    hass: HomeAssistant,
    entry: ConfigEntry,
    udp_client: MarstekUDPClient,
    host: str,
    port: int,
) -> None:
    """Verify device connectivity using a lightweight API request."""
    _LOGGER.debug("Attempting connection to %s:%s", host, port)
    try:
        parsed = await udp_client.fetch_es_mode(
            host,
            port,
            timeout=5.0,
        )
    except (TimeoutError, OSError, ValueError) as ex:
        raise _connection_not_ready(hass, entry, host, ex) from ex

    if parsed is None:
        no_reply = TimeoutError("ES.GetMode returned no usable result")
        raise _connection_not_ready(hass, entry, host, no_reply) from no_reply

    _LOGGER.debug(
        "Connection successful to device at %s - using config_entry data",
        host,
    )


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
            translation_domain=DOMAIN,
            translation_key="setup_no_data",
            translation_placeholders={"host": device_info["ip"], "error": str(err)},
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
    _LOGGER.debug("Setting up Marstek config entry: %s", entry.title)

    if is_unsupported_venus_e2(entry.data.get("device_type")):
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="unsupported_device",
        )

    stored_ip = entry.data[CONF_HOST]
    stored_port = int(entry.data.get(CONF_PORT, DEFAULT_UDP_PORT))
    # One UDP client per Open API port; devices on the same port share it
    udp_client = await _get_or_create_udp_client(
        hass, port=stored_port, host=stored_ip, owner=entry.entry_id
    )

    try:
        return await _async_setup_entry_with_client(hass, entry, udp_client, stored_ip, stored_port)
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

    _LOGGER.debug(
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
    _sync_meter_channel_issue(hass, entry, profile)
    udp_client.set_openapi_reset_prone(stored_ip, profile.openapi_reset_prone, owner=entry.entry_id)
    udp_client.set_openapi_retransmit_safe(stored_ip, profile.openapi_wifi_retransmit_safe)

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
    _async_remove_unsupported_capability_entities(hass, device_info_dict, coordinator.profile)

    # Clear any prior connection issue after successful setup
    _clear_connection_issue(hass, entry)
    _sync_openapi_reset_issue(hass, entry, coordinator.profile)
    _sync_meter_channel_issue(hass, entry, coordinator.profile)

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


def _clear_entry_reset_prone_flag(hass: HomeAssistant, entry: ConfigEntry) -> None:
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


async def _async_release_entry_resources(hass: HomeAssistant, entry: MarstekConfigEntry) -> None:
    """Drop this entry's UDP lease and tear down shared state if it was last.

    Unload and remove both end here: releasing the lease can free the pooled
    socket, and the domain-wide services and scanner only make sense while at
    least one entry is still alive.
    """
    await async_release_udp_client_for_entry(hass, entry)
    if domain_has_udp_leases(hass):
        return
    if any(
        other.entry_id != entry.entry_id and other.state in ACTIVE_ENTRY_STATES
        for other in hass.config_entries.async_entries(DOMAIN)
    ):
        return
    await _async_cleanup_last_entry(hass)


async def async_unload_entry(hass: HomeAssistant, entry: MarstekConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Marstek config entry: %s", entry.title)

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
    _clear_meter_channel_issue(hass, entry)

    await _async_release_entry_resources(hass, entry)

    return True


async def async_remove_entry(hass: HomeAssistant, entry: MarstekConfigEntry) -> None:
    """Remove a config entry and clean up stale devices."""
    _clear_entry_reset_prone_flag(hass, entry)

    # Clear any remaining repair issues
    _clear_connection_issue(hass, entry)
    _clear_openapi_reset_issue(hass, entry)
    _clear_meter_channel_issue(hass, entry)

    await _async_release_entry_resources(hass, entry)

    device_identifier = get_unique_id_from_device_info(entry.data)
    if device_identifier is None:
        return

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


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow deleting a device this entry no longer represents.

    Marstek firmware can hand out a new identity MAC after a mainboard swap
    or a factory reset, which leaves the old device stranded in the registry
    with no way to clear it. Home Assistant only offers the delete button
    when the integration implements this hook, so offer it for any device
    that is not the one the entry currently talks to. Refusing the live
    device is deliberate: deleting it would only make setup recreate it.

    https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/stale-devices
    """
    current_identifier = get_unique_id_from_device_info(config_entry.data)
    if current_identifier is None:
        return True
    return (DOMAIN, current_identifier) not in device_entry.identifiers


async def _async_update_listener(hass: HomeAssistant, entry: MarstekConfigEntry) -> None:
    """Handle options updates by reloading the entry."""
    data = peek_domain_data(hass)
    if data is not None and entry.entry_id in data.suppress_reloads:
        data.suppress_reloads.discard(entry.entry_id)
        _LOGGER.debug("Skipping reload for entry %s (metadata-only update)", entry.entry_id)
        return
    await hass.config_entries.async_reload(entry.entry_id)
