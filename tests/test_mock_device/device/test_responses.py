"""Command responses and the discovery replies the mock device sends."""

from __future__ import annotations

import time

from mock_device import MockMarstekDevice

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
