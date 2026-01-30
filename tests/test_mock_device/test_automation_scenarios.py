"""Advanced scenario tests simulating real Home Assistant automation workflows.

These tests verify that when automations rapidly switch between modes,
the mock device returns correct and consistent status information.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_tools_dir = Path(__file__).parent.parent.parent / "tools"
if str(_tools_dir) not in sys.path:
    sys.path.insert(0, str(_tools_dir))

from mock_device import MockMarstekDevice


class TestAutomationWorkflows:
    """Tests simulating real Home Assistant automation workflows."""

    def test_scenario_auto_to_passive_charging_to_auto(self) -> None:
        """Test automation: Auto -> Passive (charge during cheap tariff) -> Auto."""
        # Enable include_bat_power for testing direct bat_power response path
        device = MockMarstekDevice(
            port=30010,
            simulate=True,
            include_bat_power=True,
        )
        device.simulator.household.force_cooking_event(power=2000, duration_mins=60)
        device.simulator.start()

        try:
            time.sleep(0.3)
            mode1 = device._build_response(1, "ES.GetMode", {})["result"]
            assert mode1["mode"] == "Auto"

            # Switch to passive charging
            device._build_response(2, "ES.SetMode", {
                "id": 0,
                "config": {
                    "mode": "Passive",
                    "passive_cfg": {"power": -2500, "cd_time": 7200},
                },
            })

            status2 = device._build_response(3, "ES.GetStatus", {})["result"]
            mode2 = device._build_response(3, "ES.GetMode", {})["result"]

            assert mode2["mode"] == "Passive"
            # API bat_power: positive = charging, negative = discharging
            # Internal power=-2500 (charging) -> API bat_power=+2500
            assert status2["bat_power"] > 0
            assert 2200 < status2["bat_power"] < 2700

            time.sleep(1.0)

            # Return to auto
            device._build_response(5, "ES.SetMode", {
                "id": 0,
                "config": {"mode": "Auto"},
            })

            status4 = device._build_response(6, "ES.GetStatus", {})["result"]
            mode4 = device._build_response(6, "ES.GetMode", {})["result"]

            assert mode4["mode"] == "Auto"
            # In Auto mode with household load, battery discharges
            # API bat_power: negative = discharging
            assert status4["bat_power"] < 0
        finally:
            device.simulator.stop()

    def test_scenario_passive_discharging_peak_shaving(self) -> None:
        """Test automation: Auto -> Passive (discharge during peak) -> Auto."""
        # Enable include_bat_power for testing direct bat_power response path
        device = MockMarstekDevice(
            port=30011,
            simulate=True,
            include_bat_power=True,
        )
        device.simulator.start()

        try:
            time.sleep(0.3)

            device._build_response(1, "ES.SetMode", {
                "id": 0,
                "config": {
                    "mode": "Passive",
                    "passive_cfg": {"power": 2500, "cd_time": 1800},
                },
            })

            status = device._build_response(2, "ES.GetStatus", {})["result"]
            mode = device._build_response(2, "ES.GetMode", {})["result"]

            assert mode["mode"] == "Passive"
            # API bat_power: positive = charging, negative = discharging
            # Internal power=2500 (discharging) -> API bat_power=-2500
            assert status["bat_power"] < 0
            # Max discharge is 2500W with ~5% fluctuation
            assert 2300 < abs(status["bat_power"]) < 2700
        finally:
            device.simulator.stop()

    def test_scenario_manual_schedule_workflow(self) -> None:
        """Test automation: Set multiple manual schedules for daily routine."""
        device = MockMarstekDevice(port=30012, simulate=True)
        device.simulator.start()

        try:
            time.sleep(0.3)

            # Set night charging schedule
            device._build_response(1, "ES.SetMode", {
                "id": 0,
                "config": {
                    "mode": "Manual",
                    "manual_cfg": {
                        "time_num": 0,
                        "start_time": "00:00",
                        "end_time": "06:00",
                        "week_set": 127,
                        "power": -2000,
                        "enable": 1,
                    },
                },
            })

            # Set day discharging schedule
            device._build_response(2, "ES.SetMode", {
                "id": 0,
                "config": {
                    "mode": "Manual",
                    "manual_cfg": {
                        "time_num": 1,
                        "start_time": "07:00",
                        "end_time": "22:00",
                        "week_set": 127,
                        "power": 1500,
                        "enable": 1,
                    },
                },
            })

            mode = device._build_response(3, "ES.GetMode", {})["result"]

            assert mode["mode"] == "Manual"
            assert len(device.simulator.manual_schedules) == 2
        finally:
            device.simulator.stop()

    def test_scenario_rapid_mode_switching_stability(self) -> None:
        """Test automation: Rapid mode switches don't cause inconsistent state."""
        # Enable include_bat_power for testing direct bat_power response path
        device = MockMarstekDevice(
            port=30013,
            simulate=True,
            include_bat_power=True,
        )
        device.simulator.start()

        try:
            time.sleep(0.3)

            modes_to_test = [
                ("Passive", {"power": -1000, "cd_time": 3600}),
                ("Passive", {"power": 500, "cd_time": 3600}),
                ("Auto", None),
                ("Passive", {"power": -2000, "cd_time": 3600}),
                ("AI", None),
                ("Passive", {"power": 1500, "cd_time": 3600}),
                ("Manual", {"time_num": 0, "start_time": "00:00", "end_time": "23:59", "week_set": 127, "power": -1200, "enable": 1}),
                ("Passive", {"power": -800, "cd_time": 3600}),
            ]

            for i, (mode, config) in enumerate(modes_to_test):
                params = {"id": 0, "config": {"mode": mode}}
                if config:
                    if mode == "Passive":
                        params["config"]["passive_cfg"] = config
                    elif mode == "Manual":
                        params["config"]["manual_cfg"] = config

                device._build_response(i + 1, "ES.SetMode", params)
                get_mode = device._build_response(i + 200, "ES.GetMode", {})["result"]

                assert get_mode["mode"] == mode

            final_status = device._build_response(999, "ES.GetStatus", {})["result"]
            final_mode = device._build_response(999, "ES.GetMode", {})["result"]
            assert final_mode["mode"] == "Passive"
            # API bat_power: positive = charging, negative = discharging
            # Internal power=-800 (charging) -> API bat_power=+800
            assert final_status["bat_power"] > 0
            assert 750 < final_status["bat_power"] < 850
        finally:
            device.simulator.stop()

    def test_scenario_passive_mode_expiration(self) -> None:
        """Test automation: Passive mode expires and device returns to Auto."""
        # Enable include_bat_power for testing direct bat_power response path
        device = MockMarstekDevice(
            port=30014,
            simulate=True,
            include_bat_power=True,
        )
        device.simulator.start()

        try:
            device._build_response(1, "ES.SetMode", {
                "id": 0,
                "config": {
                    "mode": "Passive",
                    "passive_cfg": {"power": -1500, "cd_time": 2},
                },
            })

            mode1 = device._build_response(2, "ES.GetMode", {})["result"]
            status1 = device._build_response(2, "ES.GetStatus", {})["result"]
            assert mode1["mode"] == "Passive"
            # API bat_power: positive = charging
            assert status1["bat_power"] > 0

            time.sleep(3.0)

            mode2 = device._build_response(3, "ES.GetMode", {})["result"]
            assert mode2["mode"] == "Auto"
        finally:
            device.simulator.stop()


