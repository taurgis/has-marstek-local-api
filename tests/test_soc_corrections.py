"""BMS state-of-charge corrections: detection, the drift bound, and storage.

Traces come from a pack whose true energy is known. The reported percentage
either follows that energy (an honest BMS) or a counter with its own error
that the BMS resets at full charge by a jump, a climb over a few polls, or a
hold at 99.
"""

from __future__ import annotations

import math
import statistics
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

import pytest

from custom_components.marstek.capacity_estimator import (
    CARD_ATTRIBUTES,
    SOC_CORRECTED_KEY,
    UsableCapacityEstimator,
    apply_capacity_estimate,
)
from custom_components.marstek.helpers import soc_corrections
from custom_components.marstek.helpers.soc_corrections import (
    CREEP,
    FULL,
    HOLD,
    JUMP,
    MAX_EVENTS,
    SocCorrection,
    SocCorrectionTracker,
    cap_confidence,
    correction_attributes,
)

_ETA = 0.93
_T0 = 1_700_000_000.0
_RATED = 5_120.0
_TRUE = 4_600.0
_POLL = 30.0


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).isoformat()


@dataclass
class _Pack:
    """True energy plus the BMS's reported whole percent.

    ``k_charge`` scales what the BMS counts on charge, ``k_discharge`` on
    discharge. At true full the BMS resets its reading: ``jump`` at once,
    ``creep`` by ``creep_step`` points a poll, ``hold`` caps the reading at 99
    until full.
    """

    soc: float = 50.0
    k_charge: float = 1.0
    k_discharge: float = 1.0
    resync: str = "jump"
    creep_step: float = 2.0
    capacity: float = _TRUE
    estimator: UsableCapacityEstimator = field(default_factory=UsableCapacityEstimator)
    now: float = _T0
    counted: float = field(init=False)
    energy: float = field(init=False)
    shown: float = field(init=False)
    corrected_polls: int = 0

    def __post_init__(self) -> None:
        self.energy = self.soc / 100.0 * self.capacity
        self.counted = self.soc
        self.shown = float(math.floor(self.counted))
        self._observe(0.0)

    @property
    def full(self) -> bool:
        return self.energy >= self.capacity - 1e-6

    def _observe(self, watts: float) -> None:
        status = {
            "battery_soc": self.shown,
            "battery_power": watts,
            "bat_cap": _RATED,
            "device_mode": "auto",
        }
        apply_capacity_estimate(self.estimator, status, self.now)
        self.corrected_polls += int(status[SOC_CORRECTED_KEY] is True)

    def poll(self, watts: float) -> None:
        """Advance one poll at ``watts`` AC (positive discharges)."""
        if watts < 0.0 and self.full:
            watts = 0.0
        hours = _POLL / 3600.0
        dc = -watts * _ETA * hours if watts < 0.0 else -watts / _ETA * hours
        self.energy = min(self.capacity, max(0.0, self.energy + dc))
        factor = self.k_charge if dc > 0.0 else self.k_discharge
        self.counted = min(99.9, max(0.0, self.counted + factor * dc / self.capacity * 100.0))
        if self.full:
            if self.resync == "creep":
                self.counted = min(100.0, max(self.counted, self.shown + self.creep_step))
            else:
                self.counted = 100.0
        shown = float(math.floor(self.counted))
        if self.resync == "hold" and not self.full:
            shown = min(shown, 99.0)
        self.shown = shown
        self.now += _POLL
        self._observe(watts)

    def charge_to_full(self, watts: float = 2_000.0) -> None:
        while self.shown < 100.0:
            self.poll(-watts)
        for _ in range(4):
            self.poll(0.0)

    def discharge_to(self, level: float, watts: float = 700.0) -> None:
        while self.shown > level:
            self.poll(watts)
        for _ in range(4):
            self.poll(0.0)


def _cycles(pack: _Pack, count: int, low: float = 15.0) -> None:
    for _ in range(count):
        pack.charge_to_full()
        pack.discharge_to(low)


def _corrections(pack: _Pack) -> list[SocCorrection]:
    return pack.estimator._corrections.events


