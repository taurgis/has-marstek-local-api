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


def handle_es_get_status(
    request_id: int, src: str, state: dict[str, Any]
) -> dict[str, Any]:
    """Handle ES.GetStatus request."""
    return {
        "id": request_id,
        "src": src,
        "result": {
            "id": 0,
            "bat_soc": state["soc"],
            "bat_cap": 5120,
            "pv_power": 0,
            "ongrid_power": state["grid_power"],
            "offgrid_power": 0,
            "bat_power": state["power"],
            "total_pv_energy": 0,
            "total_grid_output_energy": 1000,
            "total_grid_input_energy": 500,
            "total_load_energy": 800,
        },
    }


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


def handle_pv_get_status(request_id: int, src: str) -> dict[str, Any]:
    """Handle PV.GetStatus request."""
    return {
        "id": request_id,
        "src": src,
        "result": {
            "pv1_power": 0,
            "pv1_voltage": 0,
            "pv1_current": 0,
            "pv1_state": 0,
            "pv2_power": 0,
            "pv2_voltage": 0,
            "pv2_current": 0,
            "pv2_state": 0,
            "pv3_power": 0,
            "pv3_voltage": 0,
            "pv3_current": 0,
            "pv3_state": 0,
            "pv4_power": 0,
            "pv4_voltage": 0,
            "pv4_current": 0,
            "pv4_state": 0,
        },
    }


def handle_wifi_get_status(
    request_id: int, src: str, config: dict[str, Any], ip: str, state: dict[str, Any]
) -> dict[str, Any]:
    """Handle Wifi.GetStatus request."""
    return {
        "id": request_id,
        "src": src,
        "result": {
            "rssi": state["wifi_rssi"],
            "ssid": config.get("wifi_name", "AirPort-38"),
            "sta_ip": ip,
            "sta_gate": ".".join(ip.split(".")[:3]) + ".1",
            "sta_mask": "255.255.255.0",
            "sta_dns": ".".join(ip.split(".")[:3]) + ".1",
        },
    }


def handle_em_get_status(
    request_id: int, src: str, state: dict[str, Any]
) -> dict[str, Any]:
    """Handle EM.GetStatus (Energy Meter / CT clamp) request."""
    grid_power = state["grid_power"]
    phase_variation = random.uniform(0.8, 1.2)
    a_power = int(grid_power * 0.33 * phase_variation)
    b_power = int(grid_power * 0.33 * random.uniform(0.8, 1.2))
    c_power = grid_power - a_power - b_power

    return {
        "id": request_id,
        "src": src,
        "result": {
            "ct_state": 1 if state["ct_connected"] else 0,
            "a_power": a_power,
            "b_power": b_power,
            "c_power": c_power,
            "total_power": grid_power,
        },
    }


def handle_bat_get_status(
    request_id: int, src: str, state: dict[str, Any], capacity_wh: int
) -> dict[str, Any]:
    """Handle Bat.GetStatus request."""
    return {
        "id": request_id,
        "src": src,
        "result": {
            "bat_temp": state["battery_temp"],
            "charg_flag": state["charg_flag"],
            "dischrg_flag": state["dischrg_flag"],
            "bat_capacity": int(capacity_wh * state["soc"] / 100),
            "rated_capacity": capacity_wh,
            "soc": state["soc"],
        },
    }


def handle_es_set_mode(request_id: int, src: str) -> dict[str, Any]:
    """Handle ES.SetMode response (actual mode change handled by device)."""
    return {
        "id": request_id,
        "src": src,
        "result": {"success": True},
    }


def get_static_state(soc: int, power: int, mode: str) -> dict[str, Any]:
    """Get static state when simulation is disabled."""
    return {
        "soc": soc,
        "power": power,
        "mode": mode,
        "status": STATUS_IDLE,
        "grid_power": 0,
        "household_consumption": 0,
        "passive_remaining": 0,
        "passive_cfg": None,
        "wifi_rssi": -55,
        "battery_temp": 25.0,
        "ct_connected": True,
        "charg_flag": 1,
        "dischrg_flag": 1,
    }
