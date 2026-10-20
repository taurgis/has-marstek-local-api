"""Estimate usable battery capacity from polled state of charge and power.

The Open API does not report state of health, cycle count, or cell voltage.
``Bat.GetStatus`` remaining and rated watt-hours track the BMS's own bookkeeping
and are not used here: that call is off unless the user enables a diagnostic
entity, and on some firmwares it is suspected of resetting the device.

Each fast poll already carries ``battery_soc`` and ``battery_power``. A window
is a stretch of one direction (charge or discharge), one operating mode, and
no long gap. It starts on the first poll after the whole-percent state of
charge changes, so both ends sit just past a step. When state of charge has
moved at least ``MIN_SOC_DELTA`` points the window's DC energy, estimated
from reported watts and a fixed one-way efficiency, is divided by the
state-of-charge fraction. The published value is
the median of the accepted windows. Once both charge and discharge have at
least three windows, the published value becomes the geometric mean of the
two medians, which cancels a one-way efficiency that is the same in both
directions. Each direction keeps its own newest windows, so a long run one
way cannot evict the other. Until a median exists the sensors stay unknown.
"""

from __future__ import annotations

import math
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
# ES.GetMode plus up to five more serial reads, at the options-flow maximum
# of 30 seconds between reads. Home Assistant starts the next wait after the
# poll returns, so the sample gap is the configured interval plus this.
_POLL_OCCUPANCY_SECONDS = 150.0
MAX_WINDOW_SAMPLES = 2_000
MIN_WINDOWS = 3
# Newest windows kept for each direction. A single shared cap let one long
# session evict the other direction and drop the geometric mean.
MAX_WINDOWS = 21
ABS_MIN_CAPACITY_WH = 300.0
ABS_MAX_CAPACITY_WH = 30_000.0
RATED_MIN_FRACTION = 0.15
RATED_MAX_FRACTION = 1.35

CAPACITY_DATA_KEY = "battery_usable_capacity"
SOH_DATA_KEY = "battery_state_of_health"
CAPACITY_ATTR_KEY = "battery_usable_capacity_attributes"
# Set by ``merge_device_status``. False means ES.GetStatus did not answer
# and battery power may be the previous poll's watts.
ES_STATUS_FRESH_KEY = "es_status_fresh"


def gap_limit_seconds(poll_interval: float | None) -> float:
    """Return the longest sample spacing that still belongs to one window.

    180 seconds already covers the default 30 second poll, including a few
    slow cycles. A longer configured interval would step past 180 seconds on
    every successful poll, so the limit grows by that interval plus the time
    one poll can spend on its own requests. A missed poll is another full
    interval and still ends the window.
    """
    interval = _finite(poll_interval)
    if interval is None or interval <= 0.0:
        return MAX_GAP_SECONDS
    return max(MAX_GAP_SECONDS, interval + _POLL_OCCUPANCY_SECONDS)


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
        # False until the open window starts on a state-of-charge step.
        self._aligned = False
        self._previous: _Sample | None = None
        self._rated_wh: float | None = None
        self._dirty = False
        self._gap_limit = MAX_GAP_SECONDS

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
        estimator._windows = restored
        estimator._trim_windows()
        return estimator

    def set_poll_interval(self, poll_interval: float) -> None:
        """Widen the gap limit when the fast poll is slower than 180 seconds.

        The default interval keeps the 180 second limit. A reload builds a new
        coordinator, so this is set once from the options in force.
        """
        self._gap_limit = gap_limit_seconds(poll_interval)

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

    def _published(self) -> tuple[float | None, str]:
        """Return the watt-hour estimate and which efficiency treatment produced it.

        One direction uses the median and the fixed one-way factor. Both
        directions use the geometric mean of the two medians, which does not
        depend on that factor when the two real efficiencies are equal.
        """
        if len(self._windows) < MIN_WINDOWS:
            return None, "assumed_one_way"
        discharge = [
            window.capacity_wh for window in self._windows if window.direction == "discharge"
        ]
        charge = [window.capacity_wh for window in self._windows if window.direction == "charge"]
        if len(discharge) >= MIN_WINDOWS and len(charge) >= MIN_WINDOWS:
            value = math.sqrt(statistics.median(discharge) * statistics.median(charge))
            return value, "charge_discharge_geometric_mean"
        pooled = statistics.median(window.capacity_wh for window in self._windows)
        return pooled, "assumed_one_way"

    @property
    def estimate_wh(self) -> int | None:
        """Return capacity in Wh, or None until there is enough evidence."""
        value, _correction = self._published()
        if value is None:
            return None
        return round(value)

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
        if elapsed > self._gap_limit:
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
        value, correction = self._published()
        attrs: dict[str, Any] = {
            "method": _METHOD,
            "windows_accepted": count,
            "confidence": self._confidence(),
            "min_soc_delta": MIN_SOC_DELTA,
            "efficiency_one_way": CHARGE_EFFICIENCY,
            "efficiency_correction": correction,
        }
        if value is not None and self._windows:
            capacities = [window.capacity_wh for window in self._windows]
            attrs["spread_wh"] = round(max(capacities) - min(capacities))
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
        """Open a window on a segment that is clearly charging or discharging.

        State of charge arrives in whole percents, so a window that starts on
        an arbitrary poll can be up to one point off. The window therefore
        starts on the first poll after the reported percentage changes. The
        window is accepted on the poll that completes the 20-point move, which
        is also the first poll after a change, so both ends sit the same
        distance past a step.
        """
        direction = _direction(_segment_power(previous, sample))
        if direction == 0:
            return
        self._open_direction = direction
        if sample.soc != previous.soc:
            self._open = [sample]
            self._aligned = True
            return
        self._open = [previous, sample]
        self._aligned = False

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
        if not self._aligned:
            if sample.soc != previous.soc:
                self._open = [sample]
                self._aligned = True
            return
        self._maybe_accept()

    def _maybe_accept(self) -> None:
        """Accept the open window once state of charge has moved far enough."""
        if not self._aligned or len(self._open) < MIN_SAMPLES or self._open_direction == 0:
            return
        window = self._measure(self._open, self._open_direction)
        if window is None:
            return
        if not isinstance(window, _Window):
            self._abort()
            return
        self._windows.append(window)
        self._trim_windows()
        self._dirty = True
        # The closing poll is the first after a step, so the next window
        # starts aligned on it.
        anchor = self._open[-1]
        self._open = [anchor]
        self._open_direction = 0
        self._aligned = True

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
        self._aligned = False

    def _trim_windows(self) -> None:
        """Keep the newest ``MAX_WINDOWS`` of each direction, in time order."""
        kept: list[_Window] = []
        remaining = {"discharge": MAX_WINDOWS, "charge": MAX_WINDOWS}
        for window in reversed(self._windows):
            left = remaining.get(window.direction, 0)
            if left <= 0:
                continue
            remaining[window.direction] = left - 1
            kept.append(window)
        kept.reverse()
        self._windows = kept

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
    # Missing means the caller already passed a fresh sample (unit traces).
    # An explicit False is a poll whose ES.GetStatus did not answer: the
    # watts may be carried forward while state of charge is new.
    fresh = status.get(ES_STATUS_FRESH_KEY, True) is not False
    soc = _finite(status.get("battery_soc")) if fresh else None
    power = _finite(status.get("battery_power")) if fresh else None
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
