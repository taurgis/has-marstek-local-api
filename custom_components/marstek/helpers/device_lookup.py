"""Resolve Home Assistant device IDs to Marstek config entries.

Home Assistant 2026.8 made devices belong to a single config entry and
deprecated ``DeviceEntry.config_entries`` in favor of
``DeviceEntry.config_entry_id``. Service handlers must accept the registry
ID from ``device_id()`` / the device selector, and they should still find
the device when YAML or templates pass a config-entry ID, MAC, entity ID,
or a truncated registry ID.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry, format_mac

from ..const import DOMAIN

_HEX_CHARS = frozenset("0123456789abcdef")
_MIN_PREFIX_LEN = 16
_HA_DEVICE_ID_LEN = 32


def normalize_device_id(value: Any) -> str:
    """Return a stripped device identifier string."""
    return str(value).strip()


def iter_device_config_entry_ids(device: DeviceEntry) -> list[str]:
    """Return config entry IDs for a device on HA 2024.x through 2026.8+.

    Prefers ``config_entry_id`` (HA 2026.8+) so callers do not trip the
    deprecated ``config_entries`` shim. Composite (pre-migration) devices
    still expose every split entry via ``config_entries``.
    """
    if getattr(device, "is_composite_device", False):
        return list(device.config_entries)

    config_entry_id = getattr(device, "config_entry_id", None)
    if isinstance(config_entry_id, str) and config_entry_id:
        return [config_entry_id]

    config_entries = getattr(device, "config_entries", None)
    if not config_entries:
        return []
    return [str(entry_id) for entry_id in config_entries]


def async_get_marstek_entry(
    hass: HomeAssistant,
    device: DeviceEntry,
    *,
    require_loaded: bool = True,
) -> ConfigEntry | None:
    """Return the Marstek config entry that owns ``device``."""
    for entry_id in iter_device_config_entry_ids(device):
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        if require_loaded and entry.state != ConfigEntryState.LOADED:
            continue
        return entry
    return None


def async_get_loaded_marstek_entry(
    hass: HomeAssistant, device: DeviceEntry
) -> ConfigEntry | None:
    """Return the loaded Marstek config entry that owns ``device``."""
    return async_get_marstek_entry(hass, device, require_loaded=True)


def async_find_marstek_device(
    hass: HomeAssistant, device_id: str
) -> DeviceEntry | None:
    """Find a Marstek device by registry ID or fallback identifier."""
    registry = dr.async_get(hass)
    target = normalize_device_id(device_id)
    if not target:
        return None

    device = registry.async_get(target)
    if device is not None:
        return device

    for resolver in (
        _resolve_from_config_entry_id,
        _resolve_from_entity_id,
        _resolve_from_mac,
        _resolve_from_truncated_id,
    ):
        resolved = resolver(hass, registry, target)
        if resolved is not None:
            return resolved
    return None


def async_resolve_marstek_device(
    hass: HomeAssistant, device_id: str
) -> DeviceEntry:
    """Resolve a service/action target to a Marstek device registry entry.

    Lookup order:
    1. Device registry ID (including restored HA 2026.8 composite IDs)
    2. Marstek config-entry ID
    3. Entity ID whose device is a Marstek battery
    4. BLE/Wi-Fi MAC identifier
    5. Unique truncated prefix of a Marstek device registry ID
    """
    target = normalize_device_id(device_id)
    if not target:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_device_specified",
        )

    device = async_find_marstek_device(hass, target)
    if device is not None:
        return device

    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="invalid_device",
        translation_placeholders={"device_id": target},
    )


def _marstek_devices(
    hass: HomeAssistant, registry: dr.DeviceRegistry
) -> list[DeviceEntry]:
    """Return devices owned by Marstek config entries."""
    devices: list[DeviceEntry] = []
    seen: set[str] = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            if device.id in seen:
                continue
            seen.add(device.id)
            devices.append(device)
    return devices


def _unique_device(devices: Iterable[DeviceEntry]) -> DeviceEntry | None:
    """Return the only device in ``devices``, otherwise None."""
    found: DeviceEntry | None = None
    seen: set[str] = set()
    for device in devices:
        if device.id in seen:
            continue
        seen.add(device.id)
        if found is not None:
            return None
        found = device
    return found


def _resolve_from_config_entry_id(
    hass: HomeAssistant, registry: dr.DeviceRegistry, target: str
) -> DeviceEntry | None:
    """Resolve a Marstek config-entry ID to its device."""
    entry = hass.config_entries.async_get_entry(target)
    if entry is None or entry.domain != DOMAIN:
        return None
    return _unique_device(dr.async_entries_for_config_entry(registry, entry.entry_id))


def _resolve_from_entity_id(
    hass: HomeAssistant, registry: dr.DeviceRegistry, target: str
) -> DeviceEntry | None:
    """Resolve a Marstek entity ID to its device."""
    if "." not in target:
        return None
    entity = er.async_get(hass).async_get(target)
    if entity is None or entity.platform != DOMAIN or entity.device_id is None:
        return None
    return registry.async_get(entity.device_id)


def _as_mac(value: str) -> str | None:
    """Return a formatted MAC when ``value`` looks like one."""
    formatted = format_mac(value)
    if len(formatted) != 17 or formatted.count(":") != 5:
        return None
    hex_part = formatted.replace(":", "")
    if len(hex_part) != 12 or any(char not in _HEX_CHARS for char in hex_part):
        return None
    return formatted


def _resolve_from_mac(
    hass: HomeAssistant, registry: dr.DeviceRegistry, target: str
) -> DeviceEntry | None:
    """Resolve a BLE or Wi-Fi MAC to a Marstek device."""
    mac = _as_mac(target)
    if mac is None:
        return None
    matches = [
        device
        for device in _marstek_devices(hass, registry)
        if (DOMAIN, mac) in device.identifiers
    ]
    return _unique_device(matches)


def _is_truncated_hex_id(value: str) -> bool:
    """Return whether ``value`` looks like a truncated HA device ID."""
    lowered = value.lower()
    return (
        _MIN_PREFIX_LEN <= len(lowered) < _HA_DEVICE_ID_LEN
        and all(char in _HEX_CHARS for char in lowered)
    )


def _resolve_from_truncated_id(
    hass: HomeAssistant, registry: dr.DeviceRegistry, target: str
) -> DeviceEntry | None:
    """Resolve a unique truncated prefix of a Marstek device registry ID."""
    if not _is_truncated_hex_id(target):
        return None
    prefix = target.lower()
    matches = [
        device
        for device in _marstek_devices(hass, registry)
        if device.id.startswith(prefix)
    ]
    return _unique_device(matches)


def require_loaded_marstek_entry(
    hass: HomeAssistant, device: DeviceEntry, device_id: str
) -> ConfigEntry:
    """Return the loaded Marstek entry for ``device`` or raise."""
    entry = async_get_loaded_marstek_entry(hass, device)
    if entry is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_config_entry",
            translation_placeholders={"device_id": device_id},
        )
    return entry