def test_an_honest_bms_records_full_charges_and_no_correction() -> None:
    pack = _Pack()
    _cycles(pack, 6)
    events = _corrections(pack)
    assert len(events) == 6
    assert {event.style for event in events} == {FULL}
    assert all(event.points == 0.0 for event in events)
    attrs = pack.estimator.attributes()
    assert attrs["soc_drift_bound_pct"] == 0.0
    assert attrs["soc_corrections"] == []
    assert attrs["full_charges_30d"] == 6
    assert attrs["soc_corrections_30d"] == 0
    assert attrs["last_full_charge_at"] == _iso(events[-1].at)
    assert pack.corrected_polls == 0
    assert pack.estimator.estimate_wh == pytest.approx(_TRUE, rel=0.01)


def test_a_charge_counted_short_ends_in_a_jump_that_bounds_the_error() -> None:
    """6 % short on charge: about 4 points per 70-point charge, bound near 3 %."""
    pack = _Pack(k_charge=0.94)
    _cycles(pack, 6)
    corrected = [event for event in _corrections(pack) if event.corrected]
    assert len(corrected) >= 5
    assert {event.style for event in corrected} == {JUMP}
    assert all(event.points > 3.0 for event in corrected)
    attrs = pack.estimator.attributes()
    honest = _Pack()
    _cycles(honest, 6)
    assert pack.estimator.estimate_wh is not None and honest.estimator.estimate_wh is not None
    error = abs(pack.estimator.estimate_wh / honest.estimator.estimate_wh - 1.0) * 100.0
    assert error > 2.0
    assert attrs["soc_drift_bound_pct"] == pytest.approx(error, abs=1.0)
    assert attrs["confidence"] in {"low", "medium"}
    assert attrs["soc_corrections"][-1]["at"] == _iso(corrected[-1].at)
    assert pack.corrected_polls == len(corrected)


@pytest.mark.parametrize("step", [1.0, 2.0])
def test_a_climb_over_several_polls_is_a_creep(step: float) -> None:
    """Each poll's step is inside the per-poll limit; the approach is not."""
    pack = _Pack(k_charge=0.9, resync="creep", creep_step=step)
    _cycles(pack, 4)
    corrected = [event for event in _corrections(pack) if event.corrected]
    assert corrected
    assert {event.style for event in corrected} == {CREEP}
    assert all(event.points > 3.0 for event in corrected)


def test_a_hold_at_99_is_a_negative_correction_and_drops_the_open_window() -> None:
    pack = _Pack(k_charge=1.1, resync="hold")
    _cycles(pack, 4)
    holds = [event for event in _corrections(pack) if event.style == HOLD]
    assert holds
    assert all(event.points < -3.0 for event in holds)
    assert all(event.to_soc == 100.0 for event in holds)
    closing = {window["closed_at"] for window in pack.estimator.attributes()["windows"]}
    assert not closing & {event.at for event in holds}


def test_a_flagged_step_mid_range_is_a_jump_and_resets_the_movement_count() -> None:
    pack = _Pack(soc=60.0)
    for _ in range(80):
        pack.poll(-1_500.0)
    tracker = pack.estimator._corrections
    moved = tracker.to_dict()["throughput"]
    assert moved > 0.0
    pack.counted += 10.0
    pack.poll(-1_500.0)
    event = _corrections(pack)[-1]
    assert event.style == JUMP
    assert event.points == pytest.approx(10.0, abs=1.5)
    assert event.throughput == pytest.approx(moved)
    assert tracker.to_dict()["throughput"] == 0.0
    assert pack.corrected_polls == 1


def _segment(
    tracker: SocCorrectionTracker,
    start: float,
    before: float,
    after: float,
    *,
    energy: float = 0.0,
    jumped: bool = False,
    reference: float | None = None,
) -> SocCorrection | None:
    verdict = tracker.observe(
        start=start,
        end=start + _POLL,
        soc_before=before,
        soc_after=after,
        energy_in_wh=energy,
        jumped=jumped,
        reference_wh=reference,
    )
    return verdict.correction


def _climb(
    tracker: SocCorrectionTracker, start: float, levels: list[float], **kwargs: object
) -> float:
    for before, after in pairwise(levels):
        _segment(tracker, start, before, after, **kwargs)  # type: ignore[arg-type]
        start += _POLL
    return start


