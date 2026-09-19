"""Replay GitHub-issue Open API payloads through the firmware profile + parser."""

from __future__ import annotations

from custom_components.marstek.discovery import _build_device_info
from custom_components.marstek.firmware_profile import resolve_firmware_profile
from custom_components.marstek.pymarstek.data_parser import (
    merge_device_status,
    parse_bat_status_response,
    parse_em_status_response,
    parse_es_mode_response,
    parse_es_status_response,
    parse_pv_status_response,
)


def test_issue_11_venus_a_147_getmode_zeros_do_not_clobber_live_em() -> None:
    """Issue #11: Venus A firmware 147 GetMode CT keys are zeros; EM is live."""
    profile = resolve_firmware_profile("Venus A", 147)
    mode = parse_es_mode_response(
        {
            "id": 427,
            "src": "VenusA-682499eefdd7",
            "result": {
                "id": 0,
                "mode": "Auto",
                "ongrid_power": 318,
                "offgrid_power": 0,
                "bat_soc": 41,
                "ct_state": 0,
                "a_power": 0,
                "b_power": 0,
                "c_power": 0,
                "total_power": 0,
                "input_energy": 0,
                "output_energy": 0,
            },
        },
        profile,
    )
    es_status = parse_es_status_response(
        {
            "id": 428,
            "src": "VenusA-682499eefdd7",
            "result": {
                "id": 0,
                "bat_soc": 40,
                "bat_cap": 2080,
                "pv_power": 0,
                "ongrid_power": 318,
                "offgrid_power": 0,
                "total_pv_energy": 0,
                "total_grid_output_energy": 3106,
                "total_grid_input_energy": 3225,
                "total_load_energy": 0,
            },
        },
        profile,
    )
    em = parse_em_status_response(
        {
            "id": 429,
            "src": "VenusA-682499eefdd7",
            "result": {
                "id": 0,
                "ct_state": 1,
                "a_power": 0,
                "b_power": 0,
                "c_power": 0,
                "total_power": -16,
                "input_energy": 0,
                "output_energy": 0,
            },
        },
        profile,
    )
    pv = parse_pv_status_response(
        {
            "id": 430,
            "src": "VenusA-682499eefdd7",
            "result": {
                "id": 0,
                "pv1_power": 268,
                "pv1_voltage": 33,
                "pv1_current": 0,
                "pv1_state": 1,
                "pv2_power": 29,
                "pv2_voltage": 46,
                "pv2_current": 0,
                "pv2_state": 1,
                "pv3_power": 29,
                "pv3_voltage": 33,
                "pv3_current": 0,
                "pv3_state": 1,
                "pv4_power": 25,
                "pv4_voltage": 46,
                "pv4_current": 0,
                "pv4_state": 1,
            },
        },
        profile,
    )
    merged = merge_device_status(
        es_mode_data=mode,
        es_status_data=es_status,
        em_status_data=em,
        pv_status_data=pv,
    )

    assert profile.pv_energy_scale == 1.0
    assert merged["device_mode"] == "auto"
    assert merged["ct_state"] == 1
    assert merged["em_total_power"] == -16
    assert merged["em_input_energy"] == 0
    assert merged["total_pv_energy"] is None
    assert merged["pv1_power"] == 26.8
    assert merged["pv2_power"] == 29
    assert merged["pv_power"] == 26.8 + 29 + 29 + 25


def test_issue_5_venus_a_es_pv_power_zero_uses_channel_sum() -> None:
    """Issue #5: ES.GetStatus pv_power=0 while PV channels report production."""
    profile = resolve_firmware_profile("VenusA", 147)
    es_status = parse_es_status_response(
        {
            "id": 34,
            "src": "VenusA-bc2a33602323",
            "result": {
                "id": 0,
                "bat_soc": 27,
                "bat_cap": 2080,
                "pv_power": 0,
                "ongrid_power": 0,
                "offgrid_power": 0,
                "total_pv_energy": 0,
                "total_grid_output_energy": 69711,
                "total_grid_input_energy": 1967,
                "total_load_energy": 0,
            },
        },
        profile,
    )
    em = parse_em_status_response(
        {
            "id": 35,
            "src": "VenusA-bc2a33602323",
            "result": {
                "id": 0,
                "ct_state": 1,
                "a_power": 169,
                "b_power": 0,
                "c_power": 0,
                "total_power": 169,
                "input_energy": 0,
                "output_energy": 0,
            },
        },
        profile,
    )
    pv = parse_pv_status_response(
        {
            "id": 36,
            "src": "VenusA-bc2a33602323",
            "result": {
                "id": 0,
                "pv1_power": 415,
                "pv1_voltage": 50,
                "pv1_current": 0,
                "pv1_state": 1,
                "pv2_power": 52,
                "pv2_voltage": 50,
                "pv2_current": 1,
                "pv2_state": 1,
                "pv3_power": 58,
                "pv3_voltage": 50,
                "pv3_current": 1,
                "pv3_state": 1,
                "pv4_power": 33,
                "pv4_voltage": 49,
                "pv4_current": 0,
                "pv4_state": 1,
            },
        },
        profile,
    )
    merged = merge_device_status(
        es_status_data=es_status, em_status_data=em, pv_status_data=pv
    )

    assert merged["pv1_power"] == 41.5
    assert merged["pv2_power"] == 52
    assert merged["pv_power"] == 41.5 + 52 + 58 + 33
    assert merged["em_total_power"] == 169
    assert "bat_power" not in es_status


