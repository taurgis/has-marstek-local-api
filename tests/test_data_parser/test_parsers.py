"""One test class per Open API response parser."""

from __future__ import annotations

import pytest

from custom_components.marstek.firmware_profile import resolve_firmware_profile
from custom_components.marstek.pymarstek.data_parser import (
    parse_bat_status_response,
    parse_em_status_response,
    parse_es_mode_response,
    parse_es_status_response,
    parse_pv_status_response,
    parse_wifi_status_response,
)


class TestParseWifiStatusResponse:
    """Tests for parse_wifi_status_response."""

    def test_parse_full_response(self):
        """Test parsing a complete WiFi status response."""
        response = {
            "id": 1,
            "result": {
                "rssi": -58,
                "ssid": "TestNetwork",
                "sta_ip": "192.168.1.100",
                "sta_gate": "192.168.1.1",
                "sta_mask": "255.255.255.0",
                "sta_dns": "192.168.1.1",
            },
        }

        result = parse_wifi_status_response(response)

        assert result["wifi_rssi"] == -58
        assert result["wifi_ssid"] == "TestNetwork"
        assert result["wifi_sta_ip"] == "192.168.1.100"
        assert result["wifi_sta_gate"] == "192.168.1.1"
        assert result["wifi_sta_mask"] == "255.255.255.0"
        assert result["wifi_sta_dns"] == "192.168.1.1"

    def test_parse_empty_response(self):
        """Test parsing an empty response."""
        response = {"id": 1, "result": {}}

        result = parse_wifi_status_response(response)

        assert result["wifi_rssi"] is None
        assert result["wifi_ssid"] is None

    def test_parse_partial_response(self):
        """Test parsing a partial response with only RSSI."""
        response = {
            "id": 1,
            "result": {
                "rssi": -72,
            },
        }

        result = parse_wifi_status_response(response)

        assert result["wifi_rssi"] == -72
        assert result["wifi_ssid"] is None


class TestParseEmStatusResponse:
    """Tests for parse_em_status_response (Energy Meter / CT)."""

    def test_parse_connected_ct(self):
        """Test parsing EM status with connected CT."""
        response = {
            "id": 1,
            "result": {
                "ct_state": 1,
                "a_power": 120,
                "b_power": 115,
                "c_power": 125,
                "total_power": 360,
            },
        }

        result = parse_em_status_response(response)

        assert result["ct_state"] == 1
        assert result["ct_connected"] is True
        assert result["em_a_power"] == 120
        assert result["em_b_power"] == 115
        assert result["em_c_power"] == 125
        assert result["em_total_power"] == 360

    def test_parse_disconnected_ct(self):
        """Test parsing EM status with disconnected CT."""
        response = {
            "id": 1,
            "result": {
                "ct_state": 0,
                "total_power": 0,
            },
        }

        result = parse_em_status_response(response)

        assert result["ct_state"] == 0
        assert result["ct_connected"] is False
        assert result["em_total_power"] == 0

    def test_parse_empty_response(self):
        """Test parsing empty EM response."""
        response = {"id": 1, "result": {}}

        result = parse_em_status_response(response)

        assert result["ct_state"] is None
        assert result["ct_connected"] is None
        assert result["em_a_power"] is None
        assert result["em_total_power"] is None


class TestParseBatStatusResponse:
    """Tests for parse_bat_status_response."""

    def test_parse_full_response(self):
        """Test parsing a complete battery status response."""
        response = {
            "id": 1,
            "result": {
                "bat_temp": 27.5,
                "charg_flag": 1,
                "dischrg_flag": 1,
                "bat_capacity": 2560,
                "rated_capacity": 5120,
                "soc": 50,
            },
        }

        result = parse_bat_status_response(response)

        assert result["bat_temp"] == 27.5
        assert result["bat_charg_flag"] == 1
        assert result["bat_dischrg_flag"] == 1
        assert result["bat_capacity"] == 2560
        assert result["bat_rated_capacity"] == 5120
        assert result["bat_soc_detailed"] == 50

    def test_parse_empty_response(self):
        """Test parsing empty battery response returns None values."""
        response = {"id": 1, "result": {}}

        result = parse_bat_status_response(response)

        assert result["bat_temp"] is None
        assert result["bat_charg_flag"] is None
        assert result["bat_dischrg_flag"] is None

    def test_parse_charging_disabled(self):
        """Test parsing battery with charging disabled."""
        response = {
            "id": 1,
            "result": {
                "bat_temp": 45.0,  # High temp may disable charging
                "charg_flag": 0,
                "dischrg_flag": 1,
                "soc": 95,
            },
        }

        result = parse_bat_status_response(response)

        assert result["bat_temp"] == 45.0
        assert result["bat_charg_flag"] == 0
        assert result["bat_dischrg_flag"] == 1


