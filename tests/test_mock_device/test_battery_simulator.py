"""Tests for the BatterySimulator class."""

from __future__ import annotations

import time

import pytest

import sys
from pathlib import Path

_tools_dir = Path(__file__).parent.parent.parent / "tools"
if str(_tools_dir) not in sys.path:
    sys.path.insert(0, str(_tools_dir))

from mock_device import BatterySimulator
from mock_device.const import (
    MODE_AI,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_PASSIVE,
    STATUS_CHARGING,
    STATUS_DISCHARGING,
    STATUS_IDLE,
)


class TestBatterySimulatorInitialization:
    """Tests for BatterySimulator initialization."""

    def test_initial_state(self) -> None:
        """Test simulator initializes with correct state."""
        sim = BatterySimulator(initial_soc=75)
        state = sim.get_state()

        assert state["soc"] == 75
        assert state["mode"] == MODE_AUTO
        assert state["power"] == 0
        assert state["status"] == STATUS_IDLE

    def test_default_soc(self) -> None:
        """Test default SOC is 50%."""
        sim = BatterySimulator()
        assert sim.soc == 50

    def test_custom_capacity(self) -> None:
        """Test custom battery capacity."""
        sim = BatterySimulator(capacity_wh=10240)
        assert sim.capacity_wh == 10240

    def test_custom_max_power(self) -> None:
        """Test custom max charge/discharge power."""
        sim = BatterySimulator(max_charge_power=5000, max_discharge_power=4000)
        assert sim.max_charge_power == 5000
        assert sim.max_discharge_power == 4000


class TestModeChanges:
    """Tests for mode change operations."""

    def test_set_mode_passive(self) -> None:
        """Test setting passive mode with power and duration."""
        sim = BatterySimulator(initial_soc=50)

        sim.set_mode(MODE_PASSIVE, {"power": -2000, "cd_time": 3600})
        state = sim.get_state()

        assert state["mode"] == MODE_PASSIVE
        assert sim.target_power == -2000
        assert sim.passive_end_time is not None
        assert state["passive_remaining"] > 0
        assert state["passive_cfg"] is not None
        assert state["passive_cfg"]["power"] == -2000
        assert state["passive_cfg"]["cd_time"] > 0

    def test_set_mode_manual_schedule(self) -> None:
        """Test setting manual mode with schedule configuration."""
        sim = BatterySimulator(initial_soc=50)

        schedule_config = {
            "time_num": 0,
            "start_time": "00:00",
            "end_time": "23:59",
            "week_set": 127,
            "power": -1500,
            "enable": 1,
        }
        sim.set_mode(MODE_MANUAL, schedule_config)
        state = sim.get_state()

        assert state["mode"] == MODE_MANUAL
        assert len(sim.manual_schedules) == 1
        assert sim.manual_schedules[0]["power"] == -1500

    def test_set_mode_auto(self) -> None:
        """Test switching to auto mode."""
        sim = BatterySimulator(initial_soc=50)

        sim.set_mode(MODE_PASSIVE, {"power": 1000, "cd_time": 3600})
        sim.set_mode(MODE_AUTO)

        state = sim.get_state()
        assert state["mode"] == MODE_AUTO
        assert state["passive_cfg"] is None

    def test_set_mode_ai(self) -> None:
        """Test switching to AI mode."""
        sim = BatterySimulator(initial_soc=50)
        sim.set_mode(MODE_AI)

        assert sim.get_state()["mode"] == MODE_AI


class TestSOCLimits:
    """Tests for SOC-based power limits."""

    def test_no_discharge_below_5(self) -> None:
        """Test that battery cannot discharge below 5% SOC."""
        sim = BatterySimulator(initial_soc=3)
        limited = sim._apply_soc_limits(1000)
        assert limited == 0

    def test_no_charge_above_100(self) -> None:
        """Test that battery cannot charge above 100% SOC."""
        sim = BatterySimulator(initial_soc=100)
        limited = sim._apply_soc_limits(-1000)
        assert limited == 0

    def test_taper_charging_near_full(self) -> None:
        """Test charging power tapers as SOC approaches 100%."""
        sim = BatterySimulator(initial_soc=95)
        limited = sim._apply_soc_limits(-2000)
        assert -1100 <= limited <= -900

    def test_taper_discharging_near_empty(self) -> None:
        """Test discharging power tapers as SOC approaches 0%."""
        sim = BatterySimulator(initial_soc=7)
        limited = sim._apply_soc_limits(1000)
        assert 650 <= limited <= 750


