"""Data parsing utilities for pymarstek responses."""

from __future__ import annotations

from typing import Any

import logging

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
    if (
        raw_bat_power is None
        and "bat_power" not in result
        and isinstance(pv_power, (int, float))
        and isinstance(ongrid_power, (int, float))
    ):
        # Fallback when API omits bat_power (Venus A/E devices):
        # Energy flow: battery + PV = grid export (when discharging to grid)
        # So: bat_power = pv_power - ongrid_power (API convention: - = discharging)
        # With pv=0, ongrid=+800 (export): bat_power = -800 (discharging)
        raw_bat_power = pv_power - ongrid_power
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
        "household_consumption": None,
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
    }
    
    def _is_unknown_value(value: Any) -> bool:
        return isinstance(value, str) and value.lower() == "unknown"

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
            ):
                status[key] = value
            # Preserve PV keys from previous status if they exist (device supports PV)
            elif (
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
        es_pv_power = es_status_data.get("pv_power")
        # Calculate total from individual PV channels
        total_pv_from_channels = sum(
            pv_status_data.get(f"pv{ch}_power", 0) or 0
            for ch in range(1, 5)
        )
        # If ES.GetStatus pv_power is 0 but channels have real power, override
        if (es_pv_power in (None, 0)) and total_pv_from_channels > 0:
            # Override pv_power with calculated total from PV channels
            status["pv_power"] = total_pv_from_channels
            
            # Recalculate battery power using correct pv_power
            ongrid_power = es_status_data.get("ongrid_power")
            if isinstance(ongrid_power, (int, float)):
                # bat_power = pv_power - ongrid_power (API convention)
                # Then negate for HA convention (positive = discharging)
                raw_bat_power = total_pv_from_channels - ongrid_power
                battery_power = -raw_bat_power
                status["battery_power"] = battery_power
                # Update battery status based on recalculated power
                if battery_power > 0:
                    status["battery_status"] = "discharging"
                elif battery_power < 0:
                    status["battery_status"] = "charging"
                else:
                    status["battery_status"] = "idle"
    
    if device_ip:
        status["device_ip"] = device_ip
    
    if last_update is not None:
        status["last_update"] = last_update
    
    return status

