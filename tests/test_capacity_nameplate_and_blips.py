"""A changed nameplate, a one-poll glitch, and a jump over a long poll gap."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.marstek.capacity_estimator import (
    RATED_CONFIRM_POLLS,
    SOC_CORRECTED_KEY,
    SOC_UNCONFIRMED_KEY,
    UsableCapacityEstimator,
    apply_capacity_estimate,
)
from tests.test_capacity_run_hardening import (
    _T0,
    _VENUS_E_WH,
    _drive,
    _exact,
    _percent_seconds,
    _windows,
)

_POWER_W = 2_500.0


def _published(rated: float = _VENUS_E_WH) -> UsableCapacityEstimator:
    """Return an estimator with three discharge runs of the ``rated`` pack."""
    estimator = _drive(_exact(rated, _POWER_W, 80, soc0=95.0), rated)
    assert estimator.estimate_wh == round(rated)
    return estimator


def _idle(estimator: UsableCapacityEstimator, moment: float, rated: float) -> None:
    estimator.observe(timestamp=moment, soc=50.0, power_w=0.0, mode="auto", rated_wh=rated)


def test_a_nameplate_glitch_shorter_than_the_confirmation_changes_nothing() -> None:
    estimator = _published()
    moment = _T0 + 100_000.0
    for poll in range(RATED_CONFIRM_POLLS - 1):
        _idle(estimator, moment + poll * 30.0, 2 * _VENUS_E_WH)
    _idle(estimator, moment + 90.0, _VENUS_E_WH)
    _idle(estimator, moment + 120.0, 2 * _VENUS_E_WH)
    assert estimator.attributes()["rated_capacity_wh"] == round(_VENUS_E_WH)
    assert estimator.windows_accepted == 3
    assert estimator.estimate_wh == round(_VENUS_E_WH)


def test_a_small_nameplate_change_keeps_the_runs() -> None:
    estimator = _published()
    estimator.consume_dirty()
    moment = _T0 + 100_000.0
    for poll in range(RATED_CONFIRM_POLLS):
        _idle(estimator, moment + poll * 30.0, 5_000.0)
    assert estimator.attributes()["rated_capacity_wh"] == 5_000
    assert estimator.windows_accepted == 3
    assert estimator.consume_dirty() is True
    assert estimator.to_dict()["rated_wh"] == 5_000.0


def test_an_added_module_starts_a_new_series() -> None:
    """Runs of the old pack would sit inside the new pack's band and read as wear."""
    estimator = _published()
    moment = _T0 + 100_000.0
    for poll in range(RATED_CONFIRM_POLLS):
        _idle(estimator, moment + poll * 30.0, 2 * _VENUS_E_WH)
    assert estimator.windows_accepted == 0
    assert estimator.estimate_wh is None
    assert estimator.attributes()["rated_capacity_wh"] == round(2 * _VENUS_E_WH)


def test_the_nameplate_survives_a_restart_and_a_change_while_down_still_resets() -> None:
    restored = UsableCapacityEstimator.from_dict(_published().to_dict())
    assert restored.attributes()["rated_capacity_wh"] == round(_VENUS_E_WH)
    moment = _T0 + 100_000.0
    for poll in range(RATED_CONFIRM_POLLS):
        _idle(restored, moment + poll * 30.0, 2 * _VENUS_E_WH)
    assert restored.windows_accepted == 0

    junk = UsableCapacityEstimator.from_dict({"windows": [], "rated_wh": -5})
    assert "rated_wh" not in junk.to_dict()


def _poll(estimator: UsableCapacityEstimator, moment: float, soc: float) -> dict[str, Any]:
    status: dict[str, Any] = {
        "battery_soc": soc,
        "battery_power": _POWER_W,
        "device_mode": "auto",
        "bat_cap": _VENUS_E_WH,
    }
    apply_capacity_estimate(estimator, status, moment)
    return status


def test_a_one_poll_glitch_is_dropped_and_the_run_continues() -> None:
    samples = _exact(_VENUS_E_WH, _POWER_W, 80, soc0=95.0)
    clean = _drive(samples, _VENUS_E_WH)
    estimator = UsableCapacityEstimator()
    held = []
    for index, (moment, soc, _power) in enumerate(samples):
        status = _poll(estimator, moment, 100.0 if index == 30 else soc)
        held.append(status[SOC_UNCONFIRMED_KEY])
        assert status[SOC_CORRECTED_KEY] is False
    # Only the glitch poll itself is held, and nothing is recorded for it.
    assert [index for index, flag in enumerate(held) if flag] == [30]
    kept = [(window["start_soc"], window["end_soc"]) for window in _windows(estimator)]
    assert kept == [(window["start_soc"], window["end_soc"]) for window in _windows(clean)]
    assert estimator.estimate_wh == round(_VENUS_E_WH)
    assert estimator.attributes()["soc_corrections"] == []


def test_a_real_jump_is_confirmed_on_the_next_poll() -> None:
    samples = _exact(_VENUS_E_WH, _POWER_W, 40, soc0=95.0)
    estimator = UsableCapacityEstimator()
    flags = []
    for index, (moment, soc, _power) in enumerate(samples):
        level = soc - 8.0 if index >= 30 else soc
        status = _poll(estimator, moment, level)
        flags.append((status[SOC_UNCONFIRMED_KEY], status[SOC_CORRECTED_KEY]))
    assert flags[30] == (True, False)
    assert flags[31] == (False, True)
    jump = estimator.attributes()["soc_corrections"][-1]
    assert jump["style"] == "jump"
    assert jump["from_soc"] == samples[29][1]


def test_a_step_over_a_long_poll_gap_ends_the_run_without_a_correction() -> None:
    """Endpoint watts cannot bound a load spike between polls minutes apart."""
    rated = _VENUS_E_WH
    step = _percent_seconds(rated, 1_250.0, charge=False)
    assert step > 120.0
    estimator = _drive(_exact(rated, 1_250.0, 14, soc0=90.0), rated)
    moment = _T0 + 14 * step
    for poll in (1, 2):
        estimator.observe(
            timestamp=moment + poll * step,
            soc=70.0,
            power_w=1_250.0,
            mode="auto",
            rated_wh=rated,
        )
    assert estimator.attributes()["soc_corrections"] == []
    active = estimator.attributes()["active_window"]
    assert (active["start_soc"], active["soc_delta"]) == (70.0, 0.0)


@pytest.mark.parametrize("bad", [None, float("nan")])
def test_an_unreadable_poll_clears_a_held_step(bad: float | None) -> None:
    samples = _exact(_VENUS_E_WH, _POWER_W, 10, soc0=95.0)
    estimator = _drive(samples, _VENUS_E_WH)
    moment = samples[-1][0]
    estimator.observe(
        timestamp=moment + 60.0, soc=40.0, power_w=_POWER_W, mode="auto", rated_wh=_VENUS_E_WH
    )
    assert estimator.soc_unconfirmed
    estimator.observe(
        timestamp=moment + 120.0, soc=bad, power_w=_POWER_W, mode="auto", rated_wh=_VENUS_E_WH
    )
    assert not estimator.soc_unconfirmed
