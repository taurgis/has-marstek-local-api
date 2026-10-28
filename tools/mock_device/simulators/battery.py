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
from .plug import PlugMeter, PlugReading
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


def _efficiency(value: float | None, default: float) -> float:
    """Return a one-way efficiency in (0, 1], or the shared default."""
    if value is None:
        return default
    if not 0 < value <= 1:
        raise ValueError("efficiency must be in (0, 1]")
    return float(value)


def _schedule_power_w(schedule: dict[str, Any] | None) -> int:
    """Return the watts a manual slot commands.

    A slot stored without ``power`` commands nothing. VNSEM-0 301 does that
    for every power the reporter tried (issue #104).
    """
    if not schedule:
        return 0
    power = schedule.get("power")
    if isinstance(power, bool) or not isinstance(power, int | float):
        return 0
    return int(power)


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
        # ``capacity_wh`` is the pack the physics drain. ``rated_capacity_wh``
        # is the nameplate the Open API reports. Passing a smaller capacity
        # simulates fade the BMS has not written into ``bat_cap``.
        self.rated_capacity_wh = spec.capacity_wh
        self.capacity_wh = spec.capacity_wh if capacity_wh is None else capacity_wh
        # Wall-clock seconds are multiplied by this inside ``_update_state``.
        # Reported inverter watts are multiplied by the same factor so a
        # Home Assistant poll that integrates power over wall time still
        # matches the state-of-charge movement. ``configure_capacity_experiment``
        # is what turns a faded or accelerated pack on.
        self.time_scale = 1.0
        self.charge_efficiency = CHARGE_EFFICIENCY
        self.discharge_efficiency = DISCHARGE_EFFICIENCY
        # DC watts the pack loses without them appearing on the AC port.
        self.pack_self_consumption_w = 0.0
        self.soc_jump_at: float | None = None
        self.soc_jump_by = 0.0
        self._soc_jump_armed = False
        self.soc_cycle = False
        self.cycle_low_soc = 30.0
        self.cycle_high_soc = 92.0
        self._cycle_sign = 1
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
        # Set while the network chip resets: no CT data arrives, and the
        # Control firmware's self-use loop commands 0 W until it does.
        self.network_down = False

        # Energy statistics (accumulated over time, in Wh)
        self.total_pv_energy = 0.0
        self.total_grid_output_energy = 0.0  # Battery energy exported to grid
        self.total_grid_input_energy = 0.0  # Battery energy imported from grid
        self.total_load_energy = 0.0  # Total household consumption
        self.em_input_energy = 0.0  # CT-side lifetime import
        self.em_output_energy = 0.0  # CT-side lifetime export
        self.plug = PlugMeter()  # Smart plug on the battery's own AC cord

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

    def configure_capacity_experiment(
        self,
        *,
        capacity_wh: int | None = None,
        time_scale: float = 1.0,
        soc_cycle: bool = False,
        charge_efficiency: float | None = None,
        discharge_efficiency: float | None = None,
        pack_self_consumption_w: float = 0.0,
        soc_jump_at: float | None = None,
        soc_jump_by: float = 0.0,
        power_fluctuation_pct: float | None = None,
    ) -> None:
        """Point the physics at a faded pack, a faster clock, or a SOC cycle.

        The Open API nameplate stays at ``rated_capacity_wh``. ``time_scale``
        must be positive. The cycle holds Passive mode between the low and
        high state-of-charge limits. Efficiencies replace the shared 0.93
        one-way factors. ``pack_self_consumption_w`` leaves the cells without
        showing up in reported watts. A state-of-charge jump fires once, when
        charging crosses ``soc_jump_at``.
        """
        if capacity_wh is not None:
            if capacity_wh <= 0:
                raise ValueError("capacity_wh must be positive")
            self.capacity_wh = capacity_wh
        if time_scale <= 0:
            raise ValueError("time_scale must be positive")
        self.time_scale = float(time_scale)
        self.charge_efficiency = _efficiency(charge_efficiency, CHARGE_EFFICIENCY)
        self.discharge_efficiency = _efficiency(discharge_efficiency, DISCHARGE_EFFICIENCY)
        if pack_self_consumption_w < 0:
            raise ValueError("pack_self_consumption_w must be non-negative")
        self.pack_self_consumption_w = float(pack_self_consumption_w)
        if soc_jump_by < 0:
            raise ValueError("soc_jump_by must be non-negative")
        self.soc_jump_at = soc_jump_at
        self.soc_jump_by = float(soc_jump_by)
        self._soc_jump_armed = soc_jump_at is not None and soc_jump_by > 0
        if power_fluctuation_pct is not None:
            if power_fluctuation_pct < 0:
                raise ValueError("power_fluctuation_pct must be non-negative")
            self.power_fluctuation_pct = float(power_fluctuation_pct)
        self.soc_cycle = soc_cycle
        if soc_cycle:
            self._engage_soc_cycle()

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

    def _engage_soc_cycle(self) -> None:
        """Hold Passive mode and walk the pack between two state-of-charge limits.

        The Open API mode stays Passive, so a capacity estimator that rejects
        mode changes still sees one long charge and one long discharge.
        """
        self.mode = MODE_PASSIVE
        self._cycle_sign = -1 if self.soc <= self.cycle_low_soc else 1
        self.passive_end_time = time.time() + 3650 * 24 * 3600
        self._apply_soc_cycle_target()

    def _apply_soc_cycle_target(self) -> None:
        """Point the passive setpoint at the current cycle direction."""
        if self._cycle_sign > 0 and self.soc <= self.cycle_low_soc:
            self._cycle_sign = -1
        elif self._cycle_sign < 0 and self.soc >= self.cycle_high_soc:
            self._cycle_sign = 1
        self.target_power = (
            self.max_discharge_power if self._cycle_sign > 0 else -self.max_charge_power
        )

    def _update_state(self, elapsed_seconds: float) -> None:
        """Update battery state based on elapsed time."""
        elapsed_seconds *= self.time_scale
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
        if not self.ct_connected or self.network_down:
            # No CT means no reference. Real units fall back to idle rather
            # than guessing at the house load (Control 150 0x08029c88).
            return 0
        if abs(error) <= AUTO_DEADBAND_W:
            desired_ongrid = float(self.ongrid_power)
        else:
            desired_ongrid = self.ongrid_power + error * AUTO_LOOP_GAIN
        return int(desired_ongrid - self.pv_power)

    def _calculate_target_power(self, elapsed_seconds: float | None = None) -> int:
        """Calculate target battery power based on mode."""
        if self.soc_cycle:
            self.mode = MODE_PASSIVE
            if self.passive_end_time is None or self.passive_end_time < time.time():
                self.passive_end_time = time.time() + 3650 * 24 * 3600
            self._apply_soc_cycle_target()
        if self.mode == MODE_PASSIVE:
            # Fixed power set by user (+ = discharge, - = charge)
            return self.target_power

        if self.mode == MODE_MANUAL:
            schedule = self._get_active_schedule()
            return _schedule_power_w(schedule)

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

    def _pack_dc_w(self) -> float:
        """Return watts leaving the pack, before hidden self-consumption.

        ``pv_power`` is MPPT charging power on the battery side of the
        inverter. It reaches the cells in full. ``ongrid_power`` is the AC
        port (``pv_power + actual_power``); the mock EPS socket stays 0.
        Exporting divides that port by the discharge efficiency, and
        importing multiplies it by the charge efficiency.

        With no device PV this is ``actual_power`` through the same factor,
        so Venus E 3.0 and Venus C keep the previous state-of-charge movement.
        """
        if self.ongrid_power > 0:
            from_ac = self.ongrid_power / self.discharge_efficiency
        else:
            from_ac = self.ongrid_power * self.charge_efficiency
        return from_ac - max(self.pv_power, 0)

    def _update_soc(self, elapsed_seconds: float) -> None:
        """Move the state of charge, paying conversion losses on the AC port."""
        hours = elapsed_seconds / _SECONDS_PER_HOUR
        dc_wh = self._pack_dc_w() * hours
        dc_wh += self.pack_self_consumption_w * hours
        before = self.soc
        soc_change = -(dc_wh / self.capacity_wh) * 100
        self.soc = max(0.0, min(100.0, self.soc + soc_change))
        self._apply_soc_jump(before)

    def _apply_soc_jump(self, before: float) -> None:
        """Recalibrate once when charging crosses the armed threshold."""
        if not self._soc_jump_armed or self.soc_jump_at is None:
            return
        if before < self.soc_jump_at <= self.soc:
            self.soc = min(100.0, self.soc + self.soc_jump_by)
            self._soc_jump_armed = False

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
        self.plug.accumulate(self.standby_power - self.ongrid_power, hours)

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
        # Heat follows the AC port. PV on the DC bus does not pass the
        # inverter. With no device PV, ongrid_power equals actual_power.
        loss_w = abs(self.ongrid_power) * (1 - DISCHARGE_EFFICIENCY) + self.standby_power
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
            self.plug.apply_state(state)

    def _get_persistent_state_locked(self) -> dict[str, Any]:
        return {
            "soc": float(self.soc),
            "total_pv_energy": float(self.total_pv_energy),
            "total_grid_output_energy": float(self.total_grid_output_energy),
            "total_grid_input_energy": float(self.total_grid_input_energy),
            "total_load_energy": float(self.total_load_energy),
            "em_input_energy": float(self.em_input_energy),
            "em_output_energy": float(self.em_output_energy),
            **self.plug.to_state(),
        }

    def get_plug_reading(self) -> PlugReading:
        """Return the plug's counters and watts, scaled like the inverter port."""
        with self._lock:
            return self.plug.reading(self._reported_watts(self.standby_power - self.ongrid_power))

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

    def store_manual_slot_without_power(self, config: dict[str, Any]) -> None:
        """Store a Manual slot and leave its power unset.

        Issue #104: VNSEM-0 301 answers ``set_result: true`` and keeps the
        time, weekday and enable. The Marstek app shows the power as ``---``
        for a positive, negative or zero value. The slot must not drive the
        inverter.
        """
        with self._lock:
            self.mode = MODE_MANUAL
            slot = config.get("time_num", 0)
            schedule: dict[str, Any] = {
                "time_num": slot,
                "start_time": config.get("start_time", "00:00"),
                "end_time": config.get("end_time", "23:59"),
                "week_set": config.get("week_set", 127),
                "power": None,
                "enable": config.get("enable", 1) == 1,
            }
            for index, existing in enumerate(self.manual_schedules):
                if existing.get("time_num") == slot:
                    self.manual_schedules[index] = schedule
                    break
            else:
                self.manual_schedules.append(schedule)
            print(f"[SIM] Manual schedule slot {slot} stored without power: {schedule}")

    def store_manual_slot_from_documented_manual_set(self, config: dict[str, Any]) -> None:
        """Apply Rev 3.1 ``manual_set`` 0-2 with unsigned ``power``.

        Documentation-based, not a capture. Charge is stored as negative
        watts internally so the simulator's signed physics still run. Auto
        (``manual_set`` 3) and a negative wire ``power`` are not handled
        here; the caller must not invoke this for those payloads.
        """
        with self._lock:
            self.mode = MODE_MANUAL
            slot = config.get("time_num", 0)
            raw_power = config.get("power")
            if isinstance(raw_power, int) and not isinstance(raw_power, bool):
                magnitude = raw_power
            else:
                magnitude = 0
            manual_set = config.get("manual_set")
            enable = config.get("enable", 1) == 1
            power: int | None
            if manual_set == 0:
                power = None
                enable = False
            elif manual_set == 1:
                power = -magnitude
            else:
                power = magnitude
            schedule: dict[str, Any] = {
                "time_num": slot,
                "start_time": config.get("start_time", "00:00"),
                "end_time": config.get("end_time", "23:59"),
                "week_set": config.get("week_set", 127),
                "power": power,
                "enable": enable,
                "manual_set": manual_set,
            }
            for index, existing in enumerate(self.manual_schedules):
                if existing.get("time_num") == slot:
                    self.manual_schedules[index] = schedule
                    break
            else:
                self.manual_schedules.append(schedule)
            print(f"[SIM] Manual schedule slot {slot} from documented manual_set: {schedule}")
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

    def _reported_watts(self, watts: float) -> int:
        """Return watts as the API should report them.

        At ``time_scale`` 1 this is the physical wattage. Above 1 the reported
        wattage grows with the accelerated state of charge so an integrator
        that only has wall-clock timestamps still recovers the true capacity.
        PV is included: the derived balance subtracts it from the AC port, and
        an unscaled PV term would invent battery power.
        """
        return round(watts * self.time_scale)

    def _reported_pv_channels(self) -> list[dict[str, Any]]:
        """Return MPPT channels with power scaled like the inverter port."""
        scaled: list[dict[str, Any]] = []
        for channel in self.pv_channels.snapshot():
            reading = dict(channel)
            power = self._reported_watts(float(channel.get("pv_power", 0) or 0))
            reading["pv_power"] = power
            voltage = channel.get("pv_voltage") or 0
            if isinstance(voltage, (int, float)) and not isinstance(voltage, bool) and voltage > 0:
                reading["pv_current"] = round(power / float(voltage), 2)
            scaled.append(reading)
        return scaled

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

            reported = self._reported_watts
            return {
                # Core battery state
                "soc": int(self.soc),
                "power": reported(self.actual_power),
                "mode": self.mode,
                "status": status,
                # Inverter AC port, what ES.GetStatus calls ongrid_power.
                # Scaled with ``time_scale`` so wall-clock integration matches
                # the accelerated state of charge. The meter below is not.
                "ongrid_power": reported(self.ongrid_power),
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
                # PV state (device DC input; zero on families without one).
                # Scaled with the inverter port so pv - ongrid still equals
                # the battery at any time scale.
                "pv_power": self._reported_watts(self.pv_power),
                "pv_voltage": self.pv_voltage,
                "pv_current": self.pv_current,
                "pv_channels": self._reported_pv_channels(),
            }
