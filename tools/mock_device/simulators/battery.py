"""Battery behavior simulator."""

import random
import threading
import time
from datetime import datetime
from typing import Any

from ..const import (
    BATTERY_CAPACITY_WH,
    DEFAULT_MAX_CHARGE_POWER,
    DEFAULT_MAX_DISCHARGE_POWER,
    DEFAULT_POWER_FLUCTUATION_PCT,
    DEFAULT_UPDATE_INTERVAL,
    MODE_AI,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_PASSIVE,
    SOC_MIN_DISCHARGE,
    SOC_RESERVE,
    SOC_TAPER_CHARGE,
    SOC_TAPER_DISCHARGE,
    STATUS_CHARGING,
    STATUS_DISCHARGING,
    STATUS_IDLE,
)
from .household import HouseholdSimulator
from .wifi import WiFiSimulator


class BatterySimulator:
    """Simulates realistic battery behavior over time."""

    def __init__(
        self,
        initial_soc: int = 50,
        capacity_wh: int = BATTERY_CAPACITY_WH,
        max_charge_power: int = DEFAULT_MAX_CHARGE_POWER,
        max_discharge_power: int = DEFAULT_MAX_DISCHARGE_POWER,
    ):
        self.soc = initial_soc
        self.capacity_wh = capacity_wh
        self.max_charge_power = max_charge_power
        self.max_discharge_power = max_discharge_power

        # Current state
        self.mode = MODE_AUTO
        self.target_power = 0
        self.actual_power = 0
        self.grid_power = 0
        self.passive_end_time: float | None = None
        self.manual_schedules: list[dict[str, Any]] = []

        # Temperature simulation
        self.base_temp = 25.0
        self.battery_temp = self.base_temp

        # CT state
        self.ct_connected = True

        # Sub-simulators
        self.household = HouseholdSimulator()
        self.wifi = WiFiSimulator(base_rssi=-55)

        # Simulation settings
        self.power_fluctuation_pct = DEFAULT_POWER_FLUCTUATION_PCT
        self.update_interval = DEFAULT_UPDATE_INTERVAL
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the battery simulation thread."""
        self._running = True
        self._thread = threading.Thread(target=self._simulation_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the battery simulation thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _simulation_loop(self) -> None:
        """Main simulation loop."""
        last_update = time.time()
        while self._running:
            time.sleep(0.1)

            now = time.time()
            elapsed = now - last_update
            if elapsed < self.update_interval:
                continue
            last_update = now

            with self._lock:
                self._update_state(elapsed)

    def _update_state(self, elapsed_seconds: float) -> None:
        """Update battery state based on elapsed time."""
        # Check passive mode expiration
        if self.mode == MODE_PASSIVE and self.passive_end_time:
            if time.time() >= self.passive_end_time:
                print("[SIM] Passive mode expired, switching to Auto")
                self.mode = MODE_AUTO
                self.target_power = 0
                self.passive_end_time = None

        household_consumption = self.household.get_consumption()
        target = self._calculate_target_power(household_consumption)
        target = self._apply_soc_limits(target)

        # Add fluctuation
        if target != 0:
            fluctuation = target * (random.uniform(-1, 1) * self.power_fluctuation_pct / 100)
            self.actual_power = int(target + fluctuation)
        else:
            self.actual_power = 0

        self.grid_power = household_consumption - self.actual_power

        # Update SOC
        hours = elapsed_seconds / 3600
        energy_wh = self.actual_power * hours
        soc_change = -(energy_wh / self.capacity_wh) * 100
        self.soc = max(0, min(100, self.soc + soc_change))

        # Update temperature
        self._update_temperature()

    def _calculate_target_power(self, household_consumption: int) -> int:
        """Calculate target power based on mode and consumption."""
        if self.mode == MODE_PASSIVE:
            return self.target_power

        if self.mode == MODE_MANUAL:
            schedule = self._get_active_schedule()
            return schedule.get("power", 0) if schedule else 0

        if self.mode == MODE_AUTO:
            target = min(household_consumption, self.max_discharge_power)
            return 0 if self.soc < SOC_RESERVE else target

        if self.mode == MODE_AI:
            target = household_consumption
            hour = datetime.now().hour

            if 9 <= hour < 17:
                target = int(target * 0.7)
            if 17 <= hour < 22 and self.soc < 30:
                target = int(target * 0.5)

            target = min(target, self.max_discharge_power)
            return 0 if self.soc < 15 else target

        return 0

    def _apply_soc_limits(self, target: int) -> int:
        """Apply power limits based on SOC."""
        if target > 0 and self.soc <= SOC_MIN_DISCHARGE:
            return 0
        if target < 0 and self.soc >= 100:
            return 0
        if target < 0 and self.soc > SOC_TAPER_CHARGE:
            taper = (100 - self.soc) / (100 - SOC_TAPER_CHARGE)
            target = int(target * taper)
        if target > 0 and self.soc < SOC_TAPER_DISCHARGE:
            taper = self.soc / SOC_TAPER_DISCHARGE
            target = int(target * taper)
        return target

    def _update_temperature(self) -> None:
        """Update battery temperature based on power flow."""
        power_abs = abs(self.actual_power)
        if power_abs > 100:
            heat_factor = min(power_abs / self.max_discharge_power, 1.0)
            self.battery_temp += heat_factor * 0.3 * random.uniform(0.8, 1.2)
        else:
            if self.battery_temp > self.base_temp:
                self.battery_temp -= 0.1 * random.uniform(0.5, 1.5)
            elif self.battery_temp < self.base_temp:
                self.battery_temp += 0.1 * random.uniform(0.5, 1.5)
        self.battery_temp = max(15.0, min(50.0, self.battery_temp))

    def _apply_immediate_power_update(self) -> None:
        """Immediately update power to reflect mode change."""
        household_consumption = self.household.get_consumption()
        target = self._calculate_target_power(household_consumption)
        target = self._apply_soc_limits(target)

        if target != 0:
            fluctuation = target * (random.uniform(-1, 1) * self.power_fluctuation_pct / 100)
            self.actual_power = int(target + fluctuation)
        else:
            self.actual_power = 0

        self.grid_power = household_consumption - self.actual_power
        print(f"[SIM] Immediate power update: actual={self.actual_power}W, grid={self.grid_power}W")

    def _get_active_schedule(self) -> dict[str, Any] | None:
        """Get currently active manual schedule."""
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_day = now.weekday()

        for schedule in self.manual_schedules:
            if not schedule.get("enable", True):
                continue
            week_set = schedule.get("week_set", 127)
            if not (week_set & (1 << current_day)):
                continue
            start = schedule.get("start_time", "00:00")
            end = schedule.get("end_time", "23:59")
            if start <= current_time <= end:
                return schedule
        return None

    def set_mode(self, mode: str, config: dict[str, Any] | None = None) -> None:
        """Set operating mode with optional configuration."""
        with self._lock:
            self.mode = mode
            print(f"[SIM] Mode set to: {mode}")

            if mode == MODE_PASSIVE and config:
                self.target_power = config.get("power", 0)
                duration = config.get("cd_time", 3600)
                self.passive_end_time = time.time() + duration
                print(f"[SIM] Passive: power={self.target_power}W, duration={duration}s")
                self._apply_immediate_power_update()

            elif mode == MODE_MANUAL and config:
                slot = config.get("time_num", 0)
                schedule = {
                    "time_num": slot,
                    "start_time": config.get("start_time", "00:00"),
                    "end_time": config.get("end_time", "23:59"),
                    "week_set": config.get("week_set", 127),
                    "power": config.get("power", 0),
                    "enable": config.get("enable", 1) == 1,
                }
                for i, s in enumerate(self.manual_schedules):
                    if s.get("time_num") == slot:
                        self.manual_schedules[i] = schedule
                        break
                else:
                    self.manual_schedules.append(schedule)
                print(f"[SIM] Manual schedule slot {slot}: {schedule}")
                self._apply_immediate_power_update()

            else:
                self._apply_immediate_power_update()

    def get_state(self) -> dict[str, Any]:
        """Get current battery state."""
        with self._lock:
            if self.actual_power < -50:
                status = STATUS_CHARGING
            elif self.actual_power > 50:
                status = STATUS_DISCHARGING
            else:
                status = STATUS_IDLE

            passive_remaining = 0
            if self.passive_end_time and self.mode == MODE_PASSIVE:
                passive_remaining = max(0, int(self.passive_end_time - time.time()))

            passive_cfg = None
            if self.mode == MODE_PASSIVE:
                passive_cfg = {"power": self.target_power, "cd_time": passive_remaining}

            return {
                "soc": int(self.soc),
                "power": self.actual_power,
                "mode": self.mode,
                "status": status,
                "grid_power": self.grid_power,
                "household_consumption": self.household.current_consumption,
                "passive_remaining": passive_remaining,
                "passive_cfg": passive_cfg,
                "wifi_rssi": self.wifi.get_rssi(),
                "battery_temp": round(self.battery_temp, 1),
                "ct_connected": self.ct_connected,
                "charg_flag": 1 if self.soc < 100 else 0,
                "dischrg_flag": 1 if self.soc > SOC_MIN_DISCHARGE else 0,
            }
