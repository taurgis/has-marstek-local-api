"""Tests for compatibility matrix scaling."""

from __future__ import annotations

from custom_components.marstek.pymarstek.compatibility import CompatibilityMatrix
from custom_components.marstek.pymarstek.data_parser import (
    parse_bat_status_response,
    parse_es_status_response,
    parse_pv_status_response,
)


def test_compatibility_model_and_hw_detection() -> None:
    matrix = CompatibilityMatrix("VenusE 3.0", 139)
    assert matrix.hardware_version == "3.0"
    assert matrix.model_key == "venuse"
    assert matrix.base_model == "VenusE"


def test_compatibility_scaling_hw2_fw100() -> None:
    matrix = CompatibilityMatrix("VenusE", 100)
    assert matrix.scale_value(1000, "bat_power") == 100.0
    assert matrix.scale_value(5000, "bat_capacity") == 50.0


def test_compatibility_scaling_hw2_fw200() -> None:
    matrix = CompatibilityMatrix("VenusE", 200)
    assert matrix.scale_value(1000, "bat_power") == 1000.0
    assert matrix.scale_value(250, "bat_temp") == 2500.0


def test_compatibility_unknown_model_and_fw_parse() -> None:
    matrix = CompatibilityMatrix("UnknownModel", "not-a-number")
    info = matrix.get_info()

    assert info["firmware_version"] == 0
    assert info["model_key"] == "*"
    assert matrix.scale_value("n/a", "bat_power") == "n/a"
    assert matrix.scale_value(10.0, "unknown_field") == 10.0


def test_compatibility_missing_hw_map_returns_raw() -> None:
    matrix = CompatibilityMatrix("VenusE 9.9", 200)
    assert matrix.hardware_version == "9.9"
    assert matrix.scale_value(1000, "bat_power") == 1000


def test_compatibility_capabilities_pv_support() -> None:
    assert CompatibilityMatrix("VenusA", 0).capabilities.supports_pv is True
    assert CompatibilityMatrix("VenusD", 0).capabilities.supports_pv is True
    assert CompatibilityMatrix("VenusE 3.0", 0).capabilities.supports_pv is False


def test_parse_es_status_response_applies_scaling() -> None:
    compatibility = CompatibilityMatrix("VenusE", 100)
    response = {
        "id": 1,
        "result": {
            "bat_soc": 55,
            "bat_power": 100,
            "pv_power": 0,
            "ongrid_power": 0,
        },
    }
    result = parse_es_status_response(response, compatibility=compatibility)

    assert result["battery_power"] == -10
    assert result["battery_status"] == "charging"


def test_parse_bat_status_response_applies_scaling() -> None:
    compatibility = CompatibilityMatrix("VenusE", 100)
    response = {
        "id": 1,
        "result": {
            "bat_temp": 25,
            "bat_capacity": 5000,
            "rated_capacity": 6000,
            "soc": 50,
            "charg_flag": 1,
            "dischrg_flag": 0,
        },
    }
    result = parse_bat_status_response(response, compatibility=compatibility)

    assert result["bat_temp"] == 25
    assert result["bat_capacity"] == 50.0
    assert result["bat_rated_capacity"] == 60.0


def test_parse_pv_status_response_compatibility_passthrough() -> None:
    compatibility = CompatibilityMatrix("VenusA", 200)
    response = {
        "id": 1,
        "result": {
            "pv_power": 250,
            "pv_voltage": 32,
            "pv_current": 5,
        },
    }
    result = parse_pv_status_response(response, compatibility=compatibility)
    assert result["pv1_power"] == 25.0
