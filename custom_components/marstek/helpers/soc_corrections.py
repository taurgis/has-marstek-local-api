"""Find where the BMS corrected its own state of charge, and by how much.

A LiFePO4 BMS counts energy in and out to keep its percentage. The voltage
curve is flat between roughly 20 and 90 percent, so mid-range it has nothing
to correct that count against. Small counting errors add up over partial
cycles until a cell reaches its full-charge voltage. The BMS then knows the
pack is full and sets its percentage to 100, which can be a jump from 92 or
95, a fast climb over a few polls, or a hold at 99 while energy still flows.

The size of that correction is the counting error since the last full charge.
It cannot be split between charge and discharge: a charge counted short and
a discharge counted long both end in an upward correction, yet they bias the
capacity estimate in opposite directions. What it gives is a scale. If ``j``
points were corrected after ``T`` points of charge and discharge, ``j / T`` is
about the error when one direction miscounted, more than it when the two
miscounted in opposite directions (the geometric mean cancels those), and
nothing when both counted alike, as with a stale capacity setting: that drift
never reaches full, and only a discharge to the cutoff shows it.

Every arrival at 100 percent is checked against the energy of its last
``APPROACH_POINTS`` points. Its size is the largest unexplained difference
between any point of that approach and full: the drift the BMS reset, which
for a hold is the drift where the reading reached 99. Steps that the existing
per-poll check already flags as jumps are corrections wherever they happen,
and an arrival does not count them again.

An arrival whose approach the trace does not cover, after a long restart or a
run of failed polls, is still a full charge. It is counted and moves the last
full charge, but it is not judged, so it adds nothing to the drift bound. The
part of the trace an arrival can still use is stored, so a restart shorter
than the gap limit does not break it.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Any

from .capacity_fields import finite

FULL_SOC = 100.0
# The arrival at 100 percent is judged over the points just below it. Fifteen
# points keep a 3 percent reference error to half a point and still cover a
# correction of more than ten.
APPROACH_POINTS = 15.0
# Whole-percent readings put up to a point of quantization on each end of the
# approach. Less than this is not a correction.
MIN_CORRECTION_POINTS = 3.0
# The bound pools the latest entries, and every entry of the last 30 days the
# card counts. Below this much movement in total, one missed point of
# quantization alone would read as a large drift.
MIN_BOUND_THROUGHPUT = 100.0
BOUND_EVENTS = 8
RECENT_SECONDS = 30 * 86_400
# A pack that reaches full every day, plus its corrections, inside 30 days.
MAX_EVENTS = 60
# Reported watts leave out what the unit draws for itself. A charge that
# tapers near full can spend a large share on that, so a hold must still show
# with this much of every charging second spent on the unit. A linked plug
# measured about 8 W of standby; the mock device draws 15 W. In simulation
# 20 W cleared honest packs up to that draw and kept holds of 2% and more.
STANDBY_ALLOWANCE_W = 20.0
# A run of polls at one reading keeps only its first and last point, so the
# trace grows with movement, not time. A slow solar charge or a long hold at
# 99 still fits; an approach of more than this many steps is not judged.
_MAX_TRACE = 480

JUMP = "jump"
CREEP = "creep"
HOLD = "hold"
FULL = "full"
_STYLES = frozenset({JUMP, CREEP, HOLD, FULL})


@dataclass(frozen=True, slots=True)
class SocCorrection:
    """One full charge or BMS correction.

    ``points`` is signed: positive when the BMS raised its reading. A ``full``
    arrival corrected less than ``MIN_CORRECTION_POINTS`` and is stored as 0.
    ``throughput`` is how far the reading moved, both directions, since the
    previous entry, or None when that is not known.
    """

    at: float
    from_soc: float
    to_soc: float
    points: float
    style: str
    throughput: float | None
    # False for a full charge whose approach the trace did not cover.
    judged: bool = True

    @property
    def corrected(self) -> bool:
        """Return whether the BMS changed its reading here."""
        return self.style != FULL

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form stored across restarts and shown on the card."""
        payload: dict[str, Any] = {
            "at": self.at,
            "from_soc": self.from_soc,
            "to_soc": self.to_soc,
            "points": self.points,
            "style": self.style,
            "throughput": self.throughput,
        }
        if not self.judged:
            payload["judged"] = False
        return payload

    @classmethod
    def from_dict(cls, raw: object) -> SocCorrection | None:
        """Parse a stored entry, or return None when it is not usable."""
        if not isinstance(raw, dict) or raw.get("style") not in _STYLES:
            return None
        at, before, after, points = (
            finite(raw.get(key)) for key in ("at", "from_soc", "to_soc", "points")
        )
        if at is None or before is None or after is None or points is None:
            return None
        if not (0.0 <= before <= FULL_SOC and 0.0 <= after <= FULL_SOC):
            return None
        throughput = finite(raw.get("throughput"))
        if throughput is not None and throughput < 0.0:
            throughput = None
        judged = raw.get("judged") is not False
        return cls(at, before, after, points, str(raw["style"]), throughput, judged)