def test_without_a_capacity_only_a_flagged_step_is_measured() -> None:
    tracker = SocCorrectionTracker()
    moment = _climb(tracker, _T0, [80.0 + index for index in range(13)], energy=46.0)
    event = _segment(tracker, moment, 92.0, 100.0, jumped=True)
    assert event is not None
    assert (event.style, event.points, event.from_soc) == (JUMP, 8.0, 85.0)

    tracker = SocCorrectionTracker()
    moment = _climb(tracker, _T0, [80.0 + index for index in range(20)], energy=46.0)
    event = _segment(tracker, moment, 99.0, 100.0, energy=46.0)
    assert event is not None
    assert (event.style, event.points) == (FULL, 0.0)


def test_a_top_off_from_99_and_an_uncovered_approach_are_not_entries() -> None:
    tracker = SocCorrectionTracker()
    assert _segment(tracker, _T0, 99.0, 100.0, energy=46.0, reference=4_600.0) is None
    _climb(tracker, _T0, [70.0, 80.0, 90.0], energy=460.0)
    tracker.break_trace()
    assert _segment(tracker, _T0 + 600, 99.0, 100.0, energy=46.0, reference=4_600.0) is None
    assert tracker.events == []
    assert tracker.to_dict()["throughput"] == 22.0


def test_a_jump_right_after_a_gap_is_still_measured_on_its_own_step() -> None:
    tracker = SocCorrectionTracker()
    tracker.break_trace()
    event = _segment(tracker, _T0, 91.0, 100.0, energy=20.0, jumped=True, reference=4_600.0)
    assert event is not None
    assert event.style == JUMP
    assert event.points == round(9.0 - 20.0 / 4_600.0 * 100.0, 1)


def test_the_bound_pools_movement_and_needs_enough_of_it() -> None:
    tracker = SocCorrectionTracker.from_dict(
        {
            "events": [
                {
                    "at": _T0,
                    "from_soc": 85,
                    "to_soc": 100,
                    "points": 0,
                    "style": FULL,
                    "throughput": 40,
                },
                {
                    "at": _T0 + 1,
                    "from_soc": 85,
                    "to_soc": 100,
                    "points": 4,
                    "style": JUMP,
                    "throughput": 40,
                },
            ]
        }
    )
    assert tracker.bound_pct() is None
    tracker = SocCorrectionTracker.from_dict(
        {
            "events": [
                {
                    "at": _T0 + i,
                    "from_soc": 85,
                    "to_soc": 100,
                    "points": p,
                    "style": s,
                    "throughput": 100,
                }
                for i, (p, s) in enumerate([(0, FULL), (0, FULL), (4, JUMP), (-4, HOLD)])
            ]
        }
    )
    assert tracker.bound_pct() == 2.0


@pytest.mark.parametrize(
    ("label", "bound", "expected"),
    [
        ("high", None, "high"),
        ("high", 1.9, "high"),
        ("high", 2.0, "medium"),
        ("low", 2.0, "low"),
        ("medium", 5.0, "low"),
        ("high", 7.5, "low"),
        ("collecting", 9.0, "collecting"),
    ],
)
def test_drift_caps_the_confidence_label(label: str, bound: float | None, expected: str) -> None:
    assert cap_confidence(label, bound) == expected


def test_corrections_survive_a_restart_and_junk_is_ignored() -> None:
    pack = _Pack(k_charge=0.94)
    _cycles(pack, 3)
    stored = pack.estimator.to_dict()
    assert stored["soc_corrections"]["events"]
    restored = UsableCapacityEstimator.from_dict(stored)
    assert restored._corrections.events == pack.estimator._corrections.events
    assert restored._corrections.to_dict() == pack.estimator._corrections.to_dict()

    junk = {
        "events": [
            None,
            {"style": "teleport", "at": 1, "from_soc": 1, "to_soc": 2, "points": 1},
            {"style": JUMP, "at": True, "from_soc": 1, "to_soc": 2, "points": 1},
            {"style": JUMP, "at": 1, "from_soc": 101, "to_soc": 2, "points": 1},
            {"style": JUMP, "at": 2, "from_soc": 90, "to_soc": 100, "points": 9, "throughput": -1},
        ],
        "throughput": "lots",
    }
    tracker = SocCorrectionTracker.from_dict(junk)
    assert [(event.at, event.throughput) for event in tracker.events] == [(2.0, None)]
    assert tracker.to_dict()["throughput"] == 0.0
    assert SocCorrectionTracker.from_dict("nope").events == []
    assert UsableCapacityEstimator.from_dict({"windows": []})._corrections.events == []