class TestSOCEffects:
    """Tests for SOC-related behaviors in automation scenarios."""

    def test_soc_affects_power_limits(self) -> None:
        """Test automation: Battery SOC affects actual power output."""
        # Test low SOC - can't discharge
        # Enable include_bat_power for testing direct bat_power response path
        device_low = MockMarstekDevice(
            port=30015,
            simulate=True,
            include_bat_power=True,
        )
        device_low.simulator.soc = 3
        device_low.simulator.start()

        try:
            device_low._build_response(1, "ES.SetMode", {
                "id": 0,
                "config": {
                    "mode": "Passive",
                    "passive_cfg": {"power": 2000, "cd_time": 3600},
                },
            })

            status = device_low._build_response(2, "ES.GetStatus", {})["result"]
            assert abs(status["bat_power"]) < 100
        finally:
            device_low.simulator.stop()

        # Test high SOC - charging tapers
        # Enable include_bat_power for testing direct bat_power response path
        device_high = MockMarstekDevice(
            port=30016,
            simulate=True,
            include_bat_power=True,
        )
        device_high.simulator.soc = 98
        device_high.simulator.start()

        try:
            device_high._build_response(1, "ES.SetMode", {
                "id": 0,
                "config": {
                    "mode": "Passive",
                    "passive_cfg": {"power": -2500, "cd_time": 3600},
                },
            })

            status = device_high._build_response(2, "ES.GetStatus", {})["result"]
            assert abs(status["bat_power"]) < 1000
        finally:
            device_high.simulator.stop()


