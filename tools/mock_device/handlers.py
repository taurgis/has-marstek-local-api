"""API method handlers for mock Marstek device."""

import random
from typing import Any

from .const import MODE_AUTO, MODE_MANUAL, MODE_PASSIVE, STATUS_IDLE


def handle_get_device(
    request_id: int, src: str, config: dict[str, Any], ip: str
) -> dict[str, Any]:
    """Handle Marstek.GetDevice request."""
    return {
        "id": request_id,
        "src": src,
        "result": {
            "device": config["device"],
            "ver": config["ver"],
            "ble_mac": config["ble_mac"],
            "wifi_mac": config["wifi_mac"],
            "wifi_name": config["wifi_name"],
            "ip": ip,
        },
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
            since real Venus E devices do NOT return bat_power, and we have no
            evidence other devices do either. Enable for testing purposes only.
    """
    # Negate power: internal +discharge/-charge → API +charge/-discharge
    bat_power = -state["power"]
    result: dict[str, Any] = {
        "id": request_id,
        "src": src,
        "result": {
            "id": 0,
            "bat_soc": state["soc"],
            "bat_cap": state.get("capacity_wh", 5120),
            "pv_power": state.get("pv_power", 0),
            "ongrid_power": state["grid_power"],
            "offgrid_power": 0,
            "total_pv_energy": state.get("total_pv_energy", 0),
            "total_grid_output_energy": state.get("total_grid_output_energy", 0),
            "total_grid_input_energy": state.get("total_grid_input_energy", 0),
            "total_load_energy": state.get("total_load_energy", 0),
        },
    }

    # By default, NO device returns bat_power in ES.GetStatus response
    # Real Venus E 3.0 confirmed: bat_power is NOT in the response
    # Integration uses fallback calculation: pv_power - ongrid_power
    # Enable include_bat_power=True only for testing the direct path
    if include_bat_power:
        result["result"]["bat_power"] = bat_power

    return result


def handle_es_get_mode(
    request_id: int, src: str, state: dict[str, Any]
) -> dict[str, Any]:
    """Handle ES.GetMode request."""
    return {
        "id": request_id,
        "src": src,
        "result": {
            "id": 0,
            "mode": state["mode"],
            "ongrid_power": state["grid_power"],
            "offgrid_power": 0,
            "bat_soc": state["soc"],
        },
    }


def handle_pv_get_status(
    request_id: int,
    src: str,
    pv_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle PV.GetStatus request per API spec.

    API spec returns single channel: pv_power, pv_voltage, pv_current.
    Some devices (Venus D) expose multi-channel PV (MPPT) data.
    This mock supports both formats based on provided pv_state.
    """
    state = pv_state or {}

    def _to_deciwatts(value: Any, *, channel: int | None = None) -> Any:
        """Convert PV power in watts to deciwatts for mock output.

        Only channel 1 reports deciwatts; other channels report watts.
        """
        if value is None:
            return None
        if channel not in (None, 1):
            return value
        try:
            return float(value) * 10
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
    request_id: int, src: str, state: dict[str, Any]
) -> dict[str, Any]:
    """Handle EM.GetStatus (Energy Meter / P1 meter / CT clamp) request per API spec.
    
    This returns the P1 meter reading - what's actually flowing at the meter AFTER
    battery contribution. The battery tracks its own contribution internally.
    
    Positive values = importing from grid (household consuming more than battery provides)
    Negative values = exporting to grid (battery/solar producing more than household uses)
    """
    return {
        "id": request_id,
        "src": src,
        "result": {
            "id": 0,
            "ct_state": 1 if state["ct_connected"] else 0,
            "a_power": state.get("em_a_power", 0),
            "b_power": state.get("em_b_power", 0),
            "c_power": state.get("em_c_power", 0),
            "total_power": state["grid_power"],
        },
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


def get_static_state(soc: int, power: int, mode: str) -> dict[str, Any]:
    """Get static state when simulation is disabled."""
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
        "total_pv_energy": 0,
        "total_grid_output_energy": 0,
        "total_grid_input_energy": 0,
        "total_load_energy": 0,
        "pv_power": 0,
        "pv_voltage": 0,
        "pv_current": 0,
    }
