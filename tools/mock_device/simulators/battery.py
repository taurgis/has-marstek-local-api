"""Battery behavior simulator with a closed-loop P1 meter feedback path.

Simulates how a real Marstek battery behaves in a home:

* The CT clamp / P1 meter measures **net** flow at the meter point, after the
  battery and any rooftop array have contributed.
* In Auto mode the device regulates its own output against that measurement
  to hold the meter near zero. It only ever sees the net figure, never the
  true appliance load, so the loop has to close on the meter reading.
* ``ES.GetStatus.ongrid_power`` is the **inverter's own** AC port power, not
  the meter reading. Real evidence: the Venus A firmware 147 capture in issue
  #11 reports ``ongrid_power: 318`` at the same moment ``EM.GetStatus``
  reports ``total_power: -16``. The integration derives battery power as
  ``pv_power - ongrid_power``, so reporting the meter value there would make
  Home Assistant show a battery that drains at 0 W.

Sign conventions inside the simulator:

* ``actual_power``  positive = discharging, negative = charging (AC side)
* ``ongrid_power``  positive = inverter exporting, negative = importing
* ``grid_power``    positive = house importing, negative = house exporting
"""

import math
import random
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..const import (
    AMBIENT_MEAN_C,
    AMBIENT_SWING_C,
    AUTO_DEADBAND_W,
    AUTO_LOOP_GAIN,
    BAT_TEMP_MAX_C,
    BAT_TEMP_MIN_CHARGE_C,
    CHARGE_EFFICIENCY,
    CT_MEASUREMENT_LAG_SECONDS,
    CT_NOISE_W,
    DEFAULT_HOUSE_PV_WP,
    DEFAULT_PHASE_COUNT,
    DEFAULT_POWER_FLUCTUATION_PCT,
    DEFAULT_UPDATE_INTERVAL,
    DISCHARGE_EFFICIENCY,
    MODE_AI,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_PASSIVE,
    MODE_UPS,
    POWER_RAMP_W_PER_SECOND,
    SOC_MIN_DISCHARGE,
    SOC_RESERVE,
    SOC_TAPER_CHARGE,
    SOC_TAPER_DISCHARGE,
    STATUS_CHARGING,
    STATUS_DISCHARGING,
    STATUS_IDLE,
    THERMAL_CAPACITY_J_PER_K_PER_WH,
    THERMAL_CONDUCTANCE_W_PER_K_PER_WH,
    DeviceSpec,
    device_spec,
)
from .household import HouseholdSimulator
from .solar import PVChannelSimulator, SolarSimulator
from .wifi import WiFiSimulator

_SECONDS_PER_HOUR = 3600.0

# AI mode: cheap overnight window used to top the pack up from the grid, and
# the state of charge it aims for before the morning.
_AI_NIGHT_CHARGE_HOURS = range(1, 6)
_AI_NIGHT_TARGET_SOC = 80
_AI_NIGHT_CHARGE_FRACTION = 0.6
# Below this the pack is held back for the evening peak instead of covering
# midday load the roof can already cover.
_AI_EVENING_RESERVE_SOC = 35
_AI_EVENING_HOURS = range(17, 23)

# UPS mode keeps the pack full as backup: charge until here, then hold.
_UPS_TARGET_SOC = 100
_UPS_CHARGE_FRACTION = 0.5

# Phase split of the house load for three-phase installs. Resampled slowly so
# the imbalance drifts rather than flickering every second.
_PHASE_SPLIT_UPDATE_SECONDS = 60.0


