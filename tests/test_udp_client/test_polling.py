"""Poll cycle leases, pause/resume and ES.GetMode instance probing."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.marstek.pymarstek.udp import (
    MarstekUDPClient,
)


class TestPollingControl:
    """Tests for pause_polling and resume_polling."""

    async def test_pause_and_resume(self, udp_client: MarstekUDPClient) -> None:
        """Test pausing and resuming polling."""
        device_ip = "192.168.1.100"

        assert not udp_client.is_polling_paused(device_ip)

        await udp_client.pause_polling(device_ip)
        assert udp_client.is_polling_paused(device_ip)

        await udp_client.resume_polling(device_ip)
        assert not udp_client.is_polling_paused(device_ip)

    async def test_pause_is_ref_counted(self, udp_client: MarstekUDPClient) -> None:
        """Overlapping pause/resume must not unpause while another caller holds it."""
        device_ip = "192.168.1.100"

        await udp_client.pause_polling(device_ip)
        await udp_client.pause_polling(device_ip)
        await udp_client.resume_polling(device_ip)
        assert udp_client.is_polling_paused(device_ip)

        await udp_client.resume_polling(device_ip)
        assert not udp_client.is_polling_paused(device_ip)

        await udp_client.resume_polling(device_ip)
        assert not udp_client.is_polling_paused(device_ip)


class TestPollCycleLease:
    """Tests for coordinator poll-cycle leases used by pause_polling."""

    async def test_pause_polling_waits_for_active_cycle(
        self, udp_client: MarstekUDPClient
    ) -> None:
        """Writers wait until the in-flight poll cycle finishes."""
        device_ip = "192.168.1.100"
        started = asyncio.Event()

        async def cycle() -> None:
            assert await udp_client.begin_poll_cycle(device_ip) is True
            started.set()
            await asyncio.sleep(0.05)
            await udp_client.end_poll_cycle(device_ip)

        task = asyncio.create_task(cycle())
        await started.wait()
        await udp_client.pause_polling(device_ip)
        await task
        assert udp_client.is_polling_paused(device_ip)
        assert await udp_client.begin_poll_cycle(device_ip) is False

    async def test_begin_poll_cycle_skips_when_paused(
        self, udp_client: MarstekUDPClient
    ) -> None:
        """A paused device does not start another coordinator cycle."""
        device_ip = "192.168.1.100"
        await udp_client.pause_polling(device_ip)
        assert await udp_client.begin_poll_cycle(device_ip) is False

    async def test_pause_polling_cancellation_does_not_stick(
        self, udp_client: MarstekUDPClient
    ) -> None:
        """Cancelling pause_polling must not leave polling permanently paused."""
        device_ip = "192.168.1.100"
        assert await udp_client.begin_poll_cycle(device_ip) is True
        pause_task = asyncio.create_task(udp_client.pause_polling(device_ip))
        await asyncio.sleep(0)
        assert not pause_task.done()
        pause_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pause_task
        assert not udp_client.is_polling_paused(device_ip)
        await udp_client.end_poll_cycle(device_ip)
        assert await udp_client.begin_poll_cycle(device_ip) is True
        await udp_client.end_poll_cycle(device_ip)


class TestEsGetModeCompatibility:
    """ES.GetMode accepts both Open API and vendor-library encodings."""

    def _client(self) -> MarstekUDPClient:
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0
        return client

    async def test_string_mode_on_instance_id_0_is_not_retried(self) -> None:
        """Documented string modes on id=0 remain the fast path."""
        client = self._client()
        get_mode_ids: list[int] = []

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = json.loads(message)
            if payload.get("method") == "ES.GetMode":
                get_mode_ids.append(int(payload["params"]["id"]))
                return {
                    "id": 1,
                    "result": {"mode": "Auto", "bat_soc": 55, "ongrid_power": 0},
                }
            return {"id": 2, "result": {"bat_soc": 55, "bat_power": 0}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                result = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    include_em=False,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                )

        assert result["device_mode"] == "auto"
        assert get_mode_ids == [0]
        assert client._es_mode_device_ids["192.168.1.100"] == 0

    async def test_integer_mode_on_instance_id_1_is_cached(self) -> None:
        """Vendor id=1 plus integer mode 4 still maps to UPS and is cached."""
        client = self._client()
        get_mode_ids: list[int] = []

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = json.loads(message)
            if payload.get("method") == "ES.GetMode":
                instance_id = int(payload["params"]["id"])
                get_mode_ids.append(instance_id)
                if instance_id == 0:
                    raise TimeoutError("id 0 timeout")
                return {
                    "id": 1,
                    "result": {"mode": 4, "bat_soc": 80, "ongrid_power": 0},
                }
            return {"id": 2, "result": {"bat_soc": 80, "bat_power": 0}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                first = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    include_em=False,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                )
                second = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    include_em=False,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                )

        assert first["device_mode"] == "ups"
        assert second["device_mode"] == "ups"
        assert get_mode_ids == [0, 1, 1]
        assert client._es_mode_device_ids["192.168.1.100"] == 1

    async def test_jsonrpc_error_on_id_0_falls_back_to_id_1(self) -> None:
        """A JSON-RPC error without result is not treated as a successful GetMode."""
        client = self._client()
        get_mode_ids: list[int] = []

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = json.loads(message)
            if payload.get("method") == "ES.GetMode":
                instance_id = int(payload["params"]["id"])
                get_mode_ids.append(instance_id)
                if instance_id == 0:
                    return {"id": 1, "error": {"code": -32602, "message": "invalid id"}}
                return {"id": 1, "result": {"mode": "Manual", "bat_soc": 40}}
            return {"id": 2, "result": {"bat_soc": 40, "bat_power": 0}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                result = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    include_em=False,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                )

        assert result["device_mode"] == "manual"
        assert get_mode_ids == [0, 1]

    async def test_cached_id_failure_retries_the_other_id(self) -> None:
        """If the cached instance id starts failing, the other id is tried."""
        client = self._client()
        client._es_mode_device_ids["192.168.1.100"] = 1
        get_mode_ids: list[int] = []

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = json.loads(message)
            if payload.get("method") == "ES.GetMode":
                instance_id = int(payload["params"]["id"])
                get_mode_ids.append(instance_id)
                if instance_id == 1:
                    raise TimeoutError("id 1 timeout")
                return {"id": 1, "result": {"mode": "AI"}}
            return {"id": 2, "result": {"bat_soc": 50, "bat_power": 0}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                result = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    include_em=False,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                )

        assert result["device_mode"] == "ai"
        assert get_mode_ids == [1, 0]
        assert client._es_mode_device_ids["192.168.1.100"] == 0

    async def test_parallel_path_still_retries_instance_ids(self) -> None:
        """Parallel polling retries GetMode ids sequentially inside that task."""
        client = self._client()
        get_mode_ids: list[int] = []

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = json.loads(message)
            if payload.get("method") == "ES.GetMode":
                instance_id = int(payload["params"]["id"])
                get_mode_ids.append(instance_id)
                if instance_id == 0:
                    return {"id": 1}
                return {"id": 1, "result": {"mode": 3, "bat_soc": 60}}
            return {"id": 2, "result": {"bat_soc": 60, "bat_power": 0}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            result = await client.get_device_status(
                "192.168.1.100",
                include_em=False,
                include_pv=False,
                include_wifi=False,
                include_bat=False,
                parallel_requests=True,
            )

        assert result["device_mode"] == "passive"
        assert get_mode_ids == [0, 1]

    async def test_id_fallback_skips_inter_method_delay(self) -> None:
        """Retrying GetMode id 1 must not wait the 5s inter-method delay."""
        client = self._client()

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = json.loads(message)
            if payload.get("method") == "ES.GetMode":
                if int(payload["params"]["id"]) == 0:
                    raise TimeoutError("id 0 timeout")
                return {"id": 1, "result": {"mode": "Passive"}}
            return {"id": 2, "result": {"bat_soc": 50, "bat_power": 0}}

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

    async def test_empty_result_on_id_0_does_not_probe_id_1(self) -> None:
        """A result object on id=0 is our success path; do not probe vendor id=1."""
        client = self._client()
        get_mode_ids: list[int] = []

        async def mock_send_request(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = json.loads(message)
            if payload.get("method") == "ES.GetMode":
                get_mode_ids.append(int(payload["params"]["id"]))
                return {"id": 1, "result": {}}
            return {"id": 2, "result": {"bat_soc": 50, "bat_power": 0}}

        with patch.object(client, "send_request", side_effect=mock_send_request):
            with patch("asyncio.sleep", AsyncMock()):
                result = await client.get_device_status(
                    "192.168.1.100",
                    delay_between_requests=0,
                    include_em=False,
                    include_pv=False,
                    include_wifi=False,
                    include_bat=False,
                )

        assert get_mode_ids == [0]
        assert result["device_mode"] is None
        assert client._es_mode_device_ids["192.168.1.100"] == 0
