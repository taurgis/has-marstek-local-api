"""Weekly capacity snapshots survive restarts and ignore unreadable points."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from custom_components.marstek.capacity_estimator import (
    UsableCapacityEstimator,
    apply_capacity_estimate,
)
from custom_components.marstek.helpers.capacity_history import MAX_HISTORY_POINTS

_ONE_WAY = 0.93
_TRUE_WH = 4600.0
_RATED_WH = 5120.0
_POWER_W = 2000.0
_T0 = 1_700_000_000.0
_WEEK = 7 * 24 * 3600


def _discharge_step_seconds() -> float:
    """Seconds for one state-of-charge point at the hand-worked discharge."""
    ac_wh = 0.20 * _TRUE_WH * _ONE_WAY
    return (ac_wh / _POWER_W) * 3600.0 / 20.0


def _charge_step_seconds() -> float:
    """Seconds for one state-of-charge point while charging."""
    ac_wh = 0.20 * _TRUE_WH / _ONE_WAY
    return (ac_wh / _POWER_W) * 3600.0 / 20.0


@dataclass(frozen=True)
class _Trace:
    """One synthetic ramp fed straight to the estimator."""

    steps: int
    soc_start: float
    soc_per_step: float
    step_seconds: float
    power_w: float
    pv_w: float = 0.0
    power_derived: bool = False


def _feed(estimator: UsableCapacityEstimator, trace: _Trace) -> None:
    """Feed a straight state-of-charge ramp."""
    moment = _T0
    for index in range(trace.steps + 1):
        estimator.observe(
            timestamp=moment,
            soc=trace.soc_start + index * trace.soc_per_step,
            power_w=trace.power_w,
            mode="auto",
            rated_wh=_RATED_WH,
            pv_w=trace.pv_w,
            power_derived=trace.power_derived,
        )
        moment += trace.step_seconds


def test_open_window_carries_average_power_and_pv_share() -> None:
    """A derived charge reports PV share before the 20-point window closes."""
    estimator = UsableCapacityEstimator()
    _feed(
        estimator,
        _Trace(
            steps=8,
            soc_start=40.0,
            soc_per_step=1.0,
            step_seconds=_charge_step_seconds(),
            power_w=-_POWER_W,
            pv_w=_POWER_W,
            power_derived=True,
        ),
    )

    active = estimator.attributes()["active_window"]
    assert active is not None
    assert active["direction"] == "charge"
    assert active["average_power_w"] == pytest.approx(-_POWER_W)
    assert active["pv_share"] == pytest.approx(1.0)
    assert active["start_soc"] < active["current_soc"]
    assert active["accumulated_wh"] > 0.0


def test_weekly_history_records_capacity_energy_and_restores() -> None:
    """One point per ISO week keeps medians, cycles, and lifetime energy."""
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
    assert estimator.consume_dirty() is True

    first = estimator.attributes()["history"]
    assert len(first) == 1
    assert first[0]["usable_wh"] == 4600
    assert first[0]["discharge_median_wh"] == 4600
    assert "charge_median_wh" not in first[0]
    assert first[0]["period"].startswith("2023-W")
    assert first[0]["month"] == "2023-11"
    assert "implied_round_trip_efficiency" not in first[0]

    totals = {
        "total_grid_output_energy": 40.0,
        "total_grid_input_energy": 10.0,
        "total_pv_energy": 5.0,
        "total_load_energy": 8.0,
    }
    estimator.note_lifetime(_T0 + 100.0, totals)
    assert estimator.consume_dirty() is True
    recorded = estimator.attributes()["history"][0]
    assert recorded["total_grid_output_energy"] == 40.0
    assert recorded["equivalent_cycles"] == pytest.approx(40.0 / _RATED_WH, abs=0.001)

    totals["total_grid_output_energy"] = 70.0
    estimator.note_lifetime(_T0 + 200.0, totals)
    assert estimator.consume_dirty() is False
    assert len(estimator.attributes()["history"]) == 1

    later = _T0 + _WEEK + 3_600.0
    apply_capacity_estimate(
        estimator,
        {
            "battery_soc": 50,
            "battery_power": 0,
            "device_mode": "auto",
            "bat_cap": _RATED_WH,
            "total_grid_output_energy": _RATED_WH,
            "total_grid_input_energy": 1_200.0,
            "total_pv_energy": 800.0,
            "total_load_energy": 900.0,
        },
        later,
    )
    assert estimator.consume_dirty() is True
    history = estimator.attributes()["history"]
    assert len(history) == 2
    assert history[1]["month"] == "2023-11"
    assert history[1]["usable_wh"] == 4600
    assert history[1]["equivalent_cycles"] == pytest.approx(1.0)
    assert history[1]["total_pv_energy"] == 800.0
    assert history[1]["total_load_energy"] == 900.0
    assert history[0]["period"] != history[1]["period"]

    restored = UsableCapacityEstimator.from_dict(estimator.to_dict())
    assert restored.to_dict()["history"] == estimator.to_dict()["history"]
    assert restored.estimate_wh == 4600

    legacy = UsableCapacityEstimator.from_dict({"windows": estimator.to_dict()["windows"]})
    assert legacy.estimate_wh == 4600
    assert legacy.attributes()["history"] == []

    junk = UsableCapacityEstimator.from_dict(
        {
            "windows": [],
            "history": ["nope", {"period": "2020-W01"}, {"recorded_at": 10, "usable_wh": 1}],
        }
    )
    assert junk.attributes()["history"] == []


def test_history_keeps_the_newest_three_years() -> None:
    """A store older than the cap restores the latest 156 weekly points."""
    points = [
        {
            "period": f"P{index:04d}",
            "recorded_at": 1_600_000_000.0 + index * _WEEK,
            "usable_wh": 4000 + index,
            "month": "2020-09",
        }
        for index in range(MAX_HISTORY_POINTS + 20)
    ]
    restored = UsableCapacityEstimator.from_dict({"history": points})
    history = restored.attributes()["history"]
    assert len(history) == MAX_HISTORY_POINTS
    assert history[-1]["usable_wh"] == 4000 + MAX_HISTORY_POINTS + 19
    assert history[0]["usable_wh"] == 4000 + 20
