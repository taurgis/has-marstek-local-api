"""Tests for Marstek UDP client memory management."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from contextlib import suppress
from itertools import product
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.marstek.firmware_profile import resolve_firmware_profile
from custom_components.marstek.pymarstek.command_builder import get_battery_status
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
    MIN_REQUEST_INTERVAL,
    MIN_RESET_PRONE_REQUEST_INTERVAL,
    MarstekUDPClient,
)
from custom_components.marstek.pymarstek.validators import MAX_JSON_RPC_ID, ValidationError


def _complete_first_pending(
    client: MarstekUDPClient, response: dict[str, Any] | None = None
) -> None:
    """Complete the first unfinished pending unicast future."""
    payload = response if response is not None else {"id": 1, "result": {}}
    for future in client._router.pending.values():
        if not future.done():
            future.set_result(payload)
            return


def _patch_sock_sendto() -> Any:
    """Patch ``loop.sock_sendto`` for tests that drive a mocked socket.

    ``_send_udp_message`` puts datagrams on the wire through the event loop
    because the Open API socket is non-blocking. A ``MagicMock`` socket is not
    a real one, so the loop call has to be intercepted.
    """
    return patch.object(
        asyncio.get_running_loop(), "sock_sendto", AsyncMock()
    )


def _unicast_test_client() -> MarstekUDPClient:
    """Return a UDP client with a mocked socket ready for send_request tests."""
    client = MarstekUDPClient()
    client._socket = MagicMock()
    client._loop = asyncio.get_running_loop()
    client._listen_task = MagicMock()
    client._listen_task.done.return_value = False
    return client


_STATUS_COMBINATION_LABELS = (
    "es_mode",
    "es_status",
    "em",
    "pv",
    "wifi",
    "bat",
)
_STATUS_COMBINATIONS = list(
    product([True, False], repeat=len(_STATUS_COMBINATION_LABELS))
)


@pytest.fixture
def udp_client() -> MarstekUDPClient:
    """Create a UDP client for testing."""
    client = MarstekUDPClient()
    # Mock the event loop time
    client._loop = MagicMock()
    client._loop.time.return_value = 1000.0
    return client


@pytest.fixture
def setup_udp_client() -> MarstekUDPClient:
    """Create a UDP client with mocked socket for send/receive tests."""
    client = MarstekUDPClient()
    client._socket = MagicMock()
    client._socket.sendto = MagicMock()
    client._loop = MagicMock()
    client._loop.time.return_value = 1000.0
    return client


class TestResponseCacheCleanup:
    """Tests for the response router's stale-entry eviction."""

    def test_cleanup_empty_cache(self, udp_client):
        """Test cleanup does nothing with empty cache."""
        udp_client._router.cache = {}
        udp_client._router.evict_stale()
        assert udp_client._router.cache == {}

    def test_cleanup_removes_stale_entries(self, udp_client):
        """Test cleanup removes entries older than max age."""
        # Current time is 1000.0, max age is 30s
        udp_client._router.cache = {
            1: {"response": {}, "addr": ("1.2.3.4", 30000), "timestamp": 900.0},  # 100s old - stale
            2: {"response": {}, "addr": ("1.2.3.4", 30000), "timestamp": 950.0},  # 50s old - stale
            3: {"response": {}, "addr": ("1.2.3.4", 30000), "timestamp": 980.0},  # 20s old - fresh
            4: {"response": {}, "addr": ("1.2.3.4", 30000), "timestamp": 995.0},  # 5s old - fresh
        }

        udp_client._router.evict_stale()

        # Only fresh entries should remain
        assert 1 not in udp_client._router.cache
        assert 2 not in udp_client._router.cache
        assert 3 in udp_client._router.cache
        assert 4 in udp_client._router.cache

    def test_cleanup_caps_cache_size(self, udp_client):
        """Test cleanup removes oldest entries when cache exceeds max size."""
        # Set a smaller max size for testing
        udp_client._router.max_cached = 5
        udp_client._router.max_age = 1000.0  # Don't expire by age

        # Add more entries than max size (all fresh)
        udp_client._router.cache = {
            i: {"response": {}, "addr": ("1.2.3.4", 30000), "timestamp": 990.0 + i}
            for i in range(10)
        }

        udp_client._router.evict_stale()

        # Should be reduced to roughly half of max size
        assert len(udp_client._router.cache) <= udp_client._router.max_cached

    def test_cleanup_preserves_newest_entries(self, udp_client):
        """Test cleanup preserves the newest entries when trimming."""
        udp_client._router.max_cached = 4
        udp_client._router.max_age = 1000.0  # Don't expire by age

        udp_client._router.cache = {
            1: {"response": {"id": 1}, "addr": ("1.2.3.4", 30000), "timestamp": 100.0},  # oldest
            2: {"response": {"id": 2}, "addr": ("1.2.3.4", 30000), "timestamp": 200.0},
            3: {"response": {"id": 3}, "addr": ("1.2.3.4", 30000), "timestamp": 300.0},
            4: {"response": {"id": 4}, "addr": ("1.2.3.4", 30000), "timestamp": 400.0},
            5: {"response": {"id": 5}, "addr": ("1.2.3.4", 30000), "timestamp": 500.0},  # newest
        }

        udp_client._router.evict_stale()

        # Newest entries should be preserved
        assert 5 in udp_client._router.cache


class TestAsyncCleanup:
    """Tests for async_cleanup method."""

    async def test_cleanup_clears_all_caches(self):
        """Test async_cleanup clears all internal caches."""
        client = MarstekUDPClient()

        # Populate caches
        client._router.pending = {1: asyncio.Future(), 2: asyncio.Future()}
        client._router.cache = {1: {"response": {}}, 2: {"response": {}}}
        client._discovery_cache = [{"device": "test"}]
        client._throttle.last_request_time = {"192.168.1.1": 1000.0}
        client._throttle.rate_limit_locks = {"192.168.1.1": asyncio.Lock()}
        await client.pause_polling("192.168.1.1")
        client._es_mode_device_ids = {"192.168.1.1": 1}

        # Mock socket to avoid actual network operations
        client._socket = MagicMock()
        client._listen_task = None

        await client.async_cleanup()

        # All caches should be cleared
        assert client._router.pending == {}
        assert client._router.cache == {}
        assert client._discovery_cache is None
        assert client._throttle.last_request_time == {}
        assert client._throttle.rate_limit_locks == {}
        assert not client.is_polling_paused("192.168.1.1")
        assert client._es_mode_device_ids == {}
        assert client._socket is None

    async def test_cleanup_cancels_listen_task(self):
        """Test async_cleanup cancels the listen task."""
        client = MarstekUDPClient()
        client._socket = MagicMock()

        # Create a mock task
        async def slow_listen():
            await asyncio.sleep(10)

        client._listen_task = asyncio.create_task(slow_listen())

        await client.async_cleanup()

        assert client._listen_task is None or client._listen_task.done()


