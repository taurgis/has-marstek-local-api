"""The mock's restarts and Wi-Fi drops, and what the integration makes of them."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from mock_device import MockMarstekDevice
from mock_device.const import MODE_AUTO, MODE_PASSIVE
from mock_device.simulators.restart import REBOOTING, WIFI_DROP, RestartSimulator

from custom_components.marstek.firmware_profile import resolve_firmware_profile
from custom_components.marstek.helpers.restart_detector import (
    CT_LOST,
    DROPOUT,
    ENERGY_ROLLBACK,
    MODE_CHANGED,
    RESTART,
    RestartDetector,
)
from custom_components.marstek.pymarstek.data_parser import (
    merge_device_status,
    parse_em_status_response,
    parse_es_mode_response,
    parse_es_status_response,
)

_TOTALS = (
    "total_pv_energy",
    "total_grid_output_energy",
    "total_grid_input_energy",
    "total_load_energy",
)


def _device(tmp_path: Path, **config: Any) -> MockMarstekDevice:
    """Return a Venus E 150 whose last energy save holds non-zero totals."""
    device = MockMarstekDevice(
        port=30090,
        device_config={"device": "VenusE 3.0", "ver": 150, **config},
        ip_override="127.0.0.1",
        simulate=True,
        state_dir=str(tmp_path),
        reset_state=True,
        verbose=False,
        status_interval=0,
    )
    # A fresh mock starts at 0 Wh, which the integration treats as a placeholder.
    _add_energy(device, 5000.0)
    device.restarts._saved_totals = device.restarts._totals()
    return device


def _add_energy(device: MockMarstekDevice, wh: float) -> None:
    battery = device.simulator
    for key in _TOTALS:
        setattr(battery, key, getattr(battery, key) + wh)
    battery.pv_channels.total_pv_energy = battery.total_pv_energy


def test_without_flags_nothing_is_dropped(tmp_path: Path) -> None:
    """A mock started without fault flags answers every datagram."""
    device = _device(tmp_path)
    assert device.restarts.drop_reason(0.0) is None
    assert device.restarts.drop_reason(1e12) is None


def test_a_restart_goes_silent_and_leaves_boot_traces(tmp_path: Path) -> None:
    """A restart rolls totals back, ends Passive and drops the CT until it reconnects."""
    device = _device(tmp_path, boot_s=60, ct_reconnect_s=30, energy_save_s=1e9)
    battery = device.simulator
    saved = {key: getattr(battery, key) for key in _TOTALS}
    _add_energy(device, 250.0)
    battery.mode = MODE_PASSIVE
    battery.passive_end_time = 1e12

    device.restarts.restart(1000.0)

    assert device.restarts.drop_reason(1059.0) == REBOOTING
    assert {key: getattr(battery, key) for key in _TOTALS} == saved
    assert battery.pv_channels.total_pv_energy == saved["total_pv_energy"]
    assert battery.mode == MODE_AUTO
    assert battery.passive_end_time is None
    assert battery.ct_connected is False

    assert device.restarts.drop_reason(1060.0) is None
    assert battery.ct_connected is False
    device.restarts.drop_reason(1090.0)
    assert battery.ct_connected is True


def test_energy_save_moves_the_rollback_point(tmp_path: Path) -> None:
    """Only energy counted since the last save is lost."""
    device = _device(tmp_path, energy_save_s=60, boot_s=10)
    restarts = device.restarts
    restarts.drop_reason(restarts._saved_at + 61)
    _add_energy(device, 100.0)
    kept = device.simulator.total_load_energy
    restarts.drop_reason(restarts._saved_at + 61)
    _add_energy(device, 40.0)

    restarts.restart(restarts._saved_at + 5)

    assert device.simulator.total_load_energy == kept


def test_request_and_schedule_trigger_a_restart() -> None:
    """``kill -USR1`` restarts at the next datagram; ``every_s`` repeats with jitter."""
    simulator = RestartSimulator(
        None, every_s=100, boot_s=20, rng=random.Random(1), wifi_drop_rate=0.0
    )
    first = simulator._next_restart
    assert first is not None and 75 <= first - simulator._saved_at <= 125

    simulator.request_restart()
    simulator.drop_reason(simulator._saved_at)
    assert simulator.restarts == 1

    # The requested restart starts the schedule over.
    following = simulator._next_restart
    assert following is not None and following != first
    assert simulator.drop_reason(following) == REBOOTING
    assert simulator.restarts == 2


def test_wifi_drops_follow_the_configured_rate() -> None:
    """A Wi-Fi drop loses a datagram and changes nothing else."""
    simulator = RestartSimulator(None, wifi_drop_rate=0.3, rng=random.Random(7))
    reasons = [simulator.drop_reason(float(index)) for index in range(2000)]
    assert 0.25 < reasons.count(WIFI_DROP) / len(reasons) < 0.35
    assert simulator.restarts == 0


def _poll(device: MockMarstekDevice, previous: dict[str, Any] | None) -> dict[str, Any]:
    """Run the fast tier through the integration's parsers, as one good poll."""
    profile = resolve_firmware_profile("VenusE 3.0", 150)
    return merge_device_status(
        es_mode_data=parse_es_mode_response(device._build_response(1, "ES.GetMode", {}), profile),
        es_status_data=parse_es_status_response(
            device._build_response(2, "ES.GetStatus", {}), profile
        ),
        em_status_data=parse_em_status_response(
            device._build_response(3, "EM.GetStatus", {}), profile
        ),
        previous_status=previous,
    )


def test_the_detector_tells_a_mock_restart_from_a_dropout(tmp_path: Path) -> None:
    """End to end: the mock's restart reads as a restart, its silence alone as a dropout."""
    device = _device(tmp_path, boot_s=60, ct_reconnect_s=30, energy_save_s=1e9)
    detector = RestartDetector()
    previous = _poll(device, None)
    assert detector.record_success(0.0, None, previous) is None

    # The network loses two polls; the device itself is fine.
    _add_energy(device, 20.0)
    detector.record_failure()
    detector.record_failure()
    current = _poll(device, previous)
    dropout = detector.record_success(90.0, previous, current)
    assert dropout is not None and dropout.kind == DROPOUT
    previous = current

    # The device restarts out of Passive with energy counted since its last save.
    device.simulator.mode = MODE_PASSIVE
    previous = _poll(device, previous)
    _add_energy(device, 300.0)
    previous = _poll(device, previous)
    device.restarts.restart(100.0)
    detector.record_failure()
    current = _poll(device, previous)
    restart = detector.record_success(180.0, previous, current)

    assert restart is not None
    assert restart.kind == RESTART
    assert set(restart.evidence) == {ENERGY_ROLLBACK, CT_LOST, MODE_CHANGED}


def test_a_reboot_between_two_polls_still_counts(tmp_path: Path) -> None:
    """A 30-60 s reboot can fit between two 30 s polls; the rollback still shows it."""
    device = _device(tmp_path, boot_s=40, energy_save_s=1e9)
    detector = RestartDetector()
    previous = _poll(device, None)
    detector.record_success(0.0, None, previous)
    _add_energy(device, 50.0)
    current = _poll(device, previous)
    assert detector.record_success(30.0, previous, current) is None
    previous = current

    device.restarts.restart(31.0)
    restart = detector.record_success(90.0, previous, _poll(device, previous))

    assert restart is not None
    assert restart.kind == RESTART
    assert restart.failed_polls == 0
    assert restart.evidence == (ENERGY_ROLLBACK,)