class TestParsePvStatusResponse:
    """Tests for parse_pv_status_response."""

    def test_parse_multi_channel_format(self):
        """Test parsing multi-channel PV response (pv1_, pv2_, etc.)."""
        response = {
            "id": 1,
            "result": {
                "pv1_power": 300,
                "pv1_voltage": 35,
                "pv1_current": 8.5,
                "pv1_state": 1,
                "pv2_power": 250,
                "pv2_voltage": 34,
                "pv2_current": 7.3,
                "pv2_state": 1,
            },
        }

        result = parse_pv_status_response(response)

        assert result["pv1_power"] == 30.0
        assert result["pv1_voltage"] == 35
        assert result["pv1_current"] == 8.5
        assert result["pv1_state"] == 1
        assert result["pv2_power"] == 250

    def test_parse_single_channel_format(self):
        """Test parsing single-channel PV response (pv_ without number)."""
        response = {
            "id": 1,
            "result": {
                "pv_power": 500,
                "pv_voltage": 36,
                "pv_current": 13.8,
            },
        }

        result = parse_pv_status_response(response)

        # Should be mapped to pv1_* for consistency
        assert result["pv1_power"] == 50.0
        assert result["pv1_voltage"] == 36
        assert result["pv1_current"] == 13.8
        assert result["pv1_state"] == 1  # Active since power > 0

    def test_parse_single_channel_no_power(self):
        """Test single-channel format with zero power sets state to 0."""
        response = {
            "id": 1,
            "result": {
                "pv_power": 0,
                "pv_voltage": 0,
                "pv_current": 0,
            },
        }

        result = parse_pv_status_response(response)

        assert result["pv1_power"] == 0.0
        assert result["pv1_state"] == 0  # Inactive since power = 0

    def test_channel_breakdown_wins_over_the_aggregate(self):
        """A reply carrying both forms keeps all four channels, not the total."""
        response = {
            "id": 1,
            "result": {
                "pv_power": 500,
                "pv1_power": 8000,
                "pv2_power": 280,
                "pv3_power": 240,
                "pv4_power": 180,
            },
        }

        result = parse_pv_status_response(response)

        # 8000 deciwatts on channel 1, and the aggregate is not rescaled as if
        # it were that channel (which read 50.0 W for an 1500 W array).
        assert result["pv1_power"] == 800.0
        assert result["pv2_power"] == 280
        assert result["pv3_power"] == 240
        assert result["pv4_power"] == 180

    def test_unreadable_aggregate_keeps_the_channels(self):
        """An aggregate the firmware cannot read must not hide the channels."""
        response = {
            "id": 1,
            "result": {
                "pv_power": "unknown",
                "pv1_power": 8000,
                "pv2_power": 280,
            },
        }

        result = parse_pv_status_response(response)

        assert result["pv1_power"] == 800.0
        assert result["pv2_power"] == 280


class TestParseEsModeResponse:
    """Tests for parse_es_mode_response."""

    def test_parse_auto_mode(self):
        """Test parsing Auto mode response."""
        response = {
            "id": 1,
            "result": {
                "mode": "Auto",
                "bat_soc": 55,
                "ongrid_power": -150,
            },
        }

        result = parse_es_mode_response(response)

        assert result["device_mode"] == "auto"
        assert result["battery_soc"] == 55
        assert result["ongrid_power"] == -150

    def test_parse_ups_mode(self) -> None:
        """ES.GetMode wire value UPS becomes Home Assistant state ups."""
        result = parse_es_mode_response(
            {"id": 1, "result": {"mode": "UPS", "bat_soc": 80, "ongrid_power": 0}}
        )

        assert result["device_mode"] == "ups"

    @pytest.mark.parametrize(
        ("wire_mode", "expected"),
        [
            (0, "auto"),
            (1, "ai"),
            (2, "manual"),
            (3, "passive"),
            (4, "ups"),
            ("Auto", "auto"),
            ("AI", "ai"),
            ("Manual", "manual"),
            ("Passive", "passive"),
            ("UPS", "ups"),
            ("Ups", "ups"),
            ("0", "auto"),
            ("4", "ups"),
            ("SelfUse", "selfuse"),
        ],
    )
    def test_parse_integer_and_string_modes(self, wire_mode: int | str, expected: str) -> None:
        """Reads accept Open API strings, integer codes, and our unknown-string lowercase."""
        result = parse_es_mode_response(
            {"id": 1, "result": {"mode": wire_mode, "bat_soc": 80, "ongrid_power": 0}}
        )

        assert result["device_mode"] == expected
        assert "battery_power" not in result

    @pytest.mark.parametrize("wire_mode", [True, False, 5, -1, 4.0, None, "", "5"])
    def test_parse_rejects_non_mode_wire_values(self, wire_mode: object) -> None:
        """Booleans, unknown integers, and empty values are not operating modes."""
        result = parse_es_mode_response({"id": 1, "result": {"mode": wire_mode}})

        assert result["device_mode"] is None


