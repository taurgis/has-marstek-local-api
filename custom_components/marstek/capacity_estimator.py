"""Estimate usable battery capacity from polled state of charge and power.

The Open API does not report state of health, cycle count, or cell voltage.
``Bat.GetStatus`` remaining and rated watt-hours track the BMS's own bookkeeping
and are not used here: that call is off unless the user enables a diagnostic
entity, and on some firmwares it is suspected of resetting the device.

Each fast poll already carries ``battery_soc`` and ``battery_power``. A window
is a stretch of one direction (charge or discharge), one operating mode, and
no long gap. When state of charge has moved at least ``MIN_SOC_DELTA`` points
the window's DC energy, estimated from reported watts and a fixed one-way
efficiency, is divided by the state-of-charge fraction. The published value is
the median of the accepted windows. Until that median exists the sensors stay
unknown.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

# Reported battery power is the AC-side quantity the integration already
# publishes (device ``bat_power`` when present, otherwise
# ``pv_power - ongrid_power - offgrid_power``). Nameplate ``bat_cap`` is DC
# watt-hours. These one-way factors convert the AC integral into an estimate
# of stored energy so a charge window and a discharge window agree.
# They match the mock pack model (tools/mock_device/const.py). They are not a
# measurement from Marstek hardware; a different real efficiency shifts the
# absolute watt-hours and shows up as spread between charge and discharge.
CHARGE_EFFICIENCY = 0.93
DISCHARGE_EFFICIENCY = 0.93

MIN_SOC_DELTA = 20.0
MIN_SAMPLES = 4
MIN_POWER_W = 150.0
MAX_ABS_POWER_W = 30_000.0
MAX_GAP_SECONDS = 180.0
MAX_WINDOW_SAMPLES = 2_000
MIN_WINDOWS = 3
MAX_WINDOWS = 21
ABS_MIN_CAPACITY_WH = 300.0
ABS_MAX_CAPACITY_WH = 30_000.0
RATED_MIN_FRACTION = 0.15
RATED_MAX_FRACTION = 1.35

CAPACITY_DATA_KEY = "battery_usable_capacity"
SOH_DATA_KEY = "battery_state_of_health"
CAPACITY_ATTR_KEY = "battery_usable_capacity_attributes"

_METHOD = "soc_energy_window"


class _RejectWindow:
    """Marker: the open window is not a usable capacity observation."""


_REJECT = _RejectWindow()


def _finite(value: object) -> float | None:
    """Return a finite real number, rejecting booleans and non-numeric values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):  # NaN
        return None
    return number


def _plausible(capacity_wh: float, rated_wh: float | None) -> bool:
    """Return whether a window capacity is inside the physical band."""
    if not ABS_MIN_CAPACITY_WH <= capacity_wh <= ABS_MAX_CAPACITY_WH:
        return False
    if rated_wh is None:
        return True
    low = RATED_MIN_FRACTION * rated_wh
    high = RATED_MAX_FRACTION * rated_wh
    return low <= capacity_wh <= high


@dataclass(frozen=True, slots=True)
class _Sample:
    """One poll used by the open window."""

    timestamp: float
    soc: float
    power_w: float
    mode: str | None


@dataclass(frozen=True, slots=True)
class _Window:
    """One accepted capacity observation."""

    capacity_wh: float
    direction: str
    soc_delta: float
    closed_at: float

    def to_dict(self) -> dict[str, float | str]:
        """Return the JSON form stored across restarts."""
        return {
            "capacity_wh": self.capacity_wh,
            "direction": self.direction,
            "soc_delta": self.soc_delta,
            "closed_at": self.closed_at,
        }


def _window_from_dict(raw: object) -> _Window | None:
    """Parse one persisted window, or return None when it is not usable."""
    if not isinstance(raw, dict):
        return None
    capacity = _finite(raw.get("capacity_wh"))
    soc_delta = _finite(raw.get("soc_delta"))
    closed_at = _finite(raw.get("closed_at"))
    direction = raw.get("direction")
    if capacity is None or soc_delta is None or closed_at is None:
        return None
    if direction not in ("charge", "discharge"):
        return None
    if not _plausible(capacity, None):
        return None
    if abs(soc_delta) < MIN_SOC_DELTA:
        return None
    return _Window(
        capacity_wh=capacity,
        direction=direction,
        soc_delta=soc_delta,
        closed_at=closed_at,
    )


def _mode_text(mode: str | None) -> str | None:
    """Normalize a mode name. Empty and non-strings carry no mode information."""
    if not isinstance(mode, str):
        return None
    text = mode.strip().casefold()
    return text or None


def _direction(power_w: float) -> int:
    """Return +1 discharge, -1 charge, or 0 when power is idle or absurd."""
    if abs(power_w) < MIN_POWER_W or abs(power_w) > MAX_ABS_POWER_W:
        return 0
    return 1 if power_w > 0 else -1


def _segment_power(earlier: _Sample, later: _Sample) -> float:
    """Return the trapezoidal average watts between two polls."""
    return (earlier.power_w + later.power_w) / 2.0


