"""Mode handling: static, AI, UPS and the manual schedule slots."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from mock_device import MockMarstekDevice

from custom_components.marstek.firmware_profile import (
    resolve_firmware_profile,
)

from ._helpers import (
    _ups_set_mode_params,
)


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