def test_only_the_newest_entries_are_kept() -> None:
    tracker = SocCorrectionTracker()
    moment = _T0
    for _ in range(MAX_EVENTS + 5):
        moment = _climb(tracker, moment, [80.0 + index for index in range(21)], energy=46.0)
        moment += 3_600.0
    assert len(tracker.events) == MAX_EVENTS
    assert tracker.events[-1].at == moment - 3_600.0


def test_thirty_day_counts_follow_the_latest_poll() -> None:
    day = 86_400.0
    tracker = SocCorrectionTracker.from_dict(
        {
            "events": [
                {"at": _T0, "from_soc": 85, "to_soc": 100, "points": 6, "style": JUMP},
                {"at": _T0 + 40 * day, "from_soc": 60, "to_soc": 70, "points": 9, "style": JUMP},
                {"at": _T0 + 41 * day, "from_soc": 85, "to_soc": 100, "points": 0, "style": FULL},
            ]
        }
    )
    attrs = correction_attributes(tracker, _T0 + 45 * day, _iso)
    assert attrs["soc_corrections_30d"] == 1
    assert attrs["full_charges_30d"] == 1
    assert attrs["last_full_charge_at"] == _iso(_T0 + 41 * day)
    assert [item["points"] for item in attrs["soc_corrections"]] == [6.0, 9.0]
    assert "soc_drift_bound_pct" not in attrs
    without_time = correction_attributes(tracker, None, _iso)
    assert "soc_corrections_30d" not in without_time
    assert correction_attributes(SocCorrectionTracker(), None, _iso) == {"soc_corrections": []}


def test_the_card_list_stays_out_of_the_recorder() -> None:
    assert "soc_corrections" in CARD_ATTRIBUTES


def test_a_correction_cannot_tell_which_direction_miscounted() -> None:
    """Charge counted short and discharge counted long look the same.

    Both end in an upward correction of the same size and a charge median
    above the discharge median, yet they push the estimate opposite ways.
    Publishing either direction's median alone would double one error, so the
    geometric mean stays: it splits the difference.
    """
    short = _Pack(k_charge=0.94)
    long = _Pack(k_discharge=1.06)
    for pack in (short, long):
        _cycles(pack, 10)
    errors = []
    for pack in (short, long):
        estimator = pack.estimator
        charge = statistics.median(estimator._capacities("charge"))
        discharge = statistics.median(estimator._capacities("discharge"))
        assert estimator.estimate_wh is not None
        assert charge > discharge
        assert all(event.points > 0.0 for event in _corrections(pack) if event.corrected)
        errors.append(
            {
                "published": estimator.estimate_wh / _TRUE - 1.0,
                "discharge": discharge / _TRUE - 1.0,
                "charge": charge / _TRUE - 1.0,
            }
        )
    assert short.estimator._corrections.bound_pct() == pytest.approx(
        long.estimator._corrections.bound_pct(), abs=0.5
    )
    assert errors[0]["published"] > 0.0 > errors[1]["published"]
    for side in ("discharge", "charge"):
        worst = max(abs(error[side]) for error in errors)
        assert worst > max(abs(error["published"]) for error in errors)


def _bound_and_error(**miscount: Any) -> tuple[float, float]:
    """Return the drift bound and the estimate's error against an honest pack."""
    packs = (_Pack(), _Pack(**miscount))
    for pack in packs:
        _cycles(pack, 10)
    honest, drifted = (pack.estimator.estimate_wh for pack in packs)
    bound = packs[1].estimator._corrections.bound_pct()
    assert honest is not None and drifted is not None and bound is not None
    return bound, abs(drifted / honest - 1.0) * 100.0


@pytest.mark.parametrize(
    "miscount",
    [
        {"k_charge": 0.94},
        {"k_charge": 1.06, "resync": "hold"},
        {"k_discharge": 1.06},
        {"k_discharge": 0.94, "resync": "hold"},
    ],
)
def test_the_bound_covers_the_error_for_a_hold_as_for_a_jump(miscount: dict[str, Any]) -> None:
    """Approach energy is converted with the median of its own direction.

    The published value is the mean of both directions, so it hid part of a
    hold's correction: a 3.4% error showed a 2.5% bound.
    """
    bound, error = _bound_and_error(**miscount)
    assert 0.85 * error <= bound <= error + 1.0


