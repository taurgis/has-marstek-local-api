"""Device info helpers for Marstek integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo, format_mac

from .const import DOMAIN


def get_device_identifier(device_info: dict[str, Any]) -> str:
    """Return a stable device identifier based on MAC addresses."""
    device_identifier_raw = (
        device_info.get("ble_mac")
        or device_info.get("mac")
        or device_info.get("wifi_mac")
    )
    if not device_identifier_raw:
        raise ValueError("Marstek device identifier (MAC) is required for stable entities")
    return format_mac(device_identifier_raw)


def build_device_info(device_info: dict[str, Any]) -> DeviceInfo:
    """Build DeviceInfo for a Marstek device."""
    device_identifier = get_device_identifier(device_info)
    device_type = device_info.get("device_type") or "Device"
    version = device_info.get("version")
    name = f"Marstek {device_type}"
    if version not in (None, "", 0):
        name = f"{name} v{version}"
    return DeviceInfo(
        identifiers={(DOMAIN, device_identifier)},
        name=name,
        manufacturer="Marstek",
        model=device_type,
        sw_version=str(version) if version is not None else None,
    )