class TestGridPowerConsistency:
    """Tests for grid power calculation consistency."""

    def test_grid_power_consistency(self) -> None:
        """Test automation: Grid power is calculated correctly."""
        # Enable include_bat_power for testing direct bat_power response path
        device = MockMarstekDevice(
            port=30017,
            simulate=True,
            include_bat_power=True,
        )
        device.simulator.household.force_cooking_event(power=2000, duration_mins=60)
        device.simulator.start()

        try:
            time.sleep(0.3)

            # Test charging - grid import increases
            device._build_response(1, "ES.SetMode", {
                "id": 0,
                "config": {
                    "mode": "Passive",
                    "passive_cfg": {"power": -1500, "cd_time": 3600},
                },
            })

            status1 = device._build_response(2, "ES.GetStatus", {})["result"]
            # API bat_power: positive = charging (internal power=-1500)
            assert status1["bat_power"] > 0

            # Test discharging - grid import decreases
            device._build_response(3, "ES.SetMode", {
                "id": 0,
                "config": {
                    "mode": "Passive",
                    "passive_cfg": {"power": 1500, "cd_time": 3600},
                },
            })

            status2 = device._build_response(4, "ES.GetStatus", {})["result"]
            # API bat_power: negative = discharging (internal power=1500)
            assert status2["bat_power"] < 0
            assert status2["ongrid_power"] < status1["ongrid_power"]
        finally:
            device.simulator.stop()

    def test_es_get_mode_vs_es_get_status_consistency(self) -> None:
        """Test automation: ES.GetMode and ES.GetStatus return consistent data."""
        device = MockMarstekDevice(port=30018, simulate=True)
        device.simulator.start()

        try:
            modes = [
                ("Passive", {"passive_cfg": {"power": -1000, "cd_time": 3600}}),
                ("Auto", {}),
                ("AI", {}),
                ("Manual", {"manual_cfg": {"time_num": 0, "start_time": "00:00", "end_time": "23:59", "week_set": 127, "power": 500, "enable": 1}}),
                ("Passive", {"passive_cfg": {"power": 2000, "cd_time": 3600}}),
            ]

            for mode, extra_config in modes:
                params = {"id": 0, "config": {"mode": mode, **extra_config}}
                device._build_response(1, "ES.SetMode", params)

                status = device._build_response(2, "ES.GetStatus", {})["result"]
                get_mode = device._build_response(3, "ES.GetMode", {})["result"]

                assert get_mode["mode"] == mode
                assert status["bat_soc"] == get_mode["bat_soc"]
        finally:
            device.simulator.stop()


class TestConcurrentPolling:
    """Tests for polling during mode changes."""

    def test_concurrent_polling_during_mode_change(self) -> None:
        """Test automation: Polling continues during and after mode change."""
        # Enable include_bat_power for testing direct bat_power response path
        device = MockMarstekDevice(
            port=30019,
            simulate=True,
            include_bat_power=True,
        )
        device.simulator.household.force_cooking_event(power=3000, duration_mins=60)
        device.simulator.start()

        try:
            time.sleep(0.3)

            for poll in range(5):
                device._build_response(poll * 10, "ES.GetStatus", {})

                if poll == 2:
                    device._build_response(100, "ES.SetMode", {
                        "id": 0,
                        "config": {
                            "mode": "Passive",
                            "passive_cfg": {"power": -1800, "cd_time": 3600},
                        },
                    })

                time.sleep(0.2)

            final_status = device._build_response(999, "ES.GetStatus", {})["result"]
            final_mode = device._build_response(999, "ES.GetMode", {})["result"]
            assert final_mode["mode"] == "Passive"
            # API bat_power: positive = charging (internal power=-1800)
            assert final_status["bat_power"] > 0
            assert 1700 < final_status["bat_power"] < 1900
        finally:
            device.simulator.stop()
