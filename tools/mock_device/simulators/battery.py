"""Battery behavior simulator with realistic P1 meter feedback loop.

Simulates how a real Marstek battery interacts with a P1 meter:
- P1 meter measures NET flow at meter point (after battery contribution)
- Battery compensates to keep P1 at 0 (zero import/export)
- Battery tracks its OWN contribution to avoid oscillation
"""

import random
import threading
import time
from datetime import datetime
from typing import Any, Callable

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
    """Simulates realistic Marstek battery behavior with P1 meter feedback.
    
    Key concepts:
    - Gross household consumption: What all appliances are actually using
    - Battery power: What the battery is providing (+) or absorbing (-)
    - P1/Grid power (EM total_power): Net flow at meter = gross_consumption - battery_power
      - Positive = importing from grid (household needs more than battery provides)
      - Negative = exporting to grid (battery provides more than household needs)
    
    In Auto mode, the battery aims to keep P1 at 0 (zero export/import).
    The battery tracks its OWN contribution to avoid oscillation:
    - If P1 reads 0, battery knows household = battery_power, so keeps running
    - If P1 reads +200, battery increases output to compensate
    - If P1 reads -200, battery decreases output
    
    Real example:
    1. Household uses 800W (gross consumption)
    2. Battery provides 800W (discharge)
    3. P1 meter reads: 800W - 800W = 0W (balanced!)
    4. Battery sees P1=0, knows "I'm giving 800W, P1=0, so household=800W"
    5. Battery continues at 800W (not 0!)
    """

    def __init__(
        self,
        initial_soc: int = 50,
        capacity_wh: int = BATTERY_CAPACITY_WH,
        max_charge_power: int = DEFAULT_MAX_CHARGE_POWER,
        max_discharge_power: int = DEFAULT_MAX_DISCHARGE_POWER,
        persist_callback: Callable[[dict[str, Any]], None] | None = None,
        persist_interval: float = 30.0,
    ):
        self.soc = initial_soc
        self.capacity_wh = capacity_wh
        self.max_charge_power = max_charge_power
        self.max_discharge_power = max_discharge_power

        # Current state
        self.mode = MODE_AUTO
        self.target_power = 0  # Target for passive/manual mode
        self.actual_power = 0  # What battery is doing (+ = discharge, - = charge)
        self.gross_household_consumption = 0  # What appliances use (before battery)
        self.grid_power = 0  # P1 meter reading (net flow after battery)

        # Phase power distribution (for EM.GetStatus)
        self.em_a_power = 0
        self.em_b_power = 0
        self.em_c_power = 0

        # Passive mode timing
        self.passive_end_time: float | None = None
        self.manual_schedules: list[dict[str, Any]] = []

        # Temperature simulation
        self.base_temp = 25.0
        self.battery_temp = self.base_temp

        # CT/P1 meter state
        self.ct_connected = True

        # Energy statistics (accumulated over time, in Wh)
        self.total_pv_energy = 0.0
        self.total_grid_output_energy = 0.0  # Energy exported to grid
        self.total_grid_input_energy = 0.0   # Energy imported from grid
        self.total_load_energy = 0.0         # Total household consumption

        # PV simulation (always 0 for plug-in battery without solar input)
        self.pv_power = 0
        self.pv_voltage = 0
        self.pv_current = 0

        # Sub-simulators
        self.household = HouseholdSimulator()
        self.wifi = WiFiSimulator(base_rssi=-55)

        # Simulation settings
        self.power_fluctuation_pct = DEFAULT_POWER_FLUCTUATION_PCT
        self.update_interval = DEFAULT_UPDATE_INTERVAL
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._persist_callback = persist_callback
        self._persist_interval = persist_interval
        self._last_persist = time.time()

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
        if self._persist_callback:
            self._persist_callback(self.get_persistent_state())

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

        # Get gross household consumption (what appliances actually use)
        self.gross_household_consumption = self.household.get_consumption()

        # Calculate target power based on mode
        target = self._calculate_target_power()
        target = self._apply_soc_limits(target)

        # Apply power limits
        target = max(-self.max_charge_power, min(self.max_discharge_power, target))

        # Add small fluctuation for realism
        if target != 0:
            fluctuation = target * (random.uniform(-1, 1) * self.power_fluctuation_pct / 100)
            self.actual_power = int(target + fluctuation)
        else:
            self.actual_power = 0

        # Calculate P1 meter reading (net flow AFTER battery contribution)
        # Positive = importing from grid, Negative = exporting to grid
        self.grid_power = self.gross_household_consumption - self.actual_power

        # Update phase power distribution
        self._update_phase_powers()

        # Update SOC based on actual power flow
        hours = elapsed_seconds / 3600
        energy_wh = self.actual_power * hours  # Positive = discharging
        soc_change = -(energy_wh / self.capacity_wh) * 100
        self.soc = max(0, min(100, self.soc + soc_change))

        # Update energy statistics
        self._update_energy_stats(elapsed_seconds)

        # Update temperature
        self._update_temperature()

        # Persist state periodically
        self._maybe_persist_locked()

    def _calculate_target_power(self) -> int:
        """Calculate target battery power based on mode.
        
        In Auto mode: discharge to match household consumption (keep P1 at 0).
        The battery effectively sees:
          target = gross_household_consumption (to fully offset it)
        This makes grid_power = gross - actual ≈ 0
        """
        if self.mode == MODE_PASSIVE:
            # Fixed power set by user (+ = discharge, - = charge)
            return self.target_power

        if self.mode == MODE_MANUAL:
            schedule = self._get_active_schedule()
            return schedule.get("power", 0) if schedule else 0

        if self.mode == MODE_AUTO:
            # Discharge to offset household, keep P1 at 0
            if self.soc <= SOC_RESERVE:
                return 0  # Don't discharge below reserve
            
            target = self.gross_household_consumption
            return min(target, self.max_discharge_power)

        if self.mode == MODE_AI:
            # Smarter decisions based on time of day and SOC
            if self.soc <= 15:
                return 0
            
            hour = datetime.now().hour
            target = self.gross_household_consumption

            # Night (cheap rate): might charge
            if 0 <= hour < 6:
                if self.soc < 50:
                    return -int(self.max_charge_power * 0.5)
                return 0

            # Solar hours: be conservative
            if 9 <= hour < 17:
                if self.soc > 60:
                    target = int(target * 0.5)
                else:
                    target = int(target * 0.3)

            # Evening peak: use battery
            if 17 <= hour < 22:
                if self.soc < 30:
                    target = int(target * 0.5)

            return min(target, self.max_discharge_power)

        return 0

    def _apply_soc_limits(self, target: int) -> int:
        """Apply power limits based on SOC to protect battery."""
        # Can't discharge if SOC too low
        if target > 0 and self.soc <= SOC_MIN_DISCHARGE:
            return 0
        
        # Can't charge if already full
        if target < 0 and self.soc >= 100:
            return 0
        
        # Taper charging when nearly full
        if target < 0 and self.soc > SOC_TAPER_CHARGE:
            taper = (100 - self.soc) / (100 - SOC_TAPER_CHARGE)
            target = int(target * taper)
        
        # Taper discharging when nearly empty
        if target > 0 and self.soc < SOC_TAPER_DISCHARGE:
            taper = (self.soc - SOC_MIN_DISCHARGE) / (SOC_TAPER_DISCHARGE - SOC_MIN_DISCHARGE)
            taper = max(0, taper)
            target = int(target * taper)
        
        return target

    def _update_phase_powers(self) -> None:
        """Update phase power distribution for EM.GetStatus.
        
        Realistically distributes grid power across 3 phases.
        Phase A typically has highest load (kitchen/HVAC).
        Phases MUST always sum to grid_power (total).
        """
        total = self.grid_power
        
        # Distribute with realistic imbalance (~40%/35%/25%)
        a_ratio = 0.40 + random.uniform(-0.05, 0.05)
        b_ratio = 0.35 + random.uniform(-0.05, 0.05)
        
        self.em_a_power = int(total * a_ratio)
        self.em_b_power = int(total * b_ratio)
        # Phase C gets the remainder so phases always sum to total
        self.em_c_power = total - self.em_a_power - self.em_b_power

    def _update_energy_stats(self, elapsed_seconds: float) -> None:
        """Update energy statistics based on power flow."""
        hours = elapsed_seconds / 3600
        
        # Grid energy tracking
        if self.grid_power > 0:
            self.total_grid_input_energy += self.grid_power * hours
        else:
            self.total_grid_output_energy += abs(self.grid_power) * hours
        
        # Load energy = gross household consumption
        self.total_load_energy += self.gross_household_consumption * hours

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

    def apply_persistent_state(self, state: dict[str, Any]) -> None:
        """Apply persisted state values to the simulator."""
        with self._lock:
            self.soc = float(state.get("soc", self.soc))
            self.total_pv_energy = float(
                state.get("total_pv_energy", self.total_pv_energy)
            )
            self.total_grid_output_energy = float(
                state.get("total_grid_output_energy", self.total_grid_output_energy)
            )
            self.total_grid_input_energy = float(
                state.get("total_grid_input_energy", self.total_grid_input_energy)
            )
            self.total_load_energy = float(
                state.get("total_load_energy", self.total_load_energy)
            )

    def _get_persistent_state_locked(self) -> dict[str, Any]:
        return {
            "soc": float(self.soc),
            "total_pv_energy": float(self.total_pv_energy),
            "total_grid_output_energy": float(self.total_grid_output_energy),
            "total_grid_input_energy": float(self.total_grid_input_energy),
            "total_load_energy": float(self.total_load_energy),
        }

    def get_persistent_state(self) -> dict[str, Any]:
        """Return current state suitable for persistence."""
        with self._lock:
            return self._get_persistent_state_locked()

    def _maybe_persist_locked(self) -> None:
        if not self._persist_callback:
            return
        now = time.time()
        if now - self._last_persist < self._persist_interval:
            return
        self._persist_callback(self._get_persistent_state_locked())
        self._last_persist = now

    def _apply_immediate_power_update(self) -> None:
        """Immediately update power to reflect mode change."""
        # Refresh household consumption
        self.gross_household_consumption = self.household.get_consumption()
        
        target = self._calculate_target_power()
        target = self._apply_soc_limits(target)
        target = max(-self.max_charge_power, min(self.max_discharge_power, target))

        if target != 0:
            fluctuation = target * (random.uniform(-1, 1) * self.power_fluctuation_pct / 100)
            self.actual_power = int(target + fluctuation)
        else:
            self.actual_power = 0

        self.grid_power = self.gross_household_consumption - self.actual_power
        self._update_phase_powers()
        print(f"[SIM] Immediate update: battery={self.actual_power}W, P1={self.grid_power}W")

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
        """Get current battery state for API responses."""
        with self._lock:
            # Determine status label
            if self.actual_power < -50:
                status = STATUS_CHARGING
            elif self.actual_power > 50:
                status = STATUS_DISCHARGING
            else:
                status = STATUS_IDLE

            # Passive remaining time
            passive_remaining = 0
            if self.passive_end_time and self.mode == MODE_PASSIVE:
                passive_remaining = max(0, int(self.passive_end_time - time.time()))

            passive_cfg = None
            if self.mode == MODE_PASSIVE:
                passive_cfg = {"power": self.target_power, "cd_time": passive_remaining}

            return {
                # Core battery state
                "soc": int(self.soc),
                "power": self.actual_power,
                "mode": self.mode,
                "status": status,
                
                # Grid/P1 meter state
                "grid_power": self.grid_power,
                "em_a_power": self.em_a_power,
                "em_b_power": self.em_b_power,
                "em_c_power": self.em_c_power,
                "household_consumption": self.gross_household_consumption,
                
                # Mode-specific
                "passive_remaining": passive_remaining,
                "passive_cfg": passive_cfg,
                
                # Sensors
                "wifi_rssi": self.wifi.get_rssi(),
                "battery_temp": round(self.battery_temp, 1),
                "ct_connected": self.ct_connected,
                
                # Battery flags
                "charg_flag": 1 if self.soc < 100 else 0,
                "dischrg_flag": 1 if self.soc > SOC_MIN_DISCHARGE else 0,
                
                # Energy statistics (Wh)
                "total_pv_energy": int(self.total_pv_energy),
                "total_grid_output_energy": int(self.total_grid_output_energy),
                "total_grid_input_energy": int(self.total_grid_input_energy),
                "total_load_energy": int(self.total_load_energy),
                
                # PV state
                "pv_power": self.pv_power,
                "pv_voltage": self.pv_voltage,
                "pv_current": self.pv_current,
            }
