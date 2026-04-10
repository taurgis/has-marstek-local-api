"""Data parsing utilities for pymarstek responses."""

from __future__ import annotations

import logging
from typing import Any

_LOGGER: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    """Lazy import logger to avoid circular imports."""
    global _LOGGER
    if _LOGGER is None:
        _LOGGER = logging.getLogger(__name__)
    return _LOGGER


def parse_es_mode_response(response: dict[str, Any]) -> dict[str, Any]:
    """Parse ES.GetMode response into structured data.

    ES.GetMode returns device mode and grid power info, NOT battery power.
    For actual battery power, use parse_es_status_response with ES.GetStatus.

    Args:
        response: Raw response from ES.GetMode command

    Returns:
        Dictionary with parsed mode and grid data (device_mode, ongrid_power)
    """
    result = response.get("result", {})

    battery_soc = result.get("bat_soc")
    ongrid_power = result.get("ongrid_power")
    raw_mode = result.get("mode")
    # Convert API mode to lowercase HA mode (ignore non-string placeholders)
    device_mode = raw_mode.lower() if isinstance(raw_mode, str) and raw_mode else None

    # NOTE: ongrid_power is GRID power, not battery power!
    # Positive = exporting to grid, Negative = importing from grid

    return {
        "battery_soc": battery_soc,
        "device_mode": device_mode,
        "ongrid_power": ongrid_power,
        # Don't set battery_power here - it comes from ES.GetStatus
    }


def parse_es_status_response(response: dict[str, Any]) -> dict[str, Any]:
    """Parse ES.GetStatus response into structured data.

    ES.GetStatus returns actual battery power and energy statistics.
    Field names match the official Marstek Open API spec.

    Args:
        response: Raw response from ES.GetStatus command

    Returns:
        Dictionary with parsed battery data (battery_power, battery_status, etc.)
    """
    result = response.get("result", {})

    # ES.GetStatus fields per official API spec (docs/marstek_device_openapi.MD)
    bat_soc = result.get("bat_soc")
    bat_cap = result.get("bat_cap")  # Battery capacity in Wh
    pv_power = result.get("pv_power")  # Solar power
    ongrid_power = result.get("ongrid_power")  # Grid power
    offgrid_power = result.get("offgrid_power")
    raw_bat_power: Any | None
    if "bat_power" in result:
        raw_bat_power = result.get("bat_power")
        if not isinstance(raw_bat_power, (int, float)):
            raw_bat_power = None
    else:
        raw_bat_power = None
    if raw_bat_power is None and "bat_power" not in result:
        if (
            isinstance(pv_power, (int, float))
            and isinstance(ongrid_power, (int, float))
            and (pv_power != 0 or ongrid_power != 0)
        ):
            # Fallback when API omits bat_power (Venus A/E devices):
            # Energy flow: battery + PV = grid export (when discharging to grid)
            # So: bat_power = pv_power - ongrid_power (API convention: - = discharging)
            # With pv=0, ongrid=+800 (export): bat_power = -800 (discharging)
            raw_bat_power = pv_power - ongrid_power
        elif (
            isinstance(pv_power, (int, float))
            and isinstance(ongrid_power, (int, float))
            and isinstance(offgrid_power, (int, float))
            and pv_power == 0
            and ongrid_power == 0
            and offgrid_power == 0
        ):
            # All reported flows are zero; treat as idle instead of keeping stale power.
            raw_bat_power = 0
            _get_logger().debug(
                "ES.GetStatus missing bat_power with zero flows; "
                "treating battery power as idle"
            )
    battery_power: float | None
    battery_status: str | None
    if raw_bat_power is None:
        battery_power = None
        battery_status = None
    else:
        # Convert to Home Assistant convention:
        # HA Energy Dashboard expects: positive = DISCHARGING, negative = CHARGING
        # API returns: positive = charging, negative = discharging
        # So we negate the value to match HA convention
        battery_power = -raw_bat_power

        # Calculate battery_status from battery_power (HA convention)
        # Positive = discharging (battery providing power)
        # Negative = charging (battery receiving power)
        if battery_power > 0:
            battery_status = "discharging"
        elif battery_power < 0:
            battery_status = "charging"
        else:
            battery_status = "idle"

    # Energy totals
    total_pv_energy = result.get("total_pv_energy")
    total_grid_output_energy = result.get("total_grid_output_energy")
    total_grid_input_energy = result.get("total_grid_input_energy")
    total_load_energy = result.get("total_load_energy")

    return {
        "battery_soc": bat_soc,
        "battery_power": battery_power,  # HA convention: positive = discharging
        "battery_status": battery_status,
        "ongrid_power": ongrid_power,
        "offgrid_power": offgrid_power,
        "bat_cap": bat_cap,
        "pv_power": pv_power,
        "total_pv_energy": total_pv_energy,
        "total_grid_output_energy": total_grid_output_energy,
        "total_grid_input_energy": total_grid_input_energy,
        "total_load_energy": total_load_energy,
    }


