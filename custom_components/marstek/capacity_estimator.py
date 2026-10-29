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

The nameplate must hold for a few polls before it replaces the stored one.
A change of more than a tenth is different hardware, such as an expansion
module, so the runs of the old pack are dropped and a new series starts.

Reported battery power is an AC quantity. Where it was derived from
``pv_power - ongrid_power - offgrid_power``, PV watts stay on the DC bus and
only the AC port is run through the one-way efficiency. A device ``bat_power``
keeps the previous treatment of the whole reading.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any

from .helpers.capacity_confidence import (
    GEOMETRIC_MEAN,
    assess,
    run_scatter_pct,
)
from .helpers.capacity_confidence import spread_wh as run_spread_wh
from .helpers.capacity_energy import (
    CHARGE_EFFICIENCY,
    DISCHARGE_EFFICIENCY,
    average_power_w,
    dc_energy_wh,
    pv_share,
    sample_pv,
    segment_power,
)
from .helpers.capacity_energy import Sample as _Sample
from .helpers.capacity_fields import MAX_ABS_POWER_W, finite
from .helpers.capacity_history import (
    card_history,
    empty_lifetime,
    fold_weekly_history,
    lifetime_totals,
    restore_history,
)
from .helpers.capacity_window import (
    ABS_MAX_CAPACITY_WH,
    ABS_MIN_CAPACITY_WH,
    MIN_SOC_DELTA,
    CapacityWindow,
    mode_text,
    window_from_dict,
)
from .helpers.soc_corrections import (
    SocCorrectionTracker,
    correction_attributes,
)

MIN_SAMPLES = 4
MIN_POWER_W = 150.0
# Same-sign segments below ``MIN_POWER_W`` are pauses, not a new direction.
# Two of them (about a minute at the default poll) stay inside the open
# window. A third ends it, so a long trickle cannot ride the fixed 0.93
# one-way factor into the median. The 150 W floor itself does not move:
# inverter efficiency collapses below it, and a window whose average is
# still under 150 W is rejected.
_MAX_LOW_POWER_SEGMENTS = 2
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
RATED_MIN_FRACTION = 0.15
RATED_MAX_FRACTION = 1.35
# A new nameplate counts once this many polls in a row report it, so one
# glitched ``bat_cap`` neither moves the health percentage nor resets runs.
RATED_CONFIRM_POLLS = 3
# A larger change is a different pack, such as an added expansion module.
RATED_CHANGE_FRACTION = 0.10

CAPACITY_DATA_KEY = "battery_usable_capacity"
SOH_DATA_KEY = "battery_state_of_health"
CAPACITY_ATTR_KEY = "battery_usable_capacity_attributes"
# Lists only the Lovelace cards read. They stay off the state of health sensor
# and out of the recorder.
CARD_ATTRIBUTES = frozenset({"windows", "history", "active_window", "soc_corrections"})
# Set by ``parse_es_status_response`` when battery power came from the AC
# balance rather than a device ``bat_power``. ``merge_device_status`` copies it.
BATTERY_POWER_DERIVED_KEY = "battery_power_derived"
# Set by ``merge_device_status``. False means ES.GetStatus did not answer
# and battery power may be the previous poll's watts.
ES_STATUS_FRESH_KEY = "es_status_fresh"
# True on the poll where the BMS was seen correcting its state of charge. The
# same percentage before and after that poll is no longer the same energy.
SOC_CORRECTED_KEY = "battery_soc_corrected"
# True while a step the watts cannot explain waits for confirmation. That
# poll's percentage may be a glitch and is not a level to anchor on.
SOC_UNCONFIRMED_KEY = "battery_soc_unconfirmed"
# One whole-percent step is quantization. A larger step that the watts of
# that poll cannot explain, plus one point of slack, is a BMS jump.
_SOC_JUMP_FLOOR = 2.0
_SOC_JUMP_SLACK = 1.0
# The endpoint watts bound a short segment. Over a longer one a load spike
# between the polls can move the percentage further than they explain, so
# such a step ends the window without being recorded as a correction.
_MAX_JUDGED_SEGMENT_SECONDS = 120.0
# A glitched reading can repeat on the next poll. A step is a correction only
# when the reading has not come back within this many polls after it.
_HOLD_POLLS = 2