class TestParseEsStatusResponse:
    """Tests for parse_es_status_response."""

    def test_parse_charging_status(self):
        """Test parsing charging battery status.

        API convention: bat_power > 0 = charging
        HA convention: battery_power < 0 = charging (negated)
        """
        response = {
            "id": 1,
            "result": {
                "bat_soc": 55,
                "bat_cap": 5120,
                "bat_power": 1000,  # API: positive = charging
                "pv_power": 1200,
                "ongrid_power": 200,
            },
        }

        result = parse_es_status_response(response)

        assert result["battery_soc"] == 55
        # HA convention: negative = charging
        assert result["battery_power"] == -1000
        assert result["battery_status"] == "charging"

    def test_parse_discharging_status(self):
        """Test parsing discharging battery status.

        API convention: bat_power < 0 = discharging
        HA convention: battery_power > 0 = discharging (negated)
        """
        response = {
            "id": 1,
            "result": {
                "bat_soc": 55,
                "bat_power": -800,  # API: negative = discharging
                "ongrid_power": -500,
            },
        }

        result = parse_es_status_response(response)

        # HA convention: positive = discharging
        assert result["battery_power"] == 800
        assert result["battery_status"] == "discharging"

    def test_parse_idle_status(self):
        """Test parsing idle battery status."""
        response = {
            "id": 1,
            "result": {
                "bat_soc": 55,
                "bat_power": 0,  # Zero = idle
            },
        }

        result = parse_es_status_response(response)

        assert result["battery_power"] == 0
        assert result["battery_status"] == "idle"

    def test_parse_missing_bat_power_fallback(self):
        """Test fallback calculation when bat_power is missing.

        Venus A/E devices don't provide bat_power. Fallback uses energy balance:
        bat_power = pv_power - ongrid_power (API convention)

        Example: pv=0, ongrid=+800 (exporting to grid while discharging)
        -> raw: 0 - 800 = -800 (API: discharging)
        -> HA: +800 (positive = discharging)
        """
        response = {
            "id": 1,
            "result": {
                "bat_soc": 55,
                # bat_power omitted by device (e.g., Venus A/E)
                "pv_power": 0,
                "ongrid_power": 800,  # Exporting to grid
            },
        }

        result = parse_es_status_response(response)

        # Fallback: raw = pv - ongrid = 0 - 800 = -800 (API: discharging)
        # HA convention (negate): +800 (positive = discharging)
        assert result["battery_power"] == 800
        assert result["battery_status"] == "discharging"

    def test_parse_missing_bat_power_fallback_charging(self):
        """Test fallback calculation when bat_power is missing and battery charges.

        Example: PV producing 1200W, exporting 200W to grid
        -> Battery absorbs: 1200 - 200 = 1000W (charging)
        -> raw = pv - ongrid = 1200 - 200 = 1000 (API: charging)
        -> HA: -1000 (negative = charging)
        """
        response = {
            "id": 1,
            "result": {
                "bat_soc": 55,
                # bat_power omitted by device (e.g., Venus A/E)
                "pv_power": 1200,
                "ongrid_power": 200,  # Exporting to grid
            },
        }

        result = parse_es_status_response(response)

        # Fallback: raw = pv - ongrid = 1200 - 200 = 1000 (API: charging)
        # HA convention (negate): -1000 (negative = charging)
        assert result["battery_power"] == -1000
        assert result["battery_status"] == "charging"

    def test_parse_missing_bat_power_zero_values_keeps_missing(self):
        """Test missing bat_power with zero pv/grid keeps battery values unset."""
        response = {
            "id": 1,
            "result": {
                "bat_soc": 55,
                # bat_power omitted by device
                "pv_power": 0,
                "ongrid_power": 0,
            },
        }

        result = parse_es_status_response(response)

        assert result["battery_power"] is None
        assert result["battery_status"] is None

    def test_parse_missing_bat_power_zero_flows_sets_idle(self):
        """Test missing bat_power with all zero flows sets idle battery power."""
        response = {
            "id": 1,
            "result": {
                "bat_soc": 55,
                # bat_power omitted by device
                "pv_power": 0,
                "ongrid_power": 0,
                "offgrid_power": 0,
            },
        }

        result = parse_es_status_response(response)

        assert result["battery_power"] == 0
        assert result["battery_status"] == "idle"

    def test_parse_bat_power_none_keeps_missing(self):
        """Test bat_power None remains unset to preserve previous values."""
        response = {
            "id": 1,
            "result": {
                "bat_soc": 55,
                "bat_power": None,
            },
        }

        result = parse_es_status_response(response)

        assert result["battery_power"] is None
        assert result["battery_status"] is None


