"""Device info helpers for Marstek integration."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
)

from .const import DOMAIN
from .helpers.flow_helpers import formatted_mac_or_none, get_unique_id_from_device_info


def get_device_identifier(device_info: dict[str, Any]) -> str:
    """Return a stable device identifier based on MAC addresses."""
    identifier = get_unique_id_from_device_info(device_info)
    if identifier is None:
        raise ValueError("Marstek device identifier (MAC) is required for stable entities")
    return identifier


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


def _build_connections(device_info: dict[str, Any]) -> set[tuple[str, str]]:
    """Collect the hardware addresses Home Assistant can match this device on.

    Registering the Wi-Fi MAC lets Home Assistant tie this device to the same
    hardware seen by DHCP and by router integrations, so the device page shows
    one device instead of several. The BLE MAC is registered too because it is
    what Marstek firmware keeps stable across a Wi-Fi change.
    """
    connections: set[tuple[str, str]] = set()
    for key in ("wifi_mac", "mac"):
        formatted = formatted_mac_or_none(device_info.get(key))
        if formatted is not None:
            connections.add((CONNECTION_NETWORK_MAC, formatted))
    ble_mac = formatted_mac_or_none(device_info.get("ble_mac"))
    if ble_mac is not None:
        connections.add((CONNECTION_BLUETOOTH, ble_mac))
    return connections


def build_device_info(device_info: dict[str, Any]) -> DeviceInfo:
    """Build DeviceInfo for a Marstek device."""
    device_identifier = get_device_identifier(device_info)
    device_type = device_info.get("device_type") or "Device"
    version = device_info.get("version")
    name = format_device_name(device_info)
    return DeviceInfo(
        identifiers={(DOMAIN, device_identifier)},
        connections=_build_connections(device_info),
        name=name,
        manufacturer="Marstek",
        model=device_type,
        serial_number=device_identifier,
        sw_version=str(version) if version is not None else None,
    )
