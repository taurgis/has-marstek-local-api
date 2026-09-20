"""Config flow helper utilities."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import format_mac

# Preference order for a stable device identity: the BLE MAC is the unique id
# Marstek firmware keeps across IP and Wi-Fi changes. Anything reading a MAC
# out of a device dict or an entry must walk these in this order.
IDENTITY_MAC_KEYS: tuple[str, ...] = ("ble_mac", CONF_MAC, "wifi_mac")
# Home Assistant ``format_mac`` lowercases; it does not validate. Only a
# 6-octet hex MAC is a stable Marstek identity.
_FORMATTED_MAC = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")


def formatted_mac_or_none(value: Any) -> str | None:
    """Return a normalized MAC string, or None when *value* is not a MAC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        formatted = format_mac(value.strip())
    except (TypeError, ValueError):
        return None
    if _FORMATTED_MAC.fullmatch(formatted) is None:
        return None
    return formatted


def identity_macs_from_mapping(
    data: Mapping[str, Any],
    *,
    include_unique_id: str | None = None,
) -> set[str]:
    """Collect formatted BLE, Wi-Fi, and legacy MAC identities from a mapping."""
    macs: set[str] = set()
    for key in IDENTITY_MAC_KEYS:
        formatted = formatted_mac_or_none(data.get(key))
        if formatted is not None:
            macs.add(formatted)
    formatted_unique = formatted_mac_or_none(include_unique_id)
    if formatted_unique is not None:
        macs.add(formatted_unique)
    return macs


def identity_macs_from_entry(entry: config_entries.ConfigEntry) -> set[str]:
    """Collect every stable MAC stored on a config entry, including unique_id."""
    return identity_macs_from_mapping(entry.data, include_unique_id=entry.unique_id)


def identities_overlap(left: set[str], right: set[str]) -> bool:
    """Return True when two identity sets share a MAC."""
    return bool(left & right)


def collect_configured_macs(
    entries: list[config_entries.ConfigEntry],
) -> set[str]:
    """Collect formatted MAC addresses from existing entries."""
    configured_macs: set[str] = set()
    for entry in entries:
        configured_macs.update(identity_macs_from_entry(entry))
    return configured_macs


def device_display_name(device: dict[str, Any]) -> str:
    """Build a detailed device display name for selection lists."""
    return (
        f"{device.get('device_type', 'Unknown')} "
        f"v{device.get('version', 'Unknown')} "
        f"({device.get('wifi_name', 'No WiFi')}) "
        f"- {device.get('ip', 'Unknown')}"
    )


def split_devices_by_configured(
    devices: list[dict[str, Any]],
    configured_macs: set[str],
) -> tuple[dict[str, str], list[str]]:
    """Separate device options from already-configured devices."""
    device_options: dict[str, str] = {}
    already_configured_names: list[str] = []
    for i, device in enumerate(devices):
        device_name = device_display_name(device)
        device_macs = identity_macs_from_mapping(device)
        is_configured = identities_overlap(device_macs, configured_macs)
        if is_configured:
            already_configured_names.append(device_name)
        else:
            device_options[str(i)] = device_name
    return device_options, already_configured_names


def format_already_configured_text(names: list[str]) -> str:
    """Format already-configured devices for description placeholders."""
    if not names:
        return ""
    description_lines = [f"- {name}" for name in names]
    return "\n\nAlready configured devices:\n" + "\n".join(description_lines)


_DEVICE_METADATA_KEYS: tuple[str, ...] = (
    "device_type",
    "version",
    "wifi_name",
    "wifi_mac",
    "model",
    "firmware",
)


def metadata_from_device_info(device_info: dict[str, Any]) -> dict[str, Any]:
    """Return non-empty discovery fields that should be stored on the entry."""
    updates: dict[str, Any] = {}
    for key in _DEVICE_METADATA_KEYS:
        if key not in device_info:
            continue
        value = device_info[key]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        updates[key] = value
    return updates


def get_unique_id_from_device_info(device_info: Mapping[str, Any]) -> str | None:
    """Return the stable identity MAC from a device dict or entry data.

    This is the one place the identity preference order is applied, so a
    device-registry identifier and a config-entry unique id can never drift
    apart. Returns None when no field holds a valid 6-octet MAC.
    """
    for key in IDENTITY_MAC_KEYS:
        formatted = formatted_mac_or_none(device_info.get(key))
        if formatted is not None:
            return formatted
    return None


def build_entry_data(host: str, port: int, device_info: dict[str, Any]) -> dict[str, Any]:
    """Build config entry data from device info."""
    return {
        CONF_HOST: host,
        CONF_PORT: port,
        CONF_MAC: device_info.get("mac"),
        "device_type": device_info.get("device_type"),
        "version": device_info.get("version"),
        "wifi_name": device_info.get("wifi_name"),
        "wifi_mac": device_info.get("wifi_mac"),
        "ble_mac": device_info.get("ble_mac"),
        "model": device_info.get("model"),
        "firmware": device_info.get("firmware"),
    }


def async_apply_entry_update(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    data: Mapping[str, Any],
) -> None:
    """Write *data* onto *entry* and make sure exactly one reload follows.

    A loaded entry carries this integration's update listener, and Home
    Assistant fires it whenever ``async_update_entry`` actually changes
    something -- that listener is what reloads the entry. Scheduling a reload
    here as well would set the entry up twice, so the explicit reload is for
    the two cases the listener cannot cover:

    * nothing changed (a user resubmitting the same host to retry a device),
      so no listener runs;
    * the entry is not loaded, most often ``SETUP_RETRY`` after the device
      went missing, where the listener was never registered. Without this the
      corrected host would sit unused until Home Assistant's own setup-retry
      backoff came round, which grows to ten minutes.
    """
    changed = hass.config_entries.async_update_entry(entry, data=dict(data))
    if changed and entry.state is config_entries.ConfigEntryState.LOADED:
        return
    hass.config_entries.async_schedule_reload(entry.entry_id)