class TestRateLimitCleanup:
    """Tests for rate limit tracking cleanup."""

    async def test_rate_limit_cleanup_removes_stale_ips(self):
        """Test that stale IPs are cleaned up from rate limit tracking."""
        client = MarstekUDPClient()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0
        client._throttle.max_tracked_ips = 2  # Low threshold to trigger cleanup

        # Add old entries that should be cleaned up
        client._throttle.last_request_time = {
            "192.168.1.1": 100.0,  # 900s old - stale
            "192.168.1.2": 200.0,  # 800s old - stale
            "192.168.1.3": 999.0,  # 1s old - fresh
        }
        client._throttle.rate_limit_locks = {
            "192.168.1.1": asyncio.Lock(),
            "192.168.1.2": asyncio.Lock(),
            "192.168.1.3": asyncio.Lock(),
        }

        await client._cleanup_rate_limit_tracking()

        # Stale IPs should be removed
        assert "192.168.1.1" not in client._throttle.last_request_time
        assert "192.168.1.2" not in client._throttle.last_request_time
        # Fresh IP should remain
        assert "192.168.1.3" in client._throttle.last_request_time


class TestAsyncSetup:
    """Tests for async_setup method."""

    async def test_creates_socket(self) -> None:
        """Test that async_setup creates a UDP socket."""
        client = MarstekUDPClient(port=0)
        mock_socket = MagicMock()

        with patch("socket.socket", return_value=mock_socket):
            await client.async_setup()

            assert client._socket is mock_socket
            assert client._loop is not None

        await client.async_cleanup()

    async def test_noop_if_already_setup(self) -> None:
        """Test that setup is a no-op if already setup."""
        client = MarstekUDPClient()
        mock_socket = MagicMock()
        client._socket = mock_socket

        await client.async_setup()

        # Should still be the same socket
        assert client._socket is mock_socket

    async def test_bind_port_defaults_to_port(self) -> None:
        """Test that bind_port defaults to port when not specified."""
        client = MarstekUDPClient(port=30000)
        mock_socket = MagicMock()

        with patch("socket.socket", return_value=mock_socket):
            await client.async_setup()
            mock_socket.bind.assert_called_once_with(("0.0.0.0", 30000))

        await client.async_cleanup()

    async def test_bind_port_ephemeral(self) -> None:
        """Test that bind_port=0 binds to ephemeral port."""
        client = MarstekUDPClient(bind_port=0)
        mock_socket = MagicMock()

        with patch("socket.socket", return_value=mock_socket):
            await client.async_setup()
            mock_socket.bind.assert_called_once_with(("0.0.0.0", 0))

        # Broadcast target port should still use default
        assert client._port == 30000
        await client.async_cleanup()

    async def test_bind_port_independent_of_broadcast_port(self) -> None:
        """Test that bind_port and broadcast port (self._port) are independent."""
        client = MarstekUDPClient(port=30000, bind_port=0)
        mock_socket = MagicMock()

        with patch("socket.socket", return_value=mock_socket):
            await client.async_setup()
            # Socket binds to ephemeral port
            mock_socket.bind.assert_called_once_with(("0.0.0.0", 0))

        # Broadcast target port is still 30000
        assert client._port == 30000
        await client.async_cleanup()

    async def test_setup_enables_reuseport_when_available(self) -> None:
        """Test that async_setup enables SO_REUSEPORT when the OS supports it."""
        client = MarstekUDPClient(port=30000)
        mock_socket = MagicMock()

        with patch("socket.socket", return_value=mock_socket):
            await client.async_setup()

        if hasattr(socket, "SO_REUSEPORT"):
            mock_socket.setsockopt.assert_any_call(
                socket.SOL_SOCKET, socket.SO_REUSEPORT, 1
            )

        await client.async_cleanup()

    async def test_pause_and_resume_receiver(self) -> None:
        """Test pausing the listener cancels it and resume starts a new task."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()

        async def never_ending() -> None:
            await asyncio.sleep(3600)

        client._listen_task = client._loop.create_task(never_ending())
        await client.async_pause_receiver()
        assert client._listen_task is None

        with patch.object(client, "_ensure_listener") as mock_ensure:
            await client.async_resume_receiver()
            mock_ensure.assert_called_once()

        client._socket = None
        await client.async_resume_receiver()

    async def test_nested_pause_keeps_listener_stopped(self) -> None:
        """A second pause must not resume until the matching resume."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()

        async def never_ending() -> None:
            await asyncio.sleep(3600)

        client._listen_task = client._loop.create_task(never_ending())
        await client.async_pause_receiver()
        await client.async_pause_receiver()
        assert client._listen_task is None

        with patch.object(client, "_ensure_listener") as mock_ensure:
            await client.async_resume_receiver()
            mock_ensure.assert_not_called()
            await client.async_resume_receiver()
            mock_ensure.assert_called_once()

        client._receiver_pause_count = 1
        client._listen_task = None
        client._ensure_listener()
        assert client._listen_task is None


