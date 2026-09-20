"""One shape for the device dict built from a ``Marstek.GetDevice`` reply.

Broadcast discovery, unicast GetDevice and the pooled UDP client all turn the
same ``result`` payload into the same dict. Keeping one builder means a new
field, or a change to how the BLE MAC is recovered, lands on every path at
once.
"""

from __future__ import annotations

from typing import Any

from ..firmware_profile import extract_discovery_version
from .network import mac_from_openapi_src


def non_empty_str(value: Any) -> str:
    """Return a stripped string, or empty when the value is missing."""
    if not isinstance(value, str):
        return ""
    return value.strip()


def build_device_info(
    result: dict[str, Any],
    *,
    ip: str | None = None,
    port: int | None = None,
    src: str = "",
) -> dict[str, Any]:
    """Build the device dict from a GetDevice ``result``.

    *ip* overrides the address the device reported, which matters when the
    sender address is more trustworthy than the payload. *port* is included
    only when the caller knows which listen port answered. Some firmware
    builds (Venus C ``ver`` 153) omit both MAC fields from ``result`` while
    still embedding the BLE MAC in ``src``, so fall back to that.
    """
    version = extract_discovery_version(result)
    ble_mac = non_empty_str(result.get("ble_mac"))
    wifi_mac = non_empty_str(result.get("wifi_mac"))
    if not ble_mac and not wifi_mac:
        ble_mac = mac_from_openapi_src(src)
    device_type = result.get("device", "Unknown")
    info: dict[str, Any] = {
        "id": result.get("id", 0),
        "device_type": device_type,
        "version": version,
        "wifi_name": result.get("wifi_name", ""),
        "ip": result.get("ip", "") if ip is None else ip,
        "wifi_mac": wifi_mac,
        "ble_mac": ble_mac,
        "mac": wifi_mac or ble_mac,
        "model": device_type,
        "firmware": "" if version is None else str(version),
    }
    if port is not None:
        info["port"] = port
    return info