def gap_limit_seconds(poll_interval: float | None) -> float:
    """Return the longest sample spacing that still belongs to one window.

    180 seconds already covers the default 30 second poll, including a few
    slow cycles. A longer configured interval would step past 180 seconds on
    every successful poll, so the limit grows by that interval plus the time
    one poll can spend on its own requests. A missed poll is another full
    interval and still ends the window.
    """
    interval = finite(poll_interval)
    if interval is None or interval <= 0.0:
        return MAX_GAP_SECONDS
    return max(MAX_GAP_SECONDS, interval + _POLL_OCCUPANCY_SECONDS)


_METHOD = "soc_energy_window"


class _RejectWindow:
    """Marker: the open window is not a usable capacity observation."""


_REJECT = _RejectWindow()


def _plausible(capacity_wh: float, rated_wh: float | None) -> bool:
    """Return whether a window capacity is inside the physical band."""
    if not ABS_MIN_CAPACITY_WH <= capacity_wh <= ABS_MAX_CAPACITY_WH:
        return False
    if rated_wh is None:
        return True
    low = RATED_MIN_FRACTION * rated_wh
    high = RATED_MAX_FRACTION * rated_wh
    return low <= capacity_wh <= high


def _direction(power_w: float) -> int:
    """Return +1 discharge, -1 charge, or 0 when power is idle or absurd."""
    if abs(power_w) < MIN_POWER_W or abs(power_w) > MAX_ABS_POWER_W:
        return 0
    return 1 if power_w > 0 else -1


