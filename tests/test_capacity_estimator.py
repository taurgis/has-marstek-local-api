"""Usable-capacity estimator: windows, noise, gaps, restarts, and nameplates."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from custom_components.marstek.capacity_estimator import (
    MAX_WINDOW_SAMPLES,
    MAX_WINDOWS,
    MIN_WINDOWS,
    UsableCapacityEstimator,
    apply_capacity_estimate,
    gap_limit_seconds,
)
from custom_components.marstek.pymarstek.data_parser import merge_device_status

# Written here, not imported from the estimator. A trace built with this
# one-way factor must come back as the true DC capacity; if the estimator
# stops using 0.93 these literals fail.
_ONE_WAY = 0.93
_TRUE_WH = 4600.0
_RATED_WH = 5120.0
_POWER_W = 2000.0
_T0 = 1_700_000_000.0


def _discharge_step_seconds() -> float:
    """Seconds for one state-of-charge point at the hand-worked discharge."""
    dc_wh = 0.20 * _TRUE_WH
    ac_wh = dc_wh * _ONE_WAY
    return (ac_wh / _POWER_W) * 3600.0 / 20.0


def _charge_step_seconds() -> float:
    """Seconds for one state-of-charge point while charging.

    Stored DC watt-hours are the AC integral times the one-way factor, so the
    port has to draw more than the pack stores.
    """
    dc_wh = 0.20 * _TRUE_WH
    ac_wh = dc_wh / _ONE_WAY
    return (ac_wh / _POWER_W) * 3600.0 / 20.0


@dataclass(frozen=True)
class _Trace:
    """One synthetic charge or discharge fed to the estimator."""

    steps: int
    soc_start: float
    soc_per_step: float
    step_seconds: float
    power_w: float
    rated_wh: float | None = _RATED_WH
    mode: str = "auto"
    power_scale: float = 1.0
    gap_at: int | None = None
    gap_seconds: float = 0.0
    mode_at: tuple[int, str] | None = None
    integer_soc: bool = False
    start_timestamp: float = _T0
    pv_w: float | None = 0.0
    power_derived: bool = False


def _feed(estimator: UsableCapacityEstimator, trace: _Trace) -> None:
    """Feed a straight state-of-charge ramp. ``steps`` is the number of intervals."""
    moment = trace.start_timestamp
    for index in range(trace.steps + 1):
        if trace.gap_at is not None and index == trace.gap_at:
            moment += trace.gap_seconds
        level = trace.soc_start + index * trace.soc_per_step
        if trace.integer_soc:
            level = float(int(level))
        current_mode = trace.mode
        if trace.mode_at is not None and index >= trace.mode_at[0]:
            current_mode = trace.mode_at[1]
        scale = trace.power_scale
        if trace.power_scale != 1.0 and index % 2 == 0:
            scale = 2.0 - trace.power_scale
        estimator.observe(
            timestamp=moment,
            soc=level,
            power_w=trace.power_w * scale,
            mode=current_mode,
            rated_wh=trace.rated_wh,
            pv_w=trace.pv_w,
            power_derived=trace.power_derived,
        )
        moment += trace.step_seconds


def test_three_clean_discharge_windows_publish_the_true_capacity() -> None:
    """920 Wh DC over 20 points, delivered at 93% to a 2000 W port, is 4600 Wh."""
    estimator = UsableCapacityEstimator()
    assert estimator.estimate_wh is None
    assert estimator.attributes()["confidence"] == "collecting"

    _feed(
        estimator,
        _Trace(
            steps=61,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=_discharge_step_seconds(),
            power_w=_POWER_W,
        ),
    )

    assert estimator.windows_accepted == MIN_WINDOWS
    assert estimator.estimate_wh == 4600
    assert estimator.state_of_health == pytest.approx(89.8, abs=0.05)
    assert estimator.attributes()["confidence"] == "low"
    assert estimator.attributes()["last_window_direction"] == "discharge"
    assert estimator.attributes()["method"] == "soc_energy_window"
    assert estimator.attributes()["efficiency_correction"] == "discharge_median"
    assert estimator.attributes()["discharge_median_wh"] == 4600
    assert "charge_median_wh" not in estimator.attributes()
    assert "implied_round_trip_efficiency" not in estimator.attributes()
    window = estimator.to_dict()["windows"][0]
    assert window["start_soc"] == 79.0
    assert window["end_soc"] == 59.0
    assert window["average_power_w"] == pytest.approx(_POWER_W)
    assert window["duration_s"] == pytest.approx(20.0 * _discharge_step_seconds())
    assert window["mode"] == "auto"
    assert window["pv_share"] == 0.0


def test_charge_windows_use_the_other_efficiency_and_agree() -> None:
    """Charging stores 93% of the AC draw, and the same pack still reads 4600 Wh."""
    estimator = UsableCapacityEstimator()
    _feed(
        estimator,
        _Trace(
            steps=61,
            soc_start=20.0,
            soc_per_step=1.0,
            step_seconds=_charge_step_seconds(),
            power_w=-_POWER_W,
        ),
    )

    assert estimator.estimate_wh == 4600
    assert estimator.attributes()["last_window_direction"] == "charge"
    assert estimator.attributes()["efficiency_correction"] == "charge_median"
    assert estimator.attributes()["charge_median_wh"] == 4600


def test_geometric_mean_cancels_a_shared_efficiency_error() -> None:
    """Charge and discharge medians disagree when 0.93 is wrong; their geometric mean does not.

    The trace is integrated with a one-way factor of 0.88. The estimator still
    divides and multiplies by 0.93. Each direction is biased, and the biases
    are reciprocals, so the geometric mean is the true 4600 Wh.
    """
    true_eta = 0.88
    dc_wh = 0.20 * _TRUE_WH
    discharge_step = ((dc_wh * true_eta) / _POWER_W) * 3600.0 / 20.0
    charge_step = ((dc_wh / true_eta) / _POWER_W) * 3600.0 / 20.0
    estimator = UsableCapacityEstimator()
    moment = _T0
    _feed(
        estimator,
        _Trace(
            steps=61,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=discharge_step,
            power_w=_POWER_W,
            start_timestamp=moment,
        ),
    )
    biased = _TRUE_WH * true_eta / _ONE_WAY
    assert estimator.estimate_wh == pytest.approx(biased, rel=0.01)
    assert estimator.attributes()["efficiency_correction"] == "discharge_median"

    moment += 62 * discharge_step + 1.0
    _feed(
        estimator,
        _Trace(
            steps=61,
            soc_start=20.0,
            soc_per_step=1.0,
            step_seconds=charge_step,
            power_w=-_POWER_W,
            start_timestamp=moment,
        ),
    )

    assert estimator.estimate_wh == pytest.approx(_TRUE_WH, rel=0.01)
    assert estimator.attributes()["efficiency_correction"] == "charge_discharge_geometric_mean"
    assert estimator.attributes()["spread_wh"] > 0
    # eta^2 * C_d / C_c recovers the true round trip when both paths use 0.93.
    assert estimator.attributes()["implied_round_trip_efficiency"] == pytest.approx(
        true_eta**2, abs=0.01
    )


def test_two_windows_stay_unknown() -> None:
    """One or two windows are not enough to publish a number."""
    estimator = UsableCapacityEstimator()
    _feed(
        estimator,
        _Trace(
            steps=41,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=_discharge_step_seconds(),
            power_w=_POWER_W,
        ),
    )

    assert estimator.windows_accepted == 2
    assert estimator.estimate_wh is None
    assert estimator.state_of_health is None


def test_alternating_power_noise_stays_near_the_true_capacity() -> None:
    """±10% wattage that averages out does not move the median off the pack."""
    estimator = UsableCapacityEstimator()
    _feed(
        estimator,
        _Trace(
            steps=61,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=_discharge_step_seconds(),
            power_w=_POWER_W,
            power_scale=1.1,
        ),
    )

    assert estimator.estimate_wh == pytest.approx(4600, rel=0.03)


def test_integer_soc_quantization_stays_within_a_few_percent() -> None:
    """Whole-percent readings: windows run from one step to another."""
    estimator = UsableCapacityEstimator()
    point_seconds = _discharge_step_seconds()
    _feed(
        estimator,
        _Trace(
            steps=155,
            soc_start=90.0,
            soc_per_step=-0.4,
            step_seconds=point_seconds * 0.4,
            power_w=_POWER_W,
            integer_soc=True,
        ),
    )

    # Each point is 2.5 polls, so both ends sit within 0.4 point of a step.
    assert estimator.estimate_wh == pytest.approx(4600, rel=0.02)


@pytest.mark.parametrize("phase", [0.05, 0.35, 0.65, 0.95])
def test_a_single_short_window_starts_on_a_state_of_charge_step(phase: float) -> None:
    """A 23-point evening discharge is one window, and it is not a point off.

    The first poll can sit anywhere inside a whole percent. Starting the
    window there would make one 20-point window up to 5% wrong. The window
    starts on the first poll after the percentage changes instead, so the
    error is the state of charge one poll moves, here 0.25 point.
    """
    estimator = UsableCapacityEstimator()
    point_seconds = _discharge_step_seconds()
    _feed(
        estimator,
        _Trace(
            steps=92,
            soc_start=80.0 - phase,
            soc_per_step=-0.25,
            step_seconds=point_seconds * 0.25,
            power_w=_POWER_W,
            integer_soc=True,
        ),
    )

    assert estimator.windows_accepted == 1
    window = estimator.to_dict()["windows"][0]
    assert window["soc_delta"] == 20.0
    assert window["capacity_wh"] == pytest.approx(_TRUE_WH, rel=0.0125)


def test_a_long_gap_discards_the_open_window() -> None:
    """A multi-minute hole makes the integral unusable, so nothing is published."""
    estimator = UsableCapacityEstimator()
    _feed(
        estimator,
        _Trace(
            steps=24,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=_discharge_step_seconds(),
            power_w=_POWER_W,
            gap_at=10,
            gap_seconds=400.0,
        ),
    )

    assert estimator.windows_accepted == 0
    assert estimator.estimate_wh is None


def test_a_mode_change_before_the_soc_delta_discards_the_window() -> None:
    """Switching mode mid-window throws that stretch away."""
    estimator = UsableCapacityEstimator()
    _feed(
        estimator,
        _Trace(
            steps=15,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=_discharge_step_seconds(),
            power_w=_POWER_W,
            mode_at=(8, "passive"),
        ),
    )

    assert estimator.windows_accepted == 0
    assert estimator.estimate_wh is None


def test_an_implausible_window_is_rejected_and_the_median_holds() -> None:
    """A state-of-charge jump with almost no energy must not enter the median."""
    estimator = UsableCapacityEstimator()
    _feed(
        estimator,
        _Trace(
            steps=61,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=_discharge_step_seconds(),
            power_w=_POWER_W,
        ),
    )
    assert estimator.estimate_wh == 4600

    _feed(
        estimator,
        _Trace(
            steps=21,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=_discharge_step_seconds(),
            power_w=200.0,
            start_timestamp=_T0 + 100_000.0,
        ),
    )

    assert estimator.windows_accepted == MIN_WINDOWS
    assert estimator.estimate_wh == 4600


def test_restart_restores_the_median_and_a_new_window_can_join() -> None:
    """Accepted windows survive a process restart. The open window does not."""
    original = UsableCapacityEstimator()
    _feed(
        original,
        _Trace(
            steps=61,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=_discharge_step_seconds(),
            power_w=_POWER_W,
        ),
    )
    restored = UsableCapacityEstimator.from_dict(original.to_dict())

    assert restored.estimate_wh == 4600
    assert restored.windows_accepted == MIN_WINDOWS

    blank = UsableCapacityEstimator.from_dict({"windows": "nope"})
    assert blank.estimate_wh is None
    assert UsableCapacityEstimator.from_dict(None).estimate_wh is None


def test_a_smaller_nameplate_scales_state_of_health() -> None:
    """Venus A is 2080 Wh. A pack whose physics are 1800 Wh reads about 86.5%."""
    true_wh = 1800.0
    rated = 2080.0
    dc_wh = 0.20 * true_wh
    step = ((dc_wh * _ONE_WAY) / _POWER_W) * 3600.0 / 20.0
    estimator = UsableCapacityEstimator()
    _feed(
        estimator,
        _Trace(
            steps=61,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=step,
            power_w=_POWER_W,
            rated_wh=rated,
        ),
    )

    assert estimator.estimate_wh == 1800
    assert estimator.state_of_health == pytest.approx(86.5, abs=0.05)
    assert estimator.attributes()["rated_capacity_wh"] == 2080


def test_missing_power_or_soc_never_invents_a_number() -> None:
    """Firmware that omits the fields the window needs leaves the estimate unknown."""
    estimator = UsableCapacityEstimator()
    for _ in range(10):
        estimator.observe(timestamp=_T0, soc=50.0, power_w=None, mode="auto", rated_wh=_RATED_WH)
        estimator.observe(timestamp=_T0 + 30, soc=None, power_w=2000.0, mode="auto", rated_wh=None)

    assert estimator.estimate_wh is None
    status: dict[str, object] = {"device_mode": "auto"}
    apply_capacity_estimate(estimator, status, _T0 + 60)
    assert status["battery_usable_capacity"] is None
    assert status["battery_state_of_health"] is None
    assert status["battery_usable_capacity_attributes"]["windows_accepted"] == 0


def test_bad_samples_and_persisted_junk_are_ignored() -> None:
    """NaN, impossible SOC, absurd watts, and a broken store must not publish."""
    estimator = UsableCapacityEstimator()
    estimator.observe(timestamp=_T0, soc=float("nan"), power_w=2000.0, mode="auto", rated_wh=None)
    estimator.observe(timestamp=_T0 + 30, soc=150.0, power_w=2000.0, mode="auto", rated_wh=None)
    estimator.observe(timestamp=_T0 + 60, soc=50.0, power_w=50_000.0, mode="auto", rated_wh=None)
    estimator.observe(timestamp=_T0 + 90, soc=50.0, power_w=2000.0, mode="auto", rated_wh=None)
    estimator.observe(timestamp=_T0 + 60, soc=49.0, power_w=2000.0, mode="auto", rated_wh=None)

    assert estimator.estimate_wh is None

    restored = UsableCapacityEstimator.from_dict(
        {
            "windows": [
                "nope",
                {"direction": "charge", "soc_delta": 20, "closed_at": 1},
                {"capacity_wh": 4600, "direction": "idle", "soc_delta": 20, "closed_at": 1},
                {"capacity_wh": 10, "direction": "charge", "soc_delta": 20, "closed_at": 1},
                {"capacity_wh": 4600, "direction": "charge", "soc_delta": 5, "closed_at": 1},
            ]
        }
    )
    assert restored.windows_accepted == 0


def test_soc_that_moves_against_the_power_sign_is_rejected() -> None:
    """A discharge reading while state of charge rises is not a capacity window."""
    estimator = UsableCapacityEstimator()
    _feed(
        estimator,
        _Trace(
            steps=25,
            soc_start=40.0,
            soc_per_step=1.0,
            step_seconds=_discharge_step_seconds(),
            power_w=_POWER_W,
        ),
    )

    assert estimator.windows_accepted == 0
    assert estimator.estimate_wh is None


def test_a_window_outside_absolute_bounds_is_dropped_without_a_nameplate() -> None:
    """Without bat_cap, a few hundred watt-hours is still not a Marstek pack."""
    estimator = UsableCapacityEstimator()
    _feed(
        estimator,
        _Trace(
            steps=25,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=16.0,
            power_w=200.0,
            rated_wh=None,
        ),
    )

    assert estimator.windows_accepted == 0


def test_a_missing_mode_does_not_split_the_window() -> None:
    """One poll that omits ES.GetMode is not treated as a mode change."""
    estimator = UsableCapacityEstimator()
    step = _discharge_step_seconds()
    moment = _T0
    for index in range(62):
        estimator.observe(
            timestamp=moment,
            soc=80.0 - index,
            power_w=_POWER_W,
            mode=None if index == 7 else "auto",
            rated_wh=_RATED_WH,
        )
        moment += step

    assert estimator.estimate_wh == 4600


def test_the_median_keeps_the_newest_windows_and_confidence_rises() -> None:
    """More than 21 windows slide, and twelve or more are labeled high."""
    estimator = UsableCapacityEstimator()
    moment = _T0
    discharge = _discharge_step_seconds()
    charge = _charge_step_seconds()
    for lap in range(4):
        _feed(
            estimator,
            _Trace(
                steps=61,
                soc_start=90.0,
                soc_per_step=-1.0,
                step_seconds=discharge,
                power_w=_POWER_W,
                start_timestamp=moment,
            ),
        )
        moment += 62 * discharge + 1.0
        _feed(
            estimator,
            _Trace(
                steps=61,
                soc_start=30.0,
                soc_per_step=1.0,
                step_seconds=charge,
                power_w=-_POWER_W,
                start_timestamp=moment,
            ),
        )
        moment += 62 * charge + 1.0
        if lap == 0:
            assert estimator.windows_accepted == 6
            assert estimator.attributes()["confidence"] == "medium"

    assert estimator.windows_accepted == 24
    assert estimator.attributes()["confidence"] == "high"
    assert estimator.estimate_wh == 4600
    assert estimator.attributes()["efficiency_correction"] == "charge_discharge_geometric_mean"


def test_an_open_window_that_never_moves_enough_is_capped() -> None:
    """A huge idle-ish trace cannot grow the open window without bound."""
    estimator = UsableCapacityEstimator()
    _feed(
        estimator,
        _Trace(
            steps=MAX_WINDOW_SAMPLES + 5,
            soc_start=50.0,
            soc_per_step=-0.001,
            step_seconds=30.0,
            power_w=_POWER_W,
        ),
    )

    assert estimator.windows_accepted == 0
    assert estimator.estimate_wh is None


def test_carried_power_without_es_get_status_is_not_integrated() -> None:
    """A timed-out ES.GetStatus must not pair old watts with a new state of charge."""
    estimator = UsableCapacityEstimator()
    step = _discharge_step_seconds()
    _feed(
        estimator,
        _Trace(
            steps=10,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=step,
            power_w=_POWER_W,
        ),
    )
    merged = merge_device_status(
        es_mode_data={"battery_soc": 40, "device_mode": "auto"},
        previous_status={
            "battery_soc": 70,
            "battery_power": _POWER_W,
            "device_mode": "auto",
            "bat_cap": _RATED_WH,
            "es_status_fresh": True,
        },
    )
    assert merged["es_status_fresh"] is False
    assert merged["battery_power"] == _POWER_W
    assert merged["battery_soc"] == 40
    apply_capacity_estimate(estimator, merged, _T0 + 10 * step + 30.0)

    _feed(
        estimator,
        _Trace(
            steps=61,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=step,
            power_w=_POWER_W,
            start_timestamp=_T0 + 10 * step + 60.0,
        ),
    )

    assert estimator.windows_accepted == MIN_WINDOWS
    assert estimator.estimate_wh == 4600

    fresh = merge_device_status(
        es_status_data={
            "battery_soc": 70,
            "battery_power": _POWER_W,
            "bat_cap": _RATED_WH,
        },
        previous_status={"es_status_fresh": False, "battery_power": 1},
    )
    assert fresh["es_status_fresh"] is True
    assert fresh["battery_power"] == _POWER_W


def test_each_direction_keeps_its_newest_windows_including_a_long_store() -> None:
    """One long discharge cannot evict charge windows, including after a restart."""
    estimator = UsableCapacityEstimator()
    moment = _T0
    charge_power = _POWER_W * 4_000.0 / _TRUE_WH
    for _ in range(3):
        _feed(
            estimator,
            _Trace(
                steps=21,
                soc_start=30.0,
                soc_per_step=1.0,
                step_seconds=_charge_step_seconds(),
                power_w=-charge_power,
                start_timestamp=moment,
            ),
        )
        moment += 22 * _charge_step_seconds() + 1_000.0
    for _ in range(MAX_WINDOWS + 1):
        _feed(
            estimator,
            _Trace(
                steps=21,
                soc_start=90.0,
                soc_per_step=-1.0,
                step_seconds=_discharge_step_seconds(),
                power_w=_POWER_W,
                start_timestamp=moment,
            ),
        )
        moment += 22 * _discharge_step_seconds() + 1_000.0

    assert estimator.windows_accepted == MAX_WINDOWS + 3
    assert estimator.attributes()["efficiency_correction"] == "charge_discharge_geometric_mean"
    assert estimator.estimate_wh == round((4_600.0 * 4_000.0) ** 0.5)

    stored = {
        "windows": [
            {
                "capacity_wh": 4_000.0,
                "direction": "charge",
                "soc_delta": 20.0,
                "closed_at": float(index),
            }
            for index in range(3)
        ]
        + [
            {
                "capacity_wh": 4_600.0,
                "direction": "discharge",
                "soc_delta": 20.0,
                "closed_at": float(100 + index),
            }
            for index in range(MAX_WINDOWS + 4)
        ]
    }
    restored = UsableCapacityEstimator.from_dict(stored)
    assert restored.windows_accepted == MAX_WINDOWS + 3
    assert restored.attributes()["efficiency_correction"] == "charge_discharge_geometric_mean"
    saved = restored.to_dict()
    assert list(saved) == ["windows"]
    assert saved["windows"][0]["direction"] == "charge"
    assert saved["windows"][-1]["closed_at"] == float(100 + MAX_WINDOWS + 3)


def test_a_slow_poll_interval_can_form_a_window_and_a_missed_poll_cannot() -> None:
    """300 seconds is a legal fast interval. A missed cycle at that pace is not."""
    assert gap_limit_seconds(30) == 180.0
    assert gap_limit_seconds(300) == 450.0
    assert gap_limit_seconds(None) == 180.0
    assert gap_limit_seconds(0) == 180.0

    # 400s at this wattage moves one state-of-charge point on a 4600 Wh pack.
    slow_power = _TRUE_WH * _ONE_WAY * 0.09
    slow = UsableCapacityEstimator()
    slow.set_poll_interval(300)
    _feed(
        slow,
        _Trace(
            steps=61,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=400.0,
            power_w=slow_power,
        ),
    )
    assert slow.windows_accepted == MIN_WINDOWS
    assert slow.estimate_wh == 4600

    default = UsableCapacityEstimator()
    _feed(
        default,
        _Trace(
            steps=61,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=400.0,
            power_w=slow_power,
        ),
    )
    assert default.windows_accepted == 0

    # 400s plus a short pause is still one poll. 400s plus 700s is a missed cycle.
    pause = UsableCapacityEstimator()
    pause.set_poll_interval(300)
    _feed(
        pause,
        _Trace(
            steps=24,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=400.0,
            power_w=slow_power,
            gap_at=10,
            gap_seconds=40.0,
        ),
    )
    assert pause.windows_accepted == 1

    missed = UsableCapacityEstimator()
    missed.set_poll_interval(300)
    _feed(
        missed,
        _Trace(
            steps=24,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=400.0,
            power_w=slow_power,
            gap_at=10,
            gap_seconds=700.0,
        ),
    )
    assert missed.windows_accepted == 0