def test_the_bound_does_not_lean_on_the_efficiency_assumption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same drift reads the same bound whatever the real efficiency.

    The standby allowance is a fixed number of watts, not a share of the
    charge, so it is left out here.
    """
    monkeypatch.setattr(soc_corrections, "STANDBY_ALLOWANCE_W", 0.0)
    bounds = []
    for eta in (0.93, 0.89):
        monkeypatch.setattr(sys.modules[__name__], "_ETA", eta)
        bounds.append(_bound_and_error(k_charge=1.06, resync="hold")[0])
    assert bounds[0] == pytest.approx(bounds[1], abs=0.2)


def _hold_level(
    tracker: SocCorrectionTracker, start: float, level: float, polls: int, energy: float
) -> float:
    """Sit at ``level`` for ``polls`` polls, charging ``energy`` Wh each."""
    for _ in range(polls):
        _segment(tracker, start, level, level, energy=energy, reference=_TRUE)
        start += _POLL
    return start


def test_a_slow_approach_with_long_flat_stretches_is_still_judged() -> None:
    """Forty polls a point is 520 polls before full; flat runs keep two ends."""
    tracker = SocCorrectionTracker()
    moment = _T0
    for level in range(80, 92):
        moment = _hold_level(tracker, moment, float(level), 39, 46.0 / 40.0)
        _segment(tracker, moment, level, level + 1.0, energy=46.0 / 40.0, reference=_TRUE)
        moment += _POLL
    assert len(tracker._trace) < 40
    event = _segment(tracker, moment, 92.0, 100.0, energy=46.0, jumped=True, reference=_TRUE)
    assert event is not None
    assert event.style == JUMP
    # Seven points from where each level began; up to one more of whole-percent
    # quantization from where a level ended.
    assert 7.0 <= event.points <= 8.0


def test_a_climb_that_a_flagged_step_began_is_not_counted_twice() -> None:
    tracker = SocCorrectionTracker()
    moment = _climb(
        tracker, _T0, [80.0 + index for index in range(11)], energy=46.0, reference=_TRUE
    )
    mid = _segment(tracker, moment, 90.0, 95.0, energy=46.0, jumped=True, reference=_TRUE)
    assert mid is not None and mid.points == pytest.approx(4.0, abs=0.1)
    moment = _climb(tracker, moment + _POLL, [95.0, 97.0, 99.0], energy=23.0, reference=_TRUE)
    event = _segment(tracker, moment, 99.0, 100.0, energy=23.0, reference=_TRUE)
    assert event is not None
    assert event.style == JUMP
    # Five points moved on 69 Wh: 3.5 unexplained, on top of the 4 already stored.
    assert event.points == pytest.approx(3.5, abs=0.1)
    assert tracker.to_dict()["throughput"] == 0.0


def test_a_hold_counts_from_where_the_reading_reached_99() -> None:
    """A counter that ran ahead mid-approach and then held is still a hold.

    Over the whole approach the two cancel; from the 99 entry the pack took
    five points of energy for one point of reading.
    """
    tracker = SocCorrectionTracker()
    levels = [85.0 + index for index in range(15)]
    moment = _climb(tracker, _T0, levels, energy=46.0 * 10.0 / 14.0, reference=_TRUE)
    moment = _hold_level(tracker, moment, 99.0, 10, 23.0)
    event = _segment(tracker, moment, 99.0, 100.0, reference=_TRUE)
    assert event is not None
    assert event.style == HOLD
    assert event.points == pytest.approx(-4.0, abs=0.15)


def test_an_honest_taper_that_feeds_the_unit_is_not_a_hold() -> None:
    """The last five points at 40 W reported, 15 W of it the unit's own draw."""
    tracker = SocCorrectionTracker()
    moment = _climb(
        tracker, _T0, [80.0 + index for index in range(16)], energy=46.0, reference=_TRUE
    )
    reported = 40.0 * _POLL / 3600.0
    polls = math.ceil(46.0 / (25.0 * _POLL / 3600.0))
    for level in range(95, 100):
        moment = _hold_level(tracker, moment, float(level), polls - 1, reported)
        event = _segment(tracker, moment, level, level + 1.0, energy=reported, reference=_TRUE)
        moment += _POLL
    assert event is not None
    assert (event.style, event.points) == (FULL, 0.0)
