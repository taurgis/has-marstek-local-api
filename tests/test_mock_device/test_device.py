"""Tests for MockMarstekDevice request/response handling."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mock_device import MockMarstekDevice
from mock_device.__main__ import main
from custom_components.marstek.firmware_profile import (
    DeviceFamily,
    is_unsupported_venus_e2,
    resolve_firmware_profile,
)
from custom_components.marstek.pymarstek.data_parser import (
    merge_device_status,
    parse_em_status_response,
    parse_es_status_response,
    parse_pv_status_response,
)


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

        response = device.build_response(1, "Marstek.GetDevice", {})

        assert response is not None
        assert "result" in response
        result = response["result"]
        assert "ble_mac" in result
        assert "device" in result  # device type
        assert "ip" in result
        assert result["ver"] == 145

    def test_requested_firmware_is_returned_as_integer(self) -> None:
        """Programmatic firmware generation is visible in discovery."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={"device": "VenusD", "ver": "150"},
        )

        response = device.build_response(1, "Marstek.GetDevice", {})

        assert response is not None
        assert response["result"]["ver"] == 150
        assert isinstance(response["result"]["ver"], int)

    def test_venus_c_153_getdevice_omits_result_macs(self) -> None:
        """Issue #60: Venus C 153 puts the BLE MAC in src, not result."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={
                "device": "VenusC",
                "ver": 153,
                "ble_mac": "aabbccddeeff",
                "wifi_mac": "112233445566",
            },
        )

        response = device.build_response(1, "Marstek.GetDevice", {})

        assert response is not None
        assert response["src"] == "VenusC-aabbccddeeff"
        assert response["result"]["device"] == "VenusC"
        assert response["result"]["ver"] == 153
        assert "ble_mac" not in response["result"]
        assert "wifi_mac" not in response["result"]
        assert "wifi_name" not in response["result"]
        assert "ip" in response["result"]
        em = device.build_response(2, "EM.GetStatus", {"id": 0})
        dod = device.build_response(3, "DOD.SET", {"value": 80})
        ups = device.build_response(
            4,
            "ES.SetMode",
            {"id": 0, "config": {"mode": "UPS", "ups_cfg": {"enable": 1}}},
        )
        unknown = device.build_response(5, "Set.Ver", {"ver": 153})
        status = device.build_response(6, "ES.GetStatus", {"id": 0})
        wifi = device.build_response(
            7, "Wifi.SetConfig", {"ssid": "HMG50-Open", "pass": "secret"}
        )
        assert em is not None
        assert em["error"]["code"] == -32601
        assert dod is not None
        assert dod["error"]["code"] == -32601
        assert ups is not None
        assert ups["error"]["code"] == -32601
        assert unknown is not None
        assert unknown["error"]["code"] == -32601
        assert unknown["error"]["message"] == "Method not found"
        assert status is not None
        assert "bat_power" in status["result"]
        assert wifi is not None
        assert wifi["result"]["set_result"] is True
        assert device.config["wifi_name"] == "HMG50-Open"
        assert device.profile.hmg50_control is True
        assert device.profile.supports_em_status is False
        assert device.profile.openapi_reset_prone is True

    def test_venus_c_155_serves_em_without_sys(self) -> None:
        """HMG-50 Control 155 added Open API EM.GetStatus; SYS stays absent."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={
                "device": "VenusC",
                "ver": 155,
                "ble_mac": "aabbccddeeff",
                "wifi_mac": "112233445566",
            },
        )

        em = device.build_response(2, "EM.GetStatus", {"id": 0})
        dod = device.build_response(3, "DOD.SET", {"value": 80})
        discovery = device.build_response(1, "Marstek.GetDevice", {})
        status = device.build_response(4, "ES.GetStatus", {"id": 0})

        assert em is not None
        assert "result" in em
        assert dod is not None
        assert dod["error"]["code"] == -32601
        assert discovery is not None
        assert "ble_mac" not in discovery["result"]
        assert status is not None
        assert "bat_power" not in status["result"]
        assert device.profile.openapi_reset_prone is True

    def test_hmg50_155_serves_em_get_status(self) -> None:
        """HMG-50 Control 155 recv list includes EM.GetStatus as a server method."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={
                "device": "VenusE",
                "ver": 155,
                "ble_mac": "02deadbeef09",
                "wifi_mac": "02cafebabe09",
            },
        )

        em = device.build_response(2, "EM.GetStatus", {"id": 0})

        assert em is not None
        assert "result" in em
        assert "error" not in em
        assert device.profile.openapi_reset_prone is True

    def test_unknown_method_returns_method_not_found(self) -> None:
        """VNSE3-0 has no Wifi.SetConfig recv entry; it answers -32601."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 150},
        )

        response = device.build_response(1, "Wifi.SetConfig", {"ssid": "x"})

        assert response is not None
        assert response["error"] == {
            "code": -32601,
            "message": "Method not found",
        }

    def test_venus_e_1476_pv_error_omits_firmware_150_data(self) -> None:
        """ver 1476 is app 147.6; do not copy the firmware 150 PV error.data."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 1476},
        )

        response = device.build_response(1, "PV.GetStatus", {"id": 0})

        assert response is not None
        assert response["error"] == {
            "code": -32601,
            "message": "Method not found",
        }

    def test_set_ver_follows_control_recv_list(self) -> None:
        """Set.Ver is on the 1487/149+ recv list, not HMG-50 or plain 148."""
        venus_a_148 = MockMarstekDevice(
            simulate=False, device_config={"device": "VenusA", "ver": 148}
        )
        venus_a_1487 = MockMarstekDevice(
            simulate=False, device_config={"device": "VenusA", "ver": 1487}
        )
        venus_e_150 = MockMarstekDevice(
            simulate=False, device_config={"device": "VenusE 3.0", "ver": 150}
        )
        venus_e_pro = MockMarstekDevice(
            simulate=False, device_config={"device": "VenusE Pro", "ver": 1508}
        )

        rejected = venus_a_148.build_response(1, "Set.Ver", {"version": 0})
        accepted = venus_a_1487.build_response(1, "Set.Ver", {"version": 0})
        venus_e = venus_e_150.build_response(1, "Set.Ver", {"version": 0})
        pro = venus_e_pro.build_response(1, "Set.Ver", {"version": 0})
        factory = venus_e_150.build_response(2, "Reset.Factory", {"type": 2})

        assert rejected is not None
        assert rejected["error"]["code"] == -32601
        assert accepted is not None
        assert accepted["result"]["set_result"] is True
        assert venus_e is not None
        assert venus_e["result"]["set_result"] is True
        assert pro is not None
        assert pro["result"]["set_result"] is True
        assert factory is not None
        assert factory["result"]["set_result"] is True

    def test_reset_factory_type_1_clears_energy_totals(self) -> None:
        """Rev 3.1 type 1 clears totals; the mock zeros the energy counters."""
        device = MockMarstekDevice(
            simulate=False, device_config={"device": "VenusE 3.0", "ver": 150}
        )
        device.set_energy_totals(total_pv_energy=100, total_load_energy=20)

        before = device.build_response(1, "ES.GetStatus", {"id": 0})
        reset = device.build_response(2, "Reset.Factory", {"type": 1})
        after = device.build_response(3, "ES.GetStatus", {"id": 0})

        assert before is not None
        assert before["result"]["total_pv_energy"] != 0
        assert reset is not None
        assert reset["result"]["set_result"] is True
        assert after is not None
        assert after["result"]["total_pv_energy"] == 0
        assert after["result"]["total_load_energy"] == 0

    def test_hmg50_wifi_set_config_requires_ssid(self) -> None:
        """HMG-50 Wifi.SetConfig validates ssid the way the 153 strings describe."""
        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "VenusC", "ver": 153, "wifi_name": "Old"},
        )

        missing = device.build_response(1, "Wifi.SetConfig", {"pass": "x"})
        ok = device.build_response(2, "Wifi.SetConfig", {"ssid": "OpenLAN"})
        wifi = device.build_response(3, "Wifi.GetStatus", {})

        assert missing is not None
        assert missing["error"]["code"] == -32602
        assert ok is not None
        assert ok["result"]["set_result"] is True
        assert wifi is not None
        assert wifi["result"]["ssid"] == "OpenLAN"

    def test_hmg50_getdevice_uses_venuse_identity(self) -> None:
        """HMG-50 Control 153 GetDevice reports device=VenusE, src VenusE-mac."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={
                "device": "VenusE",
                "ver": 153,
                "ble_mac": "02deadbeef09",
                "wifi_mac": "02cafebabe09",
            },
        )

        response = device.build_response(1, "Marstek.GetDevice", {})

        assert response is not None
        assert response["src"] == "VenusE-02deadbeef09"
        assert response["result"]["device"] == "VenusE"
        assert response["result"]["ver"] == 153
        assert is_unsupported_venus_e2(response["result"]["device"]) is True
        assert device.profile.family is DeviceFamily.UNKNOWN
        assert device.profile.supports_ups is False
        assert device.profile.supports_sys_dod is False
        assert response["result"]["ble_mac"] == "02deadbeef09"
        assert response["result"]["wifi_mac"] == "02cafebabe09"
        em = device.build_response(2, "EM.GetStatus", {"id": 0})
        pv = device.build_response(3, "PV.GetStatus", {})
        dod = device.build_response(4, "DOD.SET", {"value": 80})
        status = device.build_response(5, "ES.GetStatus", {"id": 0})
        assert em is not None
        assert em["error"]["code"] == -32601
        assert pv is not None
        assert pv["error"]["code"] == -32601
        assert "data" not in pv["error"]
        assert dod is not None
        assert dod["error"]["code"] == -32601
        assert status is not None
        assert "bat_power" in status["result"]
        assert status["result"]["bat_soc"] == 50

    def test_hmg50_156_serves_em_get_status(self) -> None:
        """HMG-50 Control 156 keeps Open API EM.GetStatus and is not reset-prone."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={
                "device": "VenusE",
                "ver": 156,
                "ble_mac": "02deadbeef09",
                "wifi_mac": "02cafebabe09",
            },
        )

        em = device.build_response(2, "EM.GetStatus", {"id": 0})

        assert em is not None
        assert "result" in em
        assert "error" not in em
        assert em["src"] == "VenusE-02deadbeef09"

    def test_venus_a_147_getmode_includes_zero_ct_keys(self) -> None:
        """Issue #11: Venus A 147 GetMode includes CT/energy keys as zeros."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={"device": "VenusA", "ver": 147},
        )

        mode = device.build_response(1, "ES.GetMode", {"id": 0})
        em = device.build_response(2, "EM.GetStatus", {"id": 0})
        status = device.build_response(3, "ES.GetStatus", {"id": 0})

        assert mode is not None
        assert em is not None
        assert status is not None
        assert mode["result"]["ct_state"] == 0
        assert mode["result"]["input_energy"] == 0
        assert em["result"]["ct_state"] == 1
        assert em["result"]["input_energy"] == 0
        assert status["result"]["pv_power"] == 0
        assert "bat_power" not in status["result"]

    def test_legacy_profile_round_trips_physical_watts_and_wh(self) -> None:
        """Legacy mock wire JSON decodes through production into SI units."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={
                "device": "VenusD",
                "ver": 145,
                "pv_channels": [
                    {
                        "channel": 1,
                        "pv_power": 320,
                        "pv_voltage": 42,
                        "pv_current": 7.6,
                    },
                    {
                        "channel": 2,
                        "pv_power": 280,
                        "pv_voltage": 40,
                        "pv_current": 7,
                    },
                ],
            },
        )
        device.set_energy_totals(total_pv_energy=257420)
        profile = resolve_firmware_profile("VenusD", 145)

        pv_response = device.build_response(2, "PV.GetStatus", {})
        es_response = device.build_response(3, "ES.GetStatus", {})

        assert pv_response is not None
        assert es_response is not None
        assert pv_response["result"]["pv1_power"] == 3200
        assert pv_response["result"]["pv2_power"] == 280
        assert es_response["result"]["total_pv_energy"] == 257420

        status = merge_device_status(
            pv_status_data=parse_pv_status_response(pv_response, profile),
            es_status_data=parse_es_status_response(es_response, profile),
        )
        assert status["pv1_power"] == 320
        assert status["pv2_power"] == 280
        assert status["total_pv_energy"] == 257420

    def test_venus_a_148_round_trips_unscaled_energy_and_deciwatt_pv(self) -> None:
        """Venus A 148 keeps solar Wh on the wire and channel-1 deciwatts."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={
                "device": "VenusA",
                "ver": 148,
                "pv_channels": [
                    {
                        "channel": 1,
                        "pv_power": 360,
                        "pv_voltage": 44,
                        "pv_current": 8.2,
                    },
                    {
                        "channel": 2,
                        "pv_power": 300,
                        "pv_voltage": 41,
                        "pv_current": 7.3,
                    },
                ],
            },
        )
        device.set_energy_totals(total_pv_energy=257420)
        profile = resolve_firmware_profile("VenusA", 148)

        pv_response = device.build_response(2, "PV.GetStatus", {})
        es_response = device.build_response(3, "ES.GetStatus", {})

        assert pv_response is not None
        assert es_response is not None
        assert pv_response["result"]["pv1_power"] == 3600
        assert pv_response["result"]["pv2_power"] == 300
        assert es_response["result"]["total_pv_energy"] == 257420

        status = merge_device_status(
            pv_status_data=parse_pv_status_response(pv_response, profile),
            es_status_data=parse_es_status_response(es_response, profile),
        )
        assert status["pv1_power"] == 360
        assert status["pv2_power"] == 300
        assert status["total_pv_energy"] == 257420

    def test_venus_a_149_round_trips_scaled_energy_and_deciwatt_pv(self) -> None:
        """Venus A 149 encodes solar as 0.01 kWh but still reports channel-1 deciwatts."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={
                "device": "VenusA",
                "ver": 149,
                "pv_channels": [
                    {
                        "channel": 1,
                        "pv_power": 360,
                        "pv_voltage": 44,
                        "pv_current": 8.2,
                    },
                    {
                        "channel": 2,
                        "pv_power": 300,
                        "pv_voltage": 41,
                        "pv_current": 7.3,
                    },
                ],
            },
        )
        device.set_energy_totals(total_pv_energy=257420)
        profile = resolve_firmware_profile("VenusA", 149)

        pv_response = device.build_response(2, "PV.GetStatus", {})
        es_response = device.build_response(3, "ES.GetStatus", {})

        assert pv_response is not None
        assert es_response is not None
        assert pv_response["result"]["pv1_power"] == 3600
        assert pv_response["result"]["pv2_power"] == 300
        assert es_response["result"]["total_pv_energy"] == 25742

        status = merge_device_status(
            pv_status_data=parse_pv_status_response(pv_response, profile),
            es_status_data=parse_es_status_response(es_response, profile),
        )
        assert status["pv1_power"] == 360
        assert status["pv2_power"] == 300
        assert status["total_pv_energy"] == 257420

    def test_firmware_150_round_trips_scaled_energy_and_deciwatt_pv(self) -> None:
        """Firmware 150 keeps PV1 deciwatts (#57 / 150.9) and scales solar energy (#35)."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={
                "device": "VenusA",
                "ver": 150,
                "pv_channels": [
                    {
                        "channel": 1,
                        "pv_power": 320,
                        "pv_voltage": 44,
                        "pv_current": 8.2,
                    },
                    {
                        "channel": 2,
                        "pv_power": 280,
                        "pv_voltage": 41,
                        "pv_current": 7.3,
                    },
                ],
            },
        )
        device.set_energy_totals(
            total_pv_energy=257420,
            em_input_energy=308632,
            em_output_energy=448751,
        )
        profile = resolve_firmware_profile("VenusA", "150.9")

        pv_response = device.build_response(2, "PV.GetStatus", {})
        es_response = device.build_response(3, "ES.GetStatus", {})
        em_response = device.build_response(4, "EM.GetStatus", {})

        assert pv_response is not None
        assert es_response is not None
        assert em_response is not None
        assert profile.firmware_version == 150
        assert pv_response["result"]["pv1_power"] == 3200
        assert pv_response["result"]["pv2_power"] == 280
        assert es_response["result"]["total_pv_energy"] == 25742
        assert em_response["result"]["input_energy"] == 3086320
        assert em_response["result"]["output_energy"] == 4487510

        status = merge_device_status(
            pv_status_data=parse_pv_status_response(pv_response, profile),
            es_status_data=parse_es_status_response(es_response, profile),
            em_status_data=parse_em_status_response(em_response, profile),
        )
        assert status["pv1_power"] == 320
        assert status["pv2_power"] == 280
        assert status["total_pv_energy"] == 257420
        assert status["em_input_energy"] == 308632
        assert status["em_output_energy"] == 448751

    def test_legacy_em_status_includes_unscaled_energy_keys(self) -> None:
        """Legacy firmware still reports EM energy keys (issues #11 and #21)."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 145},
        )

        response = device.build_response(1, "EM.GetStatus", {})

        assert response is not None
        assert response["result"]["input_energy"] == 0
        assert response["result"]["output_energy"] == 0

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


class TestFirmwareCli:
    """Tests for the versioned mock command line."""

    def test_cli_passes_selected_firmware_to_discovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The CLI-selected integer is used by the running mock."""
        selected: list[int | None] = []

        def capture_start(device: MockMarstekDevice) -> None:
            response = device.build_response(1, "Marstek.GetDevice", {})
            assert response is not None
            selected.append(response["result"]["ver"])

        monkeypatch.setattr(
            "sys.argv",
            ["mock_device", "--ver", "150", "--state-dir", ""],
        )
        with patch.object(MockMarstekDevice, "start", capture_start):
            main()

        assert selected == [150]

    @pytest.mark.parametrize("version", ["not-a-version", "-1"])
    def test_cli_rejects_invalid_firmware(
        self,
        version: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Invalid firmware input exits with a clear command-line error."""
        monkeypatch.setattr("sys.argv", ["mock_device", "--ver", version])

        with pytest.raises(SystemExit) as err:
            main()

        assert err.value.code == 2
        assert "--ver" in capsys.readouterr().err


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


class TestManualScheduleSlots:
    """Tests for profile-specific mock schedule validation."""

    @staticmethod
    def _manual_request(slot: object) -> dict[str, object]:
        return {
            "id": 0,
            "config": {
                "mode": "Manual",
                "manual_cfg": {
                    "time_num": slot,
                    "start_time": "00:00",
                    "end_time": "23:59",
                    "week_set": 127,
                    "power": 100,
                    "enable": 1,
                },
            },
        }

    @pytest.mark.parametrize(
        ("device_type", "accepted_slot", "rejected_slot"),
        [("Venus E mini", 5, 6), ("VenusE 3.0", 9, 10), ("Other", 9, 10)],
    )
    def test_profile_schedule_boundaries(
        self,
        device_type: str,
        accepted_slot: int,
        rejected_slot: int,
    ) -> None:
        """Known and unknown families enforce their profile slot bounds."""
        device = MockMarstekDevice(
            simulate=True,
            device_config={"device": device_type, "ver": 145},
        )

        accepted = device.build_response(
            1, "ES.SetMode", self._manual_request(accepted_slot)
        )
        assert accepted is not None
        assert accepted["result"]["set_result"] is True

        rejected = device.build_response(
            2, "ES.SetMode", self._manual_request(rejected_slot)
        )
        assert rejected is not None
        assert rejected["error"] == {"code": -32602, "message": "Invalid params"}
        mode = device.build_response(3, "ES.GetMode", {})
        assert mode is not None
        assert mode["result"]["mode"] == "Manual"

    @pytest.mark.parametrize("slot", [-1, 1.5, "5", None, True])
    def test_invalid_slot_does_not_mutate_state(self, slot: object) -> None:
        """Invalid schedule slot types return JSON-RPC Invalid params."""
        device = MockMarstekDevice(
            simulate=True,
            device_config={"device": "Venus E mini", "ver": 145},
        )

        response = device.build_response(1, "ES.SetMode", self._manual_request(slot))

        assert response is not None
        assert response["error"]["code"] == -32602
        mode = device.build_response(2, "ES.GetMode", {})
        assert mode is not None
        assert mode["result"]["mode"] == "Auto"


class TestPersistence:
    """Tests for mock device state persistence."""

    def test_persistent_state_round_trip(self, tmp_path: Path) -> None:
        """Persisted SOC and energy totals should survive restarts."""
        ble_mac = "001122334455"
        state_dir = str(tmp_path)

        device = MockMarstekDevice(
            port=30100,
            simulate=True,
            device_config={"ble_mac": ble_mac},
            state_dir=state_dir,
        )
        device.simulator.soc = 77
        device.simulator.total_pv_energy = 12.5
        device.simulator.total_grid_output_energy = 34.0
        device.simulator.total_grid_input_energy = 1234.5
        device.simulator.total_load_energy = 4567.8
        device._persist_state()

        restarted = MockMarstekDevice(
            port=30101,
            simulate=True,
            device_config={"ble_mac": ble_mac},
            state_dir=state_dir,
        )

        assert restarted.simulator.soc == pytest.approx(77.0)
        assert restarted.simulator.total_pv_energy == pytest.approx(12.5)
        assert restarted.simulator.total_grid_output_energy == pytest.approx(34.0)
        assert restarted.simulator.total_grid_input_energy == pytest.approx(1234.5)
        assert restarted.simulator.total_load_energy == pytest.approx(4567.8)

    def test_persistent_state_reset(self, tmp_path: Path) -> None:
        """Reset flag should clear persisted state."""
        ble_mac = "00aa11bb22cc"
        state_dir = str(tmp_path)

        device = MockMarstekDevice(
            port=30102,
            simulate=True,
            device_config={"ble_mac": ble_mac},
            state_dir=state_dir,
        )
        device.simulator.soc = 88
        device.simulator.total_grid_input_energy = 987.6
        device._persist_state()

        restarted = MockMarstekDevice(
            port=30103,
            simulate=True,
            device_config={"ble_mac": ble_mac},
            state_dir=state_dir,
            reset_state=True,
            initial_soc=50,
        )

        assert restarted.simulator.soc == pytest.approx(50.0)
        assert restarted.simulator.total_grid_input_energy == 0.0


def _ups_set_mode_params() -> dict[str, object]:
    return {
        "id": 0,
        "config": {"mode": "UPS", "ups_cfg": {"enable": 1}},
    }


class TestUpsMode:
    """Firmware-gated UPS handling on the mock Open API."""

    def test_legacy_mock_rejects_ups_without_changing_mode(self) -> None:
        """Legacy firmware returns Method not found and keeps Auto."""
        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 145},
        )

        response = device.build_response(1, "ES.SetMode", _ups_set_mode_params())

        assert response is not None
        assert response["error"] == {"code": -32601, "message": "Method not found"}
        assert "result" not in response
        mode = device.build_response(2, "ES.GetMode", {})
        assert mode is not None
        assert mode["result"]["mode"] == "Auto"

    def test_firmware_150_mock_accepts_ups_and_reports_it(self) -> None:
        """Firmware 150+ stores UPS and reports it from ES.GetMode."""
        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 150},
        )

        response = device.build_response(1, "ES.SetMode", _ups_set_mode_params())

        assert response is not None
        assert response["result"]["set_result"] is True
        mode = device.build_response(2, "ES.GetMode", {})
        assert mode is not None
        assert mode["result"]["mode"] == "UPS"

    def test_e_mini_150_mock_accepts_ups(self) -> None:
        """Venus E mini at firmware 150 accepts UPS."""
        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "Venus E mini", "ver": 150},
        )

        response = device.build_response(1, "ES.SetMode", _ups_set_mode_params())

        assert response is not None
        assert response["result"]["set_result"] is True
        mode = device.build_response(2, "ES.GetMode", {})
        assert mode is not None
        assert mode["result"]["mode"] == "UPS"

    def test_ups_profile_round_trip_through_parser(self) -> None:
        """Profile → select option → encoded SetMode → mock → parsed GetMode ups."""
        import json

        from custom_components.marstek.const import (
            CMD_ES_SET_MODE,
            MODE_UPS,
            selectable_operating_modes,
        )
        from custom_components.marstek.mode_config import build_mode_config
        from custom_components.marstek.pymarstek.command_builder import build_command
        from custom_components.marstek.pymarstek.data_parser import parse_es_mode_response

        profile = resolve_firmware_profile("VenusE 3.0", 150)
        assert profile.supports_ups is True
        assert MODE_UPS in selectable_operating_modes(profile)

        command = json.loads(
            build_command(
                CMD_ES_SET_MODE,
                {"id": 0, "config": build_mode_config(MODE_UPS)},
            )
        )
        assert command["params"]["config"] == {
            "mode": "UPS",
            "ups_cfg": {"enable": 1},
        }

        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 150},
        )
        set_response = device.build_response(1, "ES.SetMode", command["params"])
        assert set_response is not None
        assert set_response["result"]["set_result"] is True

        get_response = device.build_response(2, "ES.GetMode", {})
        assert get_response is not None
        parsed = parse_es_mode_response(get_response, profile)
        assert parsed["device_mode"] == "ups"


class TestSysWrites:
    """SYS method accept/reject behavior follows the firmware profile."""

    SYS_REQUESTS = (
        ("DOD.SET", {"value": 50}),
        ("Ble.Adv", {"enable": 0}),
        ("Led.Ctrl", {"state": 1}),
    )

    def test_capable_profile_accepts_sys_methods(self) -> None:
        """Firmware 150+ regular devices return set_result true for SYS writes."""
        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "VenusA", "ver": 150},
        )
        for method, params in self.SYS_REQUESTS:
            response = device.build_response(1, method, params)
            assert response is not None
            assert "error" not in response
            assert response["result"]["set_result"] is True

    def test_e_mini_known_ver_accepts_sys_without_firmware_150(self) -> None:
        """Venus E mini with a known integer ver accepts SYS without the 150 gate."""
        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "Venus E mini", "ver": 12},
        )
        assert device.profile.supports_sys_dod is True
        for method, params in self.SYS_REQUESTS:
            response = device.build_response(1, method, params)
            assert response is not None
            assert response["result"]["set_result"] is True

    def test_legacy_profile_returns_method_not_found(self) -> None:
        """Legacy mock returns JSON-RPC -32601 instead of timing out."""
        device = MockMarstekDevice(simulate=False)
        for method, params in self.SYS_REQUESTS:
            response = device.build_response(1, method, params)
            assert response is not None
            assert response["error"]["code"] == -32601
            assert response["error"]["message"] == "Method not found"


class TestVenusEFirmware150Capture:
    """Mock Venus E 3.0 / ver 150 matches the 2026-09-15 LAN GET capture."""

    def test_legacy_getmode_omits_rev31_meter_keys(self) -> None:
        """Firmware 145 Venus E does not invent GetMode CT/energy fields."""
        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 145},
        )

        response = device.build_response(1, "ES.GetMode", {"id": 0})

        assert response is not None
        result = response["result"]
        assert result["mode"] == "Auto"
        assert result["id"] == 0
        assert "ct_state" not in result
        assert "input_energy" not in result

    def test_getmode_ct_keys_are_zeros_while_em_is_live(self) -> None:
        """Firmware 150 Venus E GetMode CT template stays zeros; EM has the live CT."""
        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 150},
        )
        device._static_power = 800

        mode = device.build_response(1, "ES.GetMode", {"id": 0})
        em = device.build_response(2, "EM.GetStatus", {"id": 0})
        status = device.build_response(3, "ES.GetStatus", {"id": 0})

        assert mode is not None
        assert em is not None
        assert status is not None
        assert mode["result"]["mode"] == "Auto"
        assert mode["result"]["id"] == 0
        assert mode["result"]["ct_state"] == 0
        assert mode["result"]["a_power"] == 0
        assert mode["result"]["total_power"] == 0
        assert mode["result"]["input_energy"] == 0
        assert em["result"]["ct_state"] == 1
        assert "input_energy" in em["result"]
        assert "bat_power" not in status["result"]
        assert status["result"]["pv_power"] == 0

    def test_getmode_echoes_instance_id(self) -> None:
        """Both GetMode instance ids answer; result.id echoes the request."""
        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 150},
        )

        id0 = device.build_response(1, "ES.GetMode", {"id": 0})
        id1 = device.build_response(2, "ES.GetMode", {"id": 1})

        assert id0 is not None
        assert id1 is not None
        assert id0["result"]["id"] == 0
        assert id1["result"]["id"] == 1
        assert id0["result"]["mode"] == "Auto"

    def test_pv_method_not_found_includes_observed_data_field(self) -> None:
        """Firmware 150 Venus E PV.GetStatus matches the captured -32601 payload."""
        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 150},
        )

        response = device.build_response(1, "PV.GetStatus", {"id": 0})

        assert response is not None
        assert response["error"] == {
            "code": -32601,
            "message": "Method not found",
            "data": 424,
        }
        assert "result" not in response


class TestFirmwareUdpQuirks:
    """Reproduce Control firmware Open API quirks found in VNSE3-0 binaries."""

    def _device_with_socket(
        self, *, ver: int, device: str = "VenusE 3.0"
    ) -> MockMarstekDevice:
        mock = MockMarstekDevice(
            simulate=False,
            device_config={"device": device, "ver": ver},
        )
        mock.sock = MagicMock()
        return mock

    def test_empty_datagram_freezes_openapi(self) -> None:
        """A 0-byte UDP packet stops later Local API replies."""
        device = self._device_with_socket(ver=145)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (b"", ("127.0.0.1", 12345))

        device._handle_request()

        assert device._openapi_frozen is True
        device.sock.sendto.assert_not_called()

        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 12345),
        )
        device._handle_request()
        device.sock.sendto.assert_not_called()

    def test_invalid_json_returns_parse_error(self) -> None:
        """Malformed JSON yields JSON-RPC parse error id 0 / -32700."""
        device = self._device_with_socket(ver=150)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (b"{not json", ("127.0.0.1", 1))

        device._handle_request()

        payload = json.loads(device.sock.sendto.call_args[0][0])
        assert payload["id"] == 0
        assert payload["error"]["code"] == -32700
        assert device.sock.sendto.call_count == 1

    def test_legacy_firmware_duplicates_udp_reply(self) -> None:
        """Pre-150 Control sends Local API replies twice (WiFi + Ethernet)."""
        device = self._device_with_socket(ver=145)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 1),
        )

        device._handle_request()

        assert device.profile.openapi_reset_prone is True
        assert device.sock.sendto.call_count == 2
        first = device.sock.sendto.call_args_list[0][0][0]
        second = device.sock.sendto.call_args_list[1][0][0]
        assert first == second

    def test_firmware_150_sends_single_reply(self) -> None:
        """Control 150's Local API ethernet send anomaly fix is a single reply."""
        device = self._device_with_socket(ver=150)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 1),
        )

        device._handle_request()

        assert device.profile.openapi_reset_prone is False
        assert device.sock.sendto.call_count == 1

    def test_venus_c_153_duplicates_udp_reply(self) -> None:
        """HMG-50 Control 153 is reset-prone; mock duplicates Local API replies."""
        device = self._device_with_socket(ver=153, device="VenusC")
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 1),
        )

        device._handle_request()

        assert device.profile.hmg50_control is True
        assert device.profile.openapi_reset_prone is True
        assert device.sock.sendto.call_count == 2

    def test_venus_c_156_sends_single_reply(self) -> None:
        """HMG-50 Control 156 Open API stability fix is a single UDP reply."""
        device = self._device_with_socket(ver=156, device="VenusC")
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 1),
        )

        device._handle_request()

        assert device.profile.openapi_reset_prone is False
        assert device.sock.sendto.call_count == 1

    def test_uint16_id_truncation(self) -> None:
        """JSON-RPC ids wrap to uint16 the way json_data.c stores them."""
        device = self._device_with_socket(ver=150)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":65536,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 1),
        )

        device._handle_request()

        payload = json.loads(device.sock.sendto.call_args[0][0])
        assert payload["id"] == 0

    def test_lan_replies_go_to_listen_port_not_ephemeral_source(
        self,
    ) -> None:
        """Container/LAN firmware replies to the Open API listen port."""
        device = self._device_with_socket(ver=150)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("172.28.0.2", 54321),
        )

        device._handle_request()

        dest = device.sock.sendto.call_args[0][1]
        assert dest == ("172.28.0.2", device.port)

    def test_loopback_replies_keep_ephemeral_source_port(self) -> None:
        """Unit tests bind ephemeral on loopback and must still receive replies."""
        device = self._device_with_socket(ver=150)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 54321),
        )

        device._handle_request()

        dest = device.sock.sendto.call_args[0][1]
        assert dest == ("127.0.0.1", 54321)