class TestParsersToleratePayloadsFirmwareShouldNotSend:
    """Every parser must return defaults instead of raising.

    The three call sites already refuse a reply whose ``result`` is missing or
    not an object, but that contract belongs with the parsers: a payload shape
    nobody anticipated must degrade a single reading, not take down the poll.
    """

    PARSERS = (
        parse_es_mode_response,
        parse_es_status_response,
        parse_pv_status_response,
        parse_wifi_status_response,
        parse_em_status_response,
        parse_bat_status_response,
    )

    @pytest.mark.parametrize(
        "response",
        [
            {},
            {"id": 1},
            {"id": 1, "result": None},
            {"id": 1, "result": "OK"},
            {"id": 1, "result": []},
            {"id": 1, "result": 0},
            {"id": 1, "error": {"code": -32601, "message": "Method not found"}},
            [],
            "OK",
            None,
        ],
    )
    def test_no_parser_raises(self, response: object) -> None:
        """A malformed payload yields defaults, never an exception."""
        for parser in self.PARSERS:
            parsed = parser(response)  # type: ignore[arg-type]
            assert isinstance(parsed, dict)
            assert all(value is None for value in parsed.values())

    def test_integer_too_large_to_scale_is_dropped(self) -> None:
        """A wire integer beyond float range must not raise OverflowError."""
        parsed = parse_em_status_response(
            {"id": 1, "result": {"input_energy": 10**400, "total_power": 5}},
            resolve_firmware_profile("VenusE 3.0", 150),
        )

        assert parsed["em_input_energy"] is None
        assert parsed["em_total_power"] == 5


class TestScalePvPowerEdgeCases:
    """Tests for _scale_pv_power helper in parse_pv_status_response."""

    def test_pv_power_with_zero_value(self):
        """Test that zero PV power reports state 0."""
        response = {
            "id": 1,
            "result": {
                "pv_power": 0,
                "pv_voltage": 40.0,
                "pv_current": 0,
            },
        }

        result = parse_pv_status_response(response)

        assert result["pv1_power"] == 0.0
        assert result["pv1_state"] == 0

    def test_pv_power_scaled_correctly(self):
        """Test that PV power is scaled from deciwatts to watts."""
        response = {
            "id": 1,
            "result": {
                "pv_power": 1000,  # 1000 deciwatts = 100 watts
                "pv_voltage": 40.0,
                "pv_current": 2.5,
            },
        }

        result = parse_pv_status_response(response)

        assert result["pv1_power"] == 100.0  # 1000 / 10 = 100
        assert result["pv1_state"] == 1


class TestLoggerLazyImport:
    """Tests for lazy logger initialization."""

    def test_logger_lazy_initialized(self):
        """Test that _get_logger returns a logger."""
        from custom_components.marstek.pymarstek.data_parser import _get_logger

        logger = _get_logger()
        assert logger is not None
        assert logger.name == "custom_components.marstek.pymarstek.data_parser"

    def test_logger_cached_after_first_call(self):
        """Test that logger is cached and reused."""
        from custom_components.marstek.pymarstek.data_parser import _get_logger

        logger1 = _get_logger()
        logger2 = _get_logger()
        assert logger1 is logger2
