"""Tests for MockMarstekDevice request/response handling."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from mock_device import MockMarstekDevice
from mock_device.__main__ import main
from custom_components.marstek.firmware_profile import resolve_firmware_profile
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

    def test_rev31_profile_round_trips_physical_watts_and_wh(self) -> None:
        """Rev 3.1 mock wire JSON decodes through production into SI units."""
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
        profile = resolve_firmware_profile("VenusA", 150)

        pv_response = device.build_response(2, "PV.GetStatus", {})
        es_response = device.build_response(3, "ES.GetStatus", {})
        em_response = device.build_response(4, "EM.GetStatus", {})

        assert pv_response is not None
        assert es_response is not None
        assert em_response is not None
        assert pv_response["result"]["pv1_power"] == 320
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

    def test_legacy_em_status_omits_energy_fields(self) -> None:
        """Legacy mocks omit EM energy fields rather than sending null placeholders."""
        device = MockMarstekDevice(
            port=30005,
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 145},
        )

        response = device.build_response(1, "EM.GetStatus", {})

        assert response is not None
        assert "input_energy" not in response["result"]
        assert "output_energy" not in response["result"]

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
        [("Venus E mini", 5, 6), ("VenusE", 9, 10), ("Other", 9, 10)],
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
            device_config={"device": "VenusE", "ver": 145},
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
            device_config={"device": "VenusE", "ver": 150},
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

        profile = resolve_firmware_profile("VenusE", 150)
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
            device_config={"device": "VenusE", "ver": 150},
        )
        set_response = device.build_response(1, "ES.SetMode", command["params"])
        assert set_response is not None
        assert set_response["result"]["set_result"] is True

        get_response = device.build_response(2, "ES.GetMode", {})
        assert get_response is not None
        parsed = parse_es_mode_response(get_response, profile)
        assert parsed["device_mode"] == "ups"
