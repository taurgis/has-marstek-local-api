"""Window detail, PV correction, one-direction medians, and state-of-charge jumps."""

from __future__ import annotations

import pytest

from custom_components.marstek.capacity_estimator import (
    MIN_WINDOWS,
    UsableCapacityEstimator,
    apply_capacity_estimate,
)
from tests.test_capacity_estimator import (
    _ONE_WAY,
    _POWER_W,
    _RATED_WH,
    _T0,
    _TRUE_WH,
    _discharge_step_seconds,
    _feed,
    _Trace,
)


def _stored_windows(direction: str, capacity_wh: float, count: int) -> list[dict[str, float | str]]:
    """Build the oldest on-disk window shape: capacity, direction, delta, time."""
    return [
        {
            "capacity_wh": capacity_wh,
            "direction": direction,
            "soc_delta": 20.0,
            "closed_at": float(index),
        }
        for index in range(count)
    ]


def test_one_direction_is_not_pooled_with_a_short_other_direction() -> None:
    """Two charge windows must not pull a three-window discharge median."""
    restored = UsableCapacityEstimator.from_dict(
        {"windows": _stored_windows("discharge", 4600.0, 3) + _stored_windows("charge", 4000.0, 2)}
    )

    assert restored.estimate_wh == 4600
    assert restored.attributes()["efficiency_correction"] == "discharge_median"
    assert restored.attributes()["spread_wh"] == 0
    assert restored.attributes()["discharge_median_wh"] == 4600
    assert restored.attributes()["charge_median_wh"] == 4000
    assert restored.attributes()["implied_round_trip_efficiency"] == round(
        _ONE_WAY**2 * 4600.0 / 4000.0, 3
    )
    assert "start_soc" not in restored.to_dict()["windows"][0]


def test_two_windows_each_way_stay_unknown() -> None:
    """Four windows still do not publish when neither direction has three."""
    restored = UsableCapacityEstimator.from_dict(
        {"windows": _stored_windows("discharge", 4600.0, 2) + _stored_windows("charge", 4000.0, 2)}
    )

    assert restored.windows_accepted == 4
    assert restored.estimate_wh is None
    assert restored.attributes()["efficiency_correction"] == "assumed_one_way"
    assert restored.attributes()["implied_round_trip_efficiency"] == round(
        _ONE_WAY**2 * 4600.0 / 4000.0, 3
    )


def test_a_legacy_window_round_trips_without_invented_detail() -> None:
    """Junk or missing detail fields are dropped. The capacity itself is kept."""
    restored = UsableCapacityEstimator.from_dict(
        {
            "windows": [
                {
                    "capacity_wh": 4600.0,
                    "direction": "discharge",
                    "soc_delta": 20.0,
                    "closed_at": 10.0,
                    "start_soc": "nope",
                    "end_soc": 140.0,
                    "average_power_w": 9_000_000.0,
                    "duration_s": -5,
                    "mode": "",
                    "pv_share": 5,
                }
            ]
            * 3
        }
    )

    assert restored.estimate_wh == 4600
    saved = restored.to_dict()["windows"][0]
    assert list(saved) == ["capacity_wh", "direction", "soc_delta", "closed_at"]


def test_derived_zero_pv_matches_the_ac_only_reading() -> None:
    """Venus E omits bat_power and reports pv_power 0. That path is unchanged.

    A missing PV reading is the same as zero. It must not reject the poll.
    """
    estimator = UsableCapacityEstimator()
    _feed(
        estimator,
        _Trace(
            steps=61,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=_discharge_step_seconds(),
            power_w=_POWER_W,
            pv_w=None,
            power_derived=True,
        ),
    )

    assert estimator.estimate_wh == 4600
    assert estimator.to_dict()["windows"][0]["pv_share"] == 0.0