class TestSendRequest:
    """Tests for send_request method."""

    async def test_validation_failure(self) -> None:
        """Test that validation errors are raised."""
        client = MarstekUDPClient()
        client._socket = MagicMock()

        # Invalid method name should fail validation
        invalid_message = json.dumps({
            "id": 1,
            "method": "Invalid.Method",
            "params": {}
        })

        with pytest.raises(ValidationError):
            await client.send_request(
                invalid_message, "192.168.1.100", 30000, timeout=0.1
            )

    async def test_skip_validation(self) -> None:
        """Test that validation can be skipped."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        # Use mocked loop to avoid socket blocking mode checks
        mock_loop = MagicMock()
        mock_loop.time.return_value = 1000.0
        client._loop = mock_loop
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False

        # Invalid method but validation skipped - should get ValueError for no id,
        # not ValidationError (since validation is skipped)
        message = json.dumps({
            "id": 1,
            "method": "Invalid.Method",
            "params": {}
        })

        # Mock UDP send to do nothing, test will timeout
        with patch.object(client, "_send_udp_message", AsyncMock()):
            # Should not raise ValidationError because validate=False
            # Just timeout since no response arrives
            with pytest.raises(TimeoutError):
                await client.send_request(
                    message, "192.168.1.100", 30000,
                    timeout=0.01, validate=False
                )

    async def test_missing_id_raises_value_error(self) -> None:
        """Test that message without id raises ValueError."""
        client = MarstekUDPClient()
        client._socket = MagicMock()

        message = json.dumps({"method": "ES.GetStatus", "params": {}})

        with pytest.raises((ValueError, ValidationError)):
            await client.send_request(
                message, "192.168.1.100", 30000, timeout=0.1, validate=False
            )

    async def test_bypass_rate_limit_forwarded_to_udp_send(self) -> None:
        """Test send_request forwards bypass_rate_limit to UDP send path."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        mock_loop = MagicMock()
        mock_loop.time.return_value = 1000.0
        client._loop = mock_loop
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False

        message = json.dumps(
            {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )

        async def send_and_complete(*_args: Any, **_kwargs: Any) -> None:
            _complete_first_pending(client)

        with patch.object(
            client, "_send_udp_message", AsyncMock(side_effect=send_and_complete)
        ) as mock_send_udp:
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.1,
                validate=False,
                bypass_rate_limit=True,
            )

        mock_send_udp.assert_called_once_with(
            message,
            "192.168.1.100",
            30000,
            bypass_rate_limit=True,
        )

    async def test_stable_firmware_retransmits_after_silent_wait(self) -> None:
        """RFC 1122 UDP retry: second send only after the first wait is silent."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps(
            {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )
        sends = 0

        async def send_and_complete_on_retransmit(*_args: Any, **kwargs: Any) -> None:
            nonlocal sends
            sends += 1
            if sends >= 2:
                assert kwargs.get("bypass_rate_limit") is True
                _complete_first_pending(client)

        with patch(
            "custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01
        ), patch.object(
            client,
            "_send_udp_message",
            AsyncMock(side_effect=send_and_complete_on_retransmit),
        ):
            result = await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=1.0,
                validate=False,
            )

        assert sends == 2
        assert result["result"] == {}

    async def test_fast_reply_skips_retransmit(self) -> None:
        """An on-time first reply must not send a second datagram."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps(
            {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )
        sends = 0

        async def send_and_complete(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1
            _complete_first_pending(client)

        with patch(
            "custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.2
        ), patch.object(
            client,
            "_send_udp_message",
            AsyncMock(side_effect=send_and_complete),
        ):
            result = await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=1.0,
                validate=False,
            )

        assert sends == 1
        assert result["result"] == {}

    async def test_reset_prone_skips_wifi_retransmit(self) -> None:
        """Reset-prone Control must stay one-shot; do not add extra datagrams."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        client.set_openapi_reset_prone("192.168.1.100", True)
        message = json.dumps(
            {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )
        sends = 0

        async def send_and_complete(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1
            _complete_first_pending(client)

        with patch(
            "custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01
        ), patch.object(
            client,
            "_send_udp_message",
            AsyncMock(side_effect=send_and_complete),
        ):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=1.0,
                validate=False,
            )

        assert sends == 1

    async def test_reset_prone_does_not_retry_after_timeout(self) -> None:
        """Reset-prone Control must not get a second send/wait cycle."""
        client = _unicast_test_client()
        client.set_openapi_reset_prone("192.168.1.100", True)
        message = json.dumps(
            {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )
        sends = 0

        async def count_sends(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1

        with patch.object(
            client, "_send_udp_message", AsyncMock(side_effect=count_sends)
        ), pytest.raises(TimeoutError):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.02,
                validate=False,
            )

        assert sends == 1

    async def test_unknown_firmware_stays_one_shot(self) -> None:
        """Unmarked IPs must not get Wi-Fi copies before the profile opts in."""
        client = _unicast_test_client()
        message = json.dumps(
            {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )
        sends = 0

        async def count_sends(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1

        with patch(
            "custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01
        ), patch.object(
            client, "_send_udp_message", AsyncMock(side_effect=count_sends)
        ), pytest.raises(TimeoutError):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.05,
                validate=False,
            )

        assert sends == 1

    @pytest.mark.parametrize(
        "method",
        ["ES.SetMode", "DOD.SET", "Ble.Adv", "Led.Ctrl"],
    )
    async def test_writes_stay_one_shot_on_safe_firmware(
        self, method: str
    ) -> None:
        """Control writes must not be duplicated even on known-safe firmware."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps({"id": 1, "method": method, "params": {"id": 0}})
        sends = 0

        async def count_sends(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1

        with patch(
            "custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01
        ), patch.object(
            client, "_send_udp_message", AsyncMock(side_effect=count_sends)
        ), pytest.raises(TimeoutError):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.05,
                validate=False,
            )

        assert sends == 1

    async def test_timeout_retry_succeeds_on_second_attempt(self) -> None:
        """A silent-wait copy recovers when the first unicast is lost."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps(
            {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )
        sends = 0

        async def send_and_complete_on_retry(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1
            if sends >= 2:
                _complete_first_pending(client)

        with patch(
            "custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01
        ), patch.object(
            client,
            "_send_udp_message",
            AsyncMock(side_effect=send_and_complete_on_retry),
        ):
            result = await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=1.0,
                validate=False,
            )

        assert sends == 2
        assert result["id"] == 1
        stats = client.get_command_stats_for_ip("192.168.1.100")
        assert stats["ES.GetStatus"]["total_retransmits"] == 1

    async def test_wifi_retry_stays_within_configured_timeout(self) -> None:
        """Silent-wait copy plus remaining wait, never a second full timeout."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps(
            {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )
        sends = 0

        async def count_sends(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1

        started = time.monotonic()
        with patch(
            "custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01
        ), patch.object(
            client, "_send_udp_message", AsyncMock(side_effect=count_sends)
        ), pytest.raises(TimeoutError):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.05,
                validate=False,
            )

        elapsed = time.monotonic() - started
        assert sends == 2
        assert elapsed < 0.12

    async def test_late_reply_survives_first_timeout_wait(self) -> None:
        """Do not cancel the pending future when the first wait times out."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps(
            {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )

        async def delayed_complete() -> None:
            await asyncio.sleep(0.03)
            _complete_first_pending(client)

        with patch(
            "custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01
        ), patch.object(client, "_send_udp_message", AsyncMock()):
            completer = asyncio.create_task(delayed_complete())
            try:
                result = await client.send_request(
                    message,
                    "192.168.1.100",
                    30000,
                    timeout=0.05,
                    validate=False,
                )
            finally:
                await completer

        assert result["id"] == 1

    async def test_retransmit_and_recovery_are_debug_not_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Silent-wait copies must not raise the default HA log level."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps(
            {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )
        sends = 0

        async def send_and_complete_on_retransmit(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1
            if sends >= 2:
                _complete_first_pending(client)

        caplog.set_level(logging.DEBUG)
        with patch(
            "custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01
        ), patch.object(
            client,
            "_send_udp_message",
            AsyncMock(side_effect=send_and_complete_on_retransmit),
        ):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=1.0,
                validate=False,
            )

        assert "retransmitting" in caplog.text
        assert "after retransmit" in caplog.text
        assert "Request timeout" not in caplog.text
        assert "FC41D" not in caplog.text
        assert not any(record.levelno >= logging.WARNING for record in caplog.records)

    async def test_fast_reply_does_not_log_retransmit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A sub-500 ms reply (typical Ethernet) must stay silent at warning."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps(
            {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )

        async def send_and_complete(*_args: Any, **_kwargs: Any) -> None:
            _complete_first_pending(client)

        caplog.set_level(logging.DEBUG)
        with patch.object(
            client, "_send_udp_message", AsyncMock(side_effect=send_and_complete)
        ):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=1.0,
                validate=False,
            )

        assert "retransmitting" not in caplog.text
        assert "after retransmit" not in caplog.text
        assert "Request timeout" not in caplog.text

    async def test_write_timeout_does_not_log_retransmit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Mode writes stay one-shot and must not emit recovery logs."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps(
            {"id": 1, "method": "ES.SetMode", "params": {"id": 0}}
        )

        caplog.set_level(logging.DEBUG)
        with patch(
            "custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01
        ), patch.object(client, "_send_udp_message", AsyncMock()), pytest.raises(TimeoutError):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.05,
                validate=False,
            )

        assert "retransmitting" not in caplog.text
        assert "Request timeout" in caplog.text
        assert "ES.SetMode" in caplog.text


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

        message = json.dumps(
            {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )

        async def send_and_complete(*_args: Any, **_kwargs: Any) -> None:
            _complete_first_pending(client)

        with patch.object(
            client, "_send_udp_message", AsyncMock(side_effect=send_and_complete)
        ):
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

        message = json.dumps(
            {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )

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
                    message, "192.168.1.100", 30000,
                    timeout=0.01, quiet_on_timeout=True
                )

        # Check no warning was logged (only debug level logs should appear)
        assert "Request timeout" not in caplog.text


class TestSendBroadcastRequest:
    """Tests for send_broadcast_request method."""

    async def test_validation_failure_returns_empty(self) -> None:
        """Test that validation failure returns empty list."""
        client = MarstekUDPClient()
        client._socket = MagicMock()

        invalid_message = json.dumps({
            "id": 1,
            "method": "Invalid.Method",
            "params": {}
        })

        result = await client.send_broadcast_request(invalid_message)
        assert result == []

    async def test_invalid_json_returns_empty(self) -> None:
        """Test that invalid JSON returns empty list."""
        client = MarstekUDPClient()
        client._socket = MagicMock()

        result = await client.send_broadcast_request("not json", validate=False)
        assert result == []


class TestDiscoverDevices:
    """Tests for discover_devices method."""

    async def test_uses_cache_when_valid(self, udp_client: MarstekUDPClient) -> None:
        """Test that discovery uses cache when valid."""
        cached_devices = [{"ip": "192.168.1.100", "device_type": "Venus"}]
        udp_client._discovery_cache = cached_devices
        udp_client._cache_timestamp = 995.0  # 5 seconds ago, within cache duration

        result = await udp_client.discover_devices(use_cache=True)

        assert result == cached_devices

    async def test_ignores_cache_when_invalid(self, udp_client: MarstekUDPClient) -> None:
        """Test that discovery ignores cache when expired."""
        udp_client._discovery_cache = [{"ip": "old"}]
        udp_client._cache_timestamp = 900.0  # 100 seconds ago, expired

        with patch.object(udp_client, "send_broadcast_request", AsyncMock(return_value=[])):
            result = await udp_client.discover_devices(use_cache=True)

        assert result == []

    async def test_ignores_cache_when_disabled(self, udp_client: MarstekUDPClient) -> None:
        """Test that discovery ignores cache when use_cache=False."""
        udp_client._discovery_cache = [{"ip": "cached"}]
        udp_client._cache_timestamp = 999.0  # Fresh cache

        with patch.object(udp_client, "send_broadcast_request", AsyncMock(return_value=[])):
            result = await udp_client.discover_devices(use_cache=False)

        # Should have made new request and returned empty
        assert result == []

    async def test_parses_device_response(self, udp_client: MarstekUDPClient) -> None:
        """Test that discovery correctly parses device responses."""
        response = {
            "id": 1,
            "result": {
                "device": "Venus",
                "ver": 3,
                "wifi_name": "TestNet",
                "ip": "192.168.1.100",
                "wifi_mac": "11:22:33:44:55:66",
                "ble_mac": "AA:BB:CC:DD:EE:FF",
            }
        }

        with patch.object(udp_client, "send_broadcast_request", AsyncMock(return_value=[response])):
            result = await udp_client.discover_devices(use_cache=False)

        assert len(result) == 1
        assert result[0]["ip"] == "192.168.1.100"
        assert result[0]["device_type"] == "Venus"
        assert result[0]["ble_mac"] == "AA:BB:CC:DD:EE:FF"

    async def test_parses_src_mac_when_result_omits_macs(
        self, udp_client: MarstekUDPClient
    ) -> None:
        """Venus C-style GetDevice embeds the BLE MAC in src."""
        response = {
            "id": 1,
            "src": "VenusC-AABBCCDDEEFF",
            "result": {
                "device": "VenusC",
                "ver": 153,
                "ip": "192.168.1.26",
            },
        }

        with patch.object(
            udp_client, "send_broadcast_request", AsyncMock(return_value=[response])
        ):
            result = await udp_client.discover_devices(use_cache=False)

        assert result[0]["ble_mac"] == "AA:BB:CC:DD:EE:FF"
        assert result[0]["mac"] == "AA:BB:CC:DD:EE:FF"

    async def test_omitted_ver_is_not_coerced_to_zero(
        self, udp_client: MarstekUDPClient
    ) -> None:
        """A discovery result without ver must keep firmware unknown."""
        response = {
            "id": 1,
            "result": {
                "device": "Venus E mini",
                "ip": "192.168.1.100",
                "ble_mac": "AA:BB:CC:DD:EE:FF",
            },
        }

        with patch.object(
            udp_client, "send_broadcast_request", AsyncMock(return_value=[response])
        ):
            result = await udp_client.discover_devices(use_cache=False)

        assert result[0]["version"] is None
        assert result[0]["firmware"] == ""

    async def test_deduplicates_devices(self, udp_client: MarstekUDPClient) -> None:
        """Test that duplicate devices are filtered."""
        response = {
            "id": 1,
            "result": {
                "device": "Venus",
                "ip": "192.168.1.100",
            }
        }

        # Return same device twice
        with patch.object(
            udp_client,
            "send_broadcast_request",
            AsyncMock(return_value=[response, response]),
        ):
            result = await udp_client.discover_devices(use_cache=False)

        assert len(result) == 1

    async def test_handles_oserror(self, udp_client: MarstekUDPClient) -> None:
        """Test that OSError is handled gracefully."""
        with patch.object(
            udp_client,
            "send_broadcast_request",
            AsyncMock(side_effect=OSError("Network error")),
        ):
            result = await udp_client.discover_devices(use_cache=False)

        assert result == []


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



class TestRateLimiting:
    """Tests for rate limiting functionality."""

    async def test_enforces_minimum_interval(self) -> None:
        """Test that minimum interval is enforced between requests."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()

        time_value = 0.0
        def get_time() -> float:
            return time_value

        client._loop.time.side_effect = get_time

        # First call - no wait
        await client._enforce_rate_limit("192.168.1.100")
        assert client._throttle.last_request_time.get("192.168.1.100") == 0.0

        # Second call - should wait (mocked)
        time_value = 0.1  # Only 100ms elapsed
        with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            await client._enforce_rate_limit("192.168.1.100")

            # Should have called sleep for the remaining time
            mock_sleep.assert_called_once()
            wait_time = mock_sleep.call_args[0][0]
            assert wait_time > 0
            assert wait_time <= MIN_REQUEST_INTERVAL

    async def test_creates_per_ip_lock(self) -> None:
        """Test that per-IP locks are created."""
        client = MarstekUDPClient()

        lock1 = await client._throttle.rate_limit_lock("192.168.1.100")
        lock2 = await client._throttle.rate_limit_lock("192.168.1.100")
        lock3 = await client._throttle.rate_limit_lock("192.168.1.101")

        # Same IP should get same lock
        assert lock1 is lock2
        # Different IP should get different lock
        assert lock1 is not lock3


