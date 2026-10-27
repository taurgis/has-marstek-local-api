"""Telling a device restart apart from a Wi-Fi or network dropout."""

from __future__ import annotations

from typing import Any

from custom_components.marstek.helpers.restart_detector import (
    BOOT_PULSE,
    CT_LOST,
    DROPOUT,
    ENERGY_ROLLBACK,
    MAX_EVENTS,
    MODE_CHANGED,
    RESTART,
    Outage,
    RestartDetector,
    restart_attributes,
)
from custom_components.marstek.pymarstek.energy_guard import (
    RAW_TOTALS_KEY,
    TOTALS_FELL_KEY,
    track_restored_totals,
)

_T0 = 1_700_000_000.0
_POLL = 30.0


def _status(**overrides: Any) -> dict[str, Any]:
    status: dict[str, Any] = {
        "device_mode": "Auto",
        "ct_connected": True,
        TOTALS_FELL_KEY: False,
    }
    status.update(overrides)
    return status


def _baseline() -> tuple[RestartDetector, dict[str, Any]]:
    detector = RestartDetector()
    status = _status()
    assert detector.record_success(_T0, None, status) is None
    return detector, status


def test_polls_that_answer_record_nothing() -> None:
    """A late reply recovered by a retransmit never reaches the detector as a failure."""
    detector, status = _baseline()
    for poll in range(1, 10):
        assert detector.record_success(_T0 + poll * _POLL, status, _status()) is None
    assert detector.events == []
    assert detector.consume_dirty() is False


def test_failed_polls_without_a_boot_trace_are_a_dropout() -> None:
    """The device comes back unchanged, so the network was the problem."""
    detector, status = _baseline()
    detector.record_failure()
    detector.record_failure()

    outage = detector.record_success(_T0 + 3 * _POLL, status, _status())

    assert outage == Outage(_T0 + 3 * _POLL, DROPOUT, 3 * _POLL, 2, ())
    assert detector.restarts == 0
    assert detector.consume_dirty() is True


def test_ct_lost_across_an_outage_is_a_restart() -> None:
    """Issue #14: a reset drops the CT link."""
    detector, status = _baseline()
    detector.record_failure()

    outage = detector.record_success(_T0 + 2 * _POLL, status, _status(ct_connected=False))

    assert outage is not None
    assert outage.kind == RESTART
    assert outage.evidence == (CT_LOST,)
    assert detector.restarts == 1


def test_mode_and_ct_only_count_across_an_outage() -> None:
    """A user switching mode, or a CT flake, while online is not a restart."""
    detector, status = _baseline()
    changed = _status(device_mode="Manual", ct_connected=False)
    assert detector.record_success(_T0 + _POLL, status, changed) is None

    detector.record_failure()
    outage = detector.record_success(_T0 + 3 * _POLL, changed, _status())
    assert outage is not None
    assert outage.evidence == (MODE_CHANGED,)


def test_a_rollback_between_two_polls_is_a_restart() -> None:
    """A quick reboot can fit between two polls; the restored totals still show it."""
    detector, status = _baseline()

    outage = detector.record_success(_T0 + _POLL, status, _status(**{TOTALS_FELL_KEY: True}))

    assert outage is not None
    assert outage.kind == RESTART
    assert outage.failed_polls == 0
    assert outage.evidence == (ENERGY_ROLLBACK,)


def test_failures_before_the_first_good_poll_are_not_counted() -> None:
    """Without a baseline there is nothing to compare the recovery against."""
    detector = RestartDetector()
    detector.record_failure()
    detector.record_failure()
    assert detector.record_success(_T0, None, _status()) is None
    assert detector.record_success(_T0 + _POLL, _status(), _status()) is None


def test_round_trip_and_bad_entries() -> None:
    """Stored entries survive a reload; unusable ones and unknown traces are dropped."""
    detector, status = _baseline()
    for index in range(MAX_EVENTS + 5):
        detector.record_failure()
        detector.record_success(_T0 + (index + 1) * 100, status, _status(ct_connected=False))

    stored = detector.to_dict()
    assert len(stored["events"]) == MAX_EVENTS
    stored["events"].append({"kind": "nonsense", "at": 1})
    stored["events"].append({"kind": RESTART, "at": _T0, "offline_s": -1, "failed_polls": 1})
    stored["events"][0]["evidence"] = [CT_LOST, "made_up"]

    restored = RestartDetector.from_dict(stored)

    assert len(restored.events) == MAX_EVENTS
    assert restored.events[0].evidence == (CT_LOST,)
    assert restored.restarts == MAX_EVENTS + 5
    assert RestartDetector.from_dict(None).events == []
    assert RestartDetector.from_dict({"restarts": -2, "events": "x"}).restarts == 0