class TestDatagramStormGuards:
    """A shared UDP port must not turn the mock into a packet amplifier."""

    def _device_with_socket(self, verbose: bool = True) -> MockMarstekDevice:
        device = MockMarstekDevice(
            simulate=False, status_interval=0, verbose=verbose
        )
        device.sock = MagicMock()
        return device

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("own success reply", {"id": 1, "result": {"set_result": True}}),
            ("error reply", {"id": 2, "error": {"code": -32601, "message": "x"}}),
        ],
    )
    def test_reply_is_never_answered(
        self, label: str, payload: dict[str, object]
    ) -> None:
        """Answering a reply is what spins two sockets into a storm."""
        device = self._device_with_socket()
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            json.dumps(payload).encode(),
            ("127.0.0.1", 30000),
        )

        device._handle_request()

        device.sock.sendto.assert_not_called()

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("unknown method", {"id": 9, "method": "Wifi.SetConfig", "params": {}}),
            ("empty method", {"id": 3, "method": "", "params": {}}),
            ("missing method", {"id": 4, "params": {}}),
            ("non-string method", {"id": 5, "method": 7, "params": {}}),
        ],
    )
    def test_unanswerable_method_still_answers_method_not_found(
        self, label: str, payload: dict[str, object]
    ) -> None:
        """Real firmware answers -32601 for a method it cannot serve.

        docs/marstek_device_openapi.MD section 2.1 defines -32601 as "Method
        missing or not available on this firmware", so a request the mock
        cannot serve must still get a reply. Only a *reply* is dropped.
        """
        device = self._device_with_socket()
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            json.dumps(payload).encode(),
            ("127.0.0.1", 30000),
        )

        device._handle_request()

        sent = json.loads(device.sock.sendto.call_args[0][0].decode())
        assert sent["error"]["code"] == -32601

    def test_dropped_datagrams_are_rate_limited(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A flood of unanswerable datagrams must not print a line each."""
        device = self._device_with_socket()
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"result":{"set_result":true}}',
            ("127.0.0.1", 30000),
        )

        capsys.readouterr()
        for _ in range(500):
            device._handle_request()

        printed = capsys.readouterr().out.strip().splitlines()
        assert len(printed) == 1
        assert "not a request" in printed[0]

    def test_handled_request_logs_one_line(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Steady-state logging stays at a single line per request."""
        device = self._device_with_socket()
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 30000),
        )

        capsys.readouterr()
        device._handle_request()

        assert len(capsys.readouterr().out.strip().splitlines()) == 1

    def test_quiet_mode_logs_nothing_per_request(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--quiet silences per-request lines but keeps the device working."""
        device = self._device_with_socket(verbose=False)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 30000),
        )

        capsys.readouterr()
        device._handle_request()

        assert capsys.readouterr().out == ""
        device.sock.sendto.assert_called()

    def test_non_object_params_are_coerced_not_dropped(self) -> None:
        """Malformed params must not crash the handler or silence the reply."""
        device = self._device_with_socket()
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":7,"method":"ES.GetStatus","params":7}',
            ("127.0.0.1", 30000),
        )

        device._handle_request()

        sent = json.loads(device.sock.sendto.call_args[0][0].decode())
        assert "result" in sent

    def test_drop_counts_are_kept_per_reason(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A burst of one kind must not be reported under another."""
        device = self._device_with_socket()
        assert device.sock is not None
        capsys.readouterr()

        device._log_dropped("a reply, not a request", "1.2.3.4:30000")
        capsys.readouterr()
        device._log_dropped("invalid JSON", "1.2.3.4:30000")
        device._log_dropped("invalid JSON", "1.2.3.4:30000")
        device._log_dropped("a reply, not a request", "5.6.7.8:30000")
        device._flush_dropped()

        printed = capsys.readouterr().out.strip()
        assert "Dropped 3 datagram(s)" in printed
        assert "invalid JSON x2" in printed
        assert "a reply, not a request x1" in printed

    def test_pending_drops_survive_until_flushed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Counts suppressed by the rate limit are printed, never discarded."""
        device = self._device_with_socket()
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"result":{"set_result":true}}',
            ("127.0.0.1", 30000),
        )

        capsys.readouterr()
        for _ in range(4):
            device._handle_request()
        capsys.readouterr()

        device._flush_dropped()

        assert "Dropped 3 datagram(s)" in capsys.readouterr().out

    def test_flush_prints_nothing_when_no_drops_pending(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A clean shutdown must not emit an empty drop summary."""
        device = self._device_with_socket()
        capsys.readouterr()

        device._flush_dropped()

        assert capsys.readouterr().out == ""

    def test_shutdown_flushes_pending_drops(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """start() must print the tail of a burst on the way out."""
        device = MockMarstekDevice(
            simulate=False, status_interval=0, state_dir=str(tmp_path)
        )
        replies = [
            (b'{"id":1,"result":{"set_result":true}}', ("127.0.0.1", 30000)),
            (b'{"id":2,"result":{"set_result":true}}', ("127.0.0.1", 30000)),
        ]

        def _recvfrom(_size: int) -> tuple[bytes, tuple[str, int]]:
            if replies:
                return replies.pop(0)
            raise KeyboardInterrupt

        with patch("socket.socket") as sock_cls:
            sock_cls.return_value.recvfrom.side_effect = _recvfrom
            capsys.readouterr()
            device.start()

        # The first drop prints immediately; the second falls inside the rate
        # limit window and is only visible because shutdown flushes it.
        assert capsys.readouterr().out.count("Dropped 1 datagram(s)") == 2
