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

    @pytest.mark.parametrize(
        ("power", "manual_set", "expected_power"),
        [
            (100, 1, -100),
            (800, 2, 800),
            (0, 1, 0),
            (500, 0, None),
        ],
    )
    def test_documented_manual_set_applies_unsigned_power(
        self,
        power: int,
        manual_set: int,
        expected_power: int | None,
    ) -> None:
        """Rev 3.1 unsigned ``power`` plus ``manual_set`` 0-2 is documentation-based."""
        device = _device("VNSEM-0", 301)
        request = {
            "id": 0,
            "config": {
                "mode": "Manual",
                "manual_cfg": {
                    **_MANUAL["config"]["manual_cfg"],
                    "power": power,
                    "manual_set": manual_set,
                },
            },
        }
        try:
            response = device.build_response(862, "ES.SetMode", request)
            assert response is not None
            assert response["result"]["set_result"] is True
            slot = device.simulator.manual_schedules[0]
            assert slot["power"] == expected_power
            assert slot["manual_set"] == manual_set
        finally:
            device.simulator.stop()

    @pytest.mark.parametrize(
        ("power", "manual_set"),
        [
            (-250, 1),
            (-800, 2),
            (100, 3),
            (0, 3),
        ],
    )
    def test_unknown_manual_set_payloads_leave_power_unset(
        self,
        power: int,
        manual_set: int,
    ) -> None:
        """Negative power or auto (3) is not captured; do not invent a result."""
        device = _device("VNSEM-0", 301)
        request = {
            "id": 0,
            "config": {
                "mode": "Manual",
                "manual_cfg": {
                    **_MANUAL["config"]["manual_cfg"],
                    "power": power,
                    "manual_set": manual_set,
                },
            },
        }
        try:
            response = device.build_response(862, "ES.SetMode", request)
            assert response is not None
            assert response["result"]["set_result"] is True
            slot = device.simulator.manual_schedules[0]
            assert slot["power"] is None
            assert "manual_set" not in slot
        finally:
            device.simulator.stop()

    @pytest.mark.parametrize("slot", [6, 10, -1, None])
    def test_any_slot_is_acknowledged_but_not_stored(self, slot: int | None) -> None:
        """PMU 301 answers set_result true for every time_num (firmware emulator)."""
        device = _device("VNSEM-0", 301)
        manual = dict(_MANUAL["config"]["manual_cfg"])  # type: ignore[index]
        if slot is None:
            del manual["time_num"]
        else:
            manual["time_num"] = slot
        try:
            before = [dict(s) for s in device.simulator.manual_schedules]
            response = device.build_response(
                4, "ES.SetMode", {"id": 0, "config": {"mode": "Manual", "manual_cfg": manual}}
            )
            assert response is not None
            assert response["result"]["set_result"] is True
            assert [dict(s) for s in device.simulator.manual_schedules] == before
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
