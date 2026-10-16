"""VNSEM-0 301 ES.SetMode follows the issue #104 report, not a working capture."""

from __future__ import annotations

import pytest
from mock_device import MockMarstekDevice

_MANUAL = {
    "id": 0,
    "config": {
        "mode": "Manual",
        "manual_cfg": {
            "time_num": 0,
            "start_time": "00:00",
            "end_time": "00:01",
            "week_set": 64,
            "power": 100,
            "enable": 1,
        },
    },
}
_PASSIVE = {
    "id": 0,
    "config": {"mode": "Passive", "passive_cfg": {"power": 0, "cd_time": 60}},
}


def _device(name: str, ver: int) -> MockMarstekDevice:
    return MockMarstekDevice(
        port=0,
        simulate=True,
        verbose=False,
        status_interval=0,
        device_config={"device": name, "ver": ver, "ble_mac": "aabbccddeeff"},
    )


class TestVnsem301SetModeReport:
    """The 301 mock must not pretend a real SetMode capture exists."""

    def test_passive_is_a_parse_error_with_json_rpc_id_zero(self) -> None:
        """Passive does not change mode and answers id 0, data 401."""
        device = _device("VNSEM-0", 301)
        try:
            response = device.build_response(649, "ES.SetMode", _PASSIVE)
            assert response is not None
            assert response["id"] == 0
            assert response["error"] == {
                "code": -32700,
                "message": "Parse error",
                "data": 401,
            }
            assert device.simulator.mode == "Auto"
            assert device.simulator.passive_end_time is None
        finally:
            device.simulator.stop()

    @pytest.mark.parametrize("power", [100, -250, 0])
    def test_manual_keeps_the_slot_and_drops_power(self, power: int) -> None:
        """Time, weekday and enable stick; power stays unset for every sign."""
        device = _device("VNSEM-0", 301)
        request = {
            "id": 0,
            "config": {
                "mode": "Manual",
                "manual_cfg": {**_MANUAL["config"]["manual_cfg"], "power": power},
            },
        }
        try:
            response = device.build_response(862, "ES.SetMode", request)
            assert response is not None
            assert response["id"] == 862
            assert response["result"]["set_result"] is True
            assert device.simulator.mode == "Manual"
            slot = device.simulator.manual_schedules[0]
            assert slot["start_time"] == "00:00"
            assert slot["end_time"] == "00:01"
            assert slot["week_set"] == 64
            assert slot["enable"] is True
            assert slot["power"] is None
        finally:
            device.simulator.stop()

    def test_auto_still_changes_mode(self) -> None:
        """The report is Manual and Passive only."""
        device = _device("VNSEM-0", 301)
        try:
            response = device.build_response(
                3,
                "ES.SetMode",
                {"id": 0, "config": {"mode": "Auto", "auto_cfg": {"enable": 1}}},
            )
            assert response is not None
            assert response["result"]["set_result"] is True
            assert device.simulator.mode == "Auto"
        finally:
            device.simulator.stop()


class TestOtherMiniVersionsKeepGenericSetMode:
    """Venus E mini 145 has no SetMode capture and no #104 report."""

    def test_manual_power_is_stored(self) -> None:
        """The generic simulator applies the power it was given."""
        device = _device("Venus E mini", 145)
        try:
            response = device.build_response(1, "ES.SetMode", _MANUAL)
            assert response is not None
            assert response["result"]["set_result"] is True
            assert device.simulator.manual_schedules[0]["power"] == 100
        finally:
            device.simulator.stop()

    def test_passive_is_accepted(self) -> None:
        """Passive on the generic mini changes mode and echoes the request id."""
        device = _device("Venus E mini", 145)
        try:
            response = device.build_response(649, "ES.SetMode", _PASSIVE)
            assert response is not None
            assert response["id"] == 649
            assert response["result"]["set_result"] is True
            assert device.simulator.mode == "Passive"
        finally:
            device.simulator.stop()
