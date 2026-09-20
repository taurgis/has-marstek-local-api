"""Data parsing utilities for pymarstek responses."""

from __future__ import annotations

import logging
import math
from typing import Any

from ..const import normalize_operating_mode
from ..firmware_profile import FirmwareProfile, resolve_firmware_profile
from .energy_guard import apply_energy_total_guard, without_implausible_energy_totals

_LOGGER: logging.Logger | None = None
_LEGACY_PROFILE = resolve_firmware_profile(None, None)

# ES.GetMode Rev 3.1 CT/power/energy keys that overlap EM.GetStatus.
_GETMODE_EM_FALLBACK_KEYS = frozenset(
    {
        "ct_state",
        "ct_connected",
        "em_a_power",
        "em_b_power",
        "em_c_power",
        "em_total_power",
        "em_input_energy",
        "em_output_energy",
    }
)


def _get_logger() -> logging.Logger:
    """Lazy import logger to avoid circular imports."""
    global _LOGGER
    if _LOGGER is None:
        _LOGGER = logging.getLogger(__name__)
    return _LOGGER


def _result_fields(response: Any) -> dict[str, Any]:
    """Return the JSON-RPC ``result`` object, or an empty one.

    Callers already refuse a reply whose ``result`` is missing or not an
    object, but firmware is inconsistent enough that the parsers must not
    depend on that check living somewhere else: a ``"result": "OK"`` would
    otherwise raise AttributeError deep inside a poll.
    """
    if not isinstance(response, dict):
        return {}
    result = response.get("result")
    if not isinstance(result, dict):
        return {}
    return result


def _scale_numeric(value: Any, scale: float) -> Any:
    """Scale a numeric wire value; leave missing and non-numeric values unchanged.

    An integer too large to convert to a float is dropped rather than raised:
    the wire decoder already refuses one, and a parser that raises would take
    the whole poll cycle down with it.
    """
    if not isinstance(value, (int, float)):
        return value
    try:
        return value * scale
    except OverflowError:
        return None


def _add_scaled_meter_energy(
    parsed: dict[str, Any],
    result: dict[str, Any],
    scale: float,
) -> None:
    """Copy present EM lifetime energy fields into coordinator keys."""
    if "input_energy" in result:
        parsed["em_input_energy"] = _scale_numeric(result.get("input_energy"), scale)
    if "output_energy" in result:
        parsed["em_output_energy"] = _scale_numeric(result.get("output_energy"), scale)


def parse_es_mode_response(
    response: dict[str, Any],
    profile: FirmwareProfile | None = None,
) -> dict[str, Any]:
    """Parse ES.GetMode response into structured data.

    ES.GetMode returns device mode and grid power info, NOT battery power.
    For actual battery power, use parse_es_status_response with ES.GetStatus.
    Rev 3.1 also reports CT, phase-power, and meter energy fields that map onto
    the existing EM coordinator keys and are used only as fallbacks.

    Args:
        response: Raw response from ES.GetMode command
        profile: Firmware encoding profile; defaults to the legacy-safe contract

    Returns:
        Dictionary with parsed mode and optional fallback meter data
    """
    result = _result_fields(response)
    active_profile = profile or _LEGACY_PROFILE

    battery_soc = result.get("bat_soc")
    ongrid_power = result.get("ongrid_power")
    raw_mode = result.get("mode")
    # Convert API mode to HA mode. Prefer documented string names; also accept
    # integer 0-4 used by some firmwares. Never treat ongrid_power as battery
    # power (the vendor library does; ES.GetStatus owns battery_power).
    device_mode = normalize_operating_mode(raw_mode)

    # NOTE: ongrid_power is GRID power, not battery power!
    # Positive = exporting to grid, Negative = importing from grid

    parsed: dict[str, Any] = {
        "battery_soc": battery_soc,
        "device_mode": device_mode,
        "ongrid_power": ongrid_power,
        "offgrid_power": result.get("offgrid_power"),
        # Don't set battery_power here - it comes from ES.GetStatus
    }

    if "ct_state" in result:
        ct_state_raw = result.get("ct_state")
        parsed["ct_state"] = ct_state_raw
        parsed["ct_connected"] = ct_state_raw == 1 if ct_state_raw is not None else None

    for source_key, dest_key in (
        ("a_power", "em_a_power"),
        ("b_power", "em_b_power"),
        ("c_power", "em_c_power"),
        ("total_power", "em_total_power"),
    ):
        if source_key in result:
            parsed[dest_key] = result.get(source_key)

    _add_scaled_meter_energy(parsed, result, active_profile.em_energy_scale)
    return parsed


def parse_es_status_response(
    response: dict[str, Any],
    profile: FirmwareProfile | None = None,
) -> dict[str, Any]:
    """Parse ES.GetStatus response into structured data.

    ES.GetStatus returns actual battery power and energy statistics.
    Field names match the official Marstek Open API spec.

    Args:
        response: Raw response from ES.GetStatus command

    Returns:
        Dictionary with parsed battery data (battery_power, battery_status, etc.)
    """
    result = _result_fields(response)
    active_profile = profile or _LEGACY_PROFILE

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
                "ES.GetStatus missing bat_power with zero flows; treating battery power as idle"
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

    # Energy totals. Solar energy uses the profile scale; grid/load stay Wh.
    total_pv_energy = _scale_numeric(result.get("total_pv_energy"), active_profile.pv_energy_scale)
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