def parse_pv_status_response(response: dict[str, Any]) -> dict[str, Any]:
    """Parse PV.GetStatus response into structured data.

    Note: The API spec shows single PV channel fields (pv_power, pv_voltage, pv_current).
    Some devices may return multi-channel data with prefixes (pv1_, pv2_, etc.).
    This parser handles both formats.

    Args:
        response: Raw response from PV.GetStatus command

    Returns:
        Dictionary with parsed PV channel data (pv1-pv4 or single pv_)
    """
    result = response.get("result", {})

    pv_data: dict[str, Any] = {}

    def _scale_pv_power(raw_value: Any, *, channel: int | None = None) -> Any:
        """Scale PV power to watts.

        Channel 1 reports PV power in deciwatts; other channels report watts.
        """
        if raw_value is None:
            return None
        if channel not in (None, 1):
            return raw_value
        try:
            return float(raw_value) / 10
        except (TypeError, ValueError):
            return raw_value


    # Check for single-channel format (per API spec)
    if "pv_power" in result:
        # Single PV channel - map to pv1_* for consistency
        pv_power = result.get("pv_power")
        pv_data["pv1_power"] = _scale_pv_power(pv_power)
        if "pv_voltage" in result:
            pv_data["pv1_voltage"] = result.get("pv_voltage")
        if "pv_current" in result:
            pv_data["pv1_current"] = result.get("pv_current")
        if isinstance(pv_power, (int, float)):
            pv_data["pv1_state"] = 1 if pv_power > 0 else 0
    else:
        # Multi-channel format - extract data for each PV channel (1-4)
        for channel in range(1, 5):
            prefix = f"pv{channel}_"
            channel_keys = (
                f"{prefix}power",
                f"{prefix}voltage",
                f"{prefix}current",
                f"{prefix}state",
            )
            if not any(key in result for key in channel_keys):
                continue
            if f"{prefix}power" in result:
                pv_data[f"{prefix}power"] = _scale_pv_power(
                    result.get(f"{prefix}power"),
                    channel=channel,
                )
            if f"{prefix}voltage" in result:
                pv_data[f"{prefix}voltage"] = result.get(f"{prefix}voltage")
            if f"{prefix}current" in result:
                pv_data[f"{prefix}current"] = result.get(f"{prefix}current")
            if f"{prefix}state" in result:
                pv_data[f"{prefix}state"] = result.get(f"{prefix}state")

    return pv_data


def parse_wifi_status_response(response: dict[str, Any]) -> dict[str, Any]:
    """Parse Wifi.GetStatus response into structured data.

    Provides WiFi signal strength (RSSI) and network information.

    Args:
        response: Raw response from Wifi.GetStatus command

    Returns:
        Dictionary with WiFi data (wifi_rssi, wifi_ssid, etc.)
    """
    result = response.get("result", {})

    return {
        "wifi_rssi": result.get("rssi"),  # Signal strength in dBm
        "wifi_ssid": result.get("ssid"),
        "wifi_sta_ip": result.get("sta_ip"),
        "wifi_sta_gate": result.get("sta_gate"),
        "wifi_sta_mask": result.get("sta_mask"),
        "wifi_sta_dns": result.get("sta_dns"),
    }