@dataclass(frozen=True, slots=True)
class Verdict:
    """What one segment means for the capacity windows.

    ``hold`` means the step that reached 100 percent ended a hold: the energy
    of the approach includes charge the reading never showed, so the open
    window must not be accepted on it.
    """

    correction: SocCorrection | None = None
    hold: bool = False


_NO_VERDICT = Verdict()


@dataclass(frozen=True, slots=True)
class _Point:
    timestamp: float
    soc: float
    # DC watt-hours into the pack since the trace started.
    energy_wh: float
    jumped: bool
    # Points a mid-range jump on the step to here already stored.
    recorded: float = 0.0
    # Seconds spent charging since the trace started.
    charging_s: float = 0.0

    def to_list(self) -> list[float]:
        """Return the compact JSON form kept in the Store file."""
        return [
            self.timestamp,
            self.soc,
            round(self.energy_wh, 2),
            1.0 if self.jumped else 0.0,
            self.recorded,
            self.charging_s,
        ]

    @classmethod
    def from_list(cls, raw: object) -> _Point | None:
        """Parse a stored point, or return None when it is not usable."""
        if not isinstance(raw, list) or len(raw) != 6:
            return None
        moment, soc, energy, jumped, recorded, charging = (finite(value) for value in raw)
        if moment is None or soc is None or energy is None or jumped is None:
            return None
        if recorded is None or charging is None or charging < 0.0:
            return None
        if not 0.0 <= soc <= FULL_SOC:
            return None
        return cls(moment, soc, energy, jumped != 0.0, recorded, charging)


