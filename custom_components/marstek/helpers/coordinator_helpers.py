"""Coordinator helper utilities for Marstek integration."""

from __future__ import annotations

import logging
from typing import Any

from ..pymarstek.data_parser import total_pv_channel_power


def has_valid_status_data(device_status: dict[str, Any]) -> bool:
    """Return True if device status contains meaningful values."""
    device_mode = device_status.get("device_mode")
    battery_soc = device_status.get("battery_soc")
    battery_power = device_status.get("battery_power")
    battery_status = device_status.get("battery_status")
    # Shares the parser's channel sum: a channel the firmware cannot read
    # arrives as the literal string "unknown", and a glitched datagram can put
    # a list there. Adding one of those raised TypeError here, and this runs
    # before the coordinator can turn a bad poll into UpdateFailed.
    pv_power = total_pv_channel_power(device_status)
    em_total_power = device_status.get("em_total_power")
    wifi_rssi = device_status.get("wifi_rssi")
    bat_temp = device_status.get("bat_temp")

    return (
        device_mode not in (None, "Unknown", "unknown")
        or battery_soc is not None
        or battery_power is not None
        or battery_status not in (None, "Unknown")
        or pv_power != 0
        or em_total_power is not None
        or wifi_rssi is not None
        or bat_temp is not None
    )


def raise_if_invalid_status(
    current_ip: str, device_status: dict[str, Any], logger: logging.Logger
) -> None:
    """Raise if status data is missing or invalid."""
    has_fresh_data = device_status.get("has_fresh_data", True)
    device_mode = device_status.get("device_mode")
    battery_soc = device_status.get("battery_soc")
    battery_power = device_status.get("battery_power")
    valid_data = has_valid_status_data(device_status)

    if not has_fresh_data:
        # Debug, not warning: this raises, and the coordinator reports the
        # outage once for the whole run of failures rather than per poll.
        logger.debug(
            "No fresh data received from device at %s - keeping previous values",
            current_ip,
        )
        error_msg = f"No fresh data received from device at {current_ip}"
        raise TimeoutError(error_msg) from None

    if not valid_data:
        logger.debug(
            "No valid data received from device at %s "
            "(device_mode=%s, soc=%s, power=%s) - connection failed",
            current_ip,
            device_mode or "Unknown",
            battery_soc or 0,
            battery_power or 0,
        )
        error_msg = f"No valid data received from device at {current_ip}"
        raise TimeoutError(error_msg) from None

    if device_mode in ("Unknown", "unknown"):
        logger.debug(
            "Device %s reported device_mode=Unknown but other data is "
            "present (soc=%s, power=%s)",
            current_ip,
            battery_soc or 0,
            battery_power or 0,
        )
