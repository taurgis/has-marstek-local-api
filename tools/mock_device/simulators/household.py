"""Household power consumption simulator.

Models the gross load of a single dwelling: the figure a P1 meter would show
if no battery and no panels were connected. Four components are summed:

* an always-on base load (router, standby, heating controls);
* a smooth diurnal curve interpolated between hourly set points, so the
  baseline drifts over minutes instead of jumping every second;
* cyclic loads with a duty cycle, the fridge being the one every real trace
  shows;
* discrete appliance and cooking events.

The daily shape is a two-peak profile (morning and evening) matching the
residential load curves published by European system operators.
"""

import random
import threading
import time
from datetime import datetime

# Additional watts on top of the base load, per hour of the day. Interpolated
# between neighbouring hours so the curve is continuous.
_DIURNAL_WATTS: tuple[int, ...] = (
    60,  # 00
    50,  # 01
    45,  # 02
    45,  # 03
    50,  # 04
    80,  # 05
    180,  # 06
    330,  # 07
    290,  # 08
    210,  # 09
    180,  # 10
    190,  # 11
    250,  # 12
    215,  # 13
    180,  # 14
    195,  # 15
    250,  # 16
    410,  # 17
    530,  # 18
    470,  # 19
    390,  # 20
    300,  # 21
    195,  # 22
    110,  # 23
)

# Occupancy varies day to day; the whole diurnal curve is scaled by a factor
# resampled at midnight.
_DAILY_SCALE_RANGE = (0.80, 1.25)

# Fridge / freezer compressor. Roughly a third duty cycle at a modest draw is
# the signature every real P1 trace carries.
_FRIDGE_POWER_RANGE = (60, 95)
_FRIDGE_ON_SECONDS = (14 * 60, 22 * 60)
_FRIDGE_OFF_SECONDS = (30 * 60, 55 * 60)

# Small continuous noise from everything not modelled individually.
_NOISE_WATTS = 12

_SECONDS_PER_MINUTE = 60

# How often discrete events are rolled for. The probabilities below are per
# check, so the two numbers have to be read together: at one check every 30
# seconds a day holds 2880 rolls, and a chance that looks small still fires
# several times. Tuned for roughly two or three cooking sessions and two or
# three appliance runs a day, which together with the diurnal curve puts the
# dwelling near the ~10 kWh/day EU average.
_EVENT_CHECK_SECONDS = 30
_COOKING_CHANCE_PEAK = 0.003
_COOKING_CHANCE_OFFPEAK = 0.0003
_COOKING_PEAK_HOURS = frozenset({7, 8, 12, 13, 18, 19, 20})
_APPLIANCE_CHANCE = 0.001


