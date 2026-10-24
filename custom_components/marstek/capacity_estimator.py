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
state-of-charge fraction. That quotient is watt-hours per 100 points of
reported state of charge.

The published value is one direction's median once that direction has three
windows. Charge and discharge are not pooled. Once both have three, the
published value is the geometric mean of the two medians, which cancels a
one-way efficiency that is the same in both directions and leaves the square
root of the ratio of the two real efficiencies. Each direction keeps its own
newest windows. Until a median exists the sensors stay unknown.

Reported battery power is an AC quantity. Where it was derived from
``pv_power - ongrid_power - offgrid_power``, PV watts stay on the DC bus and
only the AC port is run through the one-way efficiency. A device ``bat_power``
keeps the previous treatment of the whole reading.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

from .helpers.capacity_history import (
    card_history,
    empty_lifetime,
    fold_weekly_history,
    lifetime_totals,
    restore_history,
)

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
# Lists only the Lovelace cards read. They stay off the state of health sensor
# and out of the recorder.
CARD_ATTRIBUTES = frozenset({"windows", "history", "active_window"})
# Set by ``parse_es_status_response`` when battery power came from the AC
# balance rather than a device ``bat_power``. ``merge_device_status`` copies it.
BATTERY_POWER_DERIVED_KEY = "battery_power_derived"
# Set by ``merge_device_status``. False means ES.GetStatus did not answer
# and battery power may be the previous poll's watts.
ES_STATUS_FRESH_KEY = "es_status_fresh"
# One whole-percent step is quantization. A larger step that the watts of
# that poll cannot explain, plus one point of slack, is a BMS jump.
_SOC_JUMP_FLOOR = 2.0
_SOC_JUMP_SLACK = 1.0


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
    pv_w: float = 0.0
    power_derived: bool = False


