"""Tests for Marstek data parser functions."""

from __future__ import annotations

import pytest

from custom_components.marstek.pymarstek.data_parser import (
    merge_device_status,
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


class TestMergeDeviceStatus:
    """Tests for merge_device_status."""

    def test_merge_all_data_sources(self):
        """Test merging data from all API sources."""
        es_mode_data = {
            "device_mode": "auto",
            "ongrid_power": 150,
            "battery_soc": 55,
        }
        es_status_data = {
            "battery_soc": 55,
            "battery_power": -250,
            "battery_status": "discharging",
        }
        pv_status_data = {
            "pv1_power": 300,
            "pv1_voltage": 35,
        }
        wifi_status_data = {
            "wifi_rssi": -58,
            "wifi_ssid": "TestNetwork",
        }
        em_status_data = {
            "ct_state": 1,
            "ct_connected": True,
            "em_total_power": 360,
        }
        bat_status_data = {
            "bat_temp": 27.5,
            "bat_charg_flag": 1,
        }

        result = merge_device_status(
            es_mode_data=es_mode_data,
            es_status_data=es_status_data,
            pv_status_data=pv_status_data,
            wifi_status_data=wifi_status_data,
            em_status_data=em_status_data,
            bat_status_data=bat_status_data,
            device_ip="192.168.1.100",
            last_update=1234567890.0,
        )

        # Check all merged fields
        assert result["device_mode"] == "auto"
        assert result["battery_soc"] == 55
        assert result["battery_power"] == -250
        assert result["battery_status"] == "discharging"
        assert result["pv1_power"] == 300
        assert result["wifi_rssi"] == -58
        assert result["ct_connected"] is True
        assert result["em_total_power"] == 360
        assert result["bat_temp"] == 27.5
        assert result["device_ip"] == "192.168.1.100"
        assert result["last_update"] == 1234567890.0

    def test_merge_with_none_data_sources(self):
        """Test merging when some data sources are None."""
        es_mode_data = {
            "device_mode": "auto",
            "ongrid_power": 150,
        }

        result = merge_device_status(
            es_mode_data=es_mode_data,
            es_status_data=None,
            pv_status_data=None,
            wifi_status_data=None,
            em_status_data=None,
            bat_status_data=None,
        )

        # Should have defaults for missing data
        assert result["device_mode"] == "auto"
        assert result["battery_soc"] is None  # Default
        assert result["wifi_rssi"] is None  # Default for optional field
        assert result["ct_connected"] is None  # Default for optional field
        assert result["bat_temp"] is None  # Default for optional field

    def test_es_status_priority_over_es_mode(self):
        """Test that ES.GetStatus battery_soc takes priority over ES.GetMode."""
        es_mode_data = {
            "battery_soc": 50,  # Lower priority
        }
        es_status_data = {
            "battery_soc": 55,  # Higher priority
        }

        result = merge_device_status(
            es_mode_data=es_mode_data,
            es_status_data=es_status_data,
        )

        # ES.GetStatus should win
        assert result["battery_soc"] == 55

    def test_previous_status_preserves_values_on_partial_failure(self):
        """Test that previous_status values are preserved when individual requests fail.
        
        This prevents intermittent "Unknown" states when a single API endpoint
        times out but others succeed.
        """
        previous_status = {
            "device_mode": "auto",
            "battery_status": "discharging",
            "battery_soc": 60,
            "battery_power": -300,
            "ct_state": 1,
            "ct_connected": True,
            "em_total_power": 500,
            "wifi_rssi": -55,
            "bat_temp": 25.0,
        }
        
        # Simulate: ES.GetMode succeeded, but EM.GetStatus and Bat.GetStatus failed
        es_mode_data = {
            "device_mode": "auto",
            "ongrid_power": 150,
        }
        es_status_data = {
            "battery_soc": 58,  # Updated value
            "battery_power": -280,
            "battery_status": "discharging",
        }
        
        result = merge_device_status(
            es_mode_data=es_mode_data,
            es_status_data=es_status_data,
            pv_status_data=None,  # Failed
            wifi_status_data=None,  # Failed  
            em_status_data=None,  # Failed - would normally set ct_state to None
            bat_status_data=None,  # Failed - would normally set bat_temp to None
            previous_status=previous_status,
        )

        # Fresh data from successful requests
        assert result["device_mode"] == "auto"
        assert result["battery_soc"] == 58  # Updated
        assert result["battery_power"] == -280  # Updated
        assert result["battery_status"] == "discharging"
        
        # Preserved values from previous_status (requests failed)
        assert result["ct_state"] == 1  # Preserved
        assert result["ct_connected"] is True  # Preserved
        assert result["wifi_rssi"] == -55  # Preserved
        assert result["bat_temp"] == 25.0  # Preserved

    def test_previous_status_unknown_values_replaced(self):
        """Test that 'Unknown' default values are replaced by previous_status."""
        previous_status = {
            "device_mode": "auto",
            "battery_status": "idle",
        }
        
        # Simulate all requests failed - merge_device_status would use defaults
        result = merge_device_status(
            es_mode_data=None,
            es_status_data=None,
            previous_status=previous_status,
        )

        # Should use previous values instead of defaults
        assert result["device_mode"] == "auto"  # Preserved from previous
        assert result["battery_status"] == "idle"  # Preserved from previous

    def test_fresh_data_overrides_previous_status(self):
        """Test that fresh data always overrides previous_status values."""
        previous_status = {
            "battery_soc": 60,
            "battery_power": 300,
            "ct_connected": True,
        }
        
        es_status_data = {
            "battery_soc": 55,  # New value
            "battery_power": 0,  # New value (idle)
        }
        em_status_data = {
            "ct_connected": False,  # CT disconnected now
        }
        
        result = merge_device_status(
            es_status_data=es_status_data,
            em_status_data=em_status_data,
            previous_status=previous_status,
        )

        # Fresh data wins
        assert result["battery_soc"] == 55
        assert result["battery_power"] == 0
        assert result["ct_connected"] is False


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

    def test_parse_bat_power_none_defaults_idle(self):
        """Test bat_power None defaults to 0 and idle status."""
        response = {
            "id": 1,
            "result": {
                "bat_soc": 55,
                "bat_power": None,
            },
        }

        result = parse_es_status_response(response)

        assert result["battery_power"] == 0
        assert result["battery_status"] == "idle"


class TestMergeDeviceStatusNoPV:
    """Tests for merge_device_status without PV data (Venus C/E devices)."""

    def test_no_pv_keys_when_no_pv_data(self):
        """Test that PV keys are NOT included when no PV data is provided.
        
        Per API docs (Chapter 4): Only Venus D supports PV, not Venus C/E.
        When pv_status_data is None, no PV keys should be in the result.
        """
        result = merge_device_status(
            es_mode_data={"device_mode": "auto"},
            es_status_data={"battery_soc": 55},
            pv_status_data=None,  # No PV data - simulates Venus C/E
            wifi_status_data=None,
            em_status_data=None,
            bat_status_data=None,
        )

        # PV keys should NOT be present when no PV data provided
        assert "pv1_power" not in result
        assert "pv2_power" not in result
        assert "pv3_power" not in result
        assert "pv4_power" not in result

    def test_pv_keys_included_when_pv_data_provided(self):
        """Test that PV keys ARE included when PV data is provided (Venus D)."""
        pv_data = {
            "pv1_power": 300,
            "pv1_voltage": 40,
            "pv1_current": 7.5,
            "pv1_state": 1,
        }

        result = merge_device_status(
            es_mode_data={"device_mode": "auto"},
            es_status_data=None,
            pv_status_data=pv_data,  # Venus D with PV data
            wifi_status_data=None,
            em_status_data=None,
            bat_status_data=None,
        )

        # PV keys should be present when PV data provided
        assert result["pv1_power"] == 300
        assert result["pv1_voltage"] == 40
        assert result["pv1_current"] == 7.5
        assert result["pv1_state"] == 1
        # Only channels with data should be present
        assert "pv2_power" not in result


class TestDeviceSupportsP:
    """Tests for device_supports_pv function."""

    def test_venus_d_supports_pv(self):
        """Test Venus D variants are correctly identified as PV capable."""
        from custom_components.marstek.const import device_supports_pv

        assert device_supports_pv("VenusD") is True
        assert device_supports_pv("Venus D") is True
        assert device_supports_pv("venusd") is True
        assert device_supports_pv("VenusD 3.0") is True
        assert device_supports_pv("Venus D 3.0") is True

    def test_venus_c_e_no_pv_support(self):
        """Test Venus C/E variants are correctly identified as NOT PV capable."""
        from custom_components.marstek.const import device_supports_pv

        assert device_supports_pv("VenusC") is False
        assert device_supports_pv("VenusE 3.0") is False
        assert device_supports_pv("Venus C") is False
        assert device_supports_pv("venusc") is False
        assert device_supports_pv("venuse") is False

    def test_none_or_empty_no_pv_support(self):
        """Test None or empty device type returns False."""
        from custom_components.marstek.const import device_supports_pv

        assert device_supports_pv(None) is False
        assert device_supports_pv("") is False
        assert device_supports_pv("Unknown") is False


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


class TestMergeStatusPreviousStatusHandling:
    """Tests for previous status handling in merge_device_status."""

    def test_pv_keys_preserved_from_previous_when_missing(self):
        """Test that PV keys from previous status are preserved when not in current."""
        previous = {
            "pv1_power": 200,
            "pv1_voltage": 35,
            "pv1_current": 5.7,
            "battery_soc": 60,
        }
        
        result = merge_device_status(
            es_mode_data={"device_mode": "auto"},
            es_status_data={"battery_soc": 65},
            pv_status_data=None,  # No PV data this time
            previous_status=previous,
        )
        
        # PV keys should be preserved from previous status
        assert result["pv1_power"] == 200
        assert result["pv1_voltage"] == 35
        assert result["pv1_current"] == 5.7
        # Fresh data should override
        assert result["battery_soc"] == 65

    def test_previous_pv_none_not_preserved(self):
        """Test that None PV values from previous are not preserved."""
        previous = {
            "pv1_power": None,
            "pv1_voltage": 35,
        }
        
        result = merge_device_status(
            es_mode_data={"device_mode": "auto"},
            es_status_data=None,
            pv_status_data=None,
            previous_status=previous,
        )
        
        # None values should not be preserved
        assert "pv1_power" not in result
        # Non-None values should be preserved
        assert result["pv1_voltage"] == 35

    def test_previous_none_values_preserved_in_status(self):
        """Test that previous non-None values replace None in status dict."""
        previous = {
            "battery_soc": 75,
            "battery_power": 500,
            "wifi_rssi": -55,
        }
        
        result = merge_device_status(
            es_mode_data=None,  # No mode data - status[battery_soc] stays None
            es_status_data=None,  # No status data
            pv_status_data=None,
            wifi_status_data=None,
            previous_status=previous,
        )
        
        # Previous values should fill in None values in status
        assert result["battery_soc"] == 75
        assert result["battery_power"] == 500
        assert result["wifi_rssi"] == -55