def _dc_energy_wh(samples: list[_Sample]) -> float:
    """Integrate reported watts and convert the AC total to DC watt-hours.

    Discharge puts more DC energy into the AC port than the port delivers, so
    the AC integral is divided by the discharge efficiency. Charge stores less
    than the port draws, so the AC integral (negative) is multiplied by the
    charge efficiency.
    """
    energy = 0.0
    for earlier, later in pairwise(samples):
        hours = (later.timestamp - earlier.timestamp) / 3600.0
        ac_wh = _segment_power(earlier, later) * hours
        if ac_wh >= 0.0:
            energy += ac_wh / DISCHARGE_EFFICIENCY
        else:
            energy += ac_wh * CHARGE_EFFICIENCY
    return energy


class UsableCapacityEstimator:
    """Sliding-median usable-capacity estimator.

    The open window is not persisted. A restart continues from the accepted
    windows and starts a new window on the next polls.
    """

    def __init__(self) -> None:
        """Start with no evidence."""
        self._windows: list[_Window] = []
        self._open: list[_Sample] = []
        self._open_direction = 0
        self._previous: _Sample | None = None
        self._rated_wh: float | None = None
        self._dirty = False

    @classmethod
    def from_dict(cls, raw: object) -> UsableCapacityEstimator:
        """Restore accepted windows. Anything unreadable is ignored."""
        estimator = cls()
        if not isinstance(raw, dict):
            return estimator
        windows = raw.get("windows")
        if not isinstance(windows, list):
            return estimator
        restored = [window for item in windows if (window := _window_from_dict(item)) is not None]
        estimator._windows = restored[-MAX_WINDOWS:]
        return estimator

    def to_dict(self) -> dict[str, Any]:
        """Return the persistent state."""
        return {"windows": [window.to_dict() for window in self._windows]}

    def consume_dirty(self) -> bool:
        """Return whether accepted windows changed since the last call."""
        dirty = self._dirty
        self._dirty = False
        return dirty

    @property
    def windows_accepted(self) -> int:
        """Return how many windows are in the median."""
        return len(self._windows)

    @property
    def estimate_wh(self) -> int | None:
        """Return the median capacity in Wh, or None until there is enough evidence."""
        if len(self._windows) < MIN_WINDOWS:
            return None
        median = statistics.median(window.capacity_wh for window in self._windows)
        return round(median)

    @property
    def state_of_health(self) -> float | None:
        """Return estimate / nameplate as a percentage, or None without either."""
        estimate = self.estimate_wh
        rated = self._rated_wh
        if estimate is None or rated is None or rated <= 0:
            return None
        return round(estimate / rated * 100.0, 1)

    def observe(
        self,
        *,
        timestamp: float,
        soc: float | None,
        power_w: float | None,
        mode: str | None,
        rated_wh: float | None,
    ) -> None:
        """Ingest one poll. Invalid samples abort the open window."""
        self._remember_rated(rated_wh)
        sample = self._make_sample(timestamp, soc, power_w, mode)
        if sample is None:
            # Drop the previous poll too, so the next good sample does not
            # integrate across the hole.
            self._abort()
            self._previous = None
            return
        previous = self._previous
        self._previous = sample
        if previous is None:
            return
        elapsed = sample.timestamp - previous.timestamp
        if elapsed <= 0.0:
            return
        if elapsed > MAX_GAP_SECONDS:
            self._abort()
            return
        if self._conflicts(previous, sample):
            self._close_open()
            self._start(previous, sample)
            return
        self._extend(previous, sample)

    def attributes(self) -> dict[str, Any]:
        """Return extra state attributes for the capacity and health sensors."""
        count = len(self._windows)
        attrs: dict[str, Any] = {
            "method": _METHOD,
            "windows_accepted": count,
            "confidence": self._confidence(),
            "min_soc_delta": MIN_SOC_DELTA,
            "efficiency_one_way": CHARGE_EFFICIENCY,
        }
        if self._rated_wh is not None:
            attrs["rated_capacity_wh"] = round(self._rated_wh)
        health = self.state_of_health
        if health is not None:
            attrs["state_of_health"] = health
        if self._windows:
            latest = self._windows[-1]
            attrs["last_window_direction"] = latest.direction
            attrs["last_window_capacity_wh"] = round(latest.capacity_wh)
            attrs["last_window_at"] = datetime.fromtimestamp(latest.closed_at, UTC).isoformat()
        return attrs

    def _remember_rated(self, rated_wh: float | None) -> None:
        """Keep the latest positive nameplate. A missing poll does not clear it."""
        rated = _finite(rated_wh)
        if rated is not None and rated > 0.0:
            self._rated_wh = rated

    def _make_sample(
        self,
        timestamp: float,
        soc: float | None,
        power_w: float | None,
        mode: str | None,
    ) -> _Sample | None:
        """Return a sample, or None when this poll cannot be integrated."""
        moment = _finite(timestamp)
        level = _finite(soc)
        power = _finite(power_w)
        if moment is None or level is None or power is None:
            return None
        if not 0.0 <= level <= 100.0 or abs(power) > MAX_ABS_POWER_W:
            return None
        return _Sample(
            timestamp=moment,
            soc=level,
            power_w=power,
            mode=_mode_text(mode),
        )

    def _conflicts(self, previous: _Sample, sample: _Sample) -> bool:
        """Return whether this segment cannot extend the open window."""
        if not self._open:
            return False
        if _mode_changed(self._open[0].mode, sample.mode):
            return True
        direction = _direction(_segment_power(previous, sample))
        if self._open_direction == 0:
            return direction == 0
        return direction != self._open_direction

    def _start(self, previous: _Sample, sample: _Sample) -> None:
        """Open a window on a segment that is clearly charging or discharging."""
        direction = _direction(_segment_power(previous, sample))
        if direction == 0:
            return
        self._open = [previous, sample]
        self._open_direction = direction
        self._maybe_accept()

    def _extend(self, previous: _Sample, sample: _Sample) -> None:
        """Append a compatible segment, opening a window when none is active."""
        if not self._open:
            self._start(previous, sample)
            return
        self._open.append(sample)
        if self._open_direction == 0:
            self._open_direction = _direction(_segment_power(previous, sample))
        if len(self._open) > MAX_WINDOW_SAMPLES:
            self._abort()
            return
        self._maybe_accept()

    def _maybe_accept(self) -> None:
        """Accept the open window once state of charge has moved far enough."""
        if len(self._open) < MIN_SAMPLES or self._open_direction == 0:
            return
        window = self._measure(self._open, self._open_direction)
        if window is None:
            return
        if not isinstance(window, _Window):
            self._abort()
            return
        self._windows.append(window)
        if len(self._windows) > MAX_WINDOWS:
            self._windows = self._windows[-MAX_WINDOWS:]
        self._dirty = True
        anchor = self._open[-1]
        self._open = [anchor]
        self._open_direction = 0

    def _close_open(self) -> None:
        """Drop a window that ended before it could be accepted.

        A window is accepted as soon as state of charge has moved far enough,
        so whatever is still open is a short tail and must not be published.
        """
        self._abort()

    def _abort(self) -> None:
        """Drop the open window. Accepted windows stay."""
        self._open = []
        self._open_direction = 0

    def _measure(self, samples: list[_Sample], direction: int) -> _Window | _RejectWindow | None:
        """Return a window, None to keep collecting, or reject it."""
        soc_delta = samples[-1].soc - samples[0].soc
        moved = -soc_delta if direction > 0 else soc_delta
        if moved < MIN_SOC_DELTA:
            return None
        energy = _dc_energy_wh(samples)
        # Discharge must remove state of charge; charge must add it.
        if direction > 0 and (soc_delta >= 0.0 or energy <= 0.0):
            return _REJECT
        if direction < 0 and (soc_delta <= 0.0 or energy >= 0.0):
            return _REJECT
        capacity = abs(energy) / (abs(soc_delta) / 100.0)
        if not _plausible(capacity, self._rated_wh):
            return _REJECT
        return _Window(
            capacity_wh=capacity,
            direction="discharge" if direction > 0 else "charge",
            soc_delta=abs(soc_delta),
            closed_at=samples[-1].timestamp,
        )

    def _confidence(self) -> str:
        """Return a coarse label. It is not a calibrated error bar."""
        count = len(self._windows)
        if count < MIN_WINDOWS:
            return "collecting"
        if count < 6:
            return "low"
        if count < 12:
            return "medium"
        return "high"


