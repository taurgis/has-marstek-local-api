"""The mock's network chip resets while cloud uploads fail, per firmware."""

from __future__ import annotations

import argparse
import random
import signal
from pathlib import Path
from typing import Any

import pytest
from mock_device import MockMarstekDevice
from mock_device.__main__ import _fault_config
from mock_device.firmware_quirks import UPLINK_ETHERNET, UPLINK_WIFI, upload_failure_resets
from mock_device.simulators.restart import NETWORK_RESET, RestartSimulator

from custom_components.marstek.firmware_profile import resolve_firmware_profile

_WIFI = (1200.0, 15.0)
_ETHERNET = (2100.0, 8.0)


@pytest.mark.parametrize(
    ("device", "ver", "wifi", "ethernet"),
    [
        ("VenusE 3.0", 150, _WIFI, _ETHERNET),
        ("VenusA", 150, _WIFI, _ETHERNET),
        ("VenusD", 150, _WIFI, _ETHERNET),
        ("VenusE 3.0", 151, _WIFI, None),
        ("VenusA", 1509, _WIFI, None),
        ("VenusD", 151, _WIFI, None),
        ("VenusE 3.0", 149, None, None),
        ("VenusE 3.0", 1476, None, None),
        ("VenusA", 1491, None, None),
        ("VenusD", 1492, None, None),
        ("VenusC", 156, None, None),
        ("VenusE", 156, None, None),
        ("Venus E mini", 145, None, None),
        ("VNSEM-0", 301, None, None),
        ("VenusE Pro", 1508, None, None),
    ],
)
def test_upload_failure_resets_follow_the_firmware_survey(
    device: str,
    ver: int,
    wifi: tuple[float, float] | None,
    ethernet: tuple[float, float] | None,
) -> None:
    """Only Control 150+ resets Wi-Fi, and only Control 150 resets Ethernet."""
    profile = resolve_firmware_profile(device, ver)
    assert upload_failure_resets(profile, UPLINK_WIFI) == wifi
    assert upload_failure_resets(profile, UPLINK_ETHERNET) == ethernet


def _device(tmp_path: Path, **config: Any) -> MockMarstekDevice:
    return MockMarstekDevice(
        port=30091,
        device_config={"device": "VenusE 3.0", "ver": 150, **config},
        ip_override="127.0.0.1",
        simulate=True,
        state_dir=str(tmp_path),
        reset_state=True,
        verbose=False,
        status_interval=0,
    )


def test_a_network_reset_is_brief_and_keeps_device_state(tmp_path: Path) -> None:
    """Silent for a few seconds, 0 W meanwhile, totals, CT link and mode untouched."""
    device = _device(tmp_path, network_reset_s=8)
    battery = device.simulator
    restarts = device.restarts
    totals = battery.total_load_energy
    mode = battery.mode

    restarts.network_reset(1000.0)

    assert restarts.drop_reason(1007.0) == NETWORK_RESET
    assert battery.network_down is True
    assert battery._regulated_target_power(1.0) == 0
    assert restarts.drop_reason(1008.0) is None
    assert battery.network_down is False
    assert battery.ct_connected is True
    assert battery.total_load_energy == totals
    assert battery.mode == mode
    assert restarts.restarts == 0
    assert restarts.network_resets == 1
    assert restarts.plug_watts(1012.0) == 2550


def test_cloud_unreachable_uses_the_firmware_period(tmp_path: Path) -> None:
    """Control 150 on Ethernet resets about every 35 min, offline 8 s."""
    device = _device(tmp_path, cloud_unreachable=True, uplink=UPLINK_ETHERNET)
    restarts = device.restarts
    assert restarts._network_every == _ETHERNET[0]
    assert restarts._network_offline == _ETHERNET[1]
    assert restarts._next_network_reset is not None
    assert 0.95 * 2100 <= restarts._next_network_reset - restarts._saved_at <= 1.05 * 2100


def test_firmware_without_the_reset_ignores_cloud_unreachable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Control 149 never resets its network chip, and the mock says so."""
    device = _device(tmp_path, ver=149, cloud_unreachable=True)
    assert device.restarts._next_network_reset is None
    assert "has no upload-failure reset" in capsys.readouterr().out


def test_request_and_schedule_trigger_a_network_reset() -> None:
    """``kill -USR2`` resets at the next datagram; the schedule repeats."""
    simulator = RestartSimulator(None, network_reset_every_s=100, rng=random.Random(3))
    first = simulator._next_network_reset
    assert first is not None and 95 <= first - simulator._saved_at <= 105

    simulator.request_network_reset()
    assert simulator.drop_reason(simulator._saved_at) == NETWORK_RESET
    following = simulator._next_network_reset
    assert following is not None and following != first
    assert simulator.drop_reason(following) == NETWORK_RESET
    assert simulator.network_resets == 2
    assert simulator.restarts == 0


def test_sigusr2_requests_a_network_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``start`` wires USR2 to a network reset."""
    device = _device(tmp_path)
    handlers: dict[int, Any] = {}
    monkeypatch.setattr(signal, "signal", lambda sig, handler: handlers.setdefault(sig, handler))
    monkeypatch.setattr("socket.socket", _StopStart)
    with pytest.raises(_StopStartError):
        device.start()
    handlers[signal.SIGUSR2](signal.SIGUSR2, None)
    assert device.restarts._network_requested is not None


class _StopStartError(Exception):
    pass


class _StopStart:
    def __init__(self, *_args: Any) -> None:
        raise _StopStartError


def test_cli_flags_reach_the_fault_config() -> None:
    """The CLI passes the cloud, uplink and period flags through, and checks the period."""
    args = _namespace(cloud_unreachable=True, uplink=UPLINK_ETHERNET, network_reset_every=60.0)
    config = _fault_config(args)
    assert config["cloud_unreachable"] is True
    assert config["uplink"] == UPLINK_ETHERNET
    assert config["network_reset_every_s"] == 60.0
    with pytest.raises(SystemExit):
        _fault_config(_namespace(network_reset_every=0.0))


def _namespace(**overrides: Any) -> argparse.Namespace:
    values: dict[str, Any] = {
        "restart_every": None,
        "boot_seconds": 45.0,
        "ct_reconnect_seconds": 60.0,
        "energy_save_seconds": 600.0,
        "wifi_drop_rate": 0.0,
        "cloud_unreachable": False,
        "uplink": UPLINK_WIFI,
        "network_reset_every": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)
