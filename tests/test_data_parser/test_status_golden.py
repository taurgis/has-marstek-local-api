"""Exact status dictionaries from before the parser was split.

These literals were captured from ``parse_es_status_response`` and
``merge_device_status`` before the field-group helpers existed. The helpers
have to keep producing them.
"""

from __future__ import annotations

import logging

import pytest

from custom_components.marstek.firmware_profile import resolve_firmware_profile
from custom_components.marstek.pymarstek.data_parser import (
    merge_device_status,
    parse_es_status_response,
)

_MINI = resolve_firmware_profile("VNSEM-0", 301)
_VENUS_A_149 = resolve_firmware_profile("VenusA", 149)


def _es(result: dict[str, object]) -> dict[str, object]:
    """Wrap one ES.GetStatus result the way the device sends it."""
    return {"id": 1, "result": result}


_PARSE_CASES: dict[str, tuple[dict[str, object], object, dict[str, object]]] = {
    "charging": (
        _es(
            {
                "bat_soc": 80,
                "bat_cap": 5120,
                "bat_power": 400,
                "pv_power": 0,
                "ongrid_power": -400,
                "offgrid_power": 0,
                "total_pv_energy": 10,
                "total_grid_output_energy": 1,
                "total_grid_input_energy": 2,
                "total_load_energy": 3,
            }
        ),
        None,
        {
            "battery_soc": 80,
            "battery_power": -400,
            "battery_power_derived": False,
            "battery_status": "charging",
            "ongrid_power": -400,
            "offgrid_power": 0,
            "bat_cap": 5120,
            "pv_power": 0,
            "total_pv_energy": 10.0,
            "total_grid_output_energy": 1,
            "total_grid_input_energy": 2,
            "total_load_energy": 3,
        },
    ),
    "discharging": (
        _es(
            {
                "bat_soc": 40,
                "bat_cap": 5120,
                "bat_power": -250,
                "pv_power": 100,
                "ongrid_power": 150,
                "offgrid_power": 0,
            }
        ),
        None,
        {
            "battery_soc": 40,
            "battery_power": 250,
            "battery_power_derived": False,
            "battery_status": "discharging",
            "ongrid_power": 150,
            "offgrid_power": 0,
            "bat_cap": 5120,
            "pv_power": 100,
            "total_pv_energy": None,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
        },
    ),
    "idle_explicit": (
        _es(
            {
                "bat_soc": 50,
                "bat_cap": 5120,
                "bat_power": 0,
                "pv_power": 0,
                "ongrid_power": 0,
                "offgrid_power": 0,
            }
        ),
        None,
        {
            "battery_soc": 50,
            "battery_power": 0,
            "battery_power_derived": False,
            "battery_status": "idle",
            "ongrid_power": 0,
            "offgrid_power": 0,
            "bat_cap": 5120,
            "pv_power": 0,
            "total_pv_energy": None,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
        },
    ),
    "missing_power_flows": (
        _es(
            {
                "bat_soc": 55,
                "bat_cap": 5000,
                "pv_power": 800,
                "ongrid_power": 200,
                "offgrid_power": 100,
            }
        ),
        None,
        {
            "battery_soc": 55,
            "battery_power": -500,
            "battery_power_derived": True,
            "battery_status": "charging",
            "ongrid_power": 200,
            "offgrid_power": 100,
            "bat_cap": 5000,
            "pv_power": 800,
            "total_pv_energy": None,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
        },
    ),
    "missing_power_idle": (
        _es(
            {
                "bat_soc": 55,
                "bat_cap": 5000,
                "pv_power": 0,
                "ongrid_power": 0,
                "offgrid_power": 0,
            }
        ),
        None,
        {
            "battery_soc": 55,
            "battery_power": 0,
            "battery_power_derived": True,
            "battery_status": "idle",
            "ongrid_power": 0,
            "offgrid_power": 0,
            "bat_cap": 5000,
            "pv_power": 0,
            "total_pv_energy": None,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
        },
    ),
    "missing_power_partial": (
        _es({"bat_soc": 55, "bat_cap": 5000, "pv_power": "x", "ongrid_power": 10}),
        None,
        {
            "battery_soc": 55,
            "battery_power": None,
            "battery_power_derived": None,
            "battery_status": None,
            "ongrid_power": 10,
            "offgrid_power": None,
            "bat_cap": 5000,
            "pv_power": "x",
            "total_pv_energy": None,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
        },
    ),
    "bad_bat_power": (
        _es(
            {
                "bat_soc": 10,
                "bat_cap": 100,
                "bat_power": "nope",
                "pv_power": 5,
                "ongrid_power": 1,
                "offgrid_power": 0,
            }
        ),
        None,
        {
            "battery_soc": 10,
            "battery_power": None,
            "battery_power_derived": None,
            "battery_status": None,
            "ongrid_power": 1,
            "offgrid_power": 0,
            "bat_cap": 100,
            "pv_power": 5,
            "total_pv_energy": None,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
        },
    ),
    "zero_pack": (
        _es({"bat_soc": 0, "bat_cap": 0, "bat_power": 0}),
        None,
        {
            "battery_soc": None,
            "battery_power": 0,
            "battery_power_derived": False,
            "battery_status": "idle",
            "ongrid_power": None,
            "offgrid_power": None,
            "bat_cap": None,
            "pv_power": None,
            "total_pv_energy": None,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
        },
    ),
    "zero_cap_live_soc": (
        _es({"bat_soc": 76, "bat_cap": 0, "bat_power": -10}),
        None,
        {
            "battery_soc": 76,
            "battery_power": 10,
            "battery_power_derived": False,
            "battery_status": "discharging",
            "ongrid_power": None,
            "offgrid_power": None,
            "bat_cap": None,
            "pv_power": None,
            "total_pv_energy": None,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
        },
    ),
    "uint16_offgrid": (
        _es({"bat_soc": 20, "bat_cap": 2000, "bat_power": 10, "offgrid_power": 65530}),
        None,
        {
            "battery_soc": 20,
            "battery_power": -10,
            "battery_power_derived": False,
            "battery_status": "charging",
            "ongrid_power": None,
            "offgrid_power": -6,
            "bat_cap": 2000,
            "pv_power": None,
            "total_pv_energy": None,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
        },
    ),
    "empty": (
        {"id": 1, "result": {}},
        None,
        {
            "battery_soc": None,
            "battery_power": None,
            "battery_power_derived": None,
            "battery_status": None,
            "ongrid_power": None,
            "offgrid_power": None,
            "bat_cap": None,
            "pv_power": None,
            "total_pv_energy": None,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
        },
    ),
    "not_object": (
        {"id": 1, "result": "OK"},
        None,
        {
            "battery_soc": None,
            "battery_power": None,
            "battery_power_derived": None,
            "battery_status": None,
            "ongrid_power": None,
            "offgrid_power": None,
            "bat_cap": None,
            "pv_power": None,
            "total_pv_energy": None,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
        },
    ),
    "mini_remaining": (
        _es(
            {
                "bat_soc": 95,
                "bat_cap": 1916,
                "bat_power": -100,
                "pv_power": 0,
                "ongrid_power": 0,
                "offgrid_power": 0,
            }
        ),
        _MINI,
        {
            "battery_soc": 95,
            "battery_power": 100,
            "battery_power_derived": False,
            "battery_status": "discharging",
            "ongrid_power": 0,
            "offgrid_power": 0,
            "bat_cap": None,
            "pv_power": 0,
            "total_pv_energy": None,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
            "bat_capacity": 1916,
        },
    ),
    "mini_zero_cap_live_soc": (
        _es({"bat_soc": 40, "bat_cap": 0, "bat_power": 0}),
        _MINI,
        {
            "battery_soc": 40,
            "battery_power": 0,
            "battery_power_derived": False,
            "battery_status": "idle",
            "ongrid_power": None,
            "offgrid_power": None,
            "bat_cap": None,
            "pv_power": None,
            "total_pv_energy": None,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
        },
    ),
    "mini_zero_both": (
        _es({"bat_soc": 0, "bat_cap": 0, "bat_power": 0}),
        _MINI,
        {
            "battery_soc": 0,
            "battery_power": 0,
            "battery_power_derived": False,
            "battery_status": "idle",
            "ongrid_power": None,
            "offgrid_power": None,
            "bat_cap": None,
            "pv_power": None,
            "total_pv_energy": None,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
            "bat_capacity": 0,
        },
    ),
    "scaled_pv": (
        _es(
            {
                "bat_soc": 49,
                "bat_cap": 2080,
                "bat_power": 0,
                "total_pv_energy": 25742,
                "pv_power": 0,
                "ongrid_power": 0,
                "offgrid_power": 0,
            }
        ),
        _VENUS_A_149,
        {
            "battery_soc": 49,
            "battery_power": 0,
            "battery_power_derived": False,
            "battery_status": "idle",
            "ongrid_power": 0,
            "offgrid_power": 0,
            "bat_cap": 2080,
            "pv_power": 0,
            "total_pv_energy": 257420.0,
            "total_grid_output_energy": None,
            "total_grid_input_energy": None,
            "total_load_energy": None,
        },
    ),
}