def test_pv_only_charge_is_not_derated_by_the_inverter_efficiency() -> None:
    """PV charging power does not pass through the inverter, so 0.93 must not.

    Applying it to the whole derived reading stores 93% of a 2000 W PV charge
    and reports 4278 Wh for a 4600 Wh pack. The AC term here is zero.
    """
    pv = 2000.0
    step = ((0.20 * _TRUE_WH) / pv) * 3600.0 / 20.0
    derived = UsableCapacityEstimator()
    _feed(
        derived,
        _Trace(
            steps=61,
            soc_start=20.0,
            soc_per_step=1.0,
            step_seconds=step,
            power_w=-pv,
            pv_w=pv,
            power_derived=True,
        ),
    )

    assert derived.estimate_wh == 4600
    assert derived.to_dict()["windows"][0]["pv_share"] == pytest.approx(1.0)

    native = UsableCapacityEstimator()
    _feed(
        native,
        _Trace(
            steps=61,
            soc_start=20.0,
            soc_per_step=1.0,
            step_seconds=step,
            power_w=-pv,
            pv_w=pv,
            power_derived=False,
        ),
    )

    assert native.estimate_wh == round(_TRUE_WH * _ONE_WAY)


def test_grid_import_plus_pv_derates_only_the_imported_watts() -> None:
    """Charging from the grid and from PV: 0.93 applies to the import only.

    500 W of PV plus 1500 W imported is 1895 W into the pack, not 1860 W.
    """
    pv = 500.0
    imported = 1500.0
    dc = pv + _ONE_WAY * imported
    step = ((0.20 * _TRUE_WH) / dc) * 3600.0 / 20.0
    estimator = UsableCapacityEstimator()
    _feed(
        estimator,
        _Trace(
            steps=61,
            soc_start=20.0,
            soc_per_step=1.0,
            step_seconds=step,
            power_w=-(imported + pv),
            pv_w=pv,
            power_derived=True,
        ),
    )

    assert estimator.estimate_wh == 4600
    assert estimator.to_dict()["windows"][0]["pv_share"] == pytest.approx(pv / (imported + pv))


def test_a_non_finite_pv_reading_drops_the_derived_window() -> None:
    """A derived poll whose PV watts are not a real number is a hole."""
    estimator = UsableCapacityEstimator()
    step = _discharge_step_seconds()
    moment = _T0
    for index in range(15):
        estimator.observe(
            timestamp=moment,
            soc=80.0 - index,
            power_w=_POWER_W,
            mode="auto",
            rated_wh=_RATED_WH,
            pv_w=0.0,
            power_derived=True,
        )
        moment += step
    estimator.observe(
        timestamp=moment,
        soc=65.0,
        power_w=_POWER_W,
        mode="auto",
        rated_wh=_RATED_WH,
        pv_w=float("nan"),
        power_derived=True,
    )

    _feed(
        estimator,
        _Trace(
            steps=61,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=step,
            power_w=_POWER_W,
            pv_w=None,
            power_derived=True,
            start_timestamp=moment + step,
        ),
    )

    assert estimator.windows_accepted == MIN_WINDOWS
    assert estimator.estimate_wh == 4600


def test_switching_off_the_derived_flag_ends_the_window() -> None:
    """A device bat_power must not be integrated with samples from the AC balance."""
    estimator = UsableCapacityEstimator()
    step = _discharge_step_seconds()
    moment = _T0
    for index in range(15):
        estimator.observe(
            timestamp=moment,
            soc=80.0 - index,
            power_w=_POWER_W,
            mode="auto",
            rated_wh=_RATED_WH,
            pv_w=400.0,
            power_derived=True,
        )
        moment += step
    for index in range(62):
        estimator.observe(
            timestamp=moment,
            soc=65.0 - index,
            power_w=_POWER_W,
            mode="auto",
            rated_wh=_RATED_WH,
            pv_w=400.0,
            power_derived=False,
        )
        moment += step

    assert estimator.windows_accepted == MIN_WINDOWS
    assert estimator.estimate_wh == 4600
    assert estimator.to_dict()["windows"][0]["pv_share"] == 0.0


