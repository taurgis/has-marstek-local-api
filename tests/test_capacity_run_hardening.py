"""Premature-abort hardening, scored against a known pack.

The truth model is the estimator's 0.93 one-way factor at and above 150 W.
Below that, truth adds a 25 W inverter standby, so a long trickle is about
22% low if it is allowed into a window. Scenarios that would publish that
bias stay rejected. Scenarios that only bridge a short hole, a two-point
integer tick, or a brief same-sign dip must keep the true watt-hours.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

import pytest

from custom_components.marstek import capacity_estimator as estimator_module
from custom_components.marstek.capacity_estimator import (
    UsableCapacityEstimator,
    apply_capacity_estimate,
)

_ETA = 0.93
_STANDBY_W = 25.0
_T0 = 1_700_000_000.0
_VENUS_E_WH = 5_120.0
_VENUS_A_WH = 2_080.0

_Sample = tuple[float, float, float]


def _low_eta(power_w: float) -> float:
    """Return one-way efficiency once standby is a large share of the watts."""
    magnitude = abs(power_w)
    if magnitude < 1.0:
        return _ETA
    return magnitude / (magnitude / _ETA + _STANDBY_W)


def _advance(
    soc: float, power_w: float, rated: float, seconds: float, *, degrade_low: bool
) -> float:
    """Move state of charge by one piecewise-constant interval."""
    hours = seconds / 3600.0
    if power_w >= 0.0:
        eta = _low_eta(power_w) if degrade_low and power_w < 150.0 else _ETA
        return soc - (power_w / eta) * hours / rated * 100.0
    return soc + (abs(power_w) * _ETA) * hours / rated * 100.0


def _percent_seconds(rated: float, power_w: float, *, charge: bool) -> float:
    """Return the poll spacing that moves one reported point at ``power_w``."""
    watts = abs(power_w)
    if charge:
        return 0.01 * rated / (watts * _ETA) * 3600.0
    return 0.01 * rated * _ETA / watts * 3600.0


def _trace(
    *,
    rated: float,
    power: float,
    dt: float,
    polls: int,
    soc0: float,
    integer: bool = False,
    degrade_low: bool = False,
    dip_every: int | None = None,
    dip_count: int = 1,
    dip_w: float = 80.0,
) -> list[_Sample]:
    """Return ``(timestamp, soc, power)`` samples. Power is piecewise constant."""
    powers: list[float] = []
    for index in range(polls):
        watts = power
        if dip_every is not None and power > 0.0 and index % dip_every < dip_count:
            watts = dip_w
        powers.append(watts)
    soc = soc0
    moment = _T0
    samples: list[_Sample] = []
    for index, watts in enumerate(powers):
        shown = float(math.floor(soc + 1e-9)) if integer else soc
        samples.append((moment, min(100.0, max(0.0, shown)), watts))
        if index + 1 == len(powers):
            break
        soc = _advance(soc, watts, rated, dt, degrade_low=degrade_low)
        moment += dt
        if power >= 0.0 and soc <= 3.0:
            break
        if power < 0.0 and soc >= 98.0:
            break
    return samples


def _drive(
    samples: list[_Sample],
    rated: float,
    misses: set[int] | None = None,
) -> UsableCapacityEstimator:
    """Feed samples. Indexes in ``misses`` are ES.GetStatus timeouts."""
    estimator = UsableCapacityEstimator()
    skipped = misses or set()
    for index, (moment, soc, power) in enumerate(samples):
        if index in skipped:
            apply_capacity_estimate(
                estimator,
                {
                    "es_status_fresh": False,
                    "battery_soc": soc,
                    "battery_power": power,
                    "device_mode": "auto",
                    "bat_cap": rated,
                },
                moment,
            )
            continue
        estimator.observe(
            timestamp=moment,
            soc=soc,
            power_w=power,
            mode="auto",
            rated_wh=rated,
        )
    return estimator


def _windows(estimator: UsableCapacityEstimator) -> list[dict[str, object]]:
    """Return the accepted windows in order."""
    windows = estimator.to_dict()["windows"]
    assert isinstance(windows, list)
    return windows


def _steady_cases() -> Iterator[tuple[float, float, bool]]:
    """Pack, watts, and charge flag for the constant-power grid."""
    packs = (_VENUS_E_WH, _VENUS_A_WH)
    powers = (800.0, 1_250.0, 2_500.0)
    for rated in packs:
        for power in powers:
            yield rated, power, False
            yield rated, power, True


@pytest.mark.parametrize(("rated", "power", "charge"), list(_steady_cases()))
def test_constant_power_recovers_the_pack_or_the_gap_limit(
    rated: float, power: float, charge: bool
) -> None:
    """One-point steps at steady watts land on the pack, unless a step exceeds 180 s."""
    signed = -power if charge else power
    dt = _percent_seconds(rated, power, charge=charge)
    samples = _trace(rated=rated, power=signed, dt=dt, polls=80, soc0=15.0 if charge else 95.0)
    estimator = _drive(samples, rated)

    if dt > 180.0:
        assert estimator.windows_accepted == 0
        assert estimator.estimate_wh is None
        return
    assert estimator.estimate_wh == round(rated)
    assert estimator.attributes()["spread_wh"] == 0
    for window in _windows(estimator):
        assert window["capacity_wh"] == pytest.approx(rated)


@pytest.mark.parametrize(
    ("rated", "power", "dt"),
    [
        (_VENUS_E_WH, 800.0, 30.0),
        (_VENUS_E_WH, 1_250.0, 30.0),
        (_VENUS_E_WH, 2_500.0, 30.0),
        (_VENUS_A_WH, 800.0, 30.0),
        (_VENUS_A_WH, 1_250.0, 30.0),
        (_VENUS_A_WH, 2_200.0, 30.0),
        (_VENUS_A_WH, 2_200.0, 60.0),
        (_VENUS_A_WH, 2_500.0, 30.0),
    ],
)
def test_integer_soc_at_a_fixed_poll_stays_inside_two_percent(
    rated: float, power: float, dt: float
) -> None:
    """Whole-percent state of charge at 30 s or 60 s stays near the nameplate."""
    per_poll = (power / _ETA) * (dt / 3600.0) / rated * 100.0
    polls = int(75.0 / per_poll) + 8
    samples = _trace(rated=rated, power=power, dt=dt, polls=polls, soc0=98.0, integer=True)
    steps = [abs(samples[index + 1][1] - samples[index][1]) for index in range(len(samples) - 1)]
    estimator = _drive(samples, rated)

    assert estimator.estimate_wh is not None
    # 2500 W on the 2080 Wh pack moves more than one point per 30 s poll, so
    # the whole-percent grid itself is a little over 2%. The other cases are
    # inside 1%.
    tolerance = 0.03 if per_poll > 1.0 else 0.015
    assert estimator.estimate_wh == pytest.approx(rated, rel=tolerance)
    assert estimator.attributes()["spread_wh"] < 0.06 * rated
    if dt >= 60.0 or per_poll > 1.0:
        assert max(steps) >= 2.0


def test_a_missed_status_poll_keeps_the_run_and_a_long_outage_drops_it() -> None:
    """ES.GetStatus timeouts are holes. The gap limit still ends a real outage."""
    rated = _VENUS_E_WH
    samples = _trace(rated=rated, power=1_250.0, dt=30.0, polls=420, soc0=98.0)
    clean = _drive(samples, rated)
    assert clean.estimate_wh == round(rated)
    clean_start = _windows(clean)[0]["start_soc"]

    for misses in ({20}, {20, 21}, {20, 21, 22, 23, 24}):
        bridged = _drive(samples, rated, misses)
        assert bridged.estimate_wh == round(rated)
        assert _windows(bridged)[0]["start_soc"] == clean_start
        assert bridged.attributes()["spread_wh"] == 0

    outage = _drive(samples, rated, {20, 21, 22, 23, 24, 25})
    assert outage.estimate_wh == round(rated)
    assert _windows(outage)[0]["start_soc"] != clean_start

    prefix = UsableCapacityEstimator()
    for index, (moment, soc, power) in enumerate(samples[:27]):
        if 20 <= index <= 25:
            apply_capacity_estimate(
                prefix,
                {
                    "es_status_fresh": False,
                    "battery_soc": 0,
                    "battery_power": 9_000.0,
                    "device_mode": "auto",
                    "bat_cap": rated,
                },
                moment,
            )
            continue
        prefix.observe(timestamp=moment, soc=soc, power_w=power, mode="auto", rated_wh=rated)
    assert prefix.windows_accepted == 0
    assert prefix.attributes()["active_window"] is None


def test_a_stale_status_payload_does_not_discard_the_open_run() -> None:
    """One fresh=False poll leaves the in-progress run where it was."""
    rated = _VENUS_E_WH
    samples = _trace(rated=rated, power=1_250.0, dt=30.0, polls=40, soc0=90.0)
    estimator = _drive(samples[:16], rated)
    active = estimator.attributes()["active_window"]
    assert active is not None
    start = active["start_soc"]
    moment, _soc, power = samples[16]
    apply_capacity_estimate(
        estimator,
        {
            "es_status_fresh": False,
            "battery_soc": 5,
            "battery_power": power,
            "device_mode": "manual",
            "bat_cap": rated,
        },
        moment,
    )
    assert estimator.attributes()["active_window"]["start_soc"] == start
    later = samples[17]
    estimator.observe(
        timestamp=later[0], soc=later[1], power_w=later[2], mode="auto", rated_wh=rated
    )
    assert estimator.attributes()["active_window"]["start_soc"] == start
    assert estimator.windows_accepted == 0


def test_auto_dips_stay_accurate_and_a_trickle_is_not_published() -> None:
    """A same-sign dip under 150 W stays in the window. A trickle never starts one."""
    rated = _VENUS_E_WH
    swung = _household(rated)
    dipped = _drive(swung, rated)
    assert dipped.estimate_wh is not None
    assert dipped.estimate_wh == pytest.approx(rated, rel=0.015)
    assert dipped.windows_accepted >= 3

    quantized = _drive(_as_integer(swung), rated)
    assert quantized.estimate_wh is not None
    assert quantized.estimate_wh == pytest.approx(rated, rel=0.025)

    trickle = _drive(
        _trace(rated=rated, power=80.0, dt=30.0, polls=40, soc0=80.0, degrade_low=True),
        rated,
    )
    assert trickle.windows_accepted == 0
    assert trickle.estimate_wh is None


def test_lowering_the_power_floor_publishes_a_biased_trickle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """80 W through the 0.93 factor is about 22% low. The 150 W floor stays."""
    rated = _VENUS_A_WH
    samples = _trace(rated=rated, power=80.0, dt=60.0, polls=500, soc0=90.0, degrade_low=True)
    blocked = _drive(samples, rated)
    assert blocked.windows_accepted == 0

    monkeypatch.setattr(estimator_module, "MIN_POWER_W", 40.0)
    published = _drive(samples, rated)
    assert published.windows_accepted >= 1
    capacity = _windows(published)[0]["capacity_wh"]
    assert isinstance(capacity, float)
    assert capacity < 0.85 * rated
    assert capacity == pytest.approx(rated * (_low_eta(80.0) / _ETA), rel=0.02)


def test_one_low_poll_is_bridged_and_a_run_of_them_ends_the_window() -> None:
    """Two low segments stay. The third ends the run instead of biasing it."""
    rated = _VENUS_E_WH
    samples = _trace(rated=rated, power=1_250.0, dt=30.0, polls=30, soc0=90.0)
    held = _splice_power(samples, {15: 80.0})
    estimator = _drive(held, rated)
    assert estimator.attributes()["active_window"] is not None
    assert estimator.windows_accepted == 0

    moderate = _trace(rated=rated, power=200.0, dt=30.0, polls=30, soc0=90.0)
    one_dip = _drive(_splice_power(moderate, {15: 80.0}), rated)
    assert one_dip.attributes()["active_window"] is not None

    two_dips = _splice_power(moderate, {15: 80.0, 16: 80.0})
    ended = _drive(two_dips[:18], rated)
    assert ended.windows_accepted == 0
    assert ended.attributes()["active_window"] is None

    long_dip = _splice_power(samples, {15: 80.0, 16: 80.0, 17: 80.0, 18: 80.0})
    aborted = _drive(long_dip[:19], rated)
    assert aborted.windows_accepted == 0
    assert aborted.attributes()["active_window"] is None


def test_an_opposite_sign_blip_below_150_w_ends_the_window() -> None:
    """A reversal is not a pause, even when both ends are under the floor."""
    rated = _VENUS_E_WH
    samples = _trace(rated=rated, power=200.0, dt=30.0, polls=20, soc0=80.0)
    flipped = _splice_power(samples, {12: -80.0})
    estimator = _drive(flipped[:13], rated)
    assert estimator.windows_accepted == 0
    assert estimator.attributes()["active_window"] is None


def test_a_two_point_tick_at_the_floor_keeps_an_exact_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step equal to the two-point floor is quantization. Three points is a jump.

    The tick is one poll early and the next poll repeats that integer, so the
    window's endpoints and energy are unchanged. ``>=`` used to abort it.
    """
    rated = _VENUS_E_WH
    samples = _exact(rated, 1_250.0, 80, soc0=90.0)
    moment, soc, power = samples[10]
    samples[10] = (moment, soc - 1.0, power)
    assert abs(samples[10][1] - samples[9][1]) == 2.0
    assert samples[11][1] == samples[10][1]

    kept = _drive(samples, rated)
    assert kept.estimate_wh == round(rated)
    assert _windows(kept)[0]["start_soc"] == 89.0
    assert _windows(kept)[0]["capacity_wh"] == pytest.approx(rated)
    assert kept.attributes()["spread_wh"] == 0

    def _exclusive(
        earlier: estimator_module._Sample,
        later: estimator_module._Sample,
        rated_wh: float | None,
    ) -> bool:
        limit = estimator_module._soc_step_limit(earlier, later, rated_wh)
        return abs(later.soc - earlier.soc) >= limit

    monkeypatch.setattr(estimator_module, "_soc_jumped", _exclusive)
    dropped = _drive(samples, rated)
    assert dropped.estimate_wh == round(rated)
    # The aborted run is gone. The replacement starts on the next real step.
    assert _windows(dropped)[0]["start_soc"] == 78.0