class TestAutoModeBehavior:
    """Tests for Auto mode power calculations."""

    def test_discharges_to_cover_household(self) -> None:
        """Test auto mode discharges to cover household consumption."""
        sim = BatterySimulator(initial_soc=50)
        household = 500
        target = sim._calculate_target_power(household)
        assert target == household

    def test_limited_by_max_discharge(self) -> None:
        """Test auto mode is limited by max discharge power."""
        sim = BatterySimulator(initial_soc=50, max_discharge_power=3000)
        household = 5000
        target = sim._calculate_target_power(household)
        assert target == 3000

    def test_no_discharge_when_soc_low(self) -> None:
        """Test auto mode doesn't discharge when SOC is below 10%."""
        sim = BatterySimulator(initial_soc=8)
        target = sim._calculate_target_power(1000)
        assert target == 0


class TestPassiveModeBehavior:
    """Tests for Passive mode behavior."""

    def test_uses_target_power(self) -> None:
        """Test passive mode returns configured target power."""
        sim = BatterySimulator(initial_soc=50)
        sim.set_mode(MODE_PASSIVE, {"power": -2500, "cd_time": 3600})
        target = sim._calculate_target_power(1000)
        assert target == -2500

    def test_expiration(self) -> None:
        """Test passive mode switches to auto when timer expires."""
        sim = BatterySimulator(initial_soc=50)
        sim.set_mode(MODE_PASSIVE, {"power": -1000, "cd_time": 1})
        sim.passive_end_time = time.time() - 1
        sim._update_state(1.0)
        assert sim.mode == MODE_AUTO
        assert sim.passive_end_time is None


class TestStatusLabels:
    """Tests for battery status labels."""

    def test_charging_status(self) -> None:
        """Test status shows 'Buying' when charging."""
        sim = BatterySimulator(initial_soc=50)
        sim.actual_power = -500
        assert sim.get_state()["status"] == STATUS_CHARGING

    def test_discharging_status(self) -> None:
        """Test status shows 'Selling' when discharging."""
        sim = BatterySimulator(initial_soc=50)
        sim.actual_power = 500
        assert sim.get_state()["status"] == STATUS_DISCHARGING

    def test_idle_status(self) -> None:
        """Test status shows 'Idle' when power near zero."""
        sim = BatterySimulator(initial_soc=50)
        sim.actual_power = 10
        assert sim.get_state()["status"] == STATUS_IDLE