def parse_em_status_response(response: dict[str, Any]) -> dict[str, Any]:
    """Parse EM.GetStatus (Energy Meter/CT) response into structured data.

    Provides CT connection state and phase power readings.

    Args:
        response: Raw response from EM.GetStatus command

    Returns:
        Dictionary with energy meter data (ct_state, phase powers, total_power)
    """
    result = response.get("result", {})

    ct_state_raw = result.get("ct_state")
    # Convert to boolean-friendly value: 0=Not connected, 1=Connected
    ct_connected = ct_state_raw == 1 if ct_state_raw is not None else None

    return {
        "ct_state": ct_state_raw,  # Raw value: 0=Not connected, 1=Connected
        "ct_connected": ct_connected,  # Boolean for binary sensor
        "em_a_power": result.get("a_power"),  # Phase A power [W]
        "em_b_power": result.get("b_power"),  # Phase B power [W]
        "em_c_power": result.get("c_power"),  # Phase C power [W]
        "em_total_power": result.get("total_power"),  # Total grid power [W]
    }


def parse_bat_status_response(response: dict[str, Any]) -> dict[str, Any]:
    """Parse Bat.GetStatus response into structured data.

    Provides detailed battery information including temperature and capacity.

    Args:
        response: Raw response from Bat.GetStatus command

    Returns:
        Dictionary with battery data (bat_temp, charge flags, capacity)
    """
    result = response.get("result", {})

    return {
        "bat_temp": result.get("bat_temp"),  # Battery temperature [°C]
        "bat_charg_flag": result.get("charg_flag"),  # Charging permission flag
        "bat_dischrg_flag": result.get("dischrg_flag"),  # Discharge permission flag
        "bat_capacity": result.get("bat_capacity"),  # Remaining capacity [Wh]
        "bat_rated_capacity": result.get("rated_capacity"),  # Rated capacity [Wh]
        "bat_soc_detailed": result.get("soc"),  # SOC from Bat.GetStatus
    }


def _is_unknown_value(value: Any) -> bool:
    """Check if value is an 'unknown' placeholder."""
    return isinstance(value, str) and value.lower() == "unknown"


def _recalculate_battery_from_pv(
    status: dict[str, Any],
    pv_status_data: dict[str, Any],
    es_status_data: dict[str, Any],
) -> None:
    """Recalculate battery power using PV channel data when ES.GetStatus is wrong."""
    es_pv_power = es_status_data.get("pv_power")
    total_pv_from_channels = sum(
        pv_status_data.get(f"pv{ch}_power", 0) or 0 for ch in range(1, 5)
    )
    # If ES.GetStatus pv_power is 0 but channels have real power, override
    if (es_pv_power in (None, 0)) and total_pv_from_channels > 0:
        status["pv_power"] = total_pv_from_channels

        ongrid_power = es_status_data.get("ongrid_power")
        if isinstance(ongrid_power, (int, float)):
            raw_bat_power = total_pv_from_channels - ongrid_power
            battery_power = -raw_bat_power
            status["battery_power"] = battery_power
            if battery_power > 0:
                status["battery_status"] = "discharging"
            elif battery_power < 0:
                status["battery_status"] = "charging"
            else:
                status["battery_status"] = "idle"


def _has_active_pv_generation(status: dict[str, Any]) -> bool:
    """Return True when current status shows active PV generation."""
    pv_power = status.get("pv_power")
    if isinstance(pv_power, (int, float)) and pv_power > 0:
        return True

    return any(
        isinstance(status.get(f"pv{channel}_power"), (int, float))
        and status[f"pv{channel}_power"] > 0
        for channel in range(1, 5)
    )


