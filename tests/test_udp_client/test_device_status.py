"""Merged status polling, including partial and tiered failures."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.marstek.firmware_profile import resolve_firmware_profile
from custom_components.marstek.pymarstek.data_parser import (
    merge_device_status,
    parse_bat_status_response,
    parse_em_status_response,
    parse_es_mode_response,
    parse_es_status_response,
    parse_pv_status_response,
    parse_wifi_status_response,
)
from custom_components.marstek.pymarstek.udp import (
    MarstekUDPClient,
)
from custom_components.marstek.pymarstek.validators import ValidationError

from ._helpers import (
    _STATUS_COMBINATION_LABELS,
    _STATUS_COMBINATIONS,
)


class TestGetDeviceStatus:
    """Tests for get_device_status method."""

    async def test_successful_full_status(self) -> None:
        """Test getting full device status successfully."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        # Mock responses for each status call
        responses = [
            {"id": 1, "result": {"mode": 0, "gridpower": 100}},  # ES.GetMode
            {"id": 2, "result": {"soc": 50, "batw": 200}},  # ES.GetStatus
            {"id": 3, "result": {"state": 1, "aw": 10, "bw": 20, "cw": 30}},  # EM.GetStatus
            {"id": 4, "result": {"p1": 100, "p2": 0, "p3": 0, "p4": 0}},  # PV.GetStatus
            {"id": 5, "result": {"rssi": -50, "ssid": "TestNet"}},  # Wifi.GetStatus
            {"id": 6, "result": {"temp": 25, "cflag": 1, "dflag": 0}},  # Bat.GetStatus
        ]
        response_iter = iter(responses)

        async def mock_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                return next(response_iter)
            except StopIteration:
                return {}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                result = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                )

        assert result["has_fresh_data"]
        # Check merged data
        assert "device_mode" in result or "ongrid_power" in result

    async def test_profile_is_per_call_and_does_not_leak(self) -> None:
        """The shared UDP client must not reuse another entry's scaling profile."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0
        methods: list[str] = []

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            method = str(json.loads(message).get("method"))
            methods.append(method)
            if method == "ES.GetStatus":
                return {
                    "id": 2,
                    "result": {
                        "bat_soc": 55,
                        "total_pv_energy": 25742,
                        "total_grid_input_energy": 1607,
                    },
                }
            if method == "EM.GetStatus":
                return {
                    "id": 3,
                    "result": {
                        "ct_state": 1,
                        "input_energy": 3086320,
                        "output_energy": 0,
                    },
                }
            return {"id": 1, "result": {"mode": "Auto"}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                scaled = await client.get_device_status(
                    "192.168.1.10",
                    delay_between_requests=0,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                    profile=resolve_firmware_profile("VenusA", 149),
                )
                legacy = await client.get_device_status(
                    "192.168.1.11",
                    delay_between_requests=0,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                    profile=resolve_firmware_profile("VenusD", 145),
                )

        assert scaled["total_pv_energy"] == 257420
        assert scaled["total_grid_input_energy"] == 1607
        assert legacy["total_pv_energy"] == 25742
        assert legacy["em_input_energy"] == 3086320
        assert methods.count("ES.GetMode") == 2
        assert methods.count("ES.GetStatus") == 2
        assert methods.count("EM.GetStatus") == 2
        assert "PV.GetStatus" not in methods
        assert len(methods) == 6

    async def test_sequential_mode_respects_delay_between_requests(self) -> None:
        """Test default sequential mode waits between request calls."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        bypass_flags: list[bool] = []

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            bypass_flags.append(bool(kwargs.get("bypass_rate_limit", False)))
            method = json.loads(message).get("method")
            if method == "ES.GetMode":
                return {"id": 1, "result": {"mode": 0, "gridpower": 100}}
            if method == "ES.GetStatus":
                return {"id": 2, "result": {"soc": 50, "batw": 200}}
            return {"id": 0, "result": {}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
                await client.get_device_status(
                    "192.168.1.100",
                    include_em=False,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                    parallel_requests=False,
                    delay_between_requests=1.5,
                )

        mock_sleep.assert_called_once_with(1.5)
        assert bypass_flags == [False, False]

    async def test_parallel_mode_skips_inter_request_delay(self) -> None:
        """Test parallel mode does not sleep between requests."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        bypass_flags: list[bool] = []

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            bypass_flags.append(bool(kwargs.get("bypass_rate_limit", False)))
            method = json.loads(message).get("method")
            if method == "ES.GetMode":
                return {"id": 1, "result": {"mode": 0, "gridpower": 100}}
            if method == "ES.GetStatus":
                return {"id": 2, "result": {"soc": 50, "batw": 200}}
            return {"id": 0, "result": {}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
                result = await client.get_device_status(
                    "192.168.1.100",
                    include_em=False,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                    parallel_requests=True,
                    delay_between_requests=5.0,
                )

        assert result["has_fresh_data"]
        mock_sleep.assert_not_called()
        assert bypass_flags == [True, True]

    async def test_parallel_mode_starts_requests_concurrently(self) -> None:
        """Test parallel mode can start multiple requests before any returns."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        release = asyncio.Event()
        started_methods: set[str] = set()

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            method = str(json.loads(message).get("method"))
            started_methods.add(method)
            await release.wait()
            if method == "ES.GetMode":
                return {"id": 1, "result": {"mode": 0, "gridpower": 100}}
            if method == "ES.GetStatus":
                return {"id": 2, "result": {"soc": 50, "batw": 200}}
            return {"id": 0, "result": {}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            status_task = asyncio.create_task(
                client.get_device_status(
                    "192.168.1.100",
                    include_em=False,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                    parallel_requests=True,
                    delay_between_requests=5.0,
                )
            )

            for _ in range(20):
                if {"ES.GetMode", "ES.GetStatus"}.issubset(started_methods):
                    break
                await asyncio.sleep(0)

            assert {"ES.GetMode", "ES.GetStatus"}.issubset(started_methods)
            release.set()
            result = await status_task

        assert result["has_fresh_data"]

    async def test_parallel_mode_full_status_with_all_tiers(self) -> None:
        """Test parallel mode schedules all enabled tier requests."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            method = json.loads(message).get("method")
            if method == "ES.GetMode":
                return {"id": 1, "result": {"mode": "Auto", "bat_soc": 55, "ongrid_power": 123}}
            if method == "ES.GetStatus":
                return {
                    "id": 2,
                    "result": {
                        "bat_soc": 66,
                        "bat_power": 150,
                        "pv_power": 400,
                        "ongrid_power": 200,
                    },
                }
            if method == "EM.GetStatus":
                return {
                    "id": 3,
                    "result": {"ct_state": 1, "a_power": 10, "b_power": 11, "c_power": 12},
                }
            if method == "PV.GetStatus":
                return {
                    "id": 4,
                    "result": {"pv1_power": 700, "pv1_voltage": 35, "pv1_current": 2.0},
                }
            if method == "Wifi.GetStatus":
                return {"id": 5, "result": {"rssi": -60, "ssid": "TestNet"}}
            if method == "Bat.GetStatus":
                return {"id": 6, "result": {"bat_temp": 30.5, "charg_flag": 1, "dischrg_flag": 0}}
            return {"id": 0, "result": {}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
                result = await client.get_device_status(
                    "192.168.1.100",
                    parallel_requests=True,
                    delay_between_requests=5.0,
                )

        assert result["has_fresh_data"]
        assert result.get("device_mode") is not None
        assert result.get("wifi_ssid") == "TestNet"
        mock_sleep.assert_not_called()

    async def test_rejected_es_mode_reply_does_not_escape_the_poll(self) -> None:
        """ValidationError is not a ValueError, so it needs naming explicitly.

        Every other read treats a rejected reply as "contributed nothing";
        ES.GetMode must not be the one that takes the whole poll down.
        """
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        async def mock_send_request(
            message: str, *args: Any, **kwargs: Any
        ) -> dict[str, Any]:
            method = str(json.loads(message).get("method"))
            if method == "ES.GetMode":
                raise ValidationError("reply failed validation")
            if method == "ES.GetStatus":
                return {"id": 2, "result": {"bat_soc": 66, "bat_power": 150}}
            return {"id": 0, "result": {}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            assert await client.fetch_es_mode("192.168.1.100") is None

            result = await client.get_device_status(
                "192.168.1.100",
                include_em=False,
                include_pv=False,
                include_wifi=False,
                include_bat=False,
            )

        assert result["has_fresh_data"]
        assert result["battery_soc"] == 66

    async def test_parallel_poll_contains_an_unexpected_error(self) -> None:
        """A read that blows up contributes nothing, like one that timed out.

        Without containment the exception would leave the gather, abandoning
        its siblings: they would go on sending UDP to a device whose poll had
        already ended, and Python would report each as a never-retrieved task
        exception.
        """
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        async def mock_send_request(
            message: str, *args: Any, **kwargs: Any
        ) -> dict[str, Any]:
            method = str(json.loads(message).get("method"))
            if method == "Wifi.GetStatus":
                raise RuntimeError("socket went away mid-poll")
            if method == "ES.GetStatus":
                return {"id": 2, "result": {"bat_soc": 66, "bat_power": 150}}
            return {"id": 0, "result": {}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            result = await client.get_device_status(
                "192.168.1.100",
                include_em=False,
                include_pv=False,
                include_wifi=True,
                include_bat=False,
                parallel_requests=True,
            )

        assert result["has_fresh_data"]
        assert result["battery_soc"] == 66
        assert result.get("wifi_ssid") is None

    async def test_partial_failure_preserves_data(self) -> None:
        """Test that partial failures preserve previous data."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        call_count = 0

        async def mock_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"id": 1, "result": {"mode": 0, "gridpower": 100}}
            raise TimeoutError("Request timeout")

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                result = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                )

        # Should still have some data from successful calls
        assert result["has_fresh_data"]

    @pytest.mark.parametrize(
        "es_mode_ok, es_status_ok, em_ok, pv_ok, wifi_ok, bat_ok",
        _STATUS_COMBINATIONS,
        ids=[
            "-".join(
                f"{label}:{'ok' if flag else 'fail'}"
                for label, flag in zip(_STATUS_COMBINATION_LABELS, combo, strict=True)
            )
            for combo in _STATUS_COMBINATIONS
        ],
    )
    async def test_all_status_combinations(
        self,
        es_mode_ok: bool,
        es_status_ok: bool,
        em_ok: bool,
        pv_ok: bool,
        wifi_ok: bool,
        bat_ok: bool,
    ) -> None:
        """Test all success/failure combinations across status requests."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        previous_status = {
            "battery_soc": 10,
            "battery_power": 111,
            "battery_status": "idle",
            "ongrid_power": 5,
            "offgrid_power": 6,
            "device_mode": "manual",
            "ct_state": 0,
            "ct_connected": False,
            "em_a_power": 1,
            "em_b_power": 2,
            "em_c_power": 3,
            "em_total_power": 4,
            "pv1_power": 9.0,
            "pv1_voltage": 90.0,
            "pv1_current": 0.9,
            "pv1_state": 1,
            "pv_power": 123.0,
            "wifi_rssi": -80,
            "wifi_ssid": "PrevNet",
            "wifi_sta_ip": "1.1.1.1",
            "wifi_sta_gate": "1.1.1.254",
            "wifi_sta_mask": "255.255.255.0",
            "wifi_sta_dns": "8.8.8.8",
            "bat_temp": 20.5,
            "bat_charg_flag": 0,
            "bat_dischrg_flag": 1,
            "bat_capacity": 1000,
            "bat_rated_capacity": 2000,
            "bat_soc_detailed": 40,
        }

        es_mode_response = {
            "id": 1,
            "result": {"mode": "Auto", "bat_soc": 55, "ongrid_power": 123},
        }
        es_status_response = {
            "id": 2,
            "result": {
                "bat_soc": 66,
                "bat_cap": 5120,
                "bat_power": 150,
                "pv_power": 400,
                "ongrid_power": 200,
                "offgrid_power": 50,
                "total_pv_energy": 1000,
                "total_grid_output_energy": 2000,
                "total_grid_input_energy": 3000,
                "total_load_energy": 4000,
            },
        }
        em_status_response = {
            "id": 3,
            "result": {
                "ct_state": 1,
                "a_power": 10,
                "b_power": 11,
                "c_power": 12,
                "total_power": 33,
            },
        }
        pv_status_response = {
            "id": 4,
            "result": {
                "pv1_power": 700,
                "pv1_voltage": 35,
                "pv1_current": 2.0,
                "pv1_state": 1,
            },
        }
        wifi_status_response = {
            "id": 5,
            "result": {
                "rssi": -60,
                "ssid": "TestNet",
                "sta_ip": "192.168.1.10",
                "sta_gate": "192.168.1.1",
                "sta_mask": "255.255.255.0",
                "sta_dns": "8.8.8.8",
            },
        }
        bat_status_response = {
            "id": 6,
            "result": {
                "bat_temp": 30.5,
                "charg_flag": 1,
                "dischrg_flag": 0,
                "bat_capacity": 2500,
                "rated_capacity": 5000,
                "soc": 77,
            },
        }

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            method = json.loads(message).get("method")
            if method == "ES.GetMode":
                if es_mode_ok:
                    return es_mode_response
                raise TimeoutError("ES.GetMode timeout")
            if method == "ES.GetStatus":
                if es_status_ok:
                    return es_status_response
                raise TimeoutError("ES.GetStatus timeout")
            if method == "EM.GetStatus":
                if em_ok:
                    return em_status_response
                raise TimeoutError("EM.GetStatus timeout")
            if method == "PV.GetStatus":
                if pv_ok:
                    return pv_status_response
                raise TimeoutError("PV.GetStatus timeout")
            if method == "Wifi.GetStatus":
                if wifi_ok:
                    return wifi_status_response
                raise TimeoutError("Wifi.GetStatus timeout")
            if method == "Bat.GetStatus":
                if bat_ok:
                    return bat_status_response
                raise TimeoutError("Bat.GetStatus timeout")
            return {}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                result = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    previous_status=previous_status,
                )

        es_mode_data = parse_es_mode_response(es_mode_response) if es_mode_ok else None
        es_status_data = (
            parse_es_status_response(es_status_response) if es_status_ok else None
        )
        em_status_data = parse_em_status_response(em_status_response) if em_ok else None
        pv_status_data = parse_pv_status_response(pv_status_response) if pv_ok else None
        wifi_status_data = (
            parse_wifi_status_response(wifi_status_response) if wifi_ok else None
        )
        bat_status_data = (
            parse_bat_status_response(bat_status_response) if bat_ok else None
        )

        expected = merge_device_status(
            es_mode_data=es_mode_data,
            es_status_data=es_status_data,
            pv_status_data=pv_status_data,
            wifi_status_data=wifi_status_data,
            em_status_data=em_status_data,
            bat_status_data=bat_status_data,
            device_ip="192.168.1.100",
            last_update=1000.0,
            previous_status=previous_status,
        )
        expected["has_fresh_data"] = any(
            (es_mode_ok, es_status_ok, em_ok, pv_ok, wifi_ok, bat_ok)
        )

        assert result == expected

    async def test_all_failures_uses_previous_status(self) -> None:
        """Test that all failures fall back to previous status."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        previous_status = {"battery_soc": 75, "device_mode": "Auto"}

        async def mock_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise TimeoutError("Request timeout")

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                result = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    previous_status=previous_status,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                )

        # Previous values should be preserved
        assert result.get("battery_soc") == 75
        # No fresh data
        assert not result["has_fresh_data"]

    async def test_jsonrpc_errors_are_not_fresh_data(self) -> None:
        """JSON-RPC errors must not reset coordinator failure handling."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0
        previous_status = {"battery_soc": 80, "device_mode": "Auto"}
        error = {"id": 1, "error": {"code": -32601, "message": "Method not found"}}

        async def mock_send_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return error

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                result = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    previous_status=previous_status,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                )

        assert result["battery_soc"] == 80
        assert result["device_mode"] == "Auto"
        assert result["has_fresh_data"] is False

    async def test_jsonrpc_error_still_applies_request_delay(self) -> None:
        """A JSON-RPC error still counts as a transmitted request for spacing."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0
        error = {"id": 1, "error": {"code": -32601, "message": "Method not found"}}

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            if json.loads(message).get("method") == "ES.GetMode":
                return error
            return {"id": 2, "result": {"bat_soc": 40, "bat_power": 0}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
                result = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=1.5,
                    include_em=False,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                )

        assert mock_sleep.await_count >= 1
        assert mock_sleep.await_args is not None
        assert mock_sleep.await_args.args[0] == 1.5
        assert result["has_fresh_data"] is True
        assert result["battery_soc"] == 40