@pytest.mark.parametrize("jump", [3.0, 5.0, 15.0])
def test_a_bms_calibration_jump_is_not_a_window(jump: float) -> None:
    """Three, five, and fifteen points in one poll never become a capacity run."""
    rated = _VENUS_E_WH
    samples = _exact(rated, 1_250.0, 14, soc0=90.0)
    estimator = _drive(samples, rated)
    assert estimator.windows_accepted == 0
    assert estimator.attributes()["active_window"]["start_soc"] == 89.0
    moment, soc, power = samples[-1]
    estimator.observe(
        timestamp=moment + _percent_seconds(rated, 1_250.0, charge=False),
        soc=soc - jump,
        power_w=power,
        mode="auto",
        rated_wh=rated,
    )
    assert estimator.windows_accepted == 0
    assert estimator.attributes()["active_window"] is None


def test_a_jump_from_85_to_100_does_not_enter_the_run() -> None:
    """The calibration step the dashboard would show as a full battery is dropped."""
    rated = _VENUS_E_WH
    samples = _exact(rated, 1_250.0, 5, soc0=90.0)
    assert samples[-1][1] == 85.0
    estimator = _drive(samples, rated)
    moment, _soc, power = samples[-1]
    estimator.observe(
        timestamp=moment + 30.0,
        soc=100.0,
        power_w=power,
        mode="auto",
        rated_wh=rated,
    )
    assert estimator.windows_accepted == 0
    assert estimator.attributes()["active_window"] is None