def parse_pv_status_response(
    response: dict[str, Any],
    profile: FirmwareProfile | None = None,
) -> dict[str, Any]:
    """Parse PV.GetStatus response into structured data.

    Note: The API spec shows single PV channel fields (pv_power, pv_voltage, pv_current).
    Some devices may return multi-channel data with prefixes (pv1_, pv2_, etc.).
    This parser handles both formats.

    Args:
        response: Raw response from PV.GetStatus command

    Returns:
        Dictionary with parsed PV channel data (pv1-pv4 or single pv_)
    """
    result = _result_fields(response)
    active_profile = profile or _LEGACY_PROFILE

    pv_data: dict[str, Any] = {}

    def _scale_pv_power(raw_value: Any, *, channel: int | None = None) -> Any:
        """Scale PV power to watts using the profile's channel-1 factor.

        Observed firmware reports channel 1 in deciwatts, including 148.3
        and 150.9 (#57). Other channels are already watts.
        """
        if raw_value is None:
            return None
        if channel not in (None, 1):
            return raw_value
        try:
            return float(raw_value) * active_profile.pv_channel_1_power_scale
        except (TypeError, ValueError):
            return raw_value

    # Multi-channel format - extract data for each PV channel (1-4). A reply
    # that carries the per-channel breakdown is read as multi-channel even when
    # it also carries the spec's aggregate ``pv_power``, because the breakdown
    # is strictly more information. Branching on the aggregate's presence
    # instead dropped channels 2-4 and rescaled the aggregate as if it were
    # channel 1, which reports the array total at a tenth of its real value.
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

    # Single-channel format (per API spec) - map to pv1_* for consistency.
    if not pv_data and "pv_power" in result:
        pv_power = result.get("pv_power")
        pv_data["pv1_power"] = _scale_pv_power(pv_power)
        if "pv_voltage" in result:
            pv_data["pv1_voltage"] = result.get("pv_voltage")
        if "pv_current" in result:
            pv_data["pv1_current"] = result.get("pv_current")
        if isinstance(pv_power, (int, float)):
            pv_data["pv1_state"] = 1 if pv_power > 0 else 0

    return pv_data


def parse_wifi_status_response(response: dict[str, Any]) -> dict[str, Any]:
    """Parse Wifi.GetStatus response into structured data.

    Provides WiFi signal strength (RSSI) and network information.

    Args:
        response: Raw response from Wifi.GetStatus command

    Returns:
        Dictionary with WiFi data (wifi_rssi, wifi_ssid, etc.)
    """
    result = _result_fields(response)

    return {
        "wifi_rssi": result.get("rssi"),  # Signal strength in dBm
        "wifi_ssid": result.get("ssid"),
        "wifi_sta_ip": result.get("sta_ip"),
        "wifi_sta_gate": result.get("sta_gate"),
        "wifi_sta_mask": result.get("sta_mask"),
        "wifi_sta_dns": result.get("sta_dns"),
    }


def parse_em_status_response(
    response: dict[str, Any],
    profile: FirmwareProfile | None = None,
) -> dict[str, Any]:
    """Parse EM.GetStatus (Energy Meter/CT) response into structured data.

    Provides CT connection state and phase power readings. Lifetime meter
    energy fields are included only when present on the wire.

    Args:
        response: Raw response from EM.GetStatus command
        profile: Firmware encoding profile; defaults to the legacy-safe contract

    Returns:
        Dictionary with energy meter data (ct_state, phase powers, total_power)
    """
    result = _result_fields(response)
    active_profile = profile or _LEGACY_PROFILE

    ct_state_raw = result.get("ct_state")
    # Convert to boolean-friendly value: 0=Not connected, 1=Connected
    ct_connected = ct_state_raw == 1 if ct_state_raw is not None else None

    parsed: dict[str, Any] = {
        "ct_state": ct_state_raw,  # Raw value: 0=Not connected, 1=Connected
        "ct_connected": ct_connected,  # Boolean for binary sensor
        "em_a_power": result.get("a_power"),  # Phase A power [W]
        "em_b_power": result.get("b_power"),  # Phase B power [W]
        "em_c_power": result.get("c_power"),  # Phase C power [W]
        "em_total_power": result.get("total_power"),  # Total grid power [W]
    }
    _add_scaled_meter_energy(parsed, result, active_profile.em_energy_scale)
    return parsed


def parse_bat_status_response(response: dict[str, Any]) -> dict[str, Any]:
    """Parse Bat.GetStatus response into structured data.

    Provides detailed battery information including temperature and capacity.

    Args:
        response: Raw response from Bat.GetStatus command

    Returns:
        Dictionary with battery data (bat_temp, charge flags, capacity)
    """
    result = _result_fields(response)

    return {
        "bat_temp": result.get("bat_temp"),  # Battery temperature [°C]
        "bat_charg_flag": result.get("charg_flag"),  # Charging permission flag
        "bat_dischrg_flag": result.get("dischrg_flag"),  # Discharge permission flag
        "bat_capacity": result.get("bat_capacity"),  # Remaining capacity [Wh]
        "bat_rated_capacity": result.get("rated_capacity"),  # Rated capacity [Wh]
        "bat_soc_detailed": result.get("soc"),  # SOC from Bat.GetStatus
    }