class TestGetDeviceStatusTieredFailures:
    """Tests for get_device_status individual tier failures."""

    async def test_pv_status_failure_continues(self) -> None:
        """Test that PV status failure doesn't break other requests."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        call_count = 0

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if "PV.GetStatus" in message:
                raise TimeoutError("PV request timeout")
            if "ES.GetMode" in message:
                return {"id": 1, "result": {"mode": 0}}
            if "ES.GetStatus" in message:
                return {"id": 2, "result": {"soc": 50}}
            if "EM.GetStatus" in message:
                return {"id": 3, "result": {"state": 1}}
            return {}

        with patch.object(client, "send_request", mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                result = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    include_wifi=False,
                    include_bat=False,
                )

        # Should still have data from other requests
        assert result["has_fresh_data"]

    async def test_wifi_status_failure_continues(self) -> None:
        """Test that WiFi status failure doesn't break other data."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            if "Wifi.GetStatus" in message:
                raise OSError("WiFi query failed")
            if "ES.GetMode" in message:
                return {"id": 1, "result": {"mode": 0}}
            if "ES.GetStatus" in message:
                return {"id": 2, "result": {"soc": 50}}
            return {}

        with patch.object(client, "send_request", mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                result = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    include_pv=False,
                    include_bat=False,
                    include_em=False,
                )

        assert result["has_fresh_data"]

    async def test_bat_status_failure_continues(self) -> None:
        """Test that battery status (slow tier) failure continues gracefully."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            if "Bat.GetStatus" in message:
                raise ValueError("Invalid battery response")
            if "ES.GetMode" in message:
                return {"id": 1, "result": {"mode": 0}}
            if "ES.GetStatus" in message:
                return {"id": 2, "result": {"soc": 75}}
            return {}

        with patch.object(client, "send_request", mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                result = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    include_pv=False,
                    include_wifi=False,
                    include_em=False,
                )

        assert result["has_fresh_data"]

    async def test_include_bat_false_never_sends_bat_get_status(self) -> None:
        """Test Bat.GetStatus is not sent when include_bat=False, even with
        other optional tiers enabled (and vice versa for Wifi.GetStatus)."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        sent_methods: list[str] = []

        async def mock_send_request(
            message: str, *args: Any, **kwargs: Any
        ) -> dict[str, Any]:
            method = json.loads(message).get("method")
            sent_methods.append(method)
            return {"id": 1, "result": {"mode": 0, "soc": 50}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    include_pv=False,
                    include_wifi=True,
                    include_em=True,
                    include_bat=False,
                )

        assert "Wifi.GetStatus" in sent_methods
        assert "Bat.GetStatus" not in sent_methods

        sent_methods.clear()
        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    include_pv=False,
                    include_wifi=False,
                    include_em=True,
                    include_bat=True,
                )

        assert "Bat.GetStatus" in sent_methods
        assert "Wifi.GetStatus" not in sent_methods