def test_repeated_unexplained_two_point_steps_are_rejected() -> None:
    """A 2-point tick every 30 s at 1250 W is a tenth of the nameplate, so it is dropped."""
    rated = _VENUS_E_WH
    samples = [
        (_T0 + index * 30.0, 90.0 - index * 2.0, 1_250.0)
        for index in range(31)
        if 90.0 - index * 2.0 >= 20.0
    ]
    estimator = _drive(samples, rated)
    assert estimator.windows_accepted == 0
    assert estimator.estimate_wh is None


def test_back_to_back_windows_chain_through_one_discharge() -> None:
    """90 → 70 → 50 → 30 is three runs, and the next one is already open."""
    rated = _VENUS_E_WH
    samples = _exact(rated, 1_250.0, 70, soc0=90.0)
    estimator = _drive(samples, rated)
    ends = [(window["start_soc"], window["end_soc"]) for window in _windows(estimator)[:3]]
    assert ends == [(89.0, 69.0), (69.0, 49.0), (49.0, 29.0)]
    for window in _windows(estimator)[:3]:
        assert window["capacity_wh"] == pytest.approx(rated)
    assert estimator.estimate_wh == round(rated)
    active = estimator.attributes()["active_window"]
    assert active is not None
    assert active["start_soc"] == 29.0
    assert 0.0 < active["soc_delta"] < 20.0