class TestManualSchedules:
    """Tests for manual schedule management."""

    def test_update_existing_slot(self) -> None:
        """Test updating an existing manual schedule slot."""
        sim = BatterySimulator(initial_soc=50)

        sim.set_mode(MODE_MANUAL, {
            "time_num": 0,
            "start_time": "08:00",
            "end_time": "16:00",
            "week_set": 127,
            "power": -1000,
            "enable": 1,
        })

        sim.set_mode(MODE_MANUAL, {
            "time_num": 0,
            "start_time": "10:00",
            "end_time": "14:00",
            "week_set": 31,
            "power": -2000,
            "enable": 1,
        })

        assert len(sim.manual_schedules) == 1
        assert sim.manual_schedules[0]["power"] == -2000
        assert sim.manual_schedules[0]["start_time"] == "10:00"

    def test_multiple_slots(self) -> None:
        """Test adding multiple manual schedule slots."""
        sim = BatterySimulator(initial_soc=50)

        sim.set_mode(MODE_MANUAL, {
            "time_num": 0,
            "start_time": "08:00",
            "end_time": "12:00",
            "power": -1500,
            "enable": 1,
        })

        sim.set_mode(MODE_MANUAL, {
            "time_num": 1,
            "start_time": "18:00",
            "end_time": "22:00",
            "power": 800,
            "enable": 1,
        })

        assert len(sim.manual_schedules) == 2
        assert sim.manual_schedules[0]["time_num"] == 0
        assert sim.manual_schedules[1]["time_num"] == 1

    def test_schedule_matches_current_time(self) -> None:
        """Test schedule matching for current time."""
        sim = BatterySimulator(initial_soc=50)
        sim.manual_schedules = [{
            "time_num": 0,
            "start_time": "00:00",
            "end_time": "23:59",
            "week_set": 127,
            "power": -1500,
            "enable": True,
        }]

        schedule = sim._get_active_schedule()
        assert schedule is not None
        assert schedule["power"] == -1500

    def test_disabled_schedule_not_matched(self) -> None:
        """Test disabled schedule is not matched."""
        sim = BatterySimulator(initial_soc=50)
        sim.manual_schedules = [{
            "time_num": 0,
            "start_time": "00:00",
            "end_time": "23:59",
            "week_set": 127,
            "power": -1500,
            "enable": False,
        }]

        assert sim._get_active_schedule() is None

    def test_wrong_day_not_matched(self) -> None:
        """Test schedule on wrong day is not matched."""
        sim = BatterySimulator(initial_soc=50)
        sim.manual_schedules = [{
            "time_num": 0,
            "start_time": "00:00",
            "end_time": "23:59",
            "week_set": 0,
            "power": -1500,
            "enable": True,
        }]

        assert sim._get_active_schedule() is None

    def test_first_match_wins(self) -> None:
        """Test first matching schedule is returned."""
        sim = BatterySimulator(initial_soc=50)
        sim.manual_schedules = [
            {"time_num": 0, "start_time": "00:00", "end_time": "23:59", "week_set": 127, "power": -1000, "enable": True},
            {"time_num": 1, "start_time": "00:00", "end_time": "23:59", "week_set": 127, "power": -2000, "enable": True},
        ]

        schedule = sim._get_active_schedule()
        assert schedule["power"] == -1000


class TestSOCChanges:
    """Tests for SOC changes during simulation."""

    def test_soc_increases_when_charging(self) -> None:
        """Test SOC increases when charging."""
        sim = BatterySimulator(initial_soc=50, capacity_wh=5120)
        sim.set_mode(MODE_PASSIVE, {"power": -2560, "cd_time": 7200})
        sim._update_state(3600)
        assert sim.soc > 95

    def test_soc_decreases_when_discharging(self) -> None:
        """Test SOC decreases when discharging."""
        sim = BatterySimulator(initial_soc=50, capacity_wh=5120)
        sim.set_mode(MODE_PASSIVE, {"power": 2560, "cd_time": 7200})
        sim._update_state(3600)
        assert sim.soc < 5


class TestThreadSafety:
    """Tests for thread-safe operations."""

    def test_get_state_is_thread_safe(self) -> None:
        """Test get_state is thread-safe (uses lock)."""
        sim = BatterySimulator(initial_soc=50)
        sim.start()

        try:
            for _ in range(10):
                state = sim.get_state()
                assert "soc" in state
                assert "power" in state
                assert "mode" in state
                time.sleep(0.05)
        finally:
            sim.stop()

    def test_set_mode_is_thread_safe(self) -> None:
        """Test set_mode is thread-safe (uses lock)."""
        sim = BatterySimulator(initial_soc=50)
        sim.start()

        try:
            sim.set_mode(MODE_PASSIVE, {"power": -1000, "cd_time": 3600})
            assert sim.get_state()["mode"] == MODE_PASSIVE

            sim.set_mode(MODE_AUTO)
            assert sim.get_state()["mode"] == MODE_AUTO
        finally:
            sim.stop()


