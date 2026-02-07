"""Device info helpers for Marstek integration."""

from __future__ import annotations

import re
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


def _format_device_type(device_type: str | None) -> str:
    """Format device type into a short, user-friendly name.

    Examples:
        VenusA 3.0 -> Venus A (3.0)
        VenusE -> Venus E
        Venus v3 -> Venus (3)
    """
    if not device_type:
        return "Device"

    raw = str(device_type).strip()
    if not raw:
        return "Device"

    base = raw
    version: str | None = None
    match = re.match(r"^(?P<base>.+?)\s+(?P<ver>[vV]?\d+(?:\.\d+)*)$", raw)
    if match:
        base = match.group("base")
        version = match.group("ver")

    base = re.sub(r"^(Venus)([A-Za-z])\b", r"\1 \2", base)
    base = " ".join(base.split())
    if not base:
        base = "Device"

    if version:
        cleaned_version = version.lstrip("vV")
        if cleaned_version:
            return f"{base} ({cleaned_version})"

    return base


def format_device_name(device_info: dict[str, Any]) -> str:
    """Return the display name for a Marstek device."""
    return _format_device_type(device_info.get("device_type"))


def build_device_info(device_info: dict[str, Any]) -> DeviceInfo:
    """Build DeviceInfo for a Marstek device."""
    device_identifier = get_device_identifier(device_info)
    device_type = device_info.get("device_type") or "Device"
    version = device_info.get("version")
    name = format_device_name(device_info)
    return DeviceInfo(
        identifiers={(DOMAIN, device_identifier)},
        name=name,
        manufacturer="Marstek",
        model=device_type,
        sw_version=str(version) if version is not None else None,
    )
