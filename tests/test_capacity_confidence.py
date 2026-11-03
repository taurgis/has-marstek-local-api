"""The confidence label: run count first, then each check that can only lower it."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from custom_components.marstek.capacity_estimator import UsableCapacityEstimator
from custom_components.marstek.helpers.capacity_confidence import (
    GEOMETRIC_MEAN,
    POWER_ASYMMETRY,
    RUN_SCATTER,
    SINGLE_DIRECTION,
    SOC_DRIFT,
    SOLAR_CHARGING,
    assess,
    power_ratio,
    run_scatter_pct,
    scatter_pct,
    spread_wh,
)


@dataclass(frozen=True)
class _Run:
    capacity_wh: float
    direction: str
    average_power_w: float | None = None
    pv_share: float | None = None


def _runs(
    direction: str,
    capacities: list[float],
    *,
    watts: float | None = 1_000.0,
    pv_share: float | None = None,
) -> list[_Run]:
    sign = 1.0 if direction == "discharge" else -1.0
    power = None if watts is None else sign * watts
    return [_Run(value, direction, power, pv_share) for value in capacities]


def _both(count: int, *, discharge_w: float = 1_000.0, charge_w: float = 1_000.0) -> list[_Run]:
    return _runs("discharge", [4_600.0] * count, watts=discharge_w) + _runs(
        "charge", [4_700.0] * count, watts=charge_w
    )


@pytest.mark.parametrize(
    ("per_direction", "label"),
    [(1, "collecting"), (2, "low"), (3, "medium"), (5, "medium"), (6, "high")],
)
def test_the_run_count_sets_the_starting_label(per_direction: int, label: str) -> None:
    runs = _both(per_direction)
    if per_direction == 1:
        runs = runs[:2]
    assert assess(runs, GEOMETRIC_MEAN, 0.9, None) == (label, [])


def test_one_direction_is_at_most_medium_and_says_so() -> None:
    runs = _runs("discharge", [4_600.0] * 12)
    assert assess(runs, "discharge_median", None, None) == ("medium", [SINGLE_DIRECTION])
    # Five runs are low already: the cap holds nothing down and is not named.
    assert assess(runs[:5], "discharge_median", None, None) == ("low", [])


@pytest.mark.parametrize(
    ("spread", "label"),
    [(0.02, "high"), (0.06, "medium"), (0.12, "low")],
)
def test_scattered_runs_lower_the_label(spread: float, label: str) -> None:
    """Half the runs sit ``spread`` away from their median."""
    low, high = 4_600.0 * (1.0 - spread), 4_600.0 * (1.0 + spread)
    runs = _runs("discharge", [low, low, low, 4_600.0, high, high, high]) + _runs(
        "charge", [4_700.0] * 6
    )
    result = assess(runs, GEOMETRIC_MEAN, 0.9, None)
    assert result == (label, [] if label == "high" else [RUN_SCATTER])


def test_scatter_needs_three_runs_and_ignores_the_offset_between_directions() -> None:
    assert scatter_pct([4_600.0, 4_000.0]) is None
    assert scatter_pct([4_600.0, 4_600.0, 4_600.0]) == 0.0
    runs = _runs("discharge", [4_600.0] * 3) + _runs("charge", [4_000.0] * 3)
    assert run_scatter_pct(runs) == 0.0
    assert spread_wh(runs, 4_300.0) == 0.0
    assert spread_wh([], 4_300.0) is None
    wide = _runs("charge", [4_000.0, 4_400.0, 4_200.0])
    assert spread_wh(wide, 4_200.0) == pytest.approx(400.0)


def test_power_asymmetry_lowers_the_geometric_mean() -> None:
    fast_charge = _both(6, discharge_w=400.0, charge_w=2_000.0)
    assert power_ratio(fast_charge) == pytest.approx(5.0)
    assert assess(fast_charge, GEOMETRIC_MEAN, 0.85, None) == ("medium", [POWER_ASYMMETRY])

    very_fast = _both(6, discharge_w=300.0, charge_w=2_400.0)
    # A good curve keeps it at medium; a poor implied round trip drops it to low.
    assert assess(very_fast, GEOMETRIC_MEAN, 0.85, None) == ("medium", [POWER_ASYMMETRY])
    assert assess(very_fast, GEOMETRIC_MEAN, None, None) == ("medium", [POWER_ASYMMETRY])
    assert assess(very_fast, GEOMETRIC_MEAN, 0.76, None) == ("low", [POWER_ASYMMETRY])


def test_power_ratio_needs_known_watts_in_both_directions() -> None:
    assert power_ratio(_both(3)) == pytest.approx(1.0)
    unknown = _runs("discharge", [4_600.0] * 3, watts=None) + _runs("charge", [4_700.0] * 3)
    assert power_ratio(unknown) is None
    stalled = _both(3, discharge_w=0.0)
    assert power_ratio(stalled) is None
    # A single direction is already capped; its watts have nothing to compare to.
    one_way = _runs("discharge", [4_600.0] * 12, watts=300.0)
    assert assess(one_way, "discharge_median", None, None) == ("medium", [SINGLE_DIRECTION])


def test_mostly_solar_charging_is_at_most_medium() -> None:
    discharge = _runs("discharge", [4_600.0] * 6)
    solar = _runs("charge", [4_700.0] * 6, pv_share=0.8)
    assert assess(discharge + solar, GEOMETRIC_MEAN, 0.9, None) == ("medium", [SOLAR_CHARGING])
    grid = _runs("charge", [4_700.0] * 6, pv_share=0.1)
    assert assess(discharge + grid, GEOMETRIC_MEAN, 0.9, None) == ("high", [])
    unknown = _runs("charge", [4_700.0] * 6) + _runs("charge", [4_700.0] * 2, pv_share=0.9)
    assert assess(discharge + unknown, GEOMETRIC_MEAN, 0.9, None) == ("high", [])


@pytest.mark.parametrize(("bound", "label"), [(1.0, "high"), (2.5, "medium"), (6.0, "low")])
def test_counting_drift_lowers_the_label(bound: float, label: str) -> None:
    expected = [] if label == "high" else [SOC_DRIFT]
    assert assess(_both(6), GEOMETRIC_MEAN, 0.9, bound) == (label, expected)


def test_every_cap_that_lowers_the_label_is_named() -> None:
    runs = _runs("discharge", [4_600.0] * 12, pv_share=None)
    label, limits = assess(runs, "discharge_median", None, 6.0)
    assert label == "low"
    assert limits == [SINGLE_DIRECTION, SOC_DRIFT]


def test_the_estimator_reports_the_runs_it_used_and_why_confidence_is_held() -> None:
    windows = [
        {"capacity_wh": 4_600.0, "direction": "discharge", "soc_delta": 20.0, "closed_at": index}
        for index in range(4)
    ] + [
        {"capacity_wh": 4_000.0, "direction": "charge", "soc_delta": 20.0, "closed_at": 10 + index}
        for index in range(2)
    ]
    attrs = UsableCapacityEstimator.from_dict({"windows": windows}).attributes()
    assert attrs["windows_accepted"] == 6
    # Two charge runs are not a median yet, so only the discharge runs count.
    assert attrs["windows_used"] == 4
    assert attrs["efficiency_correction"] == "discharge_median"
    assert attrs["confidence"] == "low"
    assert attrs["confidence_limits"] == []
    assert attrs["run_scatter_pct"] == 0.0
    assert attrs["spread_wh"] == 0

    collecting = UsableCapacityEstimator.from_dict({"windows": windows[:2]}).attributes()
    assert collecting["confidence"] == "collecting"
    assert collecting["windows_used"] == 0
    assert "run_scatter_pct" not in collecting
