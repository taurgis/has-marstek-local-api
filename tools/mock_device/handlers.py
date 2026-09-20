"""API method handlers for mock Marstek device."""

from __future__ import annotations

from typing import Any

from custom_components.marstek.firmware_profile import DeviceFamily, FirmwareProfile

from .const import STATUS_IDLE


def handle_get_device(
    request_id: int,
    src: str,
    config: dict[str, Any],
    ip: str,
    *,
    omit_result_macs: bool = False,
) -> dict[str, Any]:
    """Handle Marstek.GetDevice request.

    Venus C firmware 153 (issue #60) omits ``ble_mac`` / ``wifi_mac`` /
    ``wifi_name`` from ``result`` while still embedding the BLE MAC in ``src``.
    """
    result: dict[str, Any] = {
        "device": config["device"],
        "ver": config["ver"],
        "ip": ip,
    }
    if not omit_result_macs:
        result["ble_mac"] = config["ble_mac"]
        result["wifi_mac"] = config["wifi_mac"]
        result["wifi_name"] = config["wifi_name"]
    return {
        "id": request_id,
        "src": src,
        "result": result,
    }


def handle_ble_get_status(
    request_id: int, src: str, config: dict[str, Any], is_connected: bool = False
) -> dict[str, Any]:
    """Handle BLE.GetStatus request per API spec."""
    return {
        "id": request_id,
        "src": src,
        "result": {
            "id": 0,
            "state": "connect" if is_connected else "disconnect",
            "ble_mac": config["ble_mac"],
        },
    }


def handle_es_get_status(
    request_id: int,
    src: str,
    state: dict[str, Any],
    device_type: str,
    *,
    profile: FirmwareProfile,
    include_bat_power: bool = False,
) -> dict[str, Any]:
    """Handle ES.GetStatus request with full energy stats per API spec.

    Energy stats are now tracked in the simulator and included in state.

    Note: bat_power sign convention (per real device behavior):
      - Positive = charging (power flowing INTO battery)
      - Negative = discharging (power flowing OUT of battery)
    Internal simulator uses opposite convention, so we negate here.

    Args:
        request_id: Request ID for response
        src: Source identifier for response
        state: Battery simulator state
        device_type: Device type string (currently unused for bat_power decision)
        include_bat_power: If True, include bat_power in response. Default False
            because Venus E 3.0 and later HMG-50 images omit it. HMG-50 Control
            153 includes the field; the mock passes True for that profile.
    """
    # Negate power: internal +discharge/-charge → API +charge/-discharge
    bat_power = -state["power"]
    # Venus A/D ES.GetStatus reports pv_power=0 even while PV.GetStatus
    # channels are live (issues #5, #11). The integration sums channels.
    pv_power = 0 if profile.supports_pv else state.get("pv_power", 0)
    result: dict[str, Any] = {
        "id": request_id,
        "src": src,
        "result": {
            "id": 0,
            "bat_soc": state["soc"],
            "bat_cap": state.get("capacity_wh", 5120),
            "pv_power": pv_power,
            "ongrid_power": state["grid_power"],
            "offgrid_power": 0,
            "total_pv_energy": _encode_value(
                state.get("total_pv_energy", 0), profile.pv_energy_scale
            ),
            "total_grid_output_energy": state.get("total_grid_output_energy", 0),
            "total_grid_input_energy": state.get("total_grid_input_energy", 0),
            "total_load_energy": state.get("total_load_energy", 0),
        },
    }

    # HMG-50 Control 153 includes bat_power in the ES.GetStatus field table.
    # Venus E 3.0 and HMG-50 155/156 omit it; the integration then uses
    # pv_power - ongrid_power. Enable include_bat_power to test the direct path.
    if include_bat_power:
        result["result"]["bat_power"] = bat_power

    return result


def _getmode_instance_id(params: dict[str, Any] | None) -> int:
    """Echo ES.GetMode params.id when the client sent a non-boolean integer."""
    if not isinstance(params, dict):
        return 0
    raw_id = params.get("id", 0)
    if isinstance(raw_id, bool) or not isinstance(raw_id, int):
        return 0
    return raw_id


def _unpopulated_getmode_meter_template() -> dict[str, int]:
    """Return unpopulated GetMode CT/energy keys.

    Observed on Venus E 3.0 firmware 150 (LAN capture) and Venus A firmware
    147 (issue #11): these fields are present but stay zeros while
    ``EM.GetStatus`` reports a live CT.
    """
    return {
        "ct_state": 0,
        "a_power": 0,
        "b_power": 0,
        "c_power": 0,
        "total_power": 0,
        "input_energy": 0,
        "output_energy": 0,
    }