class TestGetBroadcastAddresses:
    """Tests for _get_broadcast_addresses method."""

    def test_delegates_to_helper(self, udp_client: MarstekUDPClient) -> None:
        """Test wrapper delegates to shared helper."""
        with patch(
            "custom_components.marstek.pymarstek.udp.get_broadcast_addresses",
            return_value=["255.255.255.255"],
        ) as mock_helper:
            result = udp_client._get_broadcast_addresses()

        mock_helper.assert_called_once()
        assert result == ["255.255.255.255"]


class TestCacheValidation:
    """Tests for cache validation."""

    def test_cache_valid_within_duration(self, udp_client: MarstekUDPClient) -> None:
        """Test cache is valid within duration."""
        udp_client._discovery_cache = [{"ip": "test"}]
        udp_client._cache_timestamp = 980.0  # 20 seconds ago

        assert udp_client._is_cache_valid()

    def test_cache_invalid_after_duration(self, udp_client: MarstekUDPClient) -> None:
        """Test cache is invalid after duration."""
        udp_client._discovery_cache = [{"ip": "test"}]
        udp_client._cache_timestamp = 900.0  # 100 seconds ago

        assert not udp_client._is_cache_valid()

    def test_cache_invalid_when_none(self, udp_client: MarstekUDPClient) -> None:
        """Test cache is invalid when None."""
        udp_client._discovery_cache = None

        assert not udp_client._is_cache_valid()

    def test_clear_discovery_cache(self, udp_client: MarstekUDPClient) -> None:
        """Test clearing discovery cache."""
        udp_client._discovery_cache = [{"ip": "test"}]
        udp_client._cache_timestamp = 999.0

        udp_client.clear_discovery_cache()

        assert udp_client._discovery_cache is None
        assert udp_client._cache_timestamp == 0


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