def test_realistic_integer_traces_do_not_change_when_the_floor_is_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """High-power two-point ticks are already inside the watt budget. ``>`` and ``>=`` agree."""
    cases = (
        (_VENUS_E_WH, 1_250.0, 30.0),
        (_VENUS_A_WH, 2_200.0, 60.0),
        (_VENUS_A_WH, 2_200.0, 30.0),
    )
    inclusive: list[int | None] = []
    for rated, power, dt in cases:
        per_poll = (power / _ETA) * (dt / 3600.0) / rated * 100.0
        polls = int(75.0 / per_poll) + 8
        samples = _trace(rated=rated, power=power, dt=dt, polls=polls, soc0=98.0, integer=True)
        inclusive.append(_drive(samples, rated).estimate_wh)

    def _exclusive(
        earlier: estimator_module._Sample,
        later: estimator_module._Sample,
        rated_wh: float | None,
    ) -> bool:
        limit = estimator_module._soc_step_limit(earlier, later, rated_wh)
        return abs(later.soc - earlier.soc) >= limit

    monkeypatch.setattr(estimator_module, "_soc_jumped", _exclusive)
    exclusive: list[int | None] = []
    for rated, power, dt in cases:
        per_poll = (power / _ETA) * (dt / 3600.0) / rated * 100.0
        polls = int(75.0 / per_poll) + 8
        samples = _trace(rated=rated, power=power, dt=dt, polls=polls, soc0=98.0, integer=True)
        exclusive.append(_drive(samples, rated).estimate_wh)
    assert exclusive == inclusive
    assert all(value is not None for value in inclusive)


