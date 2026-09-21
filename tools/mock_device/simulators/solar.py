"""Solar irradiance simulator shared by rooftop PV and device MPPT channels.

The mock needs solar for two unrelated reasons:

* A home that owns a Marstek battery usually owns panels too. Without them the
  P1 meter never reads negative, Auto mode never sees a surplus, and the mock
  battery drains to its reserve on day one and stays there. That is not what a
  Home Assistant dashboard looks like next to a real installation.
* Venus A and Venus D have their own DC inputs. A fixed ``--pv-channels``
  value reports 320 W at midnight, which no panel does.

Both use the same clear-sky curve so a mock home's rooftop array and a mock
Venus D's strings rise and fall together.
"""

from __future__ import annotations

import math
import random
import time
from datetime import datetime

# Northwest Europe, where most Venus units are installed. Latitude drives day
# length and noon elevation, so it is what makes a December curve look like
# December rather than a scaled-down June.
DEFAULT_LATITUDE = 52.0

# A fixed-tilt roof at this latitude peaks near 80% of its nameplate DC rating
# on a clear summer noon: module temperature, tilt mismatch and inverter losses
# take the rest.
_CLEAR_SKY_PEAK_FRACTION = 0.80
# Air mass losses grow faster than elevation falls, so the raw sine is bent
# down slightly at low sun.
_ELEVATION_EXPONENT = 1.15

# Cloud cover is a slow random walk, resampled this often.
_CLOUD_UPDATE_SECONDS = 20.0
# Chance per update of switching between a clear spell and an overcast one.
_CLOUD_SPELL_SWITCH_CHANCE = 0.04
# How far the cloud factor may move in one update. Real cloud edges are fast,
# but not instantaneous across a whole array.
_CLOUD_SLEW_PER_UPDATE = 0.18

_SECONDS_PER_HOUR = 3600.0


class SolarSimulator:
    """Produce a 0..1 production factor following the sun and the weather."""

    def __init__(
        self,
        peak_power_w: int = 0,
        latitude: float = DEFAULT_LATITUDE,
        rng: random.Random | None = None,
    ) -> None:
        """Initialize the simulator.

        Args:
            peak_power_w: Nameplate DC rating of the array in watts. ``0``
                disables production entirely.
            latitude: Installation latitude in degrees north.
            rng: Random source, injectable so tests can pin the weather.
        """
        self.peak_power_w = max(0, int(peak_power_w))
        self.latitude = latitude
        self._rng = rng or random.Random()
        self._cloud_factor = 1.0
        self._cloud_target = 1.0
        self._overcast = False
        self._last_cloud_update = 0.0

    def clear_sky_factor(self, when: datetime | None = None) -> float:
        """Return the cloudless production factor (0..1) for a moment in time.

        Uses the standard solar-declination and hour-angle geometry, treating
        the local clock as solar time. That is off by up to ~30 minutes against
        a real site, which does not matter for a mock and keeps the curve
        centred on local noon where a reader expects it.
        """
        moment = when or datetime.now()
        day_of_year = moment.timetuple().tm_yday
        declination = math.radians(23.45) * math.sin(2 * math.pi * (284 + day_of_year) / 365)
        solar_hours = moment.hour + moment.minute / 60 + moment.second / 3600
        hour_angle = math.radians(15 * (solar_hours - 12))

        latitude_rad = math.radians(self.latitude)
        sin_elevation = math.sin(latitude_rad) * math.sin(declination) + math.cos(
            latitude_rad
        ) * math.cos(declination) * math.cos(hour_angle)
        if sin_elevation <= 0:
            return 0.0
        return (sin_elevation**_ELEVATION_EXPONENT) * _CLEAR_SKY_PEAK_FRACTION

    def cloud_factor(self, now: float | None = None) -> float:
        """Return the current cloud transmission factor (0..1)."""
        moment = time.time() if now is None else now
        if moment - self._last_cloud_update < _CLOUD_UPDATE_SECONDS:
            return self._cloud_factor

        self._last_cloud_update = moment
        if self._rng.random() < _CLOUD_SPELL_SWITCH_CHANCE:
            self._overcast = not self._overcast
        if self._overcast:
            self._cloud_target = self._rng.uniform(0.12, 0.40)
        else:
            self._cloud_target = self._rng.uniform(0.80, 1.0)

        delta = self._cloud_target - self._cloud_factor
        step = max(-_CLOUD_SLEW_PER_UPDATE, min(_CLOUD_SLEW_PER_UPDATE, delta))
        self._cloud_factor = max(0.05, min(1.0, self._cloud_factor + step))
        return self._cloud_factor

    def get_factor(self, when: datetime | None = None, now: float | None = None) -> float:
        """Return the combined sun-and-weather production factor (0..1)."""
        clear_sky = self.clear_sky_factor(when)
        if clear_sky <= 0:
            return 0.0
        return clear_sky * self.cloud_factor(now)

    def get_power(self, when: datetime | None = None, now: float | None = None) -> int:
        """Return current production in watts for the configured array."""
        if self.peak_power_w <= 0:
            return 0
        return int(self.peak_power_w * self.get_factor(when, now))


class PVChannelSimulator:
    """Drive Venus A / Venus D MPPT channels from the shared solar curve.

    ``--pv-channels`` values are read as the channel's peak (STC) figures. The
    simulator scales power by the solar factor and keeps voltage and current
    physically consistent: string voltage sags only mildly with irradiance
    while current tracks it almost linearly, so ``P = V * I`` still holds.
    """

    # Open-circuit-ish behaviour: a string at 10% irradiance still sits near
    # 85% of its peak-power voltage.
    _MIN_VOLTAGE_FRACTION = 0.85
    # Below this factor the MPPT reports standby rather than a trickle.
    _STANDBY_FACTOR = 0.01

    def __init__(self, channels: list[dict[str, float]], solar: SolarSimulator) -> None:
        self._channels = channels
        self._solar = solar
        self.total_pv_energy = 0.0

    @property
    def configured(self) -> bool:
        """Return whether any channel was configured."""
        return bool(self._channels)

    def snapshot(self, when: datetime | None = None, now: float | None = None) -> list[dict]:
        """Return the current per-channel readings."""
        if not self._channels:
            return []
        factor = self._solar.get_factor(when, now)
        readings: list[dict] = []
        for channel in self._channels:
            peak_power = float(channel.get("pv_power", 0) or 0)
            peak_voltage = float(channel.get("pv_voltage", 0) or 0)
            power = peak_power * factor
            if factor <= self._STANDBY_FACTOR or power < 1:
                readings.append(
                    {
                        "channel": int(channel.get("channel", 0)),
                        "pv_power": 0,
                        "pv_voltage": 0,
                        "pv_current": 0,
                    }
                )
                continue
            voltage = peak_voltage * (
                self._MIN_VOLTAGE_FRACTION + (1 - self._MIN_VOLTAGE_FRACTION) * factor
            )
            current = power / voltage if voltage > 0 else 0.0
            readings.append(
                {
                    "channel": int(channel.get("channel", 0)),
                    "pv_power": round(power),
                    "pv_voltage": round(voltage, 1),
                    "pv_current": round(current, 2),
                }
            )
        return readings

    def total_power(self, readings: list[dict] | None = None) -> int:
        """Return the summed DC power across channels."""
        snapshot = self.snapshot() if readings is None else readings
        return sum(int(channel.get("pv_power", 0)) for channel in snapshot)

    def accumulate(self, power_w: int, elapsed_seconds: float) -> None:
        """Add produced energy for an elapsed interval."""
        self.total_pv_energy += power_w * (elapsed_seconds / _SECONDS_PER_HOUR)
