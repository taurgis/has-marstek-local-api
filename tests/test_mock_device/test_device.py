"""Tests for MockMarstekDevice request/response handling."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_tools_dir = Path(__file__).parent.parent.parent / "tools"
if str(_tools_dir) not in sys.path:
    sys.path.insert(0, str(_tools_dir))

from mock_device import MockMarstekDevice
from mock_device.const import STATUS_CHARGING, STATUS_DISCHARGING


class TestDeviceResponses:
    """Tests for MockMarstekDevice request/response handling."""

    def test_es_get_status_after_passive_charging(self) -> None:
        """Test ES.GetStatus returns correct power after passive charging set."""
        # Enable include_bat_power to test direct bat_power code path
        device = MockMarstekDevice(
            port=30001,
            simulate=True,
            include_bat_power=True,
        )

        set_mode_params = {
            "id": 0,
            "config": {
                "mode": "Passive",
                "passive_cfg": {"power": -1400, "cd_time": 3600},
            },
        }

        set_mode_response = device._build_response(1, "ES.SetMode", set_mode_params)
        assert set_mode_response["result"]["set_result"] is True  # Per API spec

        get_status_response = device._build_response(2, "ES.GetStatus", {})
        get_mode_response = device._build_response(3, "ES.GetMode", {})

        result = get_status_response["result"]
        mode_result = get_mode_response["result"]

        assert mode_result["mode"] == "Passive"
        # API bat_power: positive = charging, negative = discharging
        # Internal power=-1400 (charging) -> API bat_power=+1400
        assert result["bat_power"] > 0
        assert 1300 < result["bat_power"] < 1500

    def test_es_get_status_after_passive_discharge(self) -> None:
        """Test ES.GetStatus returns correct power for passive discharging."""
        # Enable include_bat_power to test direct bat_power code path
        device = MockMarstekDevice(
            port=30002,
            simulate=True,
            include_bat_power=True,
        )

        set_mode_params = {
            "id": 0,
            "config": {
                "mode": "Passive",
                "passive_cfg": {"power": 1400, "cd_time": 3600},
            },
        }

        device._build_response(1, "ES.SetMode", set_mode_params)
        get_status_response = device._build_response(2, "ES.GetStatus", {})
        get_mode_response = device._build_response(3, "ES.GetMode", {})

        result = get_status_response["result"]

        assert get_mode_response["result"]["mode"] == "Passive"
        # API bat_power: positive = charging, negative = discharging
        # Internal power=1400 (discharging) -> API bat_power=-1400
        assert result["bat_power"] < 0
        assert 1300 < abs(result["bat_power"]) < 1500

    def test_es_get_status_after_manual_mode(self) -> None:
        """Test ES.GetStatus returns correct power after manual schedule set."""
        # Enable include_bat_power to test direct bat_power code path
        device = MockMarstekDevice(
            port=30003,
            simulate=True,
            include_bat_power=True,
        )

        set_mode_params = {
            "id": 0,
            "config": {
                "mode": "Manual",
                "manual_cfg": {
                    "time_num": 0,
                    "start_time": "00:00",
                    "end_time": "23:59",
                    "week_set": 127,
                    "power": -2000,
                    "enable": 1,
                },
            },
        }

        device._build_response(1, "ES.SetMode", set_mode_params)
        get_status_response = device._build_response(2, "ES.GetStatus", {})
        get_mode_response = device._build_response(3, "ES.GetMode", {})

        result = get_status_response["result"]

        assert get_mode_response["result"]["mode"] == "Manual"
        # API bat_power: positive = charging, negative = discharging
        # Internal power=-2000 (charging) -> API bat_power=+2000
        assert result["bat_power"] > 0
        assert 1900 < result["bat_power"] < 2100

    def test_es_get_status_with_simulation_thread(self) -> None:
        """Test ES.GetStatus returns correct values with simulation thread running."""
        # Enable include_bat_power to test direct bat_power code path
        device = MockMarstekDevice(
            port=30004,
            simulate=True,
            include_bat_power=True,
        )
        device.simulator.household.force_cooking_event(power=4000, duration_mins=60)
        device.simulator.start()

        try:
            time.sleep(0.5)

            set_mode_params = {
                "id": 0,
                "config": {
                    "mode": "Passive",
                    "passive_cfg": {"power": -1400, "cd_time": 3600},
                },
            }
            device._build_response(1, "ES.SetMode", set_mode_params)

            get_status_response = device._build_response(2, "ES.GetStatus", {})
            get_mode_response = device._build_response(3, "ES.GetMode", {})
            result = get_status_response["result"]

            assert get_mode_response["result"]["mode"] == "Passive"
            # API bat_power: positive = charging, negative = discharging
            # Internal power=-1400 (charging) -> API bat_power=+1400
            assert result["bat_power"] > 0
            assert 1300 < result["bat_power"] < 1500
        finally:
            device.simulator.stop()

    def test_es_get_status_venus_a_omits_bat_power(self) -> None:
        """Test VenusA ES.GetStatus omits bat_power field."""
        device = MockMarstekDevice(
            port=30020,
            simulate=False,
            device_config={"device": "VenusA 3.0", "ver": 145},
        )

        response = device._build_response(1, "ES.GetStatus", {})

        assert response is not None
        result = response["result"]
        assert "bat_soc" in result
        assert "bat_power" not in result

    def test_es_get_status_venus_e_omits_bat_power(self) -> None:
        """Test VenusE ES.GetStatus omits bat_power field (uses fallback calculation)."""
        device = MockMarstekDevice(
            port=30021,
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 145},
        )

        response = device._build_response(1, "ES.GetStatus", {})

        assert response is not None
        result = response["result"]
        assert "bat_soc" in result
        assert "pv_power" in result
        assert "ongrid_power" in result
        # Venus E omits bat_power - integration uses fallback: pv_power - ongrid_power
        assert "bat_power" not in result

    def test_es_get_status_with_include_bat_power_flag(self) -> None:
        """Test ES.GetStatus includes bat_power when include_bat_power=True."""
        # No real device is confirmed to return bat_power, but we support it
        # via include_bat_power=True for testing the direct code path
        device = MockMarstekDevice(
            port=30022,
            simulate=False,
            include_bat_power=True,
        )

        response = device._build_response(1, "ES.GetStatus", {})

        assert response is not None
        result = response["result"]
        assert "bat_soc" in result
        # bat_power included when flag is True
        assert "bat_power" in result


class TestDeviceDiscovery:
    """Tests for device discovery responses."""

    def test_marstek_get_device(self) -> None:
        """Test Marstek.GetDevice returns device info."""
        device = MockMarstekDevice(port=30005, simulate=False)

        response = device._build_response(1, "Marstek.GetDevice", {})

        assert response is not None
        assert "result" in response
        result = response["result"]
        assert "ble_mac" in result
        assert "device" in result  # device type
        assert "ip" in result

    def test_wifi_get_status(self) -> None:
        """Test Wifi.GetStatus returns WiFi info."""
        device = MockMarstekDevice(port=30006, simulate=False)

        response = device._build_response(1, "Wifi.GetStatus", {})

        assert response is not None
        result = response["result"]
        assert "rssi" in result
        assert "ssid" in result

    def test_pv_get_status_venus_d(self) -> None:
        """Test PV.GetStatus returns panel info for VenusD (only device with PV support)."""
        # Only Venus D supports PV per API docs (Chapter 4)
        device = MockMarstekDevice(
            port=30007, 
            simulate=False,
            device_config={"device": "VenusD", "ver": 145},
        )

        response = device._build_response(1, "PV.GetStatus", {})

        assert response is not None
        result = response["result"]
        # API spec: single channel format with pv_power, pv_voltage, pv_current
        assert "pv_power" in result
        assert "pv_voltage" in result
        assert "pv_current" in result
        assert "id" in result

    def test_pv_get_status_venus_e_returns_error(self) -> None:
        """Test PV.GetStatus returns error for VenusE (no PV support per API docs)."""
        # Venus E does NOT support PV per API docs (Chapter 4)
        device = MockMarstekDevice(port=30017, simulate=False)  # Default is VenusE 3.0

        response = device._build_response(1, "PV.GetStatus", {})

        assert response is not None
        # Should return error, not result
        assert "error" in response
        assert response["error"]["code"] == -32601  # Method not found
        assert "result" not in response

    def test_bat_get_status(self) -> None:
        """Test Bat.GetStatus returns battery info."""
        device = MockMarstekDevice(port=30008, simulate=False)

        response = device._build_response(1, "Bat.GetStatus", {})

        assert response is not None
        result = response["result"]
        assert "bat_temp" in result

    def test_em_get_status(self) -> None:
        """Test EM.GetStatus returns energy meter info."""
        device = MockMarstekDevice(port=30009, simulate=False)

        response = device._build_response(1, "EM.GetStatus", {})

        assert response is not None
        result = response["result"]
        assert "ct_state" in result  # CT clamp state


class TestStaticMode:
    """Tests for static (non-simulated) mode."""

    def test_static_mode_no_simulation(self) -> None:
        """Test device works without simulation enabled."""
        device = MockMarstekDevice(port=30010, simulate=False)

        response = device._build_response(1, "ES.GetStatus", {})

        assert response is not None
        # Values should be static/default
        assert "bat_soc" in response["result"]

    def test_static_mode_set_mode_still_works(self) -> None:
        """Test mode can be set even without simulation."""
        device = MockMarstekDevice(port=30011, simulate=False)

        set_mode_params = {
            "id": 0,
            "config": {"mode": "AI"},
        }
        device._build_response(1, "ES.SetMode", set_mode_params)
        get_mode_response = device._build_response(2, "ES.GetMode", {})

        assert get_mode_response["result"]["mode"] == "AI"


class TestAIMode:
    """Tests for AI mode functionality."""

    def test_ai_mode_set_and_read(self) -> None:
        """Test AI mode can be set and read back correctly."""
        device = MockMarstekDevice(port=30012, simulate=True)

        set_mode_params = {
            "id": 0,
            "config": {
                "mode": "AI",
                "ai_cfg": {"enable": 1},
            },
        }

        set_mode_response = device._build_response(1, "ES.SetMode", set_mode_params)
        assert set_mode_response["result"]["set_result"] is True

        get_mode_response = device._build_response(2, "ES.GetMode", {})
        assert get_mode_response["result"]["mode"] == "AI"

    def test_ai_mode_with_simulation(self) -> None:
        """Test AI mode behavior with simulation running."""
        device = MockMarstekDevice(port=30013, simulate=True)
        device.simulator.start()

        try:
            set_mode_params = {
                "id": 0,
                "config": {
                    "mode": "AI",
                    "ai_cfg": {"enable": 1},
                },
            }
            device._build_response(1, "ES.SetMode", set_mode_params)

            # Let simulation run briefly
            time.sleep(0.3)

            get_mode_response = device._build_response(2, "ES.GetMode", {})
            get_status_response = device._build_response(3, "ES.GetStatus", {})

            # Mode should be AI
            assert get_mode_response["result"]["mode"] == "AI"

            # Battery should be responding (SOC and power should be reasonable)
            result = get_status_response["result"]
            assert 0 <= result["bat_soc"] <= 100
        finally:
            device.simulator.stop()