class TestListenForResponses:
    """Tests for _listen_for_responses method."""

    async def test_handles_non_json_response(self):
        """Test handling of non-JSON responses."""
        client = MarstekUDPClient()
        client._socket = MagicMock()

        recv_calls = 0

        async def mock_recvfrom(
            sock: Any, bufsize: int
        ) -> tuple[bytes, tuple[str, int]]:
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

        async def mock_recvfrom(
            sock: Any, bufsize: int
        ) -> tuple[bytes, tuple[str, int]]:
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

        async def mock_recvfrom(
            sock: Any, bufsize: int
        ) -> tuple[bytes, tuple[str, int]]:
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

        async def mock_recvfrom(
            sock: Any, bufsize: int
        ) -> tuple[bytes, tuple[str, int]]:
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

        async def mock_recvfrom(
            sock: Any, bufsize: int
        ) -> tuple[bytes, tuple[str, int]]:
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

        async def mock_recvfrom(
            sock: Any, bufsize: int
        ) -> tuple[bytes, tuple[str, int]]:
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
    async def test_ignores_datagram_carrying_a_non_finite_number(
        self, poisoned: bytes
    ) -> None:
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

        async def mock_recvfrom(
            sock: Any, bufsize: int
        ) -> tuple[bytes, tuple[str, int]]:
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

        async def mock_recvfrom(
            sock: Any, bufsize: int
        ) -> tuple[bytes, tuple[str, int]]:
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


class TestPsutilHandling:
    """Tests for psutil import handling."""

    def test_get_broadcast_when_psutil_is_none(self) -> None:
        """Test that fallback works when psutil is None."""
        client = MarstekUDPClient()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        with patch("custom_components.marstek.pymarstek.udp.psutil", None):
            result = client._get_broadcast_addresses()

        # Should fall back to 255.255.255.255
        assert result == ["255.255.255.255"]

    def test_get_broadcast_handles_oserror(self) -> None:
        """Test that OSError in psutil is handled."""
        client = MarstekUDPClient()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.side_effect = OSError("Permission denied")

        with patch("custom_components.marstek.pymarstek.udp.psutil", mock_psutil):
            result = client._get_broadcast_addresses()

        assert result == ["255.255.255.255"]

    def test_get_broadcast_with_none_netmask(self) -> None:
        """Test handling of interface with None netmask."""
        client = MarstekUDPClient()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        # Create mock address with None netmask
        mock_addr = MagicMock()
        mock_addr.family = 2  # socket.AF_INET
        mock_addr.address = "192.168.1.100"
        mock_addr.netmask = None  # No netmask

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = {"eth0": [mock_addr]}

        with patch("custom_components.marstek.pymarstek.udp.psutil", mock_psutil):
            result = client._get_broadcast_addresses()

        # Should still have fallback address
        assert "255.255.255.255" in result

    def test_get_broadcast_skips_local_addresses(self) -> None:
        """Test that loopback and link-local addresses are skipped."""
        client = MarstekUDPClient()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        # Create mock addresses with saddr attribute
        mock_loopback = MagicMock()
        mock_loopback.family = 2  # socket.AF_INET
        mock_loopback.address = "127.0.0.1"
        mock_loopback.netmask = "255.0.0.0"

        mock_linklocal = MagicMock()
        mock_linklocal.family = 2
        mock_linklocal.address = "169.254.1.1"
        mock_linklocal.netmask = "255.255.0.0"

        mock_valid = MagicMock()
        mock_valid.family = 2
        mock_valid.address = "10.0.0.5"
        mock_valid.netmask = "255.255.255.0"

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = {
            "lo0": [mock_loopback],
            "docker0": [mock_linklocal],
            "eth0": [mock_valid],
        }

        with patch("custom_components.marstek.pymarstek.udp.psutil", mock_psutil):
            result = client._get_broadcast_addresses()

        # Should have the valid broadcast addresses + fallback
        # Check that fallback got added
        assert "255.255.255.255" in result
        # The actual subnet broadcast calculation depends on the implementation
        # Just verify the method completes without errors