def _is_unusable_value(value: Any) -> bool:
    """Check whether a value must not be merged into device status.

    Two shapes qualify. Firmware sends the literal string ``"unknown"`` for a
    field it cannot read yet. And a non-finite float — decoded from a glitched
    datagram, or produced by arithmetic over one — would be carried forward by
    ``previous_status`` on every later cycle where its read fails or is
    skipped, so it would outlive the glitch that created it.
    """
    if isinstance(value, str):
        return value.lower() == "unknown"
    if isinstance(value, float):
        return not math.isfinite(value)
    return False


def total_pv_channel_power(pv_status_data: dict[str, Any]) -> float:
    """Sum the PV channel powers that are usable numbers.

    The parsers keep a value they cannot scale exactly as the wire sent it, so
    a channel the firmware cannot read yet arrives here as the literal string
    ``"unknown"`` (see :func:`_is_unusable_value`), and a glitched datagram can
    put a list or a bool there. Adding one of those to the running total
    raises, and nothing between here and the coordinator catches ``TypeError``,
    so the whole poll cycle would fail over one unreadable channel. Skip them
    instead; the channels that did report still carry the sum.
    """
    total = 0.0
    for channel in range(1, 5):
        value = pv_status_data.get(f"pv{channel}_power")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not math.isfinite(value):
            continue
        total += float(value)
    return total


def _recalculate_battery_from_pv(
    status: dict[str, Any],
    pv_status_data: dict[str, Any],
    es_status_data: dict[str, Any],
) -> None:
    """Recalculate battery power using PV channel data when ES.GetStatus is wrong."""
    es_pv_power = es_status_data.get("pv_power")
    total_pv_from_channels = total_pv_channel_power(pv_status_data)
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
        float(value) for value in (previous_power, current_power) if isinstance(value, (int, float))
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
    2. em_status_data (CT connection, phase powers, meter energy)
    3. es_mode_data (device_mode, ongrid_power, offgrid_power; Rev 3.1 CT/power/energy
       fills only keys that are still empty — never overwrites last-known EM values)
    4. bat_status_data (battery temperature, capacity details)
    5. wifi_status_data (WiFi RSSI, network info)
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
            if value is None or _is_unusable_value(value):
                continue
            status[key] = value

    # Apply previous status first (lowest priority) to preserve values
    # from last successful poll when individual requests fail
    if previous_status:
        # Only preserve non-None values from previous status
        for key, value in previous_status.items():
            extra_key = key not in status and (
                key.startswith("pv") or key in {"em_input_energy", "em_output_energy"}
            )
            if (
                value is not None
                and not _is_unusable_value(value)
                and key in status
                and status[key] is None
            ) or (extra_key and value is not None and not _is_unusable_value(value)):
                status[key] = value

    # Apply in order of priority (lowest to highest)
    # PV data is ONLY included if pv_status_data is provided (Venus A/D devices only)
    if pv_status_data:
        _apply_updates(pv_status_data)

    # Mode CT/power/energy fills gaps only. Venus E 3.0 firmware 150 returns
    # those GetMode fields as zeros even while EM.GetStatus has a live CT, and
    # Wi-Fi polls often time out — never clobber last-known or current EM data.
    if es_mode_data:
        mode_core = {
            key: value
            for key, value in es_mode_data.items()
            if key not in _GETMODE_EM_FALLBACK_KEYS
        }
        _apply_updates(mode_core)
        for key, value in es_mode_data.items():
            if key not in _GETMODE_EM_FALLBACK_KEYS:
                continue
            if value is None or _is_unusable_value(value):
                continue
            if status.get(key) is None:
                status[key] = value

    if em_status_data:
        _apply_updates(em_status_data)

    if wifi_status_data:
        _apply_updates(wifi_status_data)

    if bat_status_data:
        _apply_updates(bat_status_data)

    # ES.GetStatus has highest priority for battery data
    if es_status_data:
        _apply_updates(es_status_data)

    # Recalculate pv_power and battery_power using PV channel data when
    # ES.GetStatus returns incorrect pv_power (Venus A devices report pv_power=0
    # in ES.GetStatus but individual channels from PV.GetStatus are correct)
    if pv_status_data and es_status_data:
        _recalculate_battery_from_pv(status, pv_status_data, es_status_data)

    energy_safe_previous = without_implausible_energy_totals(previous_status)
    _resolve_contradicted_energy_totals(status, energy_safe_previous)

    if device_ip:
        status["device_ip"] = device_ip

    if last_update is not None:
        status["last_update"] = last_update

    _stabilize_grid_energy_totals(status, energy_safe_previous)
    apply_energy_total_guard(status, energy_safe_previous)

    return status