@dataclass(frozen=True, slots=True)
class _Window:
    """One accepted capacity observation.

    Windows saved before the detail fields existed load without them. A
    missing field stays unknown; it is not invented on the way back out.
    """

    capacity_wh: float
    direction: str
    soc_delta: float
    closed_at: float
    start_soc: float | None = None
    end_soc: float | None = None
    average_power_w: float | None = None
    duration_s: float | None = None
    mode: str | None = None
    pv_share: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form stored across restarts."""
        payload: dict[str, Any] = {
            "capacity_wh": self.capacity_wh,
            "direction": self.direction,
            "soc_delta": self.soc_delta,
            "closed_at": self.closed_at,
        }
        if self.start_soc is not None:
            payload["start_soc"] = self.start_soc
        if self.end_soc is not None:
            payload["end_soc"] = self.end_soc
        if self.average_power_w is not None:
            payload["average_power_w"] = self.average_power_w
        if self.duration_s is not None:
            payload["duration_s"] = self.duration_s
        if self.mode is not None:
            payload["mode"] = self.mode
        if self.pv_share is not None:
            payload["pv_share"] = self.pv_share
        return payload


def _optional_soc(raw: dict[Any, Any], key: str) -> float | None:
    """Return a stored state of charge, or None when the field is absent or junk."""
    if key not in raw:
        return None
    value = _finite(raw.get(key))
    if value is None or not 0.0 <= value <= 100.0:
        return None
    return value


def _optional_power(raw: dict[Any, Any]) -> float | None:
    """Return a stored average power, or None when it is absent or absurd."""
    if "average_power_w" not in raw:
        return None
    value = _finite(raw.get("average_power_w"))
    if value is None or abs(value) > MAX_ABS_POWER_W:
        return None
    return value


def _optional_duration(raw: dict[Any, Any]) -> float | None:
    """Return a stored duration in seconds, or None when it is absent or not positive."""
    if "duration_s" not in raw:
        return None
    value = _finite(raw.get("duration_s"))
    if value is None or value <= 0.0:
        return None
    return value


def _optional_share(raw: dict[Any, Any]) -> float | None:
    """Return a stored PV share in 0-1, or None when it is absent or outside that."""
    if "pv_share" not in raw:
        return None
    value = _finite(raw.get("pv_share"))
    if value is None or not 0.0 <= value <= 1.0:
        return None
    return value


def _window_from_dict(raw: object) -> _Window | None:
    """Parse one persisted window, or return None when it is not usable.

    Detail fields added later are optional. A store written by an older
    version still contributes its capacity to the median.
    """
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
        start_soc=_optional_soc(raw, "start_soc"),
        end_soc=_optional_soc(raw, "end_soc"),
        average_power_w=_optional_power(raw),
        duration_s=_optional_duration(raw),
        mode=_mode_text(raw.get("mode")) if "mode" in raw else None,
        pv_share=_optional_share(raw),
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
    """Return the trapezoidal average reported watts between two polls."""
    return (earlier.power_w + later.power_w) / 2.0


def _segment_dc_power(earlier: _Sample, later: _Sample) -> float:
    """Return DC watts between two polls, positive when the pack discharges.

    Discharge puts more DC energy into the AC port than the port delivers, so
    that port's watts are divided by the discharge efficiency. Charge stores
    less than the port draws, so those watts are multiplied by the charge
    efficiency.

    A derived reading is ``battery_power = ongrid + offgrid - pv`` in the
    Home Assistant sign. PV is charging power on the battery side of the
    inverter (Open API ``pv_power``), so only ``battery_power + pv_power``
    is the AC port. A device ``bat_power`` is left on the whole-reading path.
    """
    battery = _segment_power(earlier, later)
    if not (earlier.power_derived and later.power_derived):
        if battery >= 0.0:
            return battery / DISCHARGE_EFFICIENCY
        return battery * CHARGE_EFFICIENCY
    pv = max((earlier.pv_w + later.pv_w) / 2.0, 0.0)
    ac = battery + pv
    if ac >= 0.0:
        return ac / DISCHARGE_EFFICIENCY - pv
    return ac * CHARGE_EFFICIENCY - pv


def _dc_energy_wh(samples: list[_Sample]) -> float:
    """Integrate DC watts over the window. Positive energy leaves the pack."""
    energy = 0.0
    for earlier, later in pairwise(samples):
        hours = (later.timestamp - earlier.timestamp) / 3600.0
        energy += _segment_dc_power(earlier, later) * hours
    return energy


def _pv_share(samples: list[_Sample]) -> float:
    """Return how much of the absolute battery-power integral PV wattage covers.

    0 means no PV, or a device ``bat_power`` that this correction does not
    split. 1 means PV watts were at least the battery watts across the window.
    """
    pv_wh = 0.0
    battery_wh = 0.0
    for earlier, later in pairwise(samples):
        hours = (later.timestamp - earlier.timestamp) / 3600.0
        battery_wh += abs(_segment_power(earlier, later)) * hours
        if earlier.power_derived and later.power_derived:
            pv = max((earlier.pv_w + later.pv_w) / 2.0, 0.0)
            pv_wh += pv * hours
    if battery_wh <= 0.0:
        return 0.0
    return min(1.0, pv_wh / battery_wh)


def _average_power_w(samples: list[_Sample]) -> float:
    """Return the trapezoidal mean of reported battery power, signed."""
    duration = samples[-1].timestamp - samples[0].timestamp
    if duration <= 0.0:
        return 0.0
    apparent_wh = 0.0
    for earlier, later in pairwise(samples):
        hours = (later.timestamp - earlier.timestamp) / 3600.0
        apparent_wh += _segment_power(earlier, later) * hours
    return apparent_wh / (duration / 3600.0)


def _soc_step_limit(earlier: _Sample, later: _Sample, rated_wh: float | None) -> float:
    """Return the largest state-of-charge step this poll's watts can justify.

    The budget is the DC watts of the larger endpoint, including PV on a
    derived reading, divided by the nameplate. One extra point covers the
    whole-percent step. Without a nameplate the smallest pack the estimator
    will accept is used, so only a jump that pack could not physically make
    is rejected. Two points is the floor: a single reported step is never a
    recalibration.
    """
    hours = max(later.timestamp - earlier.timestamp, 0.0) / 3600.0
    power = max(abs(earlier.power_w), abs(later.power_w))
    pv = 0.0
    if earlier.power_derived and later.power_derived:
        pv = max(earlier.pv_w, later.pv_w, 0.0)
    eta = min(CHARGE_EFFICIENCY, DISCHARGE_EFFICIENCY)
    explained = (power / eta + pv) * hours / _jump_capacity_wh(rated_wh) * 100.0
    return max(_SOC_JUMP_FLOOR, explained + _SOC_JUMP_SLACK)


def _jump_capacity_wh(rated_wh: float | None) -> float:
    """Return the watt-hours a state-of-charge jump is judged against."""
    if rated_wh is not None and rated_wh > 0.0:
        return rated_wh
    return ABS_MIN_CAPACITY_WH


def _soc_jumped(earlier: _Sample, later: _Sample, rated_wh: float | None) -> bool:
    """Return whether state of charge moved further than this poll can explain."""
    return abs(later.soc - earlier.soc) >= _soc_step_limit(earlier, later, rated_wh)


def _sample_pv(pv_w: float | None, power_derived: bool) -> float | None:
    """Return PV watts to store, or None when a derived poll's PV is unusable.

    Missing PV is zero. A device ``bat_power`` does not use PV, so a bad PV
    reading must not reject that poll. Negative PV is not generation.
    """
    if not power_derived:
        return 0.0
    if pv_w is None:
        return 0.0
    number = _finite(pv_w)
    if number is None or number > MAX_ABS_POWER_W:
        return None
    return max(0.0, number)


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
        self._history: list[dict[str, Any]] = []
        self._history_baseline: list[dict[str, Any]] = []
        self._lifetime = empty_lifetime()

    @classmethod
    def from_dict(cls, raw: object) -> UsableCapacityEstimator:
        """Restore accepted windows and weekly snapshots. Unreadable parts are ignored."""
        estimator = cls()
        if not isinstance(raw, dict):
            return estimator
        windows = raw.get("windows")
        if isinstance(windows, list):
            restored = [
                window for item in windows if (window := _window_from_dict(item)) is not None
            ]
            estimator._windows = restored
            estimator._trim_windows()
        estimator._history = restore_history(raw.get("history"))
        estimator._history_baseline = [dict(point) for point in estimator._history]
        return estimator

    def set_poll_interval(self, poll_interval: float) -> None:
        """Widen the gap limit when the fast poll is slower than 180 seconds.

        The default interval keeps the 180 second limit. A reload builds a new
        coordinator, so this is set once from the options in force.
        """
        self._gap_limit = gap_limit_seconds(poll_interval)

    def to_dict(self) -> dict[str, Any]:
        """Return windows and weekly snapshots for the Store file."""
        return {
            "windows": [window.to_dict() for window in self._windows],
            "history": [dict(point) for point in self._history],
        }

    def consume_dirty(self) -> bool:
        """Return whether accepted windows changed since the last call."""
        dirty = self._dirty
        self._dirty = False
        return dirty

    @property
    def windows_accepted(self) -> int:
        """Return how many windows are in the median."""
        return len(self._windows)

    def _capacities(self, direction: str) -> list[float]:
        """Return accepted watt-hours for one direction, oldest first."""
        return [window.capacity_wh for window in self._windows if window.direction == direction]

    def _published(self) -> tuple[float | None, str]:
        """Return the watt-hour estimate and which efficiency treatment produced it.

        A direction with three windows publishes its own median and still
        depends on the fixed one-way factor. The other direction is not folded
        in. Both directions use the geometric mean of the two medians, which
        does not depend on that factor when the two real efficiencies are equal.
        """
        discharge = self._capacities("discharge")
        charge = self._capacities("charge")
        if len(discharge) >= MIN_WINDOWS and len(charge) >= MIN_WINDOWS:
            value = math.sqrt(statistics.median(discharge) * statistics.median(charge))
            return value, "charge_discharge_geometric_mean"
        if len(discharge) >= MIN_WINDOWS:
            return statistics.median(discharge), "discharge_median"
        if len(charge) >= MIN_WINDOWS:
            return statistics.median(charge), "charge_median"
        return None, "assumed_one_way"

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
        pv_w: float | None = None,
        power_derived: bool = False,
    ) -> None:
        """Ingest one poll. Invalid samples abort the open window.

        ``power_derived`` is true when ``power_w`` came from the AC balance.
        ``pv_w`` is then the DC PV charging power that must not take the
        inverter efficiency. Both default to the device-``bat_power`` path.
        """
        try:
            self._remember_rated(rated_wh)
            sample = self._make_sample(timestamp, soc, power_w, mode, pv_w, power_derived)
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
            if elapsed > self._gap_limit or _soc_jumped(previous, sample, self._rated_wh):
                self._abort()
                return
            if self._conflicts(previous, sample):
                self._close_open()
                # A device bat_power and a derived balance are different measurements.
                # The segment between them is not part of either window.
                if previous.power_derived == sample.power_derived:
                    self._start(previous, sample)
                return
            self._extend(previous, sample)
        finally:
            self._record_history(timestamp)

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
        attrs.update(self._median_attributes())
        attrs["windows"] = [window.to_dict() for window in self._windows]
        attrs["active_window"] = self._active_window()
        contributing = self._contributing_capacities(correction)
        if value is not None and contributing:
            attrs["spread_wh"] = round(max(contributing) - min(contributing))
        if self._rated_wh is not None:
            attrs["rated_capacity_wh"] = round(self._rated_wh)
        health = self.state_of_health
        if health is not None:
            attrs["state_of_health"] = health
        if self._windows:
            latest = self._windows[-1]
            attrs["last_window_direction"] = latest.direction
            attrs["last_window_capacity_wh"] = round(latest.capacity_wh)
            attrs["last_window_at"] = _iso_timestamp(latest.closed_at)
        attrs["history"] = card_history(self._history)
        return attrs

    def _active_window(self) -> dict[str, Any] | None:
        """Return the in-progress charge or discharge, before it is accepted.

        A window is accepted at ``MIN_SOC_DELTA``. Until then the card draws
        how far state of charge has moved toward that threshold. Every field
        follows state of charge only, so the attribute stays the same between
        polls until the battery gains or loses a point.
        """
        if not self._open or self._open_direction == 0:
            return None
        start = self._open[0]
        current = self._open[-1]
        delta = abs(current.soc - start.soc)
        return {
            "direction": "discharge" if self._open_direction > 0 else "charge",
            "started_at": _iso_timestamp(start.timestamp),
            "start_soc": round(start.soc, 1),
            "current_soc": round(current.soc, 1),
            "soc_delta": round(delta, 1),
            "target_soc_delta": MIN_SOC_DELTA,
            "progress": round(min(1.0, delta / MIN_SOC_DELTA), 3),
        }

    def remember_lifetime(self, status: dict[str, Any]) -> None:
        """Keep the lifetime energy counters for the next weekly snapshot."""
        self._lifetime = lifetime_totals(status)

    def _record_history(self, timestamp: float) -> None:
        """Fold the current estimate into the ISO-week history."""
        moment = _finite(timestamp)
        if moment is None:
            return
        medians = self._median_attributes()
        rated = self._rated_wh
        exported = self._lifetime.get("total_grid_output_energy")
        cycles = None
        if rated is not None and rated > 0.0 and exported is not None:
            cycles = exported / rated
        changed = fold_weekly_history(
            self._history,
            self._history_baseline,
            timestamp=moment,
            measures={
                "usable_wh": self.estimate_wh,
                "discharge_median_wh": medians.get("discharge_median_wh"),
                "charge_median_wh": medians.get("charge_median_wh"),
                "implied_round_trip_efficiency": medians.get("implied_round_trip_efficiency"),
                "equivalent_cycles": cycles,
                **self._lifetime,
            },
        )
        if not changed:
            return
        self._dirty = True
        self._history_baseline = [dict(point) for point in self._history]

    def _remember_rated(self, rated_wh: float | None) -> None:
        """Keep the latest positive nameplate. A missing poll does not clear it."""
        rated = _finite(rated_wh)
        if rated is not None and rated > 0.0:
            self._rated_wh = rated

    def _median_attributes(self) -> dict[str, Any]:
        """Return direction medians and the round-trip efficiency they imply.

        With the same assumed one-way factor η on both paths, a discharge
        median scales by η_true_discharge / η and a charge median scales by
        η / η_true_charge. Their ratio therefore carries the true round trip:

        eta_discharge * eta_charge is about eta^2 * (discharge median / charge median).
        """
        discharge = self._capacities("discharge")
        charge = self._capacities("charge")
        attrs: dict[str, Any] = {}
        if discharge:
            attrs["discharge_median_wh"] = round(statistics.median(discharge))
        if charge:
            attrs["charge_median_wh"] = round(statistics.median(charge))
        if discharge and charge:
            ratio = statistics.median(discharge) / statistics.median(charge)
            efficiency = CHARGE_EFFICIENCY * DISCHARGE_EFFICIENCY * ratio
            attrs["implied_round_trip_efficiency"] = round(efficiency, 3)
        return attrs

    def _contributing_capacities(self, correction: str) -> list[float]:
        """Return the windows that actually entered the published number."""
        if correction == "charge_discharge_geometric_mean":
            return [window.capacity_wh for window in self._windows]
        if correction == "discharge_median":
            return self._capacities("discharge")
        if correction == "charge_median":
            return self._capacities("charge")
        return []

    def _make_sample(
        self,
        timestamp: float,
        soc: float | None,
        power_w: float | None,
        mode: str | None,
        pv_w: float | None,
        power_derived: bool,
    ) -> _Sample | None:
        """Return a sample, or None when this poll cannot be integrated."""
        moment = _finite(timestamp)
        level = _finite(soc)
        power = _finite(power_w)
        derived = power_derived is True
        pv = _sample_pv(pv_w, derived)
        if moment is None or level is None or power is None or pv is None:
            return None
        if not 0.0 <= level <= 100.0 or abs(power) > MAX_ABS_POWER_W:
            return None
        return _Sample(
            timestamp=moment,
            soc=level,
            power_w=power,
            mode=_mode_text(mode),
            pv_w=pv,
            power_derived=derived,
        )

    def _conflicts(self, previous: _Sample, sample: _Sample) -> bool:
        """Return whether this segment cannot extend the open window."""
        if not self._open:
            return False
        if self._open[0].power_derived != sample.power_derived:
            return True
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
            start_soc=samples[0].soc,
            end_soc=samples[-1].soc,
            average_power_w=_average_power_w(samples),
            duration_s=samples[-1].timestamp - samples[0].timestamp,
            mode=samples[0].mode,
            pv_share=_pv_share(samples),
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


def _iso_timestamp(moment: float) -> str:
    """Return a UTC ISO-8601 timestamp for a stored unix time."""
    return datetime.fromtimestamp(moment, UTC).isoformat()


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
    pv_raw = status.get("pv_power") if fresh else None
    pv = pv_raw if isinstance(pv_raw, (int, float)) and not isinstance(pv_raw, bool) else None
    # Before ``observe``, which folds this poll's counters into the weekly point.
    estimator.remember_lifetime(status)
    estimator.observe(
        timestamp=timestamp,
        soc=soc,
        power_w=power,
        mode=mode if isinstance(mode, str) else None,
        rated_wh=rated,
        pv_w=pv,
        power_derived=status.get(BATTERY_POWER_DERIVED_KEY) is True,
    )
    status[CAPACITY_DATA_KEY] = estimator.estimate_wh
    status[SOH_DATA_KEY] = estimator.state_of_health
    status[CAPACITY_ATTR_KEY] = estimator.attributes()