def test_discharge_beside_pv_corrects_only_the_ac_port() -> None:
    """PV plus battery discharge: the inverter loss is on the AC port only.

    2000 W from the pack and 1000 W of PV at 93% leave the AC port at 2790 W.
    The derived battery reading is 1790 W. Dividing that whole reading by 0.93
    misses the PV that never went through the inverter.
    """
    pv = 1000.0
    bat_dc = 2000.0
    ac = _ONE_WAY * (pv + bat_dc)
    apparent = ac - pv
    step = ((0.20 * _TRUE_WH) / bat_dc) * 3600.0 / 20.0
    derived = UsableCapacityEstimator()
    _feed(
        derived,
        _Trace(
            steps=61,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=step,
            power_w=apparent,
            pv_w=pv,
            power_derived=True,
        ),
    )

    assert derived.estimate_wh == 4600
    assert derived.to_dict()["windows"][0]["pv_share"] == pytest.approx(pv / apparent)

    native = UsableCapacityEstimator()
    _feed(
        native,
        _Trace(
            steps=61,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=step,
            power_w=apparent,
            pv_w=pv,
            power_derived=False,
        ),
    )

    assert native.estimate_wh == round(apparent / _ONE_WAY / bat_dc * _TRUE_WH)


def test_apply_capacity_estimate_uses_derived_pv() -> None:
    """The coordinator payload's derived flag and pv_power reach the integral."""
    pv = 2000.0
    step = ((0.20 * _TRUE_WH) / pv) * 3600.0 / 20.0
    estimator = UsableCapacityEstimator()
    moment = _T0
    for index in range(62):
        apply_capacity_estimate(
            estimator,
            {
                "battery_soc": 20 + index,
                "battery_power": -pv,
                "pv_power": pv,
                "battery_power_derived": True,
                "device_mode": "auto",
                "bat_cap": _RATED_WH,
            },
            moment,
        )
        moment += step

    assert estimator.estimate_wh == 4600


def test_a_soc_jump_the_watts_cannot_explain_is_not_part_of_a_window() -> None:
    """A multi-point BMS step must not be integrated into the open window.

    Nineteen honest points plus a six-point jump in one 30 s poll at 2 kW on
    a 5120 Wh pack would still land inside the nameplate band. The jump ends
    the window instead, and the following clean discharge is the only evidence.
    """
    estimator = UsableCapacityEstimator()
    step = _discharge_step_seconds()
    moment = _T0
    for index in range(21):
        estimator.observe(
            timestamp=moment,
            soc=80.0 - index,
            power_w=_POWER_W,
            mode="auto",
            rated_wh=_RATED_WH,
        )
        moment += step
    jump_at = moment - step + 30.0
    estimator.observe(
        timestamp=jump_at,
        soc=54.0,
        power_w=_POWER_W,
        mode="auto",
        rated_wh=_RATED_WH,
    )

    assert estimator.windows_accepted == 0

    _feed(
        estimator,
        _Trace(
            steps=61,
            soc_start=80.0,
            soc_per_step=-1.0,
            step_seconds=step,
            power_w=_POWER_W,
            start_timestamp=jump_at + step,
        ),
    )

    assert estimator.windows_accepted == MIN_WINDOWS
    assert estimator.estimate_wh == 4600
    assert all(
        window["capacity_wh"] == pytest.approx(4600.0, rel=0.01)
        for window in estimator.to_dict()["windows"]
    )


def test_a_two_point_step_the_watts_explain_stays_in_the_window() -> None:
    """Two points in one poll are kept when the nameplate and the watts allow it."""
    step = (0.02 * _RATED_WH * _ONE_WAY) / _POWER_W * 3600.0
    estimator = UsableCapacityEstimator()
    _feed(
        estimator,
        _Trace(
            steps=31,
            soc_start=80.0,
            soc_per_step=-2.0,
            step_seconds=step,
            power_w=_POWER_W,
        ),
    )

    assert step < 180.0
    assert estimator.windows_accepted == MIN_WINDOWS
    assert estimator.estimate_wh == round(_RATED_WH)
