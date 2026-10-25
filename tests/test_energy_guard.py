"""Tests for lifetime energy total spike rejection."""

from __future__ import annotations

import math

import pytest

from custom_components.marstek.pymarstek.data_parser import merge_device_status
from custom_components.marstek.pymarstek.energy_guard import (
    ENERGY_TOTAL_KEYS,
    MAX_PLAUSIBLE_ENERGY_JUMP_WH,
    MAX_PLAUSIBLE_ENERGY_WH,
    apply_energy_total_guard,
    energy_total_is_plausible,
)


def test_energy_total_keys_cover_all_lifetime_meters() -> None:
    """Grid, solar, load, and CT meter totals share the same guard."""
    assert {
        "total_grid_input_energy",
        "total_grid_output_energy",
        "total_pv_energy",
        "total_load_energy",
        "em_input_energy",
        "em_output_energy",
    } == ENERGY_TOTAL_KEYS


def test_plausible_energy_accepts_real_venus_totals() -> None:
    """Live Venus E 150 grid totals and a zero meter reading are valid."""
    assert energy_total_is_plausible(0) is True
    assert energy_total_is_plausible(267386) is True
    assert energy_total_is_plausible(1_187_576) is True
    assert energy_total_is_plausible(MAX_PLAUSIBLE_ENERGY_WH) is True


def test_plausible_energy_rejects_garbage() -> None:
    """Uint32 sentinels, issue #70, negatives, and non-finite values are not Wh."""
    assert energy_total_is_plausible(2**32 - 1) is False
    assert energy_total_is_plausible(4_294_967_295_267.680) is False
    assert energy_total_is_plausible(-1) is False
    assert energy_total_is_plausible(math.inf) is False
    assert energy_total_is_plausible(math.nan) is False
    assert energy_total_is_plausible(True) is False
    assert energy_total_is_plausible(None) is False


def test_guard_ignores_issue_70_spike_on_every_meter() -> None:
    """A 10^15 Wh jump must not replace the last good total on any meter."""
    previous = dict.fromkeys(ENERGY_TOTAL_KEYS, 267386.0)
    status = dict.fromkeys(ENERGY_TOTAL_KEYS, 4_294_967_295_267.680)

    apply_energy_total_guard(status, previous)

    assert status == previous


def test_guard_unsticks_corrupt_previous_when_device_is_sane() -> None:
    """A later sane Open API reading must replace a stored garbage floor."""
    previous = dict.fromkeys(ENERGY_TOTAL_KEYS, 4_294_967_295_267.680)
    status = dict.fromkeys(ENERGY_TOTAL_KEYS, 267386.0)

    apply_energy_total_guard(status, previous)

    assert status == dict.fromkeys(ENERGY_TOTAL_KEYS, 267386.0)


def test_guard_keeps_firmware_149_solar_encoding_correction() -> None:
    """The Venus A 149 x10 solar correction is a real encoding fix, not a spike."""
    status = {"total_pv_energy": 257420.0}

    apply_energy_total_guard(status, {"total_pv_energy": 25742.0})

    assert status["total_pv_energy"] == 257420.0


def test_guard_rejects_jump_above_single_update_cap() -> None:
    """A still-finite but impossible one-poll jump is dropped."""
    previous = 1_000.0
    current = previous + MAX_PLAUSIBLE_ENERGY_JUMP_WH + 1
    status = {"total_load_energy": current}

    apply_energy_total_guard(status, {"total_load_energy": previous})

    assert status["total_load_energy"] == previous
    assert current <= MAX_PLAUSIBLE_ENERGY_WH


def test_guard_accepts_lifetime_total_that_replaces_zero(caplog: pytest.LogCaptureFixture) -> None:
    """Issue #128: 0 Wh is not a baseline that can freeze a P1 meter."""
    status = {
        "em_input_energy": 19_316_004.0,
        "em_output_energy": 17_883_019.0,
        "total_pv_energy": MAX_PLAUSIBLE_ENERGY_WH,
    }

    with caplog.at_level("WARNING"):
        apply_energy_total_guard(
            status,
            {
                "em_input_energy": 0.0,
                "em_output_energy": 0,
                "total_pv_energy": 0.0,
            },
        )

    assert status["em_input_energy"] == 19_316_004.0
    assert status["em_output_energy"] == 17_883_019.0
    assert status["total_pv_energy"] == MAX_PLAUSIBLE_ENERGY_WH
    assert "Ignoring" not in caplog.text


def test_guard_keeps_zero_when_the_replacement_is_not_plausible() -> None:
    """A uint32-scale reading must not replace a zero baseline either."""
    status = {"em_input_energy": MAX_PLAUSIBLE_ENERGY_WH + 1.0}

    apply_energy_total_guard(status, {"em_input_energy": 0.0})

    assert status["em_input_energy"] == 0.0


def test_guard_still_rejects_a_jump_from_a_positive_baseline() -> None:
    """One watt-hour of real history is enough to enforce the jump cap."""
    previous = 1.0
    current = previous + MAX_PLAUSIBLE_ENERGY_JUMP_WH + 1
    status = {"total_grid_input_energy": current}

    apply_energy_total_guard(status, {"total_grid_input_energy": previous})

    assert status["total_grid_input_energy"] == previous


def test_merge_ignores_grid_spike_and_keeps_frozen_counter_fallback() -> None:
    """Spike rejection must not disable the stalled-grid-counter integrator."""
    previous_status = {
        "ongrid_power": -360,
        "total_grid_input_energy": 1000.0,
        "total_grid_output_energy": 500.0,
        "last_update": 100.0,
    }

    spiked = merge_device_status(
        es_status_data={
            "ongrid_power": -360,
            "total_grid_input_energy": 4_294_967_295_267.680,
            "total_grid_output_energy": 500.0,
        },
        last_update=160.0,
        previous_status=previous_status,
    )
    assert spiked["total_grid_input_energy"] == 1000.0

    unstuck = merge_device_status(
        es_status_data={
            "ongrid_power": -360,
            "total_grid_input_energy": 267386.0,
            "total_grid_output_energy": 500.0,
        },
        last_update=160.0,
        previous_status={
            **previous_status,
            "total_grid_input_energy": 4_294_967_295_267.680,
        },
    )
    assert unstuck["total_grid_input_energy"] == 267392.0

    stalled = merge_device_status(
        es_status_data={
            "ongrid_power": -360,
            "total_grid_input_energy": 1000.0,
            "total_grid_output_energy": 500.0,
        },
        last_update=160.0,
        previous_status=previous_status,
    )
    assert stalled["total_grid_input_energy"] == 1006.0