def test_attributes_count_the_last_thirty_days() -> None:
    """The card reads counts and the latest restart from these attributes."""
    detector, status = _baseline()
    detector.record_failure()
    detector.record_success(_T0 + 60, status, _status(ct_connected=False))
    detector.record_failure()
    detector.record_success(_T0 + 40 * 86_400, status, _status())

    attrs = restart_attributes(detector, _T0 + 40 * 86_400)

    assert attrs["restarts_30d"] == 0
    assert attrs["dropouts_30d"] == 1
    assert attrs["last_restart_at"] == "2023-11-14T22:14:20+00:00"
    assert [item["kind"] for item in attrs["outages"]] == [DROPOUT]
    assert "last_restart_at" not in restart_attributes(RestartDetector(), _T0)


def test_raw_totals_that_fall_are_flagged() -> None:
    """A restored counter comes back lower than the last raw reply."""
    first: dict[str, Any] = {}
    track_restored_totals(first, {"total_load_energy": 5000, "total_grid_input_energy": 900}, None)
    assert first[TOTALS_FELL_KEY] is False

    second: dict[str, Any] = {}
    track_restored_totals(second, {"total_load_energy": 4980}, first)
    assert second[TOTALS_FELL_KEY] is True


def test_a_stalled_counter_lifted_by_the_floor_is_not_a_fall() -> None:
    """The comparison uses the raw totals, not the value the stall floor raised."""
    first: dict[str, Any] = {}
    track_restored_totals(first, {"total_grid_input_energy": 900}, None)
    # The parser lifts the stuck merged total; the raw reply stays at 900.
    first["total_grid_input_energy"] = 950

    second: dict[str, Any] = {}
    track_restored_totals(second, {"total_grid_input_energy": 900}, first)

    assert second[TOTALS_FELL_KEY] is False


def test_zero_and_missing_replies_are_not_falls() -> None:
    """0 is a placeholder or factory reset; a poll without ES.GetStatus keeps the baseline."""
    first: dict[str, Any] = {}
    track_restored_totals(first, {"total_pv_energy": 300}, None)

    zero: dict[str, Any] = {}
    track_restored_totals(zero, {"total_pv_energy": 0}, first)
    missing: dict[str, Any] = {}
    track_restored_totals(missing, None, first)
    garbage: dict[str, Any] = {}
    track_restored_totals(garbage, {"total_pv_energy": float("nan")}, {RAW_TOTALS_KEY: "x"})

    assert zero[TOTALS_FELL_KEY] is False
    assert missing[TOTALS_FELL_KEY] is False
    assert missing[RAW_TOTALS_KEY] == {"total_pv_energy": 300.0}
    assert garbage == {RAW_TOTALS_KEY: {}, TOTALS_FELL_KEY: False}


def test_a_plug_self_test_is_held_for_the_next_good_poll() -> None:
    """The plug sees the boot between polls; two tests before one poll are one restart."""
    detector, status = _baseline()
    detector.record_boot_pulse(_T0 + 10)
    detector.record_boot_pulse(_T0 + 20)

    outage = detector.record_success(_T0 + _POLL, status, _status())

    assert outage is not None
    assert outage.kind == RESTART
    assert outage.failed_polls == 0
    assert outage.evidence == (BOOT_PULSE,)
    assert detector.record_success(_T0 + 2 * _POLL, status, _status()) is None


def test_a_self_test_joins_the_outage_it_belongs_to() -> None:
    """Rollback and CT loss from the same boot stay one restart with all traces."""
    detector, status = _baseline()
    detector.record_failure()
    detector.record_boot_pulse(_T0 + 40)

    outage = detector.record_success(
        _T0 + 2 * _POLL, status, _status(ct_connected=False, **{TOTALS_FELL_KEY: True})
    )

    assert outage is not None
    assert outage.evidence == (ENERGY_ROLLBACK, CT_LOST, BOOT_PULSE)
    assert detector.restarts == 1