def handle_es_get_mode(
    request_id: int,
    src: str,
    state: dict[str, Any],
    *,
    profile: FirmwareProfile,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle ES.GetMode request."""
    result: dict[str, Any] = {
        "id": _getmode_instance_id(params),
        "mode": state["mode"],
        "ongrid_power": state["grid_power"],
        "offgrid_power": 0,
        "bat_soc": state["soc"],
    }
    if profile.supports_em_energy:
        if profile.family in {DeviceFamily.VENUS_E, DeviceFamily.VENUS_C}:
            result.update(_unpopulated_getmode_meter_template())
        else:
            result.update(
                {
                    "ct_state": 1 if state["ct_connected"] else 0,
                    "a_power": state.get("em_a_power", 0),
                    "b_power": state.get("em_b_power", 0),
                    "c_power": state.get("em_c_power", 0),
                    "total_power": state["grid_power"],
                    **_encoded_meter_energy(state, profile),
                }
            )
    elif profile.family is DeviceFamily.VENUS_A:
        # Issue #11: Venus A 147 already includes the GetMode CT/energy keys
        # as zeros. Venus E 144 (#21) does not.
        result.update(_unpopulated_getmode_meter_template())
    return {
        "id": request_id,
        "src": src,
        "result": result,
    }


def handle_pv_get_status(
    request_id: int,
    src: str,
    profile: FirmwareProfile,
    pv_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle PV.GetStatus request per API spec.

    API spec returns single channel: pv_power, pv_voltage, pv_current.
    Some devices (Venus D) expose multi-channel PV (MPPT) data.
    This mock supports both formats based on provided pv_state.
    """
    state = pv_state or {}

    def _to_deciwatts(value: Any, *, channel: int | None = None) -> Any:
        """Encode PV power for the wire using the profile scale.

        Channel 1 is deciwatts on observed firmware (including 150.9).
        Other channels are watts.
        """
        if value is None:
            return None
        if channel not in (None, 1):
            return value
        try:
            encoded = float(value) / profile.pv_channel_1_power_scale
            return int(encoded) if encoded.is_integer() else encoded
        except (TypeError, ValueError):
            return value

    # If pv_channels is provided, return multi-channel format (pv1_..pv4_)
    pv_channels = state.get("pv_channels")
    if isinstance(pv_channels, list) and pv_channels:
        result: dict[str, Any] = {"id": 0}
        for channel in pv_channels[:4]:
            idx = int(channel.get("channel", 0))
            if idx < 1 or idx > 4:
                continue
            prefix = f"pv{idx}_"
            result[f"{prefix}power"] = _to_deciwatts(
                channel.get("pv_power", 0),
                channel=idx,
            )
            result[f"{prefix}voltage"] = channel.get("pv_voltage", 0)
            result[f"{prefix}current"] = channel.get("pv_current", 0)
            result[f"{prefix}state"] = 1 if channel.get("pv_power", 0) > 0 else 0
        return {
            "id": request_id,
            "src": src,
            "result": result,
        }

    # Default: single-channel format
    return {
        "id": request_id,
        "src": src,
        "result": {
            "id": 0,
            "pv_power": _to_deciwatts(state.get("pv_power", 0)),
            "pv_voltage": state.get("pv_voltage", 0),
            "pv_current": state.get("pv_current", 0),
        },
    }


def _encode_value(value: Any, scale: float) -> Any:
    """Encode a physical value using the profile's wire-to-SI scale."""
    if not isinstance(value, (int, float)):
        return value
    encoded = float(value) / scale
    return int(encoded) if encoded.is_integer() else encoded


def _encoded_meter_energy(state: dict[str, Any], profile: FirmwareProfile) -> dict[str, Any]:
    """Encode physical EM lifetime energy as 0.1 Wh wire values."""
    return {
        "input_energy": _encode_value(
            state.get("em_input_energy", state.get("total_grid_input_energy", 0)),
            profile.em_energy_scale,
        ),
        "output_energy": _encode_value(
            state.get("em_output_energy", state.get("total_grid_output_energy", 0)),
            profile.em_energy_scale,
        ),
    }


def handle_wifi_get_status(
    request_id: int, src: str, config: dict[str, Any], ip: str, state: dict[str, Any]
) -> dict[str, Any]:
    """Handle Wifi.GetStatus request per API spec."""
    return {
        "id": request_id,
        "src": src,
        "result": {
            "id": 0,
            "wifi_mac": config.get("wifi_mac", ""),
            "ssid": config.get("wifi_name", "AirPort-38"),
            "rssi": state["wifi_rssi"],
            "sta_ip": ip,
            "sta_gate": ".".join(ip.split(".")[:3]) + ".1",
            "sta_mask": "255.255.255.0",
            "sta_dns": ".".join(ip.split(".")[:3]) + ".1",
        },
    }


def handle_em_get_status(
    request_id: int,
    src: str,
    state: dict[str, Any],
    *,
    profile: FirmwareProfile,
) -> dict[str, Any]:
    """Handle EM.GetStatus (Energy Meter / P1 meter / CT clamp) request per API spec.

    This returns the P1 meter reading - what's actually flowing at the meter AFTER
    battery contribution. The battery tracks its own contribution internally.

    Positive values = importing from grid (household consuming more than battery provides)
    Negative values = exporting to grid (battery/solar producing more than household uses)
    """
    result: dict[str, Any] = {
        "id": 0,
        "ct_state": 1 if state["ct_connected"] else 0,
        "a_power": state.get("em_a_power", 0),
        "b_power": state.get("em_b_power", 0),
        "c_power": state.get("em_c_power", 0),
        "total_power": state["grid_power"],
    }
    # Observed on Venus A 147 (#11), Venus E 144 (#21), and Venus E 150:
    # input_energy / output_energy are present. Scale to 0.1 Wh only when
    # the firmware profile says so; legacy builds stay unscaled (often 0).
    result.update(_encoded_meter_energy(state, profile))
    return {
        "id": request_id,
        "src": src,
        "result": result,
    }


def handle_bat_get_status(
    request_id: int, src: str, state: dict[str, Any], capacity_wh: int
) -> dict[str, Any]:
    """Handle Bat.GetStatus request per API spec.

    API spec says charg_flag and dischrg_flag are booleans.
    """
    return {
        "id": request_id,
        "src": src,
        "result": {
            "id": 0,
            "soc": state["soc"],
            "charg_flag": state["charg_flag"] == 1,  # Convert to boolean per spec
            "dischrg_flag": state["dischrg_flag"] == 1,  # Convert to boolean per spec
            "bat_temp": state["battery_temp"],
            "bat_capacity": int(capacity_wh * state["soc"] / 100),
            "rated_capacity": capacity_wh,
        },
    }


def handle_es_set_mode(request_id: int, src: str) -> dict[str, Any]:
    """Handle ES.SetMode response per API spec.

    API spec: result contains id and set_result (boolean).
    """
    return {
        "id": request_id,
        "src": src,
        "result": {
            "id": 0,
            "set_result": True,
        },
    }


def handle_sys_write(request_id: int, src: str) -> dict[str, Any]:
    """Handle DOD.SET, Ble.Adv, Led.Ctrl, and other set_result writes."""
    return {
        "id": request_id,
        "src": src,
        "result": {
            "set_result": True,
        },
    }


def handle_invalid_params(request_id: int, src: str) -> dict[str, Any]:
    """Return JSON-RPC invalid-params for malformed Open API writes."""
    return {
        "id": request_id,
        "src": src,
        "error": {
            "code": -32602,
            "message": "Invalid params",
        },
    }


def handle_wifi_set_config(
    request_id: int, src: str, params: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """Handle HMG-50 ``Wifi.SetConfig`` (ssid + optional pass)."""
    ssid = params.get("ssid")
    if not isinstance(ssid, str) or not ssid:
        return handle_invalid_params(request_id, src)
    config["wifi_name"] = ssid
    return handle_sys_write(request_id, src)


def handle_method_not_found(
    request_id: int, src: str, *, extra_data: int | None = None
) -> dict[str, Any]:
    """Return JSON-RPC method-not-found for unsupported commands."""
    error: dict[str, Any] = {
        "code": -32601,
        "message": "Method not found",
    }
    if extra_data is not None:
        error["data"] = extra_data
    return {
        "id": request_id,
        "src": src,
        "error": error,
    }


def get_static_state(
    soc: int,
    power: int,
    mode: str,
    totals: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Get static state when simulation is disabled."""
    totals = totals or {}
    return {
        "soc": soc,
        "power": power,
        "mode": mode,
        "status": STATUS_IDLE,
        "grid_power": 0,
        "em_a_power": 0,
        "em_b_power": 0,
        "em_c_power": 0,
        "household_consumption": 0,
        "passive_remaining": 0,
        "passive_cfg": None,
        "wifi_rssi": -55,
        "battery_temp": 25.0,
        "ct_connected": True,
        "charg_flag": 1,
        "dischrg_flag": 1,
        "total_pv_energy": int(totals.get("total_pv_energy", 0)),
        "total_grid_output_energy": int(totals.get("total_grid_output_energy", 0)),
        "total_grid_input_energy": int(totals.get("total_grid_input_energy", 0)),
        "total_load_energy": int(totals.get("total_load_energy", 0)),
        "em_input_energy": totals.get("em_input_energy", totals.get("total_grid_input_energy", 0)),
        "em_output_energy": totals.get(
            "em_output_energy", totals.get("total_grid_output_energy", 0)
        ),
        "pv_power": 0,
        "pv_voltage": 0,
        "pv_current": 0,
    }