class TestImmediatePowerUpdates:
    """Tests for immediate power updates after mode changes."""

    def test_passive_charging_immediate(self) -> None:
        """Test passive mode charging immediately updates actual_power."""
        sim = BatterySimulator(initial_soc=50)
        sim.set_mode(MODE_PASSIVE, {"power": -1400, "cd_time": 3600})
        state = sim.get_state()

        assert state["mode"] == MODE_PASSIVE
        assert state["power"] < 0
        assert -1500 < state["power"] < -1300
        assert state["status"] == STATUS_CHARGING

    def test_passive_discharging_immediate(self) -> None:
        """Test passive mode discharging immediately updates actual_power."""
        sim = BatterySimulator(initial_soc=50)
        sim.set_mode(MODE_PASSIVE, {"power": 1400, "cd_time": 3600})
        state = sim.get_state()

        assert state["power"] > 0
        assert 1300 < state["power"] < 1500
        assert state["status"] == STATUS_DISCHARGING

    def test_passive_zero_power_immediate(self) -> None:
        """Test passive mode with zero power stops battery activity."""
        sim = BatterySimulator(initial_soc=50)
        sim.set_mode(MODE_PASSIVE, {"power": 2000, "cd_time": 3600})
        sim.set_mode(MODE_PASSIVE, {"power": 0, "cd_time": 3600})
        state = sim.get_state()

        assert state["power"] == 0
        assert state["status"] == STATUS_IDLE

    def test_manual_active_schedule_immediate(self) -> None:
        """Test manual mode with active schedule immediately updates power."""
        sim = BatterySimulator(initial_soc=50)
        sim.set_mode(MODE_MANUAL, {
            "time_num": 0,
            "start_time": "00:00",
            "end_time": "23:59",
            "week_set": 127,
            "power": -1500,
            "enable": 1,
        })
        state = sim.get_state()

        assert state["mode"] == MODE_MANUAL
        assert state["power"] < 0
        assert -1600 < state["power"] < -1400

    def test_rapid_mode_switches_reflect_latest(self) -> None:
        """Test rapid mode switches always reflect the most recent mode."""
        sim = BatterySimulator(initial_soc=50)

        sim.set_mode(MODE_PASSIVE, {"power": 1000, "cd_time": 3600})
        sim.set_mode(MODE_PASSIVE, {"power": -2000, "cd_time": 3600})
        sim.set_mode(MODE_PASSIVE, {"power": -500, "cd_time": 3600})

        state = sim.get_state()
        assert -600 < state["power"] < -400

    def test_with_simulation_thread_running(self) -> None:
        """Test immediate update works with simulation thread active."""
        sim = BatterySimulator(initial_soc=50)
        sim.household.force_cooking_event(power=4000, duration_mins=60)
        sim.start()

        try:
            time.sleep(0.5)
            sim.set_mode(MODE_PASSIVE, {"power": -1400, "cd_time": 3600})
            state = sim.get_state()

            assert state["mode"] == MODE_PASSIVE
            assert state["power"] < 0
            assert -1500 < state["power"] < -1300
            assert state["status"] == STATUS_CHARGING
        finally:
            sim.stop()

    def test_simulation_thread_respects_mode_change(self) -> None:
        """Test simulation loop uses new mode after mode change."""
        sim = BatterySimulator(initial_soc=50)
        sim.household.force_cooking_event(power=5000, duration_mins=60)
        sim.start()

        try:
            time.sleep(1.5)
            sim.set_mode(MODE_PASSIVE, {"power": -1400, "cd_time": 3600})
            time.sleep(1.5)

            state = sim.get_state()
            assert state["mode"] == MODE_PASSIVE
            assert state["power"] < 0
            assert -1500 < state["power"] < -1300
        finally:
            sim.stop()


class TestGridPowerCalculation:
    """Tests for grid power calculation."""

    def test_grid_power_reduced_by_discharge(self) -> None:
        """Test battery discharge reduces grid power import."""
        sim = BatterySimulator(initial_soc=50)
        sim.household.current_consumption = 1000
        sim.actual_power = 800
        sim.grid_power = sim.household.current_consumption - sim.actual_power

        assert sim.get_state()["grid_power"] == 200

    def test_grid_power_increased_by_charge(self) -> None:
        """Test battery charging increases grid power import."""
        sim = BatterySimulator(initial_soc=50)
        sim.household.current_consumption = 500
        sim.actual_power = -1000
        sim.grid_power = sim.household.current_consumption - sim.actual_power

        assert sim.get_state()["grid_power"] == 1500

    def test_state_includes_household_consumption(self) -> None:
        """Test state includes household consumption value."""
        sim = BatterySimulator(initial_soc=50)
        state = sim.get_state()

        assert "household_consumption" in state
        assert state["household_consumption"] >= 50