def _soc_step_limit(earlier: _Sample, later: _Sample, rated_wh: float | None) -> float:
    """Return the largest state-of-charge step this poll's watts can justify.

    The budget is the DC watts of the larger endpoint, including PV on a
    derived reading, divided by the nameplate. One extra point covers the
    whole-percent step. Without a nameplate the smallest pack the estimator
    will accept is used, so only a jump that pack could not physically make
    is rejected. Two points is the floor: a step of exactly two reported
    points is still integer quantization, not a recalibration.
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
    """Return whether state of charge moved further than this poll can explain.

    A step equal to the limit is the largest one the watts, the one-point
    slack, and the two-point floor justify. Only a larger step is a BMS jump.
    ``>=`` treated that equal step as a jump, so a two-point integer tick
    aborted a window the floor was written to keep.
    """
    return abs(later.soc - earlier.soc) > _soc_step_limit(earlier, later, rated_wh)


def _returned(previous: _Sample, pending: _Sample, sample: _Sample, rated_wh: float | None) -> bool:
    """Return whether ``sample`` came back from a step to the level before it.

    Nearer the old level than the stepped one, within what this poll explains.
    A three-point spike followed by an ordinary one-point move lands two
    points from the spike, which the step floor alone would call confirmed.
    """
    back = abs(sample.soc - previous.soc)
    return back < abs(sample.soc - pending.soc) and not _soc_jumped(previous, sample, rated_wh)


def _power_sign(power_w: float) -> int:
    """Return +1, -1, or 0 from the sign of a watt reading."""
    if power_w > 0.0:
        return 1
    if power_w < 0.0:
        return -1
    return 0


def _low_power_pause(segment_w: float, later_w: float, open_direction: int) -> bool:
    """Return whether a segment is a same-sign dip under the power floor.

    The trapezoidal average decides the magnitude. The later sample's sign
    decides a reversal: opposite watts are not a pause, even below 150 W.
    Exact zero has no direction and counts as a pause.
    """
    if abs(segment_w) >= MIN_POWER_W:
        return False
    sign = _power_sign(later_w)
    return sign == 0 or sign == open_direction


class UsableCapacityEstimator:
    """Sliding-median usable-capacity estimator.

    The open window is not persisted. A restart continues from the accepted
    windows and starts a new window on the next polls. The last poll is kept,
    so the correction trace continues across a restart within the gap limit.
    """

    def __init__(self) -> None:
        """Start with no evidence."""
        self._windows: list[CapacityWindow] = []
        self._open: list[_Sample] = []
        self._open_direction = 0
        # Consecutive same-sign segments below ``MIN_POWER_W`` inside the
        # open window. Not persisted: the open window itself is not.
        self._low_power_segments = 0
        # False until the open window starts on a state-of-charge step.
        self._aligned = False
        self._previous: _Sample | None = None
        # Samples after an unexplained step, held until a later poll shows
        # whether the reading stayed there. ``_previous`` stays before them.
        self._held: list[_Sample] = []
        # Where the last folded segment started. A jump back to its level
        # ends a spike whose first leg fit under the two-point floor.
        self._prior: _Sample | None = None
        self._rated_wh: float | None = None
        self._rated_candidate: float | None = None
        self._rated_seen = 0
        self._dirty = False
        self._gap_limit = MAX_GAP_SECONDS
        self._history: list[dict[str, Any]] = []
        self._history_baseline: list[dict[str, Any]] = []
        self._lifetime = empty_lifetime()
        self._corrections = SocCorrectionTracker()
        # A correction recorded since ``consume_correction`` last ran.
        self._corrected = False

    @classmethod
    def from_dict(cls, raw: object) -> UsableCapacityEstimator:
        """Restore accepted windows and weekly snapshots. Unreadable parts are ignored."""
        estimator = cls()
        if not isinstance(raw, dict):
            return estimator
        windows = raw.get("windows")
        if isinstance(windows, list):
            restored = [
                window for item in windows if (window := window_from_dict(item)) is not None
            ]
            estimator._windows = restored
            estimator._trim_windows()
        estimator._history = restore_history(raw.get("history"))
        estimator._history_baseline = [dict(point) for point in estimator._history]
        estimator._corrections = SocCorrectionTracker.from_dict(raw.get("soc_corrections"))
        last = raw.get("last_sample")
        if isinstance(last, dict):
            estimator._previous = estimator._make_sample(
                last.get("timestamp"),
                last.get("soc"),
                last.get("power_w"),
                last.get("mode"),
                last.get("pv_w"),
                last.get("power_derived") is True,
            )
        rated = finite(raw.get("rated_wh"))
        if rated is not None and rated > 0.0:
            estimator._rated_wh = rated
        return estimator

    def set_poll_interval(self, poll_interval: float) -> None:
        """Widen the gap limit when the fast poll is slower than 180 seconds.

        The default interval keeps the 180 second limit. A reload builds a new
        coordinator, so this is set once from the options in force.
        """
        self._gap_limit = gap_limit_seconds(poll_interval)

    def to_dict(self) -> dict[str, Any]:
        """Return windows, weekly snapshots, the nameplate and the last poll.

        The nameplate is kept so a module added while Home Assistant was down
        still starts a new series. The last poll links the first one after a
        short restart to the stored correction trace.
        """
        payload: dict[str, Any] = {
            "windows": [window.to_dict() for window in self._windows],
            "history": [dict(point) for point in self._history],
            "soc_corrections": self._corrections.to_dict(),
        }
        if self._rated_wh is not None:
            payload["rated_wh"] = self._rated_wh
        if self._previous is not None:
            payload["last_sample"] = asdict(self._previous)
        return payload

    def consume_dirty(self) -> bool:
        """Return whether accepted windows changed since the last call."""
        dirty = self._dirty
        self._dirty = False
        return dirty

    def consume_correction(self) -> bool:
        """Return whether the BMS corrected its percentage since the last call."""
        corrected = self._corrected
        self._corrected = False
        return corrected

    @property
    def windows_accepted(self) -> int:
        """Return how many windows are in the median."""
        return len(self._windows)

    @property
    def soc_unconfirmed(self) -> bool:
        """Return whether the latest percentage waits to be confirmed or dropped."""
        return bool(self._held)

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
            return value, GEOMETRIC_MEAN
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
                self._corrections.break_trace()
                self._previous = None
                self._prior = None
                self._held = []
                return
            self._advance(sample)
        finally:
            self._record_history(timestamp)

    def _advance(self, sample: _Sample) -> None:
        """Fold one good poll in, holding an unexplained step until it settles."""
        previous = self._previous
        self._previous = sample
        if previous is None:
            return
        held = self._held
        if not held:
            self._segment(previous, sample)
        elif _returned(previous, held[-1], sample, self._rated_wh):
            # Off for a poll or two and straight back is a glitch, not a correction.
            self._held = []
            self._segment(previous, sample)
        elif len(held) < _HOLD_POLLS:
            held.append(sample)
            self._previous = previous
        else:
            # The reading stayed near the new level: the step was real.
            self._held = []
            self._segment(previous, held[0], confirmed=True)
            self._previous = held[0]
            for later in (*held[1:], sample):
                self._advance(later)

    def _segment(self, previous: _Sample, sample: _Sample, *, confirmed: bool = False) -> None:
        """Fold the stretch between two good polls in.

        A step the watts cannot explain is held unless ``confirmed``.
        """
        prior, self._prior = self._prior, None
        elapsed = sample.timestamp - previous.timestamp
        if elapsed <= 0.0:
            return
        if elapsed > self._gap_limit:
            self._corrections.break_trace()
            self._abort()
            return
        jumped = _soc_jumped(previous, sample, self._rated_wh)
        if jumped and not confirmed:
            if prior is None or not _returned(prior, previous, sample, self._rated_wh):
                self._prior = prior
                self._held = [sample]
                self._previous = previous
                return
            # The far side of a spike whose first leg fit under the floor.
            jumped = False
            previous = self._level(previous, prior.soc)
        self._prior = previous
        if jumped and elapsed > _MAX_JUDGED_SEGMENT_SECONDS:
            # Still counted as movement, and as a full charge on reaching 100.
            self._corrections.break_trace()
            self._track_correction(previous, sample, jumped=False)
            self._abort()
            return
        # A hold ends on the step that reaches 100 percent. The open window
        # then holds charge the reading never showed, so it is dropped.
        if self._track_correction(previous, sample, jumped) or jumped:
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

    def _level(self, spike: _Sample, soc: float) -> _Sample:
        """Put a spike's reading back to the level before it, where it was kept.

        Left in the trace, it would read as a correction on the next arrival
        at 100 percent. Left at the edge of the open window, it would move
        that run's end by the size of the spike.
        """
        leveled = replace(spike, soc=soc)
        if self._open and self._open[-1] is spike:
            self._open[-1] = leveled
        self._corrections.level_last(spike.timestamp, soc)
        return leveled

    def attributes(self) -> dict[str, Any]:
        """Return extra state attributes for the capacity and health sensors.

        ``spread_wh`` is the larger range within one direction, scaled to the
        published value; ``run_scatter_pct`` is the larger median deviation.
        Neither counts the gap between the charge and discharge medians.
        """
        count = len(self._windows)
        value, correction = self._published()
        used = self._contributing(correction)
        medians = self._median_attributes()
        now = self._previous.timestamp if self._previous is not None else None
        confidence, limits = assess(
            used,
            correction,
            medians.get("implied_round_trip_efficiency"),
            self._corrections.bound_pct(now),
        )
        attrs: dict[str, Any] = {
            "method": _METHOD,
            "windows_accepted": count,
            "windows_used": len(used),
            "confidence": confidence,
            "confidence_limits": limits,
            "min_soc_delta": MIN_SOC_DELTA,
            "efficiency_one_way": CHARGE_EFFICIENCY,
            "efficiency_correction": correction,
        }
        attrs.update(medians)
        attrs["windows"] = [window.to_dict() for window in self._windows]
        attrs["active_window"] = self._active_window()
        if value is not None and used:
            spread = run_spread_wh(used, value)
            if spread is not None:
                attrs["spread_wh"] = round(spread)
            scatter = run_scatter_pct(used)
            if scatter is not None:
                attrs["run_scatter_pct"] = round(scatter, 1)
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
        attrs.update(correction_attributes(self._corrections, now, _iso_timestamp))
        return attrs

    def _track_correction(self, previous: _Sample, sample: _Sample, jumped: bool) -> bool:
        """Pass one segment to the correction tracker; True when it ended a hold."""
        energy_in = -dc_energy_wh([previous, sample])
        verdict = self._corrections.observe(
            start=previous.timestamp,
            end=sample.timestamp,
            soc_before=previous.soc,
            soc_after=sample.soc,
            energy_in_wh=energy_in,
            jumped=jumped,
            reference_wh=self._correction_reference(energy_in),
        )
        if verdict.correction is not None:
            self._dirty = True
            self._corrected = self._corrected or verdict.correction.corrected
        return verdict.hold

    def _correction_reference(self, energy_in_wh: float) -> float | None:
        """Return the watt-hours one counted percent stands for in this direction.

        A direction median is what the BMS counts per point of that energy,
        so the points it explains are the points the BMS itself counted. The
        unexplained rest is then the whole correction whichever direction
        miscounted, and the one-way efficiency cancels. The published value
        would split a hold's correction with the other direction. It stands
        in until this direction has its median.
        """
        capacities = self._capacities("charge" if energy_in_wh > 0.0 else "discharge")
        if len(capacities) >= MIN_WINDOWS:
            return statistics.median(capacities)
        return self._published()[0]

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
        moment = finite(timestamp)
        if moment is None:
            return
        medians = self._median_attributes()
        rated = self._rated_wh
        exported = self._lifetime.get("total_grid_output_energy")
        cycles = None
        if rated is not None and rated > 0.0 and exported is not None:
            cycles = exported / rated
        value, correction = self._published()
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
            # The trend compares only weeks of one pack and one method.
            tags={"rated_wh": rated, "method": correction if value is not None else None},
        )
        if not changed:
            return
        self._dirty = True
        self._history_baseline = [dict(point) for point in self._history]

    def _remember_rated(self, rated_wh: float | None) -> None:
        """Keep the nameplate. A different one counts after a few polls in a row.

        A missing poll does not clear it. A change of more than a tenth drops
        the runs, which measured the old pack and would otherwise stay inside
        the plausible band of the new one.
        """
        rated = finite(rated_wh)
        if rated is None or rated <= 0.0:
            return
        current = self._rated_wh
        if current is None:
            self._rated_wh = rated
            return
        if rated == current:
            self._rated_candidate = None
            self._rated_seen = 0
            return
        if rated != self._rated_candidate:
            self._rated_candidate = rated
            self._rated_seen = 0
        self._rated_seen += 1
        if self._rated_seen < RATED_CONFIRM_POLLS:
            return
        self._rated_candidate = None
        self._rated_seen = 0
        self._rated_wh = rated
        self._dirty = True
        if abs(rated - current) > RATED_CHANGE_FRACTION * current:
            self._windows = []
            self._abort()
            self._held = []
            self._prior = None
            self._corrections.break_trace()

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
        # One run per direction is too thin a base for a round trip.
        if len(discharge) >= MIN_WINDOWS and len(charge) >= MIN_WINDOWS:
            ratio = statistics.median(discharge) / statistics.median(charge)
            efficiency = CHARGE_EFFICIENCY * DISCHARGE_EFFICIENCY * ratio
            attrs["implied_round_trip_efficiency"] = round(efficiency, 3)
        return attrs

    def _contributing(self, correction: str) -> list[CapacityWindow]:
        """Return the windows that actually entered the published number."""
        if correction == GEOMETRIC_MEAN:
            return list(self._windows)
        if correction in ("discharge_median", "charge_median"):
            direction = correction.removesuffix("_median")
            return [window for window in self._windows if window.direction == direction]
        return []

    def _make_sample(
        self,
        timestamp: float | None,
        soc: float | None,
        power_w: float | None,
        mode: str | None,
        pv_w: float | None,
        power_derived: bool,
    ) -> _Sample | None:
        """Return a sample, or None when this poll cannot be integrated."""
        moment = finite(timestamp)
        level = finite(soc)
        power = finite(power_w)
        derived = power_derived is True
        pv = sample_pv(pv_w, derived)
        if moment is None or level is None or power is None or pv is None:
            return None
        if not 0.0 <= level <= 100.0 or abs(power) > MAX_ABS_POWER_W:
            return None
        return _Sample(
            timestamp=moment,
            soc=level,
            power_w=power,
            mode=mode_text(mode),
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
        segment = segment_power(previous, sample)
        direction = _direction(segment)
        if self._open_direction == 0:
            self._low_power_segments = 0
            return direction == 0
        if direction == self._open_direction:
            self._low_power_segments = 0
            return False
        if _low_power_pause(segment, sample.power_w, self._open_direction):
            self._low_power_segments += 1
            return self._low_power_segments > _MAX_LOW_POWER_SEGMENTS
        self._low_power_segments = 0
        return True

    def _start(self, previous: _Sample, sample: _Sample) -> None:
        """Open a window on a segment that is clearly charging or discharging.

        State of charge arrives in whole percents, so a window that starts on
        an arbitrary poll can be up to one point off. The window therefore
        starts on the first poll after the reported percentage changes. The
        window is accepted on the poll that completes the 20-point move, which
        is also the first poll after a change, so both ends sit the same
        distance past a step.
        """
        direction = _direction(segment_power(previous, sample))
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
            self._open_direction = _direction(segment_power(previous, sample))
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
        if not isinstance(window, CapacityWindow):
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
        self._low_power_segments = 0
        self._aligned = False

    def _trim_windows(self) -> None:
        """Keep the newest ``MAX_WINDOWS`` of each direction, in time order."""
        kept: list[CapacityWindow] = []
        remaining = {"discharge": MAX_WINDOWS, "charge": MAX_WINDOWS}
        for window in reversed(self._windows):
            left = remaining.get(window.direction, 0)
            if left <= 0:
                continue
            remaining[window.direction] = left - 1
            kept.append(window)
        kept.reverse()
        self._windows = kept

    def _measure(
        self, samples: list[_Sample], direction: int
    ) -> CapacityWindow | _RejectWindow | None:
        """Return a window, None to keep collecting, or reject it."""
        soc_delta = samples[-1].soc - samples[0].soc
        moved = -soc_delta if direction > 0 else soc_delta
        if moved < MIN_SOC_DELTA:
            return None
        # Brief dips are bridged above. A window that is still under 150 W
        # on average spent its energy where 0.93 does not describe the
        # inverter, so it is not a capacity observation.
        if abs(average_power_w(samples)) < MIN_POWER_W:
            return _REJECT
        energy = dc_energy_wh(samples)
        # Discharge must remove state of charge; charge must add it.
        if direction > 0 and (soc_delta >= 0.0 or energy <= 0.0):
            return _REJECT
        if direction < 0 and (soc_delta <= 0.0 or energy >= 0.0):
            return _REJECT
        capacity = abs(energy) / (abs(soc_delta) / 100.0)
        if not _plausible(capacity, self._rated_wh):
            return _REJECT
        return CapacityWindow(
            capacity_wh=capacity,
            direction="discharge" if direction > 0 else "charge",
            soc_delta=abs(soc_delta),
            closed_at=samples[-1].timestamp,
            start_soc=samples[0].soc,
            end_soc=samples[-1].soc,
            average_power_w=average_power_w(samples),
            duration_s=samples[-1].timestamp - samples[0].timestamp,
            mode=samples[0].mode,
            pv_share=pv_share(samples),
        )


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
    # watts may be carried forward while state of charge is new. Passing
    # that pair in would invent energy. Passing None aborts the open window,
    # which a total poll failure does not do: the coordinator simply skips
    # this call and the gap limit bridges the hole. Skip the sample and leave
    # the previous fresh one in place. The next ES.GetStatus integrates
    # across the gap when it is still inside the limit, and aborts when it
    # is not, or when state of charge jumped.
    fresh = status.get(ES_STATUS_FRESH_KEY, True) is not False
    # Before ``observe``, which folds this poll's counters into the weekly point.
    estimator.remember_lifetime(status)
    if fresh:
        soc = finite(status.get("battery_soc"))
        power = finite(status.get("battery_power"))
        rated = finite(status.get("bat_cap"))
        mode = status.get("device_mode")
        pv_raw = status.get("pv_power")
        pv = pv_raw if isinstance(pv_raw, (int, float)) and not isinstance(pv_raw, bool) else None
        estimator.observe(
            timestamp=timestamp,
            soc=soc,
            power_w=power,
            mode=mode if isinstance(mode, str) else None,
            rated_wh=rated,
            pv_w=pv,
            power_derived=status.get(BATTERY_POWER_DERIVED_KEY) is True,
        )
    status[SOC_CORRECTED_KEY] = estimator.consume_correction()
    status[SOC_UNCONFIRMED_KEY] = estimator.soc_unconfirmed
    status[CAPACITY_DATA_KEY] = estimator.estimate_wh
    status[SOH_DATA_KEY] = estimator.state_of_health
    status[CAPACITY_ATTR_KEY] = estimator.attributes()