def test_issue_4_venus_a_bat_get_status_booleans() -> None:
    """Issue #4: Bat.GetStatus uses boolean charge flags and float capacity."""
    parsed = parse_bat_status_response(
        {
            "id": 334,
            "src": "VenusA-bc2a33602323",
            "result": {
                "id": 0,
                "soc": 23,
                "charg_flag": True,
                "dischrg_flag": True,
                "bat_temp": 14.0,
                "bat_capacity": 488.0,
                "rated_capacity": 2080.0,
            },
        }
    )

    assert parsed["bat_soc_detailed"] == 23
    assert parsed["bat_charg_flag"] is True
    assert parsed["bat_dischrg_flag"] is True
    assert parsed["bat_temp"] == 14.0
    assert parsed["bat_capacity"] == 488.0
    assert parsed["bat_rated_capacity"] == 2080.0


def test_issue_21_venus_e_144_getmode_has_no_ct_keys() -> None:
    """Issue #21: Venus E firmware 144 GetMode is mode/power/SoC only."""
    profile = resolve_firmware_profile("VenusE 3.0", 144)
    mode = parse_es_mode_response(
        {
            "id": 681,
            "src": "VenusE 3.0-xxxxx",
            "result": {
                "id": 0,
                "mode": "Manual",
                "ongrid_power": -497,
                "offgrid_power": 0,
                "bat_soc": 99,
            },
        },
        profile,
    )
    es_status = parse_es_status_response(
        {
            "id": 682,
            "src": "VenusE 3.0-xxxxx",
            "result": {
                "id": 0,
                "bat_soc": 99,
                "bat_cap": 5120,
                "pv_power": 0,
                "ongrid_power": -497,
                "offgrid_power": 0,
                "total_pv_energy": 0,
                "total_grid_output_energy": 5102,
                "total_grid_input_energy": 7816,
                "total_load_energy": 0,
            },
        },
        profile,
    )
    em = parse_em_status_response(
        {
            "id": 683,
            "src": "VenusE 3.0-xxxxx",
            "result": {
                "id": 0,
                "ct_state": 1,
                "a_power": 0,
                "b_power": 0,
                "c_power": 0,
                "total_power": 0,
                "input_energy": 0,
                "output_energy": 0,
            },
        },
        profile,
    )
    merged = merge_device_status(
        es_mode_data=mode, es_status_data=es_status, em_status_data=em
    )

    assert profile.supports_ups is False
    assert profile.supports_em_energy is False
    assert "ct_state" not in mode
    assert merged["device_mode"] == "manual"
    assert merged["battery_soc"] == 99
    assert merged["total_grid_input_energy"] == 7816
    assert merged["ct_state"] == 1
    assert merged["em_input_energy"] == 0


def test_issue_35_venus_a_149_scales_solar_but_not_grid_totals() -> None:
    """Issue #35: raw total_pv_energy 25968 is 0.01 kWh; grid totals stay Wh."""
    profile = resolve_firmware_profile("VenusA", 149)
    parsed = parse_es_status_response(
        {
            "id": 54,
            "src": "VenusA-...",
            "result": {
                "id": 0,
                "bat_soc": 13,
                "bat_cap": 2080,
                "pv_power": 180,
                "ongrid_power": 0,
                "offgrid_power": 0,
                "total_pv_energy": 25968,
                "total_grid_output_energy": 233453,
                "total_grid_input_energy": 18770,
                "total_load_energy": 0,
            },
        },
        profile,
    )

    assert profile.pv_energy_scale == 10.0
    assert parsed["total_pv_energy"] == 259680
    assert parsed["total_grid_output_energy"] == 233453
    assert parsed["total_grid_input_energy"] == 18770
    assert parsed["pv_power"] == 180


def test_issue_60_venus_c_153_getdevice_mac_from_src() -> None:
    """Issue #60: Venus C 153 GetDevice omits result MACs; src carries BLE MAC."""
    info = _build_device_info(
        {"device": "VenusC", "ver": 153, "ip": "192.168.2.37"},
        "192.168.2.37",
        30000,
        src="VenusC-AABBCCDDEEFF",
    )

    assert info["device_type"] == "VenusC"
    assert info["version"] == 153
    assert info["ble_mac"] == "AA:BB:CC:DD:EE:FF"
    assert info["wifi_mac"] == ""
    profile = resolve_firmware_profile(info["device_type"], info["version"])
    assert profile.supports_pv is False
    assert profile.supports_ups is True
    assert profile.supports_sys_dod is True