class SocCorrectionTracker:
    """Keep the recent trace and the corrections it showed."""

    def __init__(self) -> None:
        """Start with no corrections and no trace."""
        self._events: list[SocCorrection] = []
        self._throughput = 0.0
        self._trace: deque[_Point] = deque(maxlen=_MAX_TRACE)
        # Every arrival at 100 percent, including a top-off from 99.
        self._last_full_at: float | None = None
        # The lowest reading since that arrival. A shallow dip is a top-off.
        self._low_since_full: float | None = None
        # When the tracker first saw a poll, for "never full since".
        self._since: float | None = None

    @classmethod
    def from_dict(cls, raw: object) -> SocCorrectionTracker:
        """Restore stored corrections and the stored part of the trace.

        A store written before the last full charge was kept on its own takes
        it from the newest stored full charge, and its tracking start from the
        oldest entry.
        """
        tracker = cls()
        if not isinstance(raw, dict):
            return tracker
        events = raw.get("events")
        if isinstance(events, list):
            parsed = [event for item in events if (event := SocCorrection.from_dict(item))]
            tracker._events = sorted(parsed, key=lambda event: event.at)[-MAX_EVENTS:]
        throughput = finite(raw.get("throughput"))
        if throughput is not None and throughput >= 0.0:
            tracker._throughput = throughput
        fulls = [event.at for event in tracker._events if event.to_soc >= FULL_SOC]
        tracker._last_full_at = finite(raw.get("last_full_at")) or max(fulls, default=None)
        low = finite(raw.get("low_since_full"))
        if low is not None and 0.0 <= low <= FULL_SOC:
            tracker._low_since_full = low
        oldest = min((event.at for event in tracker._events), default=None)
        tracker._since = finite(raw.get("since")) or oldest
        tracker._trace.extend(_restore_trace(raw.get("trace")))
        return tracker

    def to_dict(self) -> dict[str, Any]:
        """Return the corrections, the movement since the last one, and the trace tail."""
        payload: dict[str, Any] = {
            "events": [event.to_dict() for event in self._events],
            "throughput": round(self._throughput, 1),
        }
        if self._trace:
            payload["trace"] = [point.to_list() for point in _approach_tail(list(self._trace))]
        for key, value in (
            ("last_full_at", self._last_full_at),
            ("low_since_full", self._low_since_full),
            ("since", self._since),
        ):
            if value is not None:
                payload[key] = value
        return payload

    @property
    def last_full_at(self) -> float | None:
        """Return when the reading last reached 100 percent."""
        return self._last_full_at

    @property
    def since(self) -> float | None:
        """Return when tracking began."""
        return self._since

    @property
    def events(self) -> list[SocCorrection]:
        """Return stored entries, oldest first."""
        return list(self._events)

    def break_trace(self) -> None:
        """Forget the trace after a gap or an unusable poll.

        The energy across the hole is unknown, so no approach may span it.
        The movement count stays: drift keeps growing across a gap.
        """
        self._trace.clear()

    def level_last(self, at: float, soc: float) -> None:
        """Set the last point's reading after it turned out to be a glitch."""
        if self._trace and self._trace[-1].timestamp == at:
            self._trace[-1] = replace(self._trace[-1], soc=soc)

    def observe(
        self,
        *,
        start: float,
        end: float,
        soc_before: float,
        soc_after: float,
        energy_in_wh: float,
        jumped: bool,
        reference_wh: float | None,
    ) -> Verdict:
        """Fold one segment between two good polls in.

        ``energy_in_wh`` is DC energy into the pack over the segment, negative
        on discharge. ``jumped`` is the per-poll check's verdict on the step.
        ``reference_wh`` is the capacity that converts energy to points: the
        median of windows in the segment's direction, or the published value
        until that direction has one. Without it only flagged jumps are measured.
        """
        if self._since is None:
            self._since = start
        low = min(soc_before, soc_after)
        if self._low_since_full is None or low < self._low_since_full:
            self._low_since_full = low
        if not self._trace or self._trace[-1].timestamp != start:
            self._trace.clear()
            self._trace.append(_Point(start, soc_before, 0.0, jumped=False))
        previous = self._trace[-1]
        energy = previous.energy_wh + energy_in_wh
        charging = previous.charging_s + (end - start if energy_in_wh > 0.0 else 0.0)
        if len(self._trace) >= 2 and self._trace[-2].soc == previous.soc == soc_after:
            # The middle of a run never bounds an approach: drop it.
            self._trace.pop()
        self._trace.append(_Point(end, soc_after, energy, jumped, charging_s=charging))
        moved = soc_after - soc_before
        if soc_before < FULL_SOC <= soc_after:
            return self._arrive(end, reference_wh)
        if jumped:
            return self._record_jump(end, soc_before, soc_after, energy_in_wh, reference_wh)
        self._throughput += abs(moved)
        return _NO_VERDICT

    def _record_jump(
        self,
        at: float,
        soc_before: float,
        soc_after: float,
        energy_in_wh: float,
        reference_wh: float | None,
    ) -> Verdict:
        """Record a flagged step that did not end at 100 percent."""
        moved = soc_after - soc_before
        explained = _explained(energy_in_wh, reference_wh)
        event = self._append(at, soc_before, soc_after, moved - explained, JUMP)
        # An arrival whose approach spans this step must not count it again.
        self._trace[-1] = replace(self._trace[-1], recorded=event.points)
        return Verdict(correction=event)

    def _arrive(self, at: float, reference_wh: float | None) -> Verdict:
        """Judge the approach that just reached 100 percent."""
        trace = list(self._trace)
        arrival = trace[-1]
        base = _approach_base(trace)
        # Every judged arrival starts the next approach from here.
        self._trace.clear()
        self._trace.append(_Point(arrival.timestamp, arrival.soc, 0.0, jumped=False))
        low = self._low_since_full
        self._last_full_at = at
        self._low_since_full = arrival.soc
        if base is None:
            last = trace[-2]
            if not arrival.jumped:
                if low is None or low <= FULL_SOC - APPROACH_POINTS:
                    # A full charge the trace does not cover: counted, not judged.
                    event = self._append(at, last.soc, arrival.soc, 0.0, FULL, judged=False)
                    return Verdict(correction=event)
                # A top-off from just below full.
                self._throughput += arrival.soc - last.soc
                return _NO_VERDICT
            base = last
        span = trace[trace.index(base) :]
        flagged = any(point.jumped for point in span[1:])
        if reference_wh is None:
            # Without a capacity only a flagged step is a measurement.
            if not flagged:
                return Verdict(correction=self._append(at, base.soc, arrival.soc, 0.0, FULL))
            recorded = sum(point.recorded for point in span[1:])
            points = sum(
                later.soc - earlier.soc for earlier, later in pairwise(span) if later.jumped
            )
            points -= recorded
        else:
            points, recorded = _largest_unexplained(span, reference_wh)
        # A climb that a mid-range jump began is one correction: the noise
        # floor applies to the whole of it, the entry stores the rest.
        style = _style(points + recorded, flagged)
        event = self._append(at, base.soc, arrival.soc, points if style != FULL else 0.0, style)
        return Verdict(correction=event, hold=style == HOLD)

    def _append(
        self,
        at: float,
        before: float,
        after: float,
        points: float,
        style: str,
        *,
        judged: bool = True,
    ) -> SocCorrection:
        """Store one entry and start counting movement again."""
        # Rounded as stored, so a restart restores the same entry.
        event = SocCorrection(
            at, before, after, round(points, 1), style, round(self._throughput, 1), judged
        )
        self._events.append(event)
        del self._events[:-MAX_EVENTS]
        self._throughput = 0.0
        return event

    def bound_pct(self, now: float | None = None) -> float | None:
        """Return how far drift could move the published capacity, in percent.

        Corrected points over movement, pooled across the judged entries that
        know their movement: the latest ``BOUND_EVENTS``, and with ``now``
        every one of the last 30 days, so a correction the card names is
        never left out. A full charge under ``MIN_CORRECTION_POINTS`` adds
        its movement and no points. Pooling keeps a run of small corrections
        from reading as zero, as a median of per-charge ratios would when
        most charges stay under the threshold.
        """
        known = [event for event in self._events if event.throughput is not None and event.judged]
        first = len(known) - BOUND_EVENTS
        recent = [
            event
            for index, event in enumerate(known)
            if index >= first or (now is not None and now - event.at <= RECENT_SECONDS)
        ]
        moved = sum(event.throughput or 0.0 for event in recent)
        if moved < MIN_BOUND_THROUGHPUT:
            return None
        corrected = sum(abs(event.points) for event in recent)
        return round(corrected / moved * 100.0, 1)