def _mode_changed(start: str | None, current: str | None) -> bool:
    """Return whether both polls named a mode and the names differ.

    A missing mode is absence of information, not a mode change. Firmware
    sometimes omits ``ES.GetMode`` for one cycle while power and state of
    charge stay valid.
    """
    if start is None or current is None:
        return False
    return start != current


def apply_capacity_estimate(
    estimator: UsableCapacityEstimator,
    status: dict[str, Any],
    timestamp: float,
) -> None:
    """Update ``estimator`` from one coordinator payload and write the results.

    ``battery_power`` uses the integration's sign (positive discharges).
    ``bat_cap`` is the nameplate watt-hours when the firmware profile says so.
    Venus E mini leaves it empty; the watt-hour estimate still runs, and the
    state-of-health percentage stays unknown without a nameplate.
    """
    soc = _finite(status.get("battery_soc"))
    power = _finite(status.get("battery_power"))
    rated = _finite(status.get("bat_cap"))
    mode = status.get("device_mode")
    estimator.observe(
        timestamp=timestamp,
        soc=soc,
        power_w=power,
        mode=mode if isinstance(mode, str) else None,
        rated_wh=rated,
    )
    status[CAPACITY_DATA_KEY] = estimator.estimate_wh
    status[SOH_DATA_KEY] = estimator.state_of_health
    status[CAPACITY_ATTR_KEY] = estimator.attributes()