class BatterySimulator:
    """Simulates realistic Marstek battery behavior with P1 meter feedback.

    Power balance maintained every tick::

        ongrid_power = pv_power + actual_power
        grid_power   = household_load + standby_power - ongrid_power

    In Auto mode the controller nudges its own output by the measured meter
    imbalance until ``grid_power`` sits inside the deadband. A load step
    therefore shows on the meter for a second or two before the battery
    covers it, and a rooftop surplus pushes the controller negative so the
    battery charges, exactly as a real unit does.
    """

    def __init__(
        self,
        initial_soc: int = 50,
        capacity_wh: int | None = None,
        max_charge_power: int | None = None,
        max_discharge_power: int | None = None,
        persist_callback: Callable[[dict[str, Any]], None] | None = None,
        persist_interval: float = 30.0,
        device_type: str | None = None,
        house_pv_wp: int | None = None,
        pv_channels: list[dict[str, float]] | None = None,
        phases: int = DEFAULT_PHASE_COUNT,
        rng: random.Random | None = None,
    ):
        spec: DeviceSpec = device_spec(device_type)
        self.spec = spec
        self.soc = initial_soc
        self.capacity_wh = spec.capacity_wh if capacity_wh is None else capacity_wh
        self.max_charge_power = (
            spec.max_charge_power if max_charge_power is None else max_charge_power
        )
        self.max_discharge_power = (
            spec.max_discharge_power if max_discharge_power is None else max_discharge_power
        )
        self.standby_power = spec.standby_power
        self.phases = 3 if phases == 3 else 1
        self._rng = rng or random.Random()

        # Current state
        self.mode = MODE_AUTO
        self.target_power = 0  # Target for passive/manual mode
        self.setpoint_power = 0  # Commanded AC power before ramp limiting
        self.actual_power = 0  # What battery is doing (+ = discharge, - = charge)
        self.gross_household_consumption = 0  # What appliances use (before battery)
        self.grid_power = 0  # P1 meter reading (net flow after battery and PV)
        self.ongrid_power = 0  # Inverter AC port power (+ = exporting)

        # Phase power distribution (for EM.GetStatus)
        self.em_a_power = 0
        self.em_b_power = 0
        self.em_c_power = 0
        self._phase_split = (0.45, 0.33)
        self._last_phase_split_update = 0.0

        # Passive mode timing
        self.passive_end_time: float | None = None
        self.manual_schedules: list[dict[str, Any]] = []

        # Temperature simulation
        self.base_temp = AMBIENT_MEAN_C
        self.ambient_temp = AMBIENT_MEAN_C
        self.battery_temp = self._ambient_temperature()

        # CT/P1 meter state
        self.ct_connected = True
        self._measured_grid_power = 0.0

        # Energy statistics (accumulated over time, in Wh)
        self.total_pv_energy = 0.0
        self.total_grid_output_energy = 0.0  # Battery energy exported to grid
        self.total_grid_input_energy = 0.0  # Battery energy imported from grid
        self.total_load_energy = 0.0  # Total household consumption
        self.em_input_energy = 0.0  # CT-side lifetime import
        self.em_output_energy = 0.0  # CT-side lifetime export

        # PV state. ``pv_power`` is the device's own DC input (Venus A/D).
        self.pv_power = 0
        self.pv_voltage = 0
        self.pv_current = 0

        # Sub-simulators
        self.household = HouseholdSimulator(rng=self._rng)
        self.wifi = WiFiSimulator(base_rssi=-55)
        self.solar = SolarSimulator(
            peak_power_w=self._default_house_pv_wp(house_pv_wp, pv_channels),
            rng=self._rng,
        )
        # Device MPPT channels share the sky with the rooftop array.
        self.pv_channels = PVChannelSimulator(list(pv_channels or []), self.solar)
        self.house_pv_power = 0
        # Pinned inputs. Setting either freezes that side of the house so a
        # scenario can ask "the P1 meter reads X, what does the battery do?"
        # and get the same answer a real unit would give.
        self._house_load_override: int | None = None
        self._house_pv_override: int | None = None

        # Simulation settings
        self.power_fluctuation_pct = DEFAULT_POWER_FLUCTUATION_PCT
        self.ramp_rate_w_per_s = POWER_RAMP_W_PER_SECOND
        self.update_interval = DEFAULT_UPDATE_INTERVAL
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._persist_callback = persist_callback
        self._persist_interval = persist_interval
        self._last_persist = time.time()

    @staticmethod
    def _default_house_pv_wp(
        house_pv_wp: int | None,
        pv_channels: list[dict[str, float]] | None,
    ) -> int:
        """Choose the rooftop array size for the simulated dwelling.

        A Venus A or D with DC strings attached already has panels in the
        picture; adding a separate rooftop inverter on top would double the
        sun. Everything else gets a modest array so Auto mode has a surplus
        to charge from.
        """
        if house_pv_wp is not None:
            return max(0, house_pv_wp)
        if pv_channels:
            return 0
        return DEFAULT_HOUSE_PV_WP

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
        if (
            self.mode == MODE_PASSIVE
            and self.passive_end_time
            and time.time() >= self.passive_end_time
        ):
            print("[SIM] Passive mode expired, switching to Auto")
            self.mode = MODE_AUTO
            self.target_power = 0
            self.passive_end_time = None

        self._refresh_inputs()
        self._advance_power(elapsed_seconds)
        self._settle_flows()
        self._update_soc(elapsed_seconds)
        self._update_energy_stats(elapsed_seconds)
        self._update_temperature(elapsed_seconds)
        self._maybe_persist_locked()

    def set_house_load(self, watts: int | None) -> None:
        """Pin the gross household load, or restore the simulated dwelling.

        ``None`` hands the house back to :class:`HouseholdSimulator`.
        """
        self._house_load_override = None if watts is None else max(0, int(watts))

    def set_house_pv(self, watts: int | None) -> None:
        """Pin rooftop PV production, or restore the solar curve."""
        self._house_pv_override = None if watts is None else max(0, int(watts))

    def _refresh_inputs(self) -> None:
        """Sample the house, the roof and the device's own DC strings."""
        if self._house_load_override is None:
            self.gross_household_consumption = self.household.get_consumption()
        else:
            self.gross_household_consumption = self._house_load_override
        if self._house_pv_override is None:
            self.house_pv_power = self.solar.get_power()
        else:
            self.house_pv_power = self._house_pv_override
        if self.pv_channels.configured:
            channels = self.pv_channels.snapshot()
            self.pv_power = self.pv_channels.total_power(channels)
            first = channels[0] if channels else {}
            self.pv_voltage = first.get("pv_voltage", 0)
            self.pv_current = first.get("pv_current", 0)

    def _advance_power(self, elapsed_seconds: float) -> None:
        """Move the inverter toward its setpoint under the ramp-rate limit."""
        target = self._calculate_target_power(elapsed_seconds)
        target = self._apply_soc_limits(target)
        target = max(-self.max_charge_power, min(self.max_discharge_power, target))
        self.setpoint_power = target

        ramp_limit = max(1.0, self.ramp_rate_w_per_s * max(elapsed_seconds, 0.0))
        delta = target - self.actual_power
        if abs(delta) > ramp_limit:
            delta = math.copysign(ramp_limit, delta)
        reached = self.actual_power + delta

        if abs(reached) < 1:
            self.actual_power = 0
            return
        # Setpoint tracking is good but never exact.
        jitter = reached * (self._rng.uniform(-1, 1) * self.power_fluctuation_pct / 100)
        self.actual_power = int(reached + jitter)

    def _settle_flows(self) -> None:
        """Recompute the inverter port, the meter reading and the phases."""
        self.ongrid_power = self.pv_power + self.actual_power
        self.grid_power = (
            self.gross_household_consumption
            + self.standby_power
            - self.house_pv_power
            - self.ongrid_power
        )
        self._update_phase_powers()

    def _measure_grid_power(self, elapsed_seconds: float) -> float:
        """Return the meter reading the controller currently believes.

        A CT link reports about once a second and the controller filters it,
        so the value driving regulation always trails the truth. That lag is
        what puts a visible transient on the meter when a load switches on.
        """
        if not self.ct_connected:
            return 0.0
        elapsed = max(elapsed_seconds, 0.0)
        alpha = elapsed / (elapsed + CT_MEASUREMENT_LAG_SECONDS) if elapsed > 0 else 0.0
        self._measured_grid_power += (self.grid_power - self._measured_grid_power) * alpha
        return self._measured_grid_power + self._rng.uniform(-CT_NOISE_W, CT_NOISE_W)

    def _regulated_target_power(self, elapsed_seconds: float | None = None) -> int:
        """Return the battery setpoint that drives the meter toward zero.

        The controller commands its AC port, so PV is subtracted back out to
        get the battery's own share: ``battery = desired_ongrid - pv_power``.
        A rooftop surplus therefore turns into charging without any special
        case, which is how a real unit ends up absorbing midday export.
        """
        interval = self.update_interval if elapsed_seconds is None else elapsed_seconds
        error = self._measure_grid_power(interval)
        if not self.ct_connected:
            # No CT means no reference. Real units fall back to idle rather
            # than guessing at the house load.
            return 0
        if abs(error) <= AUTO_DEADBAND_W:
            desired_ongrid = float(self.ongrid_power)
        else:
            desired_ongrid = self.ongrid_power + error * AUTO_LOOP_GAIN
        return int(desired_ongrid - self.pv_power)

    def _calculate_target_power(self, elapsed_seconds: float | None = None) -> int:
        """Calculate target battery power based on mode."""
        if self.mode == MODE_PASSIVE:
            # Fixed power set by user (+ = discharge, - = charge)
            return self.target_power

        if self.mode == MODE_MANUAL:
            schedule = self._get_active_schedule()
            return schedule.get("power", 0) if schedule else 0

        if self.mode == MODE_UPS:
            # Backup mode: fill the pack and hold it there.
            if self.soc >= _UPS_TARGET_SOC:
                return 0
            return -int(self.max_charge_power * _UPS_CHARGE_FRACTION)

        if self.mode == MODE_AUTO:
            return self._self_consumption_target(elapsed_seconds)

        if self.mode == MODE_AI:
            hour = datetime.now().hour
            # Cheap overnight window: top up from the grid.
            if hour in _AI_NIGHT_CHARGE_HOURS and self.soc < _AI_NIGHT_TARGET_SOC:
                return -int(self.max_charge_power * _AI_NIGHT_CHARGE_FRACTION)
            target = self._self_consumption_target(elapsed_seconds)
            # Outside the evening peak, hold a reserve back for it.
            if target > 0 and hour not in _AI_EVENING_HOURS and self.soc <= _AI_EVENING_RESERVE_SOC:
                return 0
            return target

        return 0

    def _self_consumption_target(self, elapsed_seconds: float | None = None) -> int:
        """Return the Auto-mode setpoint, honouring the discharge reserve."""
        target = self._regulated_target_power(elapsed_seconds)
        if target > 0 and self.soc <= SOC_RESERVE:
            # Below the reserve the unit stops supplying the house but still
            # accepts a surplus, so the pack recovers on the next sunny hour.
            return 0
        return target

    def _apply_soc_limits(self, target: int) -> int:
        """Apply power limits based on SOC to protect battery."""
        # Can't discharge if SOC too low
        if target > 0 and self.soc <= SOC_MIN_DISCHARGE:
            return 0

        # Can't charge if already full
        if target < 0 and self.soc >= 100:
            return 0

        # The BMS refuses to charge a cold or hot pack.
        if target < 0 and not self._charge_allowed():
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

    def _charge_allowed(self) -> bool:
        """Return whether the BMS permits charging at the current temperature."""
        return BAT_TEMP_MIN_CHARGE_C <= self.battery_temp < BAT_TEMP_MAX_C

    def _update_phase_powers(self) -> None:
        """Split the meter reading across phases for EM.GetStatus.

        A Venus is a single-phase unit. On a single-phase supply everything
        lands on phase A. On a three-phase supply each phase carries its own
        share of the house load while the battery only offsets the phase it
        is plugged into, which is why one phase can export while the others
        still import. Phase C takes the remainder so the three always sum to
        the total.
        """
        total = self.grid_power
        if self.phases == 1:
            self.em_a_power = total
            self.em_b_power = 0
            self.em_c_power = 0
            return

        now = time.time()
        if now - self._last_phase_split_update > _PHASE_SPLIT_UPDATE_SECONDS:
            self._last_phase_split_update = now
            self._phase_split = (
                0.45 + self._rng.uniform(-0.07, 0.07),
                0.33 + self._rng.uniform(-0.05, 0.05),
            )

        house_side = self.gross_household_consumption + self.standby_power - self.house_pv_power
        a_ratio, b_ratio = self._phase_split
        # The inverter sits on phase A only.
        self.em_a_power = int(house_side * a_ratio) - self.ongrid_power
        self.em_b_power = int(house_side * b_ratio)
        self.em_c_power = total - self.em_a_power - self.em_b_power

    def _update_soc(self, elapsed_seconds: float) -> None:
        """Move the state of charge, paying conversion losses in both directions."""
        hours = elapsed_seconds / _SECONDS_PER_HOUR
        ac_wh = self.actual_power * hours
        # Discharging, the pack gives up more than reaches the AC port;
        # charging, part of what the port draws is lost on the way in.
        dc_wh = ac_wh / DISCHARGE_EFFICIENCY if ac_wh > 0 else ac_wh * CHARGE_EFFICIENCY
        soc_change = -(dc_wh / self.capacity_wh) * 100
        self.soc = max(0, min(100, self.soc + soc_change))

    def _update_energy_stats(self, elapsed_seconds: float) -> None:
        """Update energy statistics based on power flow.

        ``total_grid_input_energy`` / ``total_grid_output_energy`` are the
        **device's** lifetime counters and follow ``ongrid_power``; that is
        the same relationship the integration falls back on when firmware
        stalls those counters. The ``EM`` totals follow the meter instead,
        because they are measured at a different point and diverge from the
        device counters in exactly the way a real install does.
        """
        hours = elapsed_seconds / _SECONDS_PER_HOUR

        if self.ongrid_power > 0:
            self.total_grid_output_energy += self.ongrid_power * hours
        else:
            self.total_grid_input_energy += abs(self.ongrid_power) * hours

        if self.grid_power > 0:
            self.em_input_energy += self.grid_power * hours
        else:
            self.em_output_energy += abs(self.grid_power) * hours

        self.total_load_energy += self.gross_household_consumption * hours

        if self.pv_channels.configured:
            self.pv_channels.accumulate(self.pv_power, elapsed_seconds)
            self.total_pv_energy = self.pv_channels.total_pv_energy

    def _ambient_temperature(self, when: datetime | None = None) -> float:
        """Return ambient temperature, coldest before dawn and warmest mid-afternoon."""
        moment = when or datetime.now()
        hours = moment.hour + moment.minute / 60
        return self.base_temp + AMBIENT_SWING_C * math.sin(2 * math.pi * (hours - 9) / 24)

    def _update_temperature(self, elapsed_seconds: float) -> None:
        """Advance pack temperature from conversion losses and ambient exchange.

        Heat in is the power actually lost to conversion plus the unit's own
        auxiliary draw; heat out is proportional to the gap to ambient. At
        continuous full power the pack settles about twelve degrees above
        ambient, reached over roughly an hour -- not the degree-per-second
        climb a fixed per-tick increment produces.
        """
        self.ambient_temp = self._ambient_temperature()
        loss_w = abs(self.actual_power) * (1 - DISCHARGE_EFFICIENCY) + self.standby_power
        heat_capacity = THERMAL_CAPACITY_J_PER_K_PER_WH * self.capacity_wh
        conductance = THERMAL_CONDUCTANCE_W_PER_K_PER_WH * self.capacity_wh
        net_w = loss_w - (self.battery_temp - self.ambient_temp) * conductance
        self.battery_temp += net_w / heat_capacity * max(elapsed_seconds, 0.0)
        self.battery_temp = max(-10.0, min(60.0, self.battery_temp))

    def apply_persistent_state(self, state: dict[str, Any]) -> None:
        """Apply persisted state values to the simulator."""
        with self._lock:
            self.soc = float(state.get("soc", self.soc))
            self.total_pv_energy = float(state.get("total_pv_energy", self.total_pv_energy))
            self.pv_channels.total_pv_energy = self.total_pv_energy
            self.total_grid_output_energy = float(
                state.get("total_grid_output_energy", self.total_grid_output_energy)
            )
            self.total_grid_input_energy = float(
                state.get("total_grid_input_energy", self.total_grid_input_energy)
            )
            self.total_load_energy = float(state.get("total_load_energy", self.total_load_energy))
            self.em_input_energy = float(state.get("em_input_energy", self.total_grid_input_energy))
            self.em_output_energy = float(
                state.get("em_output_energy", self.total_grid_output_energy)
            )

    def _get_persistent_state_locked(self) -> dict[str, Any]:
        return {
            "soc": float(self.soc),
            "total_pv_energy": float(self.total_pv_energy),
            "total_grid_output_energy": float(self.total_grid_output_energy),
            "total_grid_input_energy": float(self.total_grid_input_energy),
            "total_load_energy": float(self.total_load_energy),
            "em_input_energy": float(self.em_input_energy),
            "em_output_energy": float(self.em_output_energy),
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
        """Start moving toward the new setpoint as soon as the mode changes."""
        self._refresh_inputs()
        self._advance_power(self.update_interval)
        self._settle_flows()
        print(
            f"[SIM] Setpoint {self.setpoint_power}W, "
            f"battery={self.actual_power}W, P1={self.grid_power}W"
        )

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

    def settle(self, seconds: float = 10.0, step: float = 1.0) -> None:
        """Advance the simulation without waiting on the background thread.

        Regulation, ramping and the CT lag all need time to converge, so a
        caller that wants a settled reading (a test, or a scripted scenario)
        needs to give the loop its seconds.
        """
        with self._lock:
            remaining = seconds
            while remaining > 0:
                self._update_state(min(step, remaining))
                remaining -= step

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
                # Inverter AC port, what ES.GetStatus calls ongrid_power
                "ongrid_power": self.ongrid_power,
                # Grid/P1 meter state
                "grid_power": self.grid_power,
                "em_a_power": self.em_a_power,
                "em_b_power": self.em_b_power,
                "em_c_power": self.em_c_power,
                "household_consumption": self.gross_household_consumption,
                "house_pv_power": self.house_pv_power,
                # Mode-specific
                "passive_remaining": passive_remaining,
                "passive_cfg": passive_cfg,
                # Sensors
                "wifi_rssi": self.wifi.get_rssi(),
                "battery_temp": round(self.battery_temp, 1),
                "ambient_temp": round(self.ambient_temp, 1),
                "ct_connected": self.ct_connected,
                # Battery flags
                "charg_flag": 1 if self.soc < 100 and self._charge_allowed() else 0,
                "dischrg_flag": 1 if self.soc > SOC_MIN_DISCHARGE else 0,
                # Energy statistics (Wh)
                "total_pv_energy": int(self.total_pv_energy),
                "total_grid_output_energy": int(self.total_grid_output_energy),
                "total_grid_input_energy": int(self.total_grid_input_energy),
                "total_load_energy": int(self.total_load_energy),
                "em_input_energy": int(self.em_input_energy),
                "em_output_energy": int(self.em_output_energy),
                # PV state (device DC input; zero on families without one)
                "pv_power": self.pv_power,
                "pv_voltage": self.pv_voltage,
                "pv_current": self.pv_current,
                "pv_channels": self.pv_channels.snapshot(),
            }