@pytest.mark.parametrize("name", sorted(_PARSE_CASES))
def test_parse_es_status_matches_captured_output(name: str) -> None:
    """Each captured ES.GetStatus payload still parses to the same dictionary."""
    response, profile, expected = _PARSE_CASES[name]
    assert parse_es_status_response(response, profile) == expected


def test_missing_bat_power_idle_still_logs(caplog: pytest.LogCaptureFixture) -> None:
    """The idle fallback keeps the same debug line."""
    with caplog.at_level(logging.DEBUG):
        parse_es_status_response(
            _es(
                {
                    "bat_soc": 55,
                    "bat_cap": 5000,
                    "pv_power": 0,
                    "ongrid_power": 0,
                    "offgrid_power": 0,
                }
            )
        )
    assert "treating battery power as idle" in caplog.text


def test_merge_blank_matches_captured_output() -> None:
    """A merge with no replies is the default status plus the energy bookkeeping."""
    assert merge_device_status() == {
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
        "wifi_rssi": None,
        "wifi_ssid": None,
        "ct_state": None,
        "ct_connected": None,
        "em_a_power": None,
        "em_b_power": None,
        "em_c_power": None,
        "em_total_power": None,
        "bat_temp": None,
        "bat_charg_flag": None,
        "bat_dischrg_flag": None,
        "bat_capacity": None,
        "bat_rated_capacity": None,
        "bat_soc_detailed": None,
        "es_energy_totals_raw": {},
        "es_energy_totals_fell": False,
        "es_status_fresh": False,
    }


