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
capacity estimate in opposite directions. What it does give is a bound. If
``j`` points were corrected after ``T`` points of charge and discharge, the
geometric mean of the two direction medians is off by at most about ``j / T``.

Every arrival at 100 percent is checked against the energy of its last
``APPROACH_POINTS`` points. Steps that the existing per-poll check already
flags as jumps are corrections wherever they happen.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
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
# The bound pools the latest entries. Below this much movement in total, one
# missed point of quantization alone would read as a large drift.
MIN_BOUND_THROUGHPUT = 100.0
BOUND_EVENTS = 8
MAX_EVENTS = 20
# Four hours of 30 second polls. An approach longer than that is not judged.
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

    @property
    def corrected(self) -> bool:
        """Return whether the BMS changed its reading here."""
        return self.style != FULL

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form stored across restarts and shown on the card."""
        return {
            "at": self.at,
            "from_soc": self.from_soc,
            "to_soc": self.to_soc,
            "points": self.points,
            "style": self.style,
            "throughput": self.throughput,
        }

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
        return cls(at, before, after, points, str(raw["style"]), throughput)


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


class SocCorrectionTracker:
    """Keep the recent trace and the corrections it showed."""

    def __init__(self) -> None:
        """Start with no corrections and no trace."""
        self._events: list[SocCorrection] = []
        self._throughput = 0.0
        self._trace: deque[_Point] = deque(maxlen=_MAX_TRACE)

    @classmethod
    def from_dict(cls, raw: object) -> SocCorrectionTracker:
        """Restore stored corrections. The trace starts again after a restart."""
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
        return tracker

    def to_dict(self) -> dict[str, Any]:
        """Return the corrections and the movement since the last one."""
        return {
            "events": [event.to_dict() for event in self._events],
            "throughput": round(self._throughput, 1),
        }

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
        if not self._trace or self._trace[-1].timestamp != start:
            self._trace.clear()
            self._trace.append(_Point(start, soc_before, 0.0, jumped=False))
        energy = self._trace[-1].energy_wh + energy_in_wh
        self._trace.append(_Point(end, soc_after, energy, jumped))
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
        return Verdict(correction=event)

    def _arrive(self, at: float, reference_wh: float | None) -> Verdict:
        """Judge the approach that just reached 100 percent."""
        trace = list(self._trace)
        arrival = trace[-1]
        base = _approach_base(trace)
        # Every judged arrival starts the next approach from here.
        self._trace.clear()
        self._trace.append(_Point(arrival.timestamp, arrival.soc, 0.0, jumped=False))
        if base is None:
            last = trace[-2]
            if not arrival.jumped:
                # A top-off from 99, or an approach the trace does not cover.
                self._throughput += arrival.soc - last.soc
                return _NO_VERDICT
            base = last
        span = trace[trace.index(base) :]
        flagged = any(point.jumped for point in span[1:])
        moved = arrival.soc - base.soc
        if reference_wh is None:
            # Without a capacity only a flagged step is a measurement.
            if not flagged:
                return Verdict(correction=self._append(at, base.soc, arrival.soc, 0.0, FULL))
            points = sum(
                later.soc - earlier.soc for earlier, later in pairwise(span) if later.jumped
            )
        else:
            points = moved - _explained(arrival.energy_wh - base.energy_wh, reference_wh)
        style = _style(points, flagged)
        event = self._append(at, base.soc, arrival.soc, points if style != FULL else 0.0, style)
        return Verdict(correction=event, hold=style == HOLD)

    def _append(
        self, at: float, before: float, after: float, points: float, style: str
    ) -> SocCorrection:
        """Store one entry and start counting movement again."""
        # Rounded as stored, so a restart restores the same entry.
        event = SocCorrection(
            at, before, after, round(points, 1), style, round(self._throughput, 1)
        )
        self._events.append(event)
        del self._events[:-MAX_EVENTS]
        self._throughput = 0.0
        return event

    def bound_pct(self) -> float | None:
        """Return how far drift could move the published capacity, in percent.

        Corrected points over movement, pooled across the latest entries that
        know their movement. A full charge under ``MIN_CORRECTION_POINTS``
        adds its movement and no points. Pooling keeps a run of small
        corrections from reading as zero, as a median of per-charge ratios
        would when most charges stay under the threshold.
        """
        known = [event for event in self._events if event.throughput is not None]
        recent = known[-BOUND_EVENTS:]
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
_RECENT_SECONDS = 30 * 86_400
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
    """
    events = tracker.events
    corrected = [event for event in events if event.corrected]
    attrs: dict[str, Any] = {
        "soc_corrections": [
            {**event.to_dict(), "at": iso(event.at)} for event in corrected[-_CARD_EVENTS:]
        ],
    }
    if now is not None:
        recent = [event for event in events if now - event.at <= _RECENT_SECONDS]
        attrs["soc_corrections_30d"] = sum(1 for event in recent if event.corrected)
        attrs["full_charges_30d"] = sum(1 for event in recent if event.to_soc >= FULL_SOC)
    fulls = [event for event in events if event.to_soc >= FULL_SOC]
    if fulls:
        attrs["last_full_charge_at"] = iso(fulls[-1].at)
    bound = tracker.bound_pct()
    if bound is not None:
        attrs["soc_drift_bound_pct"] = bound
    return attrs