def _resolve_contradicted_energy_totals(
    status: dict[str, Any],
    previous_status: dict[str, Any] | None,
) -> None:
    """Handle firmware totals that are contradicted by other telemetry.

    Best-effort rules:
    - `total_pv_energy=0` is only treated as invalid when PV generation is
      currently active or when a previous non-zero lifetime total exists.
    - `total_load_energy=0` is preserved unless it would reset a previous
      non-zero lifetime total, because firmware semantics are less clear.
    """
    previous_pv_total = previous_status.get("total_pv_energy") if previous_status else None
    current_pv_total = status.get("total_pv_energy")
    if isinstance(current_pv_total, (int, float)) and current_pv_total == 0:
        if isinstance(previous_pv_total, (int, float)) and previous_pv_total > 0:
            status["total_pv_energy"] = previous_pv_total
        elif _has_active_pv_generation(status):
            status["total_pv_energy"] = None

    previous_load_total = previous_status.get("total_load_energy") if previous_status else None
    current_load_total = status.get("total_load_energy")
    if (
        isinstance(current_load_total, (int, float))
        and current_load_total == 0
        and isinstance(previous_load_total, (int, float))
        and previous_load_total > 0
    ):
        status["total_load_energy"] = previous_load_total


def _average_ongrid_power(
    previous_power: Any,
    current_power: Any,
) -> float | None:
    """Return the average grid power between two samples when available."""
    numeric_values = [
        float(value)
        for value in (previous_power, current_power)
        if isinstance(value, (int, float))
    ]
    if not numeric_values:
        return None
    return sum(numeric_values) / len(numeric_values)


def _stabilize_grid_energy_totals(
    status: dict[str, Any],
    previous_status: dict[str, Any] | None,
) -> None:
    """Preserve moving grid totals when firmware counters stall.

    Some Venus E firmware builds keep returning the same
    total_grid_input_energy/total_grid_output_energy values even while
    ongrid_power shows sustained import/export. When that happens, keep the
    last known total as a floor and integrate the current flow until the device
    counters start advancing again.
    """
    if not previous_status:
        return

    previous_timestamp = previous_status.get("last_update")
    current_timestamp = status.get("last_update")
    if not isinstance(previous_timestamp, (int, float)) or not isinstance(
        current_timestamp, (int, float)
    ):
        return

    elapsed_seconds = current_timestamp - previous_timestamp
    if elapsed_seconds < 0:
        return

    average_ongrid_power = _average_ongrid_power(
        previous_status.get("ongrid_power"),
        status.get("ongrid_power"),
    )
    elapsed_hours = elapsed_seconds / 3600
    import_delta_wh = (
        abs(average_ongrid_power) * elapsed_hours
        if average_ongrid_power is not None and average_ongrid_power < 0
        else 0.0
    )
    export_delta_wh = (
        average_ongrid_power * elapsed_hours
        if average_ongrid_power is not None and average_ongrid_power > 0
        else 0.0
    )

    def _stabilize_total(total_key: str, delta_wh: float) -> None:
        previous_total = previous_status.get(total_key)
        current_total = status.get(total_key)
        if not isinstance(previous_total, (int, float)) and not isinstance(
            current_total, (int, float)
        ):
            return

        if (
            isinstance(previous_total, (int, float))
            and isinstance(current_total, (int, float))
            and current_total > previous_total
        ):
            return

        baseline_total: float | None = None
        if isinstance(previous_total, (int, float)):
            baseline_total = float(previous_total)
        elif isinstance(current_total, (int, float)):
            baseline_total = float(current_total)

        if baseline_total is None:
            return

        status[total_key] = baseline_total + delta_wh

    _stabilize_total("total_grid_input_energy", import_delta_wh)
    _stabilize_total("total_grid_output_energy", export_delta_wh)


