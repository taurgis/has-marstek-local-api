"""Issue #128: P1 lifetime totals must not stick at the GetMode zero placeholder."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.marstek.firmware_profile import resolve_firmware_profile
from custom_components.marstek.pymarstek.data_parser import (
    merge_device_status,
    parse_em_status_response,
    parse_es_mode_response,
)
from custom_components.marstek.pymarstek.udp import MarstekUDPClient

_PROFILE = resolve_firmware_profile("VenusA", 150)
# The reporter's EM.GetStatus body, firmware 150, scale 0.1 Wh → Wh.
_P1_INPUT_RAW = 193_160_040
_P1_OUTPUT_RAW = 178_830_190
_P1_INPUT_WH = 19_316_004.0
_P1_OUTPUT_WH = 17_883_019.0


def _getmode_zeros() -> dict[str, Any]:
    return {
        "id": 511,
        "src": "VenusA-2cf8ec204286",
        "result": {
            "id": 0,
            "mode": "Auto",
            "ongrid_power": 243,
            "offgrid_power": 0,
            "bat_soc": 23,
            "ct_state": 0,
            "a_power": 0,
            "b_power": 0,
            "c_power": 0,
            "total_power": 0,
            "input_energy": 0,
            "output_energy": 0,
        },
    }


def _es_status() -> dict[str, Any]:
    return {
        "id": 508,
        "result": {
            "id": 0,
            "bat_soc": 23,
            "bat_cap": 4160,
            "pv_power": 0,
            "ongrid_power": 303,
            "offgrid_power": 0,
            "total_pv_energy": 213,
            "total_grid_output_energy": 373282,
            "total_grid_input_energy": 478233,
            "total_load_energy": 0,
        },
    }


def _em_p1() -> dict[str, Any]:
    return {
        "id": 509,
        "result": {
            "id": 0,
            "ct_state": 1,
            "a_power": 0,
            "b_power": 0,
            "c_power": 0,
            "total_power": 0,
            "input_energy": _P1_INPUT_RAW,
            "output_energy": _P1_OUTPUT_RAW,
        },
    }


def test_getmode_placeholder_energy_is_not_parsed() -> None:
    """ct_state 0 and a 0/0 pair are not meter totals."""
    disconnected = parse_es_mode_response(_getmode_zeros(), _PROFILE)
    assert "em_input_energy" not in disconnected
    assert "em_output_energy" not in disconnected
    assert disconnected["ct_state"] == 0
    assert disconnected["device_mode"] == "auto"

    both_zero = parse_es_mode_response(
        {
            "id": 1,
            "result": {
                "mode": "Auto",
                "ct_state": 1,
                "input_energy": 0,
                "output_energy": 0,
            },
        },
        _PROFILE,
    )
    assert "em_input_energy" not in both_zero
    assert "em_output_energy" not in both_zero

    partial = parse_es_mode_response(
        {
            "id": 1,
            "result": {
                "mode": "Auto",
                "ct_state": 0,
                "input_energy": 10,
                "output_energy": 0,
            },
        },
        _PROFILE,
    )
    assert partial["em_input_energy"] == 1.0
    assert partial["em_output_energy"] == 0.0


def test_timeout_then_p1_reading_publishes_the_lifetime_totals() -> None:
    """EM timing out must not seed 0 Wh, and the next reply must stick."""
    mode = parse_es_mode_response(_getmode_zeros(), _PROFILE)
    es_status = {
        "battery_soc": 23,
        "battery_power": 303,
        "ongrid_power": 303,
        "total_pv_energy": 213,
        "total_grid_output_energy": 373282,
        "total_grid_input_energy": 478233,
        "total_load_energy": 0,
    }
    missed = merge_device_status(
        es_mode_data=mode,
        es_status_data=es_status,
        last_update=1_000.0,
    )
    assert "em_input_energy" not in missed
    assert "em_output_energy" not in missed

    em = parse_em_status_response(_em_p1(), _PROFILE)
    published = merge_device_status(
        es_mode_data=mode,
        es_status_data=es_status,
        em_status_data=em,
        last_update=1_060.0,
        previous_status=missed,
    )
    assert published["em_input_energy"] == _P1_INPUT_WH
    assert published["em_output_energy"] == _P1_OUTPUT_WH
    assert published["ct_state"] == 1

    poisoned = merge_device_status(
        es_mode_data=mode,
        es_status_data=es_status,
        em_status_data=em,
        last_update=1_120.0,
        previous_status={
            **missed,
            "em_input_energy": 0.0,
            "em_output_energy": 0.0,
        },
    )
    assert poisoned["em_input_energy"] == _P1_INPUT_WH
    assert poisoned["em_output_energy"] == _P1_OUTPUT_WH


def test_real_em_zero_is_still_a_reading() -> None:
    """EM.GetStatus reporting 0 Wh is a meter at zero, not a GetMode placeholder."""
    em = parse_em_status_response(
        {"id": 1, "result": {"ct_state": 1, "input_energy": 0, "output_energy": 0}},
        _PROFILE,
    )
    merged = merge_device_status(
        es_mode_data=parse_es_mode_response(_getmode_zeros(), _PROFILE),
        em_status_data=em,
    )
    assert merged["em_input_energy"] == 0.0
    assert merged["em_output_energy"] == 0.0


async def test_poll_cycle_recovers_p1_totals_after_an_em_timeout() -> None:
    """One timed-out EM.GetStatus, then the reporter's payload, through the client."""
    client = MarstekUDPClient()
    client._socket = MagicMock()
    client._loop = MagicMock()
    client._loop.time.return_value = 1_000.0
    attempts = {"em": 0}

    async def mock_send_request(message: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        method = json.loads(message).get("method")
        if method == "ES.GetMode":
            return _getmode_zeros()
        if method == "ES.GetStatus":
            return _es_status()
        if method == "EM.GetStatus":
            attempts["em"] += 1
            if attempts["em"] == 1:
                raise TimeoutError("EM.GetStatus timeout")
            return _em_p1()
        raise TimeoutError(str(method))

    with (
        patch.object(client, "send_request", side_effect=mock_send_request),
        patch("asyncio.sleep", AsyncMock()),
    ):
        first = await client.get_device_status(
            "10.1.0.12",
            delay_between_requests=0,
            include_pv=False,
            include_wifi=False,
            include_bat=False,
            profile=_PROFILE,
        )
        client._loop.time.return_value = 1_060.0
        second = await client.get_device_status(
            "10.1.0.12",
            delay_between_requests=0,
            include_pv=False,
            include_wifi=False,
            include_bat=False,
            profile=_PROFILE,
            previous_status=first,
        )

    assert "em_input_energy" not in first
    assert "em_output_energy" not in first
    assert second["em_input_energy"] == _P1_INPUT_WH
    assert second["em_output_energy"] == _P1_OUTPUT_WH
    assert second["battery_soc"] == 23
