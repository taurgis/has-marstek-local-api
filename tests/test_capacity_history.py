"""Weekly capacity snapshots survive restarts and ignore unreadable points."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from custom_components.marstek.capacity_estimator import (
    UsableCapacityEstimator,
    apply_capacity_estimate,
)
from custom_components.marstek.helpers.capacity_history import (
    MAX_HISTORY_POINTS,
    card_history,
    fold_weekly_history,
)

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


def _poll_counters(
    estimator: UsableCapacityEstimator, timestamp: float, totals: dict[str, float]
) -> None:
    """Feed one poll that carries only lifetime counters."""
    estimator.remember_lifetime(totals)
    estimator.observe(timestamp=timestamp, soc=None, power_w=None, mode=None, rated_wh=_RATED_WH)


def test_open_window_follows_state_of_charge_only() -> None:
    """The open window reports progress toward the 20-point threshold."""
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
    assert active == {
        "direction": "charge",
        # The window starts on the first poll after the percentage changes.
        "started_at": datetime.fromtimestamp(_T0 + _charge_step_seconds(), UTC).isoformat(),
        "start_soc": 41.0,
        "current_soc": 48.0,
        "soc_delta": 7.0,
        "target_soc_delta": 20.0,
        "progress": 0.35,
    }


def test_attributes_stay_the_same_until_something_material_changes() -> None:
    """A poll that only moves time, power, or a few watt-hours writes no new state.

    Every changed attribute is a new state row in the recorder and a diff to
    every open frontend, so polls between state-of-charge steps must not
    change any of them.
    """
    estimator = UsableCapacityEstimator()
    trace = _Trace(
        steps=61,
        soc_start=80.0,
        soc_per_step=-1.0,
        step_seconds=_discharge_step_seconds(),
        power_w=_POWER_W,
    )
    _feed(estimator, trace)
    moment = _T0 + (trace.steps + 1) * trace.step_seconds
    totals = {"total_grid_output_energy": 40.0}
    estimator.remember_lifetime(totals)
    estimator.observe(timestamp=moment, soc=18.0, power_w=_POWER_W, mode="auto", rated_wh=_RATED_WH)
    before = estimator.attributes()
    assert before["active_window"] is not None
    assert before["history"]

    totals["total_grid_output_energy"] = 70.0
    estimator.remember_lifetime(totals)
    estimator.observe(
        timestamp=moment + 10.0, soc=18.0, power_w=1500.0, mode="auto", rated_wh=_RATED_WH
    )

    assert estimator.attributes() == before


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

    first = estimator.to_dict()["history"]
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
    _poll_counters(estimator, _T0 + 100.0, totals)
    assert estimator.consume_dirty() is True
    recorded = estimator.to_dict()["history"][0]
    assert recorded["total_grid_output_energy"] == 40.0
    assert recorded["equivalent_cycles"] == pytest.approx(40.0 / _RATED_WH, abs=0.001)

    totals["total_grid_output_energy"] = 70.0
    _poll_counters(estimator, _T0 + 200.0, totals)
    assert estimator.consume_dirty() is False
    assert len(estimator.to_dict()["history"]) == 1

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
    history = estimator.to_dict()["history"]
    assert len(history) == 2
    assert history[1]["month"] == "2023-11"
    assert history[1]["usable_wh"] == 4600
    assert history[1]["equivalent_cycles"] == pytest.approx(1.0)
    assert history[1]["total_pv_energy"] == 800.0
    assert history[1]["total_load_energy"] == 900.0
    assert history[0]["period"] != history[1]["period"]
    # The sensor attribute keeps only what the cards draw.
    # The nameplate and method let the card compare only like weeks.
    tags = {"rated_wh": round(_RATED_WH), "method": "discharge_median"}
    assert estimator.attributes()["history"] == [
        {"week_start": 1_699_833_600.0, "usable_wh": 4600, **tags},
        {"week_start": 1_699_833_600.0 + _WEEK, "usable_wh": 4600, **tags},
    ]

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
    history = restored.to_dict()["history"]
    assert len(history) == MAX_HISTORY_POINTS
    assert history[-1]["usable_wh"] == 4000 + MAX_HISTORY_POINTS + 19
    assert history[0]["usable_wh"] == 4000 + 20


def test_the_nameplate_and_method_label_a_week_but_never_make_one() -> None:
    history: list[dict[str, object]] = []
    tags = {"rated_wh": 5_120.4, "method": "charge_discharge_geometric_mean"}
    assert not fold_weekly_history(
        history, [], timestamp=_T0, measures={"usable_wh": None}, tags=tags
    )
    assert history == []

    assert fold_weekly_history(history, [], timestamp=_T0, measures={"usable_wh": 4_600}, tags=tags)
    baseline = [dict(point) for point in history]
    assert history[0]["rated_wh"] == 5_120
    assert card_history(history)[0]["method"] == "charge_discharge_geometric_mean"
    # Another efficiency treatment is a change worth writing, like a new value.
    changed = {**tags, "method": "discharge_median"}
    assert fold_weekly_history(
        history, baseline, timestamp=_T0 + 60, measures={"usable_wh": 4_600}, tags=changed
    )

    junk = {"rated_wh": -1, "method": "guess"}
    assert fold_weekly_history(history, [], timestamp=_T0, measures={"usable_wh": 1}, tags=junk)
    assert "rated_wh" not in history[0]
    assert "method" not in history[0]
    assert set(card_history(history)[0]) == {"week_start", "usable_wh"}