def _household(rated: float) -> list[_Sample]:
    """Return an Auto discharge that swings from 200 W to 2000 W and dips to 80 W."""
    samples: list[_Sample] = []
    soc = 98.0
    moment = _T0
    for index in range(460):
        swing = 200.0 + 1_800.0 * (0.5 + 0.5 * math.sin(index / 3.0))
        watts = 80.0 if index % 10 == 0 else swing
        samples.append((moment, soc, watts))
        soc = _advance(soc, watts, rated, 30.0, degrade_low=True)
        moment += 30.0
        if soc <= 3.0:
            break
    return samples


def _as_integer(samples: list[_Sample]) -> list[_Sample]:
    """Floor each sample's state of charge to a reported whole percent."""
    return [(moment, float(math.floor(soc + 1e-9)), power) for moment, soc, power in samples]


def _exact(rated: float, power: float, points: int, *, soc0: float) -> list[_Sample]:
    """Return one-point discharge steps whose energy is exactly the pack."""
    dt = _percent_seconds(rated, power, charge=False)
    return [(_T0 + index * dt, soc0 - index, power) for index in range(points + 1)]


def _splice_power(samples: list[_Sample], overrides: dict[int, float]) -> list[_Sample]:
    """Return a copy with battery watts replaced at the given indexes."""
    spliced = list(samples)
    for index, power in overrides.items():
        moment, soc, _power = spliced[index]
        spliced[index] = (moment, soc, power)
    return spliced