def merge_device_status(
    es_mode_data: dict[str, Any] | None = None,
    es_status_data: dict[str, Any] | None = None,
    pv_status_data: dict[str, Any] | None = None,
    wifi_status_data: dict[str, Any] | None = None,
    em_status_data: dict[str, Any] | None = None,
    bat_status_data: dict[str, Any] | None = None,
    device_ip: str | None = None,
    last_update: float | None = None,
    previous_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge all status data into a complete device status.

    Priority order for overlapping keys:
    1. es_status_data (most accurate for battery_power, battery_status)
    2. es_mode_data (device_mode, ongrid_power)
    3. bat_status_data (battery temperature, capacity details)
    4. wifi_status_data (WiFi RSSI, network info)
    5. em_status_data (CT connection, phase powers)
    6. pv_status_data (PV channel data)
    7. previous_status (fallback for any values not provided by current data)

    Note: Battery power is recalculated using PV channel data when ES.GetStatus
    returns incorrect pv_power (common on Venus A devices).

    Args:
        es_mode_data: Parsed ES.GetMode data (device_mode, ongrid_power)
        es_status_data: Parsed ES.GetStatus data (battery_power, battery_status)
        pv_status_data: Parsed PV.GetStatus data
        wifi_status_data: Parsed Wifi.GetStatus data (rssi, network info)
        em_status_data: Parsed EM.GetStatus data (CT state, phase powers)
        bat_status_data: Parsed Bat.GetStatus data (temperature, capacity)
        device_ip: Device IP address
        last_update: Timestamp of last update
        previous_status: Previous device status to preserve values when individual
            requests fail (prevents intermittent "Unknown" states)

    Returns:
        Complete device status dictionary
    """
    # Start with defaults (None ensures previous values are preserved on timeouts)
    # Note: PV keys are NOT included by default - only added when device supports PV
    # Venus A and Venus D support PV; Venus C/E do NOT
    status: dict[str, Any] = {
        "battery_soc": None,
        "battery_power": None,
        "device_mode": None,
        "battery_status": None,
        "ongrid_power": None,
        "offgrid_power": None,
        "pv_power": None,
        "bat_cap": None,
        "household_consumption": None,
        "total_pv_energy": None,
        "total_grid_output_energy": None,
        "total_grid_input_energy": None,
        "total_load_energy": None,
        # WiFi status defaults
        "wifi_rssi": None,
        "wifi_ssid": None,
        # Energy meter / CT defaults
        "ct_state": None,
        "ct_connected": None,
        "em_a_power": None,
        "em_b_power": None,
        "em_c_power": None,
        "em_total_power": None,
        # Battery details defaults
        "bat_temp": None,
        "bat_charg_flag": None,
        "bat_dischrg_flag": None,
        "bat_capacity": None,
        "bat_rated_capacity": None,
        "bat_soc_detailed": None,
    }

    def _apply_updates(updates: dict[str, Any]) -> None:
        for key, value in updates.items():
            if value is None or _is_unknown_value(value):
                continue
            status[key] = value

    # Apply previous status first (lowest priority) to preserve values
    # from last successful poll when individual requests fail
    if previous_status:
        # Only preserve non-None values from previous status
        for key, value in previous_status.items():
            if (
                value is not None
                and not _is_unknown_value(value)
                and key in status
                and status[key] is None
            ) or (
                key.startswith("pv")
                and key not in status
                and value is not None
                and not _is_unknown_value(value)
            ):
                status[key] = value

    # Apply in order of priority (lowest to highest)
    # PV data is ONLY included if pv_status_data is provided (Venus A/D devices only)
    if pv_status_data:
        _apply_updates(pv_status_data)

    if em_status_data:
        _apply_updates(em_status_data)

    if wifi_status_data:
        _apply_updates(wifi_status_data)

    if bat_status_data:
        _apply_updates(bat_status_data)

    if es_mode_data:
        _apply_updates(es_mode_data)

    # ES.GetStatus has highest priority for battery data
    if es_status_data:
        _apply_updates(es_status_data)

    # Recalculate pv_power and battery_power using PV channel data when
    # ES.GetStatus returns incorrect pv_power (Venus A devices report pv_power=0
    # in ES.GetStatus but individual channels from PV.GetStatus are correct)
    if pv_status_data and es_status_data:
        _recalculate_battery_from_pv(status, pv_status_data, es_status_data)

    _resolve_contradicted_energy_totals(status, previous_status)

    if device_ip:
        status["device_ip"] = device_ip

    if last_update is not None:
        status["last_update"] = last_update

    _stabilize_grid_energy_totals(status, previous_status)

    return status