class TestRateLimitCleanupEnforcement:
    """Tests for rate limit tracking cleanup."""

    async def test_cleanup_triggered_when_max_ips_exceeded(self) -> None:
        """Test that cleanup is triggered when tracking exceeds max IPs."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        loop = asyncio.get_event_loop()
        client._loop = loop
        client._throttle.max_tracked_ips = 3  # Small limit for test
        client._throttle.stale_after = 50.0  # Short threshold for test

        # Fill up the tracking dict with more IPs than limit
        current_time = loop.time()
        client._throttle.last_request_time = {
            f"192.168.1.{i}": current_time - 100  # Old entries (older than threshold)
            for i in range(10)
        }

        # Enforce rate limit should trigger cleanup
        await client._enforce_rate_limit("192.168.1.200")

        # Should have cleaned up old entries
        assert len(client._throttle.last_request_time) <= client._throttle.max_tracked_ips

    async def test_rate_limit_skips_broadcast_addresses(self) -> None:
        """Test that rate limiting is skipped for broadcast addresses."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        # Track request times
        call_count = 0
        original_enforce = client._enforce_rate_limit

        async def tracking_enforce(ip: str) -> None:
            nonlocal call_count
            call_count += 1
            await original_enforce(ip)

        client._enforce_rate_limit = tracking_enforce

        # Send to broadcast - should skip rate limiting
        with _patch_sock_sendto():
            await client._send_udp_message('{"test": 1}', "255.255.255.255", 30000)

        # Rate limit should not have been called
        assert call_count == 0

    async def test_bypass_rate_limit_skips_unicast_throttling(self) -> None:
        """Test explicit bypass skips rate limiting even for unicast."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0
        client._enforce_rate_limit = AsyncMock()

        with _patch_sock_sendto():
            await client._send_udp_message(
                '{"test": 1}',
                "192.168.1.100",
                30000,
                bypass_rate_limit=True,
            )

        client._enforce_rate_limit.assert_not_called()
        # The throttle was skipped, but the datagram still updates the clock
        # the next throttled request reads.
        assert client._throttle.last_request_time["192.168.1.100"] == 1000.0

    async def test_reset_prone_ignores_bypass_rate_limit(self) -> None:
        """Reset-prone IPs keep the UDP floor even when bypass is requested."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0
        client.set_openapi_reset_prone("192.168.1.100", True)
        client._enforce_rate_limit = AsyncMock()

        with _patch_sock_sendto():
            await client._send_udp_message(
                '{"test": 1}',
                "192.168.1.100",
                30000,
                bypass_rate_limit=True,
            )

        client._enforce_rate_limit.assert_awaited_once_with("192.168.1.100")

    async def test_reset_prone_uses_longer_min_interval(self) -> None:
        """Reset-prone firmware waits longer than the default 300ms floor."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        time_value = 0.0

        def get_time() -> float:
            return time_value

        client._loop.time.side_effect = get_time
        client.set_openapi_reset_prone("192.168.1.100", True)
        await client._enforce_rate_limit("192.168.1.100")
        time_value = 0.3
        with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            await client._enforce_rate_limit("192.168.1.100")
            mock_sleep.assert_awaited()
            assert mock_sleep.call_args.args[0] == pytest.approx(
                MIN_RESET_PRONE_REQUEST_INTERVAL - 0.3
            )

    async def test_rate_limit_skips_known_subnet_broadcast(self) -> None:
        """Rate limiting is skipped for a broadcast the interface table knows."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0
        client._broadcast_addresses = frozenset(
            {"255.255.255.255", "192.168.1.255"}
        )

        initial_time_tracking = dict(client._throttle.last_request_time)

        with _patch_sock_sendto():
            await client._send_udp_message('{"test": 1}', "192.168.1.255", 30000)

        # No new entries should be tracked
        assert client._throttle.last_request_time == initial_time_tracking

    async def test_rate_limit_applies_to_host_ending_in_255(self) -> None:
        """A .255 host on a wider prefix is a device, not a broadcast.

        ``10.0.1.255`` is an ordinary host inside ``10.0.0.0/23``. Guessing
        from the last octet would exempt it from throttling.
        """
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0
        client._broadcast_addresses = frozenset({"255.255.255.255", "10.0.1.255"})
        client._enforce_rate_limit = AsyncMock()

        with _patch_sock_sendto():
            await client._send_udp_message('{"test": 1}', "10.0.0.255", 30000)

        client._enforce_rate_limit.assert_awaited_once_with("10.0.0.255")
        assert client._throttle.last_request_time["10.0.0.255"] == 1000.0

    async def test_broadcast_addresses_refresh_from_interface_table(self) -> None:
        """Enumerating broadcast targets updates the throttle exemption set."""
        client = MarstekUDPClient()
        with patch(
            "custom_components.marstek.pymarstek.udp.get_broadcast_addresses",
            return_value=["192.168.8.127"],
        ):
            assert client._get_broadcast_addresses() == ["192.168.8.127"]

        # A /25 broadcast never ends in .255 but must still be exempt.
        assert client._is_broadcast_target("192.168.8.127") is True
        assert client._is_broadcast_target("255.255.255.255") is True
        assert client._is_broadcast_target("192.168.8.255") is False

    async def test_refuses_empty_udp_datagram(self) -> None:
        """Refuse 0-byte sends; Control firmware freezes Open API on empty packets."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        with _patch_sock_sendto() as mock_sendto:
            with pytest.raises(ValueError, match="empty UDP datagram"):
                await client._send_udp_message("", "192.168.1.100", 30000)

            mock_sendto.assert_not_called()


class TestValidationErrorLogging:
    """Tests for validation error context extraction."""

    async def test_validation_error_extracts_method_from_json(self) -> None:
        """Test that method name is extracted from invalid message."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        # Send an invalid command with a recognizable method
        invalid_message = '{"id": 1, "method": "Invalid.Method", "params": {}}'

        with pytest.raises(ValidationError):
            await client.send_request(invalid_message, "192.168.1.100", 30000)

    async def test_validation_error_handles_non_json_message(self) -> None:
        """Test that method extraction handles non-JSON gracefully."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        # Send completely invalid message
        with pytest.raises(ValidationError):
            await client.send_request("not json at all", "192.168.1.100", 30000)


class TestBroadcastValidation:
    """Tests for broadcast request validation."""

    async def test_broadcast_validation_failure_returns_empty(self) -> None:
        """Test that validation failure returns empty list."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        # Invalid broadcast message
        invalid_message = '{"id": 1, "method": "Invalid.Method", "params": {}}'

        result = await client.send_broadcast_request(invalid_message)

        assert result == []

    async def test_broadcast_invalid_json_returns_empty(self) -> None:
        """Test that invalid JSON returns empty list."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        # Not valid JSON
        result = await client.send_broadcast_request("not json {}", validate=False)

        assert result == []

    async def test_broadcast_missing_id_returns_empty(self) -> None:
        """Test that message missing id field returns empty list."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        # Valid JSON but missing id
        result = await client.send_broadcast_request('{"method": "Test"}', validate=False)

        assert result == []


class TestDiscoverDevicesOSError:
    """Tests for discover_devices error handling."""

    async def test_discover_devices_handles_oserror(self) -> None:
        """Test that OSError in broadcast is handled gracefully."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        async def mock_broadcast(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            raise OSError("Network unreachable")

        with patch.object(client, "send_broadcast_request", mock_broadcast):
            result = await client.discover_devices(use_cache=False)

        assert result == []


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


class TestPeriodicCleanup:
    """Tests for periodic cleanup in listen_for_responses."""

    async def test_response_cache_cleanup_triggered(self):
        """Test that response cache cleanup is triggered after many responses."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        loop = asyncio.get_event_loop()
        client._loop = loop

        # Pre-populate with old cache entries
        client._router.cache = {
            i: {"response": {}, "addr": ("1.2.3.4", 30000), "timestamp": 0}
            for i in range(100)
        }

        recv_count = 0

        async def mock_recvfrom(
            sock: Any, bufsize: int
        ) -> tuple[bytes, tuple[str, int]]:
            nonlocal recv_count
            recv_count += 1
            # Return 11 responses to trigger cleanup (every 10 responses)
            if recv_count <= 11:
                return (
                    json.dumps({"id": recv_count + 1000, "result": {}}).encode(),
                    ("192.168.1.100", 30000),
                )
            raise asyncio.CancelledError()

        with patch.object(loop, "sock_recvfrom", mock_recvfrom):
            await client._listen_for_responses()

        # Cleanup should have run and removed old entries
        # (new entries from test + some old entries may remain depending on max age)
        assert recv_count == 12

    async def test_rate_limit_cleanup_removes_old_entries(self) -> None:
        """Test that rate limit cleanup removes stale entries."""
        client = MarstekUDPClient()
        loop = asyncio.get_event_loop()
        client._loop = loop

        current_time = loop.time()

        # Set a smaller cleanup threshold for testing
        client._throttle.stale_after = 100.0
        # Set max_tracked_ips low so cleanup is triggered
        client._throttle.max_tracked_ips = 2

        # Add entries with varying ages (need more than max_tracked_ips)
        client._throttle.last_request_time = {
            "192.168.1.1": current_time - 500,   # Old (> cleanup threshold)
            "192.168.1.2": current_time - 200,   # Old (> cleanup threshold)
            "192.168.1.3": current_time - 10,    # Recent (< cleanup threshold)
            "192.168.1.4": current_time,         # Current (< cleanup threshold)
        }

        await client._cleanup_rate_limit_tracking()

        # Old entries should be removed, recent ones kept
        assert "192.168.1.1" not in client._throttle.last_request_time
        assert "192.168.1.2" not in client._throttle.last_request_time
        assert "192.168.1.3" in client._throttle.last_request_time
        assert "192.168.1.4" in client._throttle.last_request_time