def _explained(energy_in_wh: float, reference_wh: float | None) -> float:
    """Return the points ``energy_in_wh`` moves a pack of ``reference_wh``."""
    if reference_wh is None or reference_wh <= 0.0:
        return 0.0
    return energy_in_wh / reference_wh * 100.0


def _largest_unexplained(span: list[_Point], reference_wh: float) -> tuple[float, float]:
    """Return the unexplained points from the point of the approach that differs most.

    The BMS resets the drift it has when the pack reaches full, not the drift
    at the start of the approach. A hold has the most where the reading
    reached 99, a creep where it started to climb, a jump on the step before
    it. Points a mid-range jump already stored after that point are taken
    out and returned second.
    """
    arrival = span[-1]
    recorded = 0.0
    largest = (0.0, 0.0)
    for later, point in pairwise(reversed(span)):
        recorded += later.recorded
        moved = arrival.soc - point.soc
        energy = arrival.energy_wh - point.energy_wh
        unexplained = moved - _explained(energy, reference_wh)
        if unexplained < 0.0:
            # A hold only counts what is left with the unit's own draw stored nowhere.
            seconds = arrival.charging_s - point.charging_s
            stored = energy - STANDBY_ALLOWANCE_W * seconds / 3600.0
            unexplained = min(0.0, moved - _explained(stored, reference_wh))
        if abs(unexplained) > abs(sum(largest)):
            largest = (unexplained - recorded, recorded)
    return largest


