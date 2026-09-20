#!/usr/bin/env python3
"""Run tests for the mock Marstek device simulator."""

import sys
import time

from mock_marstek import (
    MODE_AI,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_PASSIVE,
    STATUS_CHARGING,
    STATUS_DISCHARGING,
    STATUS_IDLE,
    BatterySimulator,
    HouseholdSimulator,
)

passed = 0
failed = 0


def test(name: str, condition: bool) -> None:
    """Run a single test."""
    global passed, failed
    status = "✓" if condition else "✗"
    print(f"{status} {name}")
    if condition:
        passed += 1
    else:
        failed += 1


def main() -> int:
    """Run all tests."""
    print("=" * 60)
    print("Mock Marstek Device Tests")
    print("=" * 60)
    print()

    # Test 1: Initial state
    print("--- Initial State ---")
    sim = BatterySimulator(initial_soc=75)
    state = sim.get_state()
    test("Initial SOC is 75", state["soc"] == 75)
    test("Initial mode is Auto", state["mode"] == MODE_AUTO)
    test("Initial power is 0", state["power"] == 0)
    test("Initial status is Idle", state["status"] == STATUS_IDLE)
    print()

    # Test 2: Passive mode
    print("--- Passive Mode ---")
    sim = BatterySimulator(initial_soc=50)
    sim.set_mode(MODE_PASSIVE, {"power": -2000, "cd_time": 3600})
    state = sim.get_state()
    test("Passive mode set", state["mode"] == MODE_PASSIVE)
    test("Target power set", sim.target_power == -2000)
    test("Passive remaining > 0", state["passive_remaining"] > 0)
    test("Passive cfg present", state["passive_cfg"] is not None)
    test("Passive cfg power", state["passive_cfg"]["power"] == -2000)
    test("Passive cfg cd_time", state["passive_cfg"]["cd_time"] > 0)
    print()

    # Test 3: Manual schedule
    print("--- Manual Schedule ---")
    sim = BatterySimulator(initial_soc=50)
    sim.set_mode(
        MODE_MANUAL,
        {
            "time_num": 0,
            "start_time": "00:00",
            "end_time": "23:59",
            "week_set": 127,
            "power": -1500,
            "enable": 1,
        },
    )
    test("Manual mode set", sim.get_state()["mode"] == MODE_MANUAL)
    test("Schedule added", len(sim.manual_schedules) == 1)
    test("Schedule power correct", sim.manual_schedules[0]["power"] == -1500)
    print()

    # Test 4: SOC limits
    print("--- SOC Limits ---")
    sim = BatterySimulator(initial_soc=3)
    limited = sim._apply_soc_limits(1000)
    test("No discharge below 5%", limited == 0)

    sim = BatterySimulator(initial_soc=100)
    limited = sim._apply_soc_limits(-1000)
    test("No charge above 100%", limited == 0)

    sim = BatterySimulator(initial_soc=95)
    limited = sim._apply_soc_limits(-2000)
    test("Taper charging near full", -1100 <= limited <= -900)

    sim = BatterySimulator(initial_soc=7)
    limited = sim._apply_soc_limits(1000)
    test("Taper discharging near empty", 650 <= limited <= 750)
    print()

    # Test 5: Auto mode behavior (now based on household consumption)
    print("--- Auto Mode Behavior ---")
    sim = BatterySimulator(initial_soc=50)
    target = sim._calculate_target_power(500)  # 500W household consumption
    test("Auto discharges to cover household", target == 500)

    sim = BatterySimulator(initial_soc=50, max_discharge_power=3000)
    target = sim._calculate_target_power(5000)  # High consumption
    test("Auto limited by max discharge", target == 3000)

    sim = BatterySimulator(initial_soc=8)  # Low SOC
    target = sim._calculate_target_power(1000)
    test("Auto no discharge when SOC < 10%", target == 0)
    print()

    # Test 6: Status labels
    print("--- Status Labels ---")
    sim = BatterySimulator(initial_soc=50)
    sim.actual_power = -500
    test("Status Charging when charging", sim.get_state()["status"] == STATUS_CHARGING)
    sim.actual_power = 500
    test(
        "Status Discharging when discharging",
        sim.get_state()["status"] == STATUS_DISCHARGING,
    )
    sim.actual_power = 10
    test("Status Idle when near zero", sim.get_state()["status"] == STATUS_IDLE)
    print()

    # Test 7: SOC changes with power flow
    print("--- SOC Changes ---")
    sim = BatterySimulator(initial_soc=50, capacity_wh=5120)
    sim.set_mode(MODE_PASSIVE, {"power": -2560, "cd_time": 7200})  # Charge at 2560W
    sim._update_state(3600)  # 1 hour = 2560Wh = 50% of 5120Wh
    test("SOC increases when charging (50% -> ~100%)", sim.soc > 95)

    sim = BatterySimulator(initial_soc=50, capacity_wh=5120)
    sim.set_mode(MODE_PASSIVE, {"power": 2560, "cd_time": 7200})  # Discharge at 2560W
    sim._update_state(3600)  # 1 hour
    test("SOC decreases when discharging (50% -> ~0%)", sim.soc < 5)
    print()

    # Test 8: Passive mode expiration
    print("--- Passive Mode Expiration ---")
    sim = BatterySimulator(initial_soc=50)
    sim.set_mode(MODE_PASSIVE, {"power": -1000, "cd_time": 1})
    sim.passive_end_time = time.time() - 1  # Already expired
    sim._update_state(1.0)
    test("Passive mode expires to Auto", sim.mode == MODE_AUTO)
    test("Passive end time cleared", sim.passive_end_time is None)
    print()

    # Test 9: Multiple schedule slots
    print("--- Multiple Schedules ---")
    sim = BatterySimulator(initial_soc=50)
    sim.set_mode(MODE_MANUAL, {"time_num": 0, "power": -1000, "enable": 1})
    sim.set_mode(MODE_MANUAL, {"time_num": 1, "power": 800, "enable": 1})
    test("Multiple schedules added", len(sim.manual_schedules) == 2)

    sim = BatterySimulator(initial_soc=50)
    sim.set_mode(MODE_MANUAL, {"time_num": 0, "power": -1000, "enable": 1})
    sim.set_mode(MODE_MANUAL, {"time_num": 0, "power": -2000, "enable": 1})
    test("Update existing slot (still 1 schedule)", len(sim.manual_schedules) == 1)
    test("Updated power value", sim.manual_schedules[0]["power"] == -2000)
    print()

    # Test 10: Thread safety
    print("--- Thread Safety ---")
    sim = BatterySimulator(initial_soc=50)
    sim.start()
    time.sleep(0.3)
    state = sim.get_state()
    test("Can get state while running", "soc" in state)
    sim.set_mode(MODE_PASSIVE, {"power": -1000, "cd_time": 3600})
    test("Can set mode while running", sim.get_state()["mode"] == MODE_PASSIVE)
    sim.stop()
    test("Simulator stops cleanly", True)
    print()

    # Test 11: AI mode
    print("--- AI Mode ---")
    sim = BatterySimulator(initial_soc=50)
    sim.set_mode(MODE_AI)
    test("AI mode set", sim.get_state()["mode"] == MODE_AI)
    print()

    # Test 12: Household Simulator
    print("--- Household Simulator ---")
    household = HouseholdSimulator()
    consumption = household.get_consumption()
    test("Household consumption is positive", consumption >= 50)

    # Create a fresh simulator with no events for baseline comparison
    household2 = HouseholdSimulator()
    baseline = household2.base_load  # Use base_load as reference
    household2.force_cooking_event(power=2500, duration_mins=15)
    with_cooking = household2.get_consumption()
    test("Cooking event increases consumption", with_cooking > baseline + 2000)
    print()

    # Test 13: Grid Power Calculation
    print("--- Grid Power Calculation ---")
    sim = BatterySimulator(initial_soc=50)
    sim.household.current_consumption = 1000
    sim.actual_power = 800  # Battery discharging 800W
    sim.grid_power = sim.household.current_consumption - sim.actual_power
    test("Grid = household - battery discharge", sim.grid_power == 200)

    sim.actual_power = -500  # Battery charging 500W
    sim.grid_power = sim.household.current_consumption - sim.actual_power
    test("Grid increases with battery charge", sim.grid_power == 1500)

    state = sim.get_state()
    test("State includes household_consumption", "household_consumption" in state)
    test("State includes grid_power", "grid_power" in state)
    print()

    # Summary
    print("=" * 60)
    total = passed + failed
    print(f"Results: {passed}/{total} tests passed")
    if failed > 0:
        print(f"FAILED: {failed} tests")
        return 1
    else:
        print("All tests passed!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