def test_merge_previous_drops_unusable_and_unknown_keys() -> None:
    """Previous NaN power is dropped, and a non-PV extra key is not copied."""
    merged = merge_device_status(
        previous_status={
            "battery_soc": 33,
            "pv1_power": 12,
            "mystery": 1,
            "em_input_energy": 4,
            "battery_power": float("nan"),
        }
    )
    assert merged["battery_soc"] == 33
    assert merged["pv1_power"] == 12
    assert merged["em_input_energy"] == 4
    assert merged["battery_power"] is None
    assert "mystery" not in merged
    assert merged["es_status_fresh"] is False


def test_merge_ignores_zero_mode_soc_and_keeps_meter_gaps() -> None:
    """ES.GetMode SoC 0 is ignored, and its meter fields fill empty keys."""
    assert (
        merge_device_status(
            es_mode_data={
                "device_mode": "auto",
                "battery_soc": 0,
                "ongrid_power": -20,
                "ct_state": 1,
                "em_a_power": 15,
            }
        )["battery_soc"]
        is None
    )
    merged = merge_device_status(
        es_mode_data={
            "device_mode": "auto",
            "battery_soc": 0,
            "ongrid_power": -20,
            "ct_state": 1,
            "em_a_power": 15,
        }
    )
    assert merged["device_mode"] == "auto"
    assert merged["ongrid_power"] == -20
    assert merged["ct_state"] == 1
    assert merged["em_a_power"] == 15


def test_merge_layered_replies_match_captured_output() -> None:
    """Mode, meter, Wi-Fi, battery, PV and ES.GetStatus still merge the same way."""
    merged = merge_device_status(
        es_mode_data={
            "device_mode": "ai",
            "battery_soc": 10,
            "ongrid_power": 1,
            "em_total_power": 9,
        },
        es_status_data={
            "battery_soc": 60,
            "battery_power": 100,
            "battery_status": "discharging",
            "ongrid_power": 80,
            "pv_power": 0,
            "battery_power_derived": False,
        },
        em_status_data={"ct_connected": True, "em_total_power": 80, "ct_state": 1},
        wifi_status_data={"wifi_rssi": -40, "wifi_ssid": "lab"},
        bat_status_data={"bat_temp": 25.5, "bat_capacity": None},
        pv_status_data={"pv1_power": 500, "pv_power": 500},
        device_ip="172.28.0.20",
        last_update=1_700_000_000.0,
        previous_status={
            "battery_soc": 59,
            "wifi_ssid": "old",
            "total_pv_energy": 10,
            "last_update": 1_699_999_000.0,
            "ongrid_power": 70,
            "total_grid_output_energy": 100,
        },
    )
    assert merged == {
        "battery_soc": 60,
        "battery_power": -420.0,
        "device_mode": "ai",
        "battery_status": "charging",
        "ongrid_power": 80,
        "offgrid_power": None,
        "pv_power": 500.0,
        "bat_cap": None,
        "total_pv_energy": 10,
        "total_grid_output_energy": 120.83333333333334,
        "total_grid_input_energy": None,
        "total_load_energy": None,
        "wifi_rssi": -40,
        "wifi_ssid": "lab",
        "ct_state": 1,
        "ct_connected": True,
        "em_a_power": None,
        "em_b_power": None,
        "em_c_power": None,
        "em_total_power": 80,
        "bat_temp": 25.5,
        "bat_charg_flag": None,
        "bat_dischrg_flag": None,
        "bat_capacity": None,
        "bat_rated_capacity": None,
        "bat_soc_detailed": None,
        "pv1_power": 500,
        "battery_power_derived": True,
        "es_energy_totals_raw": {},
        "es_energy_totals_fell": False,
        "device_ip": "172.28.0.20",
        "last_update": 1700000000.0,
        "es_status_fresh": True,
    }