class HouseholdSimulator:
    """Simulates realistic household power consumption (what a P1 meter would see)."""

    def __init__(self, base_load: int = 120, rng: random.Random | None = None):
        """Initialize household simulator.

        Args:
            base_load: Always-on load in watts (router, standby, controls).
            rng: Random source, injectable so tests can pin the profile.
        """
        self.base_load = base_load
        self.current_consumption = base_load
        self._rng = rng or random.Random()
        self._lock = threading.Lock()

        # Event simulation
        self._cooking_until: float = 0
        self._cooking_power: int = 0
        self._appliance_until: float = 0
        self._appliance_power: int = 0

        # Time-based patterns
        self._last_event_check: float = 0
        self._daily_scale: float = self._rng.uniform(*_DAILY_SCALE_RANGE)
        self._daily_scale_day: int = datetime.now().toordinal()

        # Fridge compressor cycle
        self._fridge_power: int = self._rng.randint(*_FRIDGE_POWER_RANGE)
        self._fridge_running: bool = False
        self._fridge_switch_at: float = 0.0

        # Second-by-second fluctuation state
        self._fluctuation_base: int = 0
        self._fluctuation_target: int = 0
        self._last_fluctuation_update: float = 0

    def get_consumption(self) -> int:
        """Get current household power consumption in watts (positive = consuming from grid)."""
        with self._lock:
            now = time.time()

            # Check for random events on the event cadence
            if now - self._last_event_check > _EVENT_CHECK_SECONDS:
                self._last_event_check = now
                self._maybe_trigger_event(now)

            consumption = self.base_load
            consumption += self._diurnal_load(datetime.now())
            consumption += self._fridge_load(now)

            if now < self._cooking_until:
                consumption += self._cooking_power
            if now < self._appliance_until:
                consumption += self._appliance_power

            consumption += self._get_micro_fluctuation(now)

            # A dwelling on the grid never draws nothing; the always-on floor
            # is the smallest honest reading.
            self.current_consumption = max(30, int(consumption))
            return self.current_consumption

    def _diurnal_load(self, moment: datetime) -> float:
        """Return the smooth time-of-day load in watts.

        Interpolates between the hourly set points instead of resampling a
        range on every call. Redrawing a random value each second turned the
        baseline into several hundred watts of white noise, which no dwelling
        produces and which buried every real signal in the trace.
        """
        ordinal = moment.toordinal()
        if ordinal != self._daily_scale_day:
            self._daily_scale_day = ordinal
            self._daily_scale = self._rng.uniform(*_DAILY_SCALE_RANGE)

        hour = moment.hour
        fraction = (moment.minute * 60 + moment.second) / 3600
        current = _DIURNAL_WATTS[hour]
        following = _DIURNAL_WATTS[(hour + 1) % 24]
        return (current + (following - current) * fraction) * self._daily_scale

    def _fridge_load(self, now: float) -> int:
        """Return the fridge compressor draw, toggling on its duty cycle."""
        if now >= self._fridge_switch_at:
            self._fridge_running = not self._fridge_running
            if self._fridge_running:
                self._fridge_power = self._rng.randint(*_FRIDGE_POWER_RANGE)
                self._fridge_switch_at = now + self._rng.randint(*_FRIDGE_ON_SECONDS)
            else:
                self._fridge_switch_at = now + self._rng.randint(*_FRIDGE_OFF_SECONDS)
        return self._fridge_power if self._fridge_running else 0

    def _get_micro_fluctuation(self, now: float) -> int:
        """Get micro-fluctuations that change every second."""
        # Update fluctuation target every 1-3 seconds
        if now - self._last_fluctuation_update > self._rng.uniform(0.5, 2.0):
            self._last_fluctuation_update = now
            self._fluctuation_base = self._fluctuation_target

            # Random walk with occasional small switching loads
            if self._rng.random() < 0.08:
                spike = self._rng.choice([-1, 1]) * self._rng.randint(30, 120)
                self._fluctuation_target = max(
                    -_NOISE_WATTS * 3, min(200, self._fluctuation_base + spike)
                )
            else:
                drift = self._rng.randint(-_NOISE_WATTS, _NOISE_WATTS)
                self._fluctuation_target = max(
                    -_NOISE_WATTS * 3, min(120, self._fluctuation_base + drift)
                )

        # Smooth interpolation between base and target
        elapsed = now - self._last_fluctuation_update
        progress = min(1.0, elapsed / 1.0)
        span = self._fluctuation_target - self._fluctuation_base
        current = self._fluctuation_base + span * progress

        return int(current)

    def _maybe_trigger_event(self, now: float) -> None:
        """Randomly trigger household events."""
        hour = datetime.now().hour

        # Cooking events
        if now >= self._cooking_until:
            cooking_chance = (
                _COOKING_CHANCE_PEAK if hour in _COOKING_PEAK_HOURS else _COOKING_CHANCE_OFFPEAK
            )

            if self._rng.random() < cooking_chance:
                self._cooking_power = self._rng.randint(1200, 2600)
                self._cooking_until = now + self._rng.randint(5, 30) * _SECONDS_PER_MINUTE
                print(
                    f"[HOUSE] 🍳 Cooking started: {self._cooking_power}W "
                    f"for {int((self._cooking_until - now) / 60)} min"
                )

        # Appliance events
        if now >= self._appliance_until and self._rng.random() < _APPLIANCE_CHANCE:
            appliances = [
                ("Washing machine", 400, 800, 30, 60),
                ("Dryer", 1400, 2400, 45, 90),
                ("Dishwasher", 1100, 1800, 60, 120),
                ("Vacuum cleaner", 700, 1300, 10, 30),
                ("Iron", 900, 1800, 10, 20),
                ("Kettle", 1800, 2400, 2, 5),
                ("Microwave", 800, 1200, 2, 10),
            ]
            name, min_power, max_power, min_mins, max_mins = self._rng.choice(appliances)
            self._appliance_power = self._rng.randint(min_power, max_power)
            self._appliance_until = (
                now + self._rng.randint(min_mins, max_mins) * _SECONDS_PER_MINUTE
            )
            print(
                f"[HOUSE] 🔌 {name} started: {self._appliance_power}W "
                f"for {int((self._appliance_until - now) / 60)} min"
            )

    def force_cooking_event(self, power: int = 2500, duration_mins: int = 15) -> None:
        """Force a cooking event for testing."""
        with self._lock:
            self._cooking_power = power
            self._cooking_until = time.time() + duration_mins * _SECONDS_PER_MINUTE
            print(f"[HOUSE] 🍳 Forced cooking: {power}W for {duration_mins} min")
