"""Socket setup, teardown and the periodic cleanup passes."""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.marstek.pymarstek.udp import (
    MIN_RESET_PRONE_REQUEST_INTERVAL,
    MarstekUDPClient,
)

from ._helpers import (
    _patch_sock_sendto,
)


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
            mock_socket.setsockopt.assert_any_call(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

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
        client._broadcast_addresses = frozenset({"255.255.255.255", "192.168.1.255"})

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
            i: {"response": {}, "addr": ("1.2.3.4", 30000), "timestamp": 0} for i in range(100)
        }

        recv_count = 0

        async def mock_recvfrom(sock: Any, bufsize: int) -> tuple[bytes, tuple[str, int]]:
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
            "192.168.1.1": current_time - 500,  # Old (> cleanup threshold)
            "192.168.1.2": current_time - 200,  # Old (> cleanup threshold)
            "192.168.1.3": current_time - 10,  # Recent (< cleanup threshold)
            "192.168.1.4": current_time,  # Current (< cleanup threshold)
        }

        await client._cleanup_rate_limit_tracking()

        # Old entries should be removed, recent ones kept
        assert "192.168.1.1" not in client._throttle.last_request_time
        assert "192.168.1.2" not in client._throttle.last_request_time
        assert "192.168.1.3" in client._throttle.last_request_time
        assert "192.168.1.4" in client._throttle.last_request_time