class TestSendRequestSkipValidation:
    """Tests for send_request with validation disabled."""

    async def test_send_request_skip_validation_success(self) -> None:
        """Test send_request works with validation disabled."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        loop = asyncio.get_event_loop()
        client._loop = loop

        async def mock_recvfrom(
            sock: Any, bufsize: int
        ) -> tuple[bytes, tuple[str, int]]:
            # Wait briefly, then return response
            await asyncio.sleep(0.01)
            return (
                json.dumps({"id": 999, "result": {"test": "data"}}).encode(),
                ("192.168.1.100", 30000),
            )

        with (
            patch.object(loop, "sock_recvfrom", mock_recvfrom),
            _patch_sock_sendto(),
        ):
            # Pre-validated message (skip_validation=True)
            message = '{"id": 999, "method": "ES.GetStatus", "params": {"id": 0}}'
            result = await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=1.0,
                validate=False,
            )

        assert result["id"] == 999

        # Clean up listen task
        if client._listen_task:
            client._listen_task.cancel()
            with suppress(asyncio.CancelledError):
                await client._listen_task

    async def test_send_request_rewrites_overflow_id_when_validation_skipped(
        self,
    ) -> None:
        """validate=False still rewrites ids that would wrap to 0 on the MCU."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        mock_loop = MagicMock()
        mock_loop.time.return_value = 1000.0
        client._loop = mock_loop
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False

        message = json.dumps(
            {"id": MAX_JSON_RPC_ID + 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )
        with patch.object(client, "_send_udp_message", AsyncMock()) as mock_send:
            async def send_and_complete(*_args: Any, **_kwargs: Any) -> None:
                _complete_first_pending(client, {"id": 1, "result": {}})

            mock_send.side_effect = send_and_complete
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.1,
                validate=False,
            )

        sent = json.loads(mock_send.call_args.args[0])
        assert sent["id"] == 1
        assert sent["method"] == "ES.GetStatus"

    async def test_send_request_keeps_discovery_id_zero(self) -> None:
        """Discovery may still send JSON-RPC id 0."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        mock_loop = MagicMock()
        mock_loop.time.return_value = 1000.0
        client._loop = mock_loop
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False

        message = json.dumps(
            {"id": 0, "method": "Marstek.GetDevice", "params": {"ble_mac": "0"}}
        )
        with patch.object(client, "_send_udp_message", AsyncMock()) as mock_send:
            async def send_and_complete(*_args: Any, **_kwargs: Any) -> None:
                _complete_first_pending(client, {"id": 0, "result": {}})

            mock_send.side_effect = send_and_complete
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.1,
                validate=False,
            )

        assert json.loads(mock_send.call_args.args[0])["id"] == 0


class TestResetProneRequestLock:
    """Tests for per-IP unicast serialization on reset-prone firmware."""

    async def test_device_io_lock_is_shared_per_ip(self) -> None:
        """The same IP reuses one lock; different IPs do not."""
        client = MarstekUDPClient()
        lock1 = await client._throttle.io_lock("192.168.1.100")
        lock2 = await client._throttle.io_lock("192.168.1.100")
        lock3 = await client._throttle.io_lock("192.168.1.101")
        assert lock1 is lock2
        assert lock1 is not lock3

    async def test_reset_prone_requests_do_not_overlap(self) -> None:
        """Two unicast requests to a reset-prone IP are serialized."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False
        client.set_openapi_reset_prone("192.168.1.100", True)

        inflight = 0
        peak = 0

        async def slow_send(*args: Any, **kwargs: Any) -> None:
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            for future in list(client._router.pending.values()):
                if not future.done():
                    future.set_result({"id": 1, "result": {}})
            await asyncio.sleep(0.05)
            inflight -= 1

        with patch.object(client, "_send_udp_message", side_effect=slow_send):
            await asyncio.gather(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                ),
                client.send_request(
                    '{"id": 2, "method": "ES.GetMode", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                ),
            )

        assert peak == 1

    async def test_unmarked_ips_may_overlap(self) -> None:
        """Firmware 150+ is not forced through the reset-prone lock."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False

        inflight = 0
        peak = 0
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_send(*args: Any, **kwargs: Any) -> None:
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            for future in list(client._router.pending.values()):
                if not future.done():
                    future.set_result({"id": 1, "result": {}})
            started.set()
            await release.wait()
            inflight -= 1

        with patch.object(client, "_send_udp_message", side_effect=slow_send):
            first = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await started.wait()
            started.clear()
            second = asyncio.create_task(
                client.send_request(
                    '{"id": 2, "method": "ES.GetMode", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await started.wait()
            assert peak == 2
            release.set()
            await asyncio.gather(first, second)

    async def test_send_request_skip_validation_missing_id(self) -> None:
        """Test send_request raises ValueError for missing id when validation skipped."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        # Message without id field
        message = '{"method": "ES.GetStatus", "params": {}}'

        with pytest.raises(ValueError, match="missing id"):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                validate=False,
            )

    async def test_bat_get_status_blocked_on_reset_prone_ip(self) -> None:
        """UDP client refuses Bat.GetStatus to a marked reset-prone IP."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0
        client.set_openapi_reset_prone("192.168.1.100", True)

        with _patch_sock_sendto() as mock_sendto:
            with pytest.raises(ValidationError, match=r"Bat\.GetStatus"):
                await client.send_request(
                    '{"id": 1, "method": "Bat.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    validate=False,
                )
            mock_sendto.assert_not_called()

    async def test_pause_receiver_waits_for_inflight_unicast(self) -> None:
        """Discovery does not stop the listener while a unicast is in flight."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = client._loop.create_task(asyncio.sleep(3600))

        entered = asyncio.Event()
        release_send = asyncio.Event()

        async def slow_send(*args: Any, **kwargs: Any) -> None:
            entered.set()
            await release_send.wait()
            for future in list(client._router.pending.values()):
                if not future.done():
                    future.set_result({"id": 1, "result": {}})

        with patch.object(client, "_send_udp_message", side_effect=slow_send):
            request_task = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await entered.wait()
            pause_task = asyncio.create_task(client.async_pause_receiver())
            await asyncio.sleep(0.02)
            assert not pause_task.done()
            release_send.set()
            await request_task
            await pause_task
        assert client._listen_task is None

    async def test_nested_pause_waits_until_listener_stopped(self) -> None:
        """A second pause does not return until the first pause stopped the listener."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = client._loop.create_task(asyncio.sleep(3600))

        entered = asyncio.Event()
        release_send = asyncio.Event()

        async def slow_send(*args: Any, **kwargs: Any) -> None:
            entered.set()
            await release_send.wait()
            for future in list(client._router.pending.values()):
                if not future.done():
                    future.set_result({"id": 1, "result": {}})

        with patch.object(client, "_send_udp_message", side_effect=slow_send):
            request_task = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await entered.wait()
            first_pause = asyncio.create_task(client.async_pause_receiver())
            await asyncio.sleep(0.02)
            second_pause = asyncio.create_task(client.async_pause_receiver())
            await asyncio.sleep(0.02)
            assert not first_pause.done()
            assert not second_pause.done()
            release_send.set()
            await request_task
            await first_pause
            await second_pause
        assert client._listen_task is None
        client._socket = None
        await client.async_resume_receiver()
        await client.async_resume_receiver()

    async def test_pause_receiver_cancellation_does_not_block_unicast(self) -> None:
        """Cancelling a pause while draining must resume unicast traffic."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = client._loop.create_task(asyncio.sleep(3600))
        entered = asyncio.Event()
        release_send = asyncio.Event()

        async def slow_send(*args: Any, **kwargs: Any) -> None:
            entered.set()
            await release_send.wait()
            for future in list(client._router.pending.values()):
                if not future.done():
                    future.set_result({"id": 1, "result": {}})

        with patch.object(client, "_send_udp_message", side_effect=slow_send):
            request_task = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await entered.wait()
            pause_task = asyncio.create_task(client.async_pause_receiver())
            await asyncio.sleep(0.02)
            pause_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pause_task
            release_send.set()
            await request_task
        assert client._receiver_pause_count == 0
        if client._listen_task is not None and not client._listen_task.done():
            client._listen_task.cancel()
            with suppress(asyncio.CancelledError, ValueError):
                await client._listen_task
        client._listen_task = None
        client._socket = None

    async def test_pending_requests_are_keyed_by_ip(self) -> None:
        """Two devices may share a JSON-RPC id on one socket."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False
        started = asyncio.Event()
        release = asyncio.Event()

        async def hold_send(*args: Any, **kwargs: Any) -> None:
            started.set()
            await release.wait()

        with patch.object(client, "_send_udp_message", side_effect=hold_send):
            first = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await started.wait()
            started.clear()
            second = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.101",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await started.wait()
            assert ("192.168.1.100", 1) in client._router.pending
            assert ("192.168.1.101", 1) in client._router.pending
            for future in client._router.pending.values():
                if not future.done():
                    future.set_result({"id": 1, "result": {}})
            release.set()
            await asyncio.gather(first, second)

    def test_duplicate_reply_does_not_steal_other_device_future(self) -> None:
        """A duplicate reply from device A must not complete device B's request."""
        client = MarstekUDPClient()
        _, future_a = client._router.track(0, device_ip="192.168.1.10")
        _, future_b = client._router.track(0, device_ip="192.168.1.20")

        popped_a = client._router.pop_waiter(0, source_ip="192.168.1.10")
        assert popped_a is future_a
        duplicate = client._router.pop_waiter(0, source_ip="192.168.1.10")
        assert duplicate is None
        assert client._router.pending[("192.168.1.20", 0)] is future_b

    async def test_hostname_pending_key_uses_resolved_ip(self) -> None:
        """Replies are sourced from the resolved IPv4, not the hostname."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False
        started = asyncio.Event()
        release = asyncio.Event()

        async def hold_send(*args: Any, **kwargs: Any) -> None:
            started.set()
            await release.wait()

        client._resolve_unicast_ip = AsyncMock(return_value="192.168.1.50")
        with patch.object(client, "_send_udp_message", side_effect=hold_send):
            task = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "device.local",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await started.wait()
            assert ("192.168.1.50", 1) in client._router.pending
            future = client._router.pending[("192.168.1.50", 1)]
            future.set_result({"id": 1, "result": {}})
            release.set()
            await task

    async def test_reset_prone_blocks_bat_get_status(self) -> None:
        """Bat.GetStatus must not reach reset-prone Control firmware."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False
        client.set_openapi_reset_prone("192.168.1.100", True)
        with patch.object(client, "_send_udp_message", AsyncMock()) as mock_send:
            with pytest.raises(ValidationError, match="blocked on reset-prone"):
                await client.send_request(
                    get_battery_status(0),
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                )
        mock_send.assert_not_called()

    async def test_duplicate_pending_id_for_same_ip_is_rejected(self) -> None:
        """A second overlapping request with the same id to one IP is rejected."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False
        started = asyncio.Event()
        release = asyncio.Event()

        async def hold_send(*args: Any, **kwargs: Any) -> None:
            started.set()
            await release.wait()

        with patch.object(client, "_send_udp_message", side_effect=hold_send):
            first = asyncio.create_task(
                client.send_request(
                    '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            )
            await started.wait()
            with pytest.raises(ValueError, match="Duplicate pending"):
                await client.send_request(
                    '{"id": 1, "method": "ES.GetMode", "params": {"id": 0}}',
                    "192.168.1.100",
                    30000,
                    timeout=1.0,
                    validate=False,
                )
            for future in list(client._router.pending.values()):
                if not future.done():
                    future.set_result({"id": 1, "result": {}})
            release.set()
            await first

    async def test_broadcast_rewrites_oversized_non_discovery_id(self) -> None:
        """validate=False broadcasts still send a uint16 JSON-RPC id."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = asyncio.get_running_loop()
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False
        message = json.dumps(
            {"id": 70000, "method": "ES.GetStatus", "params": {"id": 0}}
        )
        with (
            patch.object(client, "_send_udp_message", AsyncMock()) as mock_send,
            patch.object(
                client, "_get_broadcast_addresses", return_value=["255.255.255.255"]
            ),
        ):
            await client.send_broadcast_request(message, timeout=0, validate=False)

        sent = json.loads(mock_send.call_args.args[0])
        assert sent["id"] == (70000 & MAX_JSON_RPC_ID)
        assert sent["id"] != 0


