"""Config flow helper utilities."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PORT
from homeassistant.helpers.device_registry import format_mac


def collect_configured_macs(
    entries: list[config_entries.ConfigEntry],
) -> set[str]:
    """Collect formatted MAC addresses from existing entries."""
    configured_macs: set[str] = set()
    for entry in entries:
        entry_mac = (
            entry.data.get("ble_mac")
            or entry.data.get(CONF_MAC)
            or entry.data.get("wifi_mac")
        )
        if entry_mac:
            configured_macs.add(format_mac(entry_mac))
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
        device_mac = device.get("ble_mac") or device.get("mac") or device.get("wifi_mac")
        is_configured = bool(device_mac and format_mac(device_mac) in configured_macs)
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


def get_unique_id_from_device_info(device_info: dict[str, Any]) -> str | None:
    """Return formatted unique id from device info, if available."""
    unique_id_mac = (
        device_info.get("ble_mac")
        or device_info.get("mac")
        or device_info.get("wifi_mac")
    )
    if not unique_id_mac:
        return None
    try:
        return format_mac(unique_id_mac)
    except (TypeError, ValueError):
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