def _approach_tail(trace: list[_Point]) -> list[_Point]:
    """Return the part of the trace a later arrival at 100 percent can use.

    That is everything from the latest point an approach could start on. With
    no such point, the last one: it links the next poll to the stored trace.
    """
    level = FULL_SOC - APPROACH_POINTS
    for index in range(len(trace) - 1, -1, -1):
        if trace[index].soc <= level:
            return trace[index:]
    return trace[-1:]


def _restore_trace(raw: object) -> list[_Point]:
    """Return a stored trace tail, or nothing when any of it is unusable."""
    if not isinstance(raw, list):
        return []
    points: list[_Point] = []
    for item in raw[-_MAX_TRACE:]:
        point = _Point.from_list(item)
        if point is None or (points and point.timestamp <= points[-1].timestamp):
            return []
        points.append(point)
    return points


def _approach_base(trace: list[_Point]) -> _Point | None:
    """Return the latest point at least ``APPROACH_POINTS`` below the arrival."""
    level = trace[-1].soc - APPROACH_POINTS
    for point in reversed(trace[:-1]):
        if point.soc <= level:
            return point
    return None


def _style(points: float, flagged: bool) -> str:
    """Name an arrival from its unexplained points."""
    if abs(points) < MIN_CORRECTION_POINTS:
        return FULL
    if flagged and points > 0.0:
        return JUMP
    return CREEP if points > 0.0 else HOLD


# Drift bounds that cap the coarse confidence label. Two percent is about what
# the window medians resolve; five is more than a year of fade on most packs.
_MEDIUM_BOUND_PCT = 2.0
_LOW_BOUND_PCT = 5.0
_CONFIDENCE_ORDER = ("low", "medium", "high")
_CARD_EVENTS = 10


def cap_confidence(label: str, bound_pct: float | None) -> str:
    """Lower a confidence label when the BMS drifts more than the windows resolve."""
    if bound_pct is None or label not in _CONFIDENCE_ORDER:
        return label
    if bound_pct >= _LOW_BOUND_PCT:
        cap = "low"
    elif bound_pct >= _MEDIUM_BOUND_PCT:
        cap = "medium"
    else:
        return label
    order = _CONFIDENCE_ORDER
    return order[min(order.index(label), order.index(cap))]


def correction_attributes(
    tracker: SocCorrectionTracker,
    now: float | None,
    iso: Callable[[float], str],
) -> dict[str, Any]:
    """Return the sensor attributes that describe full charges and corrections.

    ``now`` is the latest poll's time. The 30-day counts are left out without it.
    ``full_charges_checked_30d`` is how many of those full charges had an
    approach the trace covered; only those could show a correction.
    """
    events = tracker.events
    corrected = [event for event in events if event.corrected]
    attrs: dict[str, Any] = {
        "soc_corrections": [
            {**event.to_dict(), "at": iso(event.at)} for event in corrected[-_CARD_EVENTS:]
        ],
    }
    if now is not None:
        recent = [event for event in events if now - event.at <= RECENT_SECONDS]
        fulls = [event for event in recent if event.to_soc >= FULL_SOC]
        attrs["soc_corrections_30d"] = sum(1 for event in recent if event.corrected)
        attrs["full_charges_30d"] = len(fulls)
        attrs["full_charges_checked_30d"] = sum(1 for event in fulls if event.judged)
    if tracker.last_full_at is not None:
        attrs["last_full_charge_at"] = iso(tracker.last_full_at)
    if tracker.since is not None:
        attrs["soc_tracking_since"] = iso(tracker.since)
    bound = tracker.bound_pct(now)
    if bound is not None:
        attrs["soc_drift_bound_pct"] = bound
    return attrs