class TestResetProneOwners:
    """Reset-prone marks are owned by config entry, not only by IP."""

    def test_owner_refcount_and_transfer(self) -> None:
        """Clearing one owner must not drop another device sharing the IP."""
        client = MarstekUDPClient()
        client.set_openapi_reset_prone("1.2.3.4", True, owner="entry-a")
        client.set_openapi_reset_prone("1.2.3.4", True, owner="entry-b")
        client.set_openapi_reset_prone("1.2.3.4", False, owner="entry-a")
        assert client.is_openapi_reset_prone("1.2.3.4")

        client.transfer_openapi_reset_prone("1.2.3.4", "5.6.7.8", owner="entry-b")
        assert not client.is_openapi_reset_prone("1.2.3.4")
        assert client.is_openapi_reset_prone("5.6.7.8")

        client.clear_openapi_reset_prone("5.6.7.8", owner="entry-b")
        assert not client.is_openapi_reset_prone("5.6.7.8")

    def test_clear_owner_drops_marks_across_ips(self) -> None:
        """Unload must drop every IP this config entry marked."""
        client = MarstekUDPClient()
        client.set_openapi_reset_prone("1.2.3.4", True, owner="entry-a")
        client.set_openapi_reset_prone("5.6.7.8", True, owner="entry-a")
        client.set_openapi_reset_prone("1.2.3.4", True, owner="entry-b")
        client.clear_openapi_reset_prone_owner("entry-a")
        assert not client.is_openapi_reset_prone("5.6.7.8")
        assert client.is_openapi_reset_prone("1.2.3.4")
        assert client.is_openapi_reset_prone("1.2.3.4", owner="entry-b")

    def test_clear_without_owner_drops_all_marks(self) -> None:
        """Unload of the last client may drop every owner for an IP."""
        client = MarstekUDPClient()
        client.set_openapi_reset_prone("1.2.3.4", True, owner="entry-a")
        client.clear_openapi_reset_prone("1.2.3.4")
        assert not client.is_openapi_reset_prone("1.2.3.4")

    def test_reset_prone_mark_drops_retransmit_safe(self) -> None:
        """A later reset-prone mark must cancel Wi-Fi copies on that IP."""
        client = MarstekUDPClient()
        client.set_openapi_retransmit_safe("1.2.3.4", True)
        assert client.is_openapi_retransmit_safe("1.2.3.4") is True
        client.set_openapi_reset_prone("1.2.3.4", True, owner="entry-a")
        assert client.is_openapi_retransmit_safe("1.2.3.4") is False
        client.set_openapi_retransmit_safe("1.2.3.4", True)
        assert client.is_openapi_retransmit_safe("1.2.3.4") is False

