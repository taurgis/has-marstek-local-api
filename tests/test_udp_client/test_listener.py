"""The response listener task and the command statistics it feeds."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.marstek.pymarstek.udp import (
    MarstekUDPClient,
)

from ._helpers import (
    _complete_first_pending,
)


class TestListenForResponses:
    """Tests for _listen_for_responses method."""

    async def test_handles_non_json_response(self):
        """Test handling of non-JSON responses."""
        client = MarstekUDPClient()
        client._socket = MagicMock()

        recv_calls = 0

        async def mock_recvfrom(sock: Any, bufsize: int) -> tuple[bytes, tuple[str, int]]:
            nonlocal recv_calls
            recv_calls += 1
            if recv_calls == 1:
                return (b"not json", ("192.168.1.100", 30000))
            # Second call: cancel to exit loop
            raise asyncio.CancelledError()

        client._loop = asyncio.get_event_loop()

        with patch.object(client._loop, "sock_recvfrom", mock_recvfrom):
            # The method breaks on CancelledError, doesn't re-raise
            await client._listen_for_responses()

        # Should have processed the non-JSON, then received cancel
        assert recv_calls == 2

    async def test_handles_oserror_and_continues(self):
        """Test that OSError during receive continues loop."""
        client = MarstekUDPClient()
        client._socket = MagicMock()

        recv_calls = 0

        async def mock_recvfrom(sock: Any, bufsize: int) -> tuple[bytes, tuple[str, int]]:
            nonlocal recv_calls
            recv_calls += 1
            if recv_calls == 1:
                raise OSError("Network error")
            # Second call after error: cancel to exit loop
            raise asyncio.CancelledError()

        client._loop = asyncio.get_event_loop()

        with patch.object(client._loop, "sock_recvfrom", mock_recvfrom):
            with patch("asyncio.sleep", AsyncMock()):
                # The method breaks on CancelledError, doesn't re-raise
                await client._listen_for_responses()

        # Should have caught the OSError, slept, then got cancelled
        assert recv_calls == 2

    async def test_matches_response_with_zero_request_id(self) -> None:
        """Test that a wrapped request ID of 0 still resolves pending requests."""
        client = MarstekUDPClient()
        client._socket = MagicMock()

        loop = asyncio.get_event_loop()
        client._loop = loop

        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        client._router.pending[0] = future

        recv_calls = 0
        response = {"id": 0, "result": {"mode": "Auto"}}

        async def mock_recvfrom(sock: Any, bufsize: int) -> tuple[bytes, tuple[str, int]]:
            nonlocal recv_calls
            recv_calls += 1
            if recv_calls == 1:
                return (json.dumps(response).encode(), ("192.168.1.100", 30000))
            raise asyncio.CancelledError()

        with patch.object(loop, "sock_recvfrom", mock_recvfrom):
            await client._listen_for_responses()

        assert recv_calls == 2
        assert future.done()
        assert future.result() == response
        assert client._router.cache[("192.168.1.100", 0)]["response"] == response

    async def test_ignores_empty_udp_datagram(self) -> None:
        """Empty datagrams must not be decoded; Control firmware freezes on them."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        loop = asyncio.get_event_loop()
        client._loop = loop

        recv_calls = 0

        async def mock_recvfrom(sock: Any, bufsize: int) -> tuple[bytes, tuple[str, int]]:
            nonlocal recv_calls
            recv_calls += 1
            if recv_calls == 1:
                return (b"", ("192.168.1.100", 30000))
            raise asyncio.CancelledError()

        with patch.object(loop, "sock_recvfrom", mock_recvfrom):
            await client._listen_for_responses()

        assert recv_calls == 2
        assert client._router.pending == {}
        assert client._router.cache == {}

    async def test_ignores_malformed_utf8_datagram(self) -> None:
        """A non-UTF-8 datagram must not kill the UDP listener."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        loop = asyncio.get_event_loop()
        client._loop = loop

        recv_calls = 0
        response = {"id": 7, "result": {"mode": "Auto"}}
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        client._router.pending[7] = future

        async def mock_recvfrom(sock: Any, bufsize: int) -> tuple[bytes, tuple[str, int]]:
            nonlocal recv_calls
            recv_calls += 1
            if recv_calls == 1:
                return (b"\xff\xfe not utf-8", ("192.168.1.100", 30000))
            if recv_calls == 2:
                return (json.dumps(response).encode(), ("192.168.1.100", 30000))
            raise asyncio.CancelledError()

        with patch.object(loop, "sock_recvfrom", mock_recvfrom):
            await client._listen_for_responses()

        assert recv_calls == 3
        assert future.done()
        assert future.result() == response

    async def test_unexpected_listener_error_does_not_stop_loop(self) -> None:
        """A programming error while decoding one datagram must not stop polling."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        loop = asyncio.get_event_loop()
        client._loop = loop

        recv_calls = 0
        response = {"id": 8, "result": {"mode": "Auto"}}
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        client._router.pending[8] = future

        async def mock_recvfrom(sock: Any, bufsize: int) -> tuple[bytes, tuple[str, int]]:
            nonlocal recv_calls
            recv_calls += 1
            if recv_calls == 1:
                raise RuntimeError("unexpected listener failure")
            if recv_calls == 2:
                return (json.dumps(response).encode(), ("192.168.1.100", 30000))
            raise asyncio.CancelledError()

        with (
            patch.object(loop, "sock_recvfrom", mock_recvfrom),
            patch("asyncio.sleep", AsyncMock()),
        ):
            await client._listen_for_responses()

        assert recv_calls == 3
        assert future.done()
        assert future.result() == response

    @pytest.mark.parametrize(
        "poisoned",
        [
            b'{"id": 9, "result": {"total_power": NaN}}',
            b'{"id": 9, "result": {"total_power": Infinity}}',
            b'{"id": 9, "result": {"total_power": -Infinity}}',
            b'{"id": 9, "result": {"total_power": 1e400}}',
            b'{"id": 9, "result": {"input_energy": 1' + b"0" * 400 + b"}}",
        ],
    )
    async def test_ignores_datagram_carrying_a_non_finite_number(self, poisoned: bytes) -> None:
        """A non-finite number is not JSON; such a reply must never resolve.

        Python's decoder accepts bare NaN/Infinity and overflows 1e400 to inf,
        so without the strict decoder one glitched datagram writes a value into
        coordinator state that no later poll can overwrite.
        """
        client = MarstekUDPClient()
        client._socket = MagicMock()
        loop = asyncio.get_event_loop()
        client._loop = loop

        good = {"id": 9, "result": {"total_power": 120}}
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        client._router.pending[9] = future

        recv_calls = 0

        async def mock_recvfrom(sock: Any, bufsize: int) -> tuple[bytes, tuple[str, int]]:
            nonlocal recv_calls
            recv_calls += 1
            if recv_calls == 1:
                return (poisoned, ("192.168.1.100", 30000))
            if recv_calls == 2:
                return (json.dumps(good).encode(), ("192.168.1.100", 30000))
            raise asyncio.CancelledError()

        with patch.object(loop, "sock_recvfrom", mock_recvfrom):
            await client._listen_for_responses()

        # The poisoned datagram is dropped, and the retry that follows it is
        # still delivered on the same pending id.
        assert recv_calls == 3
        assert future.done()
        assert future.result() == good

    async def test_matches_uint16_truncated_response_id(self) -> None:
        """Control firmware stores JSON-RPC id as uint16 (65537 → 1)."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        loop = asyncio.get_event_loop()
        client._loop = loop

        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        client._router.pending[1] = future

        recv_calls = 0
        response = {"id": 65537, "result": {"mode": "Auto"}}

        async def mock_recvfrom(sock: Any, bufsize: int) -> tuple[bytes, tuple[str, int]]:
            nonlocal recv_calls
            recv_calls += 1
            if recv_calls == 1:
                return (json.dumps(response).encode(), ("192.168.1.100", 30000))
            raise asyncio.CancelledError()

        with patch.object(loop, "sock_recvfrom", mock_recvfrom):
            await client._listen_for_responses()

        assert future.done()
        assert future.result() == response
        assert ("192.168.1.100", 1) in client._router.cache


class TestCommandStats:
    """Tests for command diagnostics tracking."""

    async def test_command_stats_success(self) -> None:
        """Test command stats recorded on success."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        mock_loop = MagicMock()
        mock_loop.time.return_value = 1000.0
        client._loop = mock_loop
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False

        message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})

        async def send_and_complete(*_args: Any, **_kwargs: Any) -> None:
            _complete_first_pending(client)

        with patch.object(client, "_send_udp_message", AsyncMock(side_effect=send_and_complete)):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.1,
                validate=False,
            )

        stats = client.get_command_stats_for_ip("192.168.1.100")
        assert stats["ES.GetStatus"]["total_attempts"] == 1
        assert stats["ES.GetStatus"]["total_success"] == 1
        assert stats["ES.GetStatus"]["total_timeouts"] == 0
        assert stats["ES.GetStatus"]["last_success"] is True

    async def test_command_stats_timeout(self) -> None:
        """Test command stats recorded on timeout."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        mock_loop = MagicMock()
        mock_loop.time.return_value = 1000.0
        client._loop = mock_loop
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False

        message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})

        with patch.object(client, "_send_udp_message", AsyncMock()):
            with pytest.raises(TimeoutError):
                await client.send_request(
                    message,
                    "192.168.1.100",
                    30000,
                    timeout=0.02,
                    validate=False,
                )

        stats = client.get_command_stats_for_ip("192.168.1.100")
        assert stats["ES.GetStatus"]["total_attempts"] == 1
        assert stats["ES.GetStatus"]["total_success"] == 0
        assert stats["ES.GetStatus"]["total_timeouts"] == 1
        assert stats["ES.GetStatus"]["last_timeout"] is True

    async def test_timeout_with_quiet_option(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that quiet_on_timeout suppresses warnings."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        # Use mocked loop to avoid socket blocking mode checks
        mock_loop = MagicMock()
        mock_loop.time.return_value = 1000.0
        client._loop = mock_loop
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False

        message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})

        # Mock send to do nothing - will timeout waiting for response
        with patch.object(client, "_send_udp_message", AsyncMock()):
            with pytest.raises(TimeoutError):
                await client.send_request(
                    message, "192.168.1.100", 30000, timeout=0.01, quiet_on_timeout=True
                )

        # Check no warning was logged (only debug level logs should appear)
        assert "Request timeout" not in caplog.text
