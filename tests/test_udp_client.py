"""Tests for Marstek UDP client memory management."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.marstek.pymarstek.udp import MarstekUDPClient


@pytest.fixture
def udp_client():
    """Create a UDP client for testing."""
    client = MarstekUDPClient()
    # Mock the event loop time
    client._loop = MagicMock()
    client._loop.time.return_value = 1000.0
    return client


class TestResponseCacheCleanup:
    """Tests for _cleanup_response_cache method."""

    def test_cleanup_empty_cache(self, udp_client):
        """Test cleanup does nothing with empty cache."""
        udp_client._response_cache = {}
        udp_client._cleanup_response_cache()
        assert udp_client._response_cache == {}

    def test_cleanup_removes_stale_entries(self, udp_client):
        """Test cleanup removes entries older than max age."""
        # Current time is 1000.0, max age is 30s
        udp_client._response_cache = {
            1: {"response": {}, "addr": ("1.2.3.4", 30000), "timestamp": 900.0},  # 100s old - stale
            2: {"response": {}, "addr": ("1.2.3.4", 30000), "timestamp": 950.0},  # 50s old - stale
            3: {"response": {}, "addr": ("1.2.3.4", 30000), "timestamp": 980.0},  # 20s old - fresh
            4: {"response": {}, "addr": ("1.2.3.4", 30000), "timestamp": 995.0},  # 5s old - fresh
        }

        udp_client._cleanup_response_cache()

        # Only fresh entries should remain
        assert 1 not in udp_client._response_cache
        assert 2 not in udp_client._response_cache
        assert 3 in udp_client._response_cache
        assert 4 in udp_client._response_cache

    def test_cleanup_caps_cache_size(self, udp_client):
        """Test cleanup removes oldest entries when cache exceeds max size."""
        # Set a smaller max size for testing
        udp_client._response_cache_max_size = 5
        udp_client._response_cache_max_age = 1000.0  # Don't expire by age

        # Add more entries than max size (all fresh)
        udp_client._response_cache = {
            i: {"response": {}, "addr": ("1.2.3.4", 30000), "timestamp": 990.0 + i}
            for i in range(10)
        }

        udp_client._cleanup_response_cache()

        # Should be reduced to roughly half of max size
        assert len(udp_client._response_cache) <= udp_client._response_cache_max_size

    def test_cleanup_preserves_newest_entries(self, udp_client):
        """Test cleanup preserves the newest entries when trimming."""
        udp_client._response_cache_max_size = 4
        udp_client._response_cache_max_age = 1000.0  # Don't expire by age

        udp_client._response_cache = {
            1: {"response": {"id": 1}, "addr": ("1.2.3.4", 30000), "timestamp": 100.0},  # oldest
            2: {"response": {"id": 2}, "addr": ("1.2.3.4", 30000), "timestamp": 200.0},
            3: {"response": {"id": 3}, "addr": ("1.2.3.4", 30000), "timestamp": 300.0},
            4: {"response": {"id": 4}, "addr": ("1.2.3.4", 30000), "timestamp": 400.0},
            5: {"response": {"id": 5}, "addr": ("1.2.3.4", 30000), "timestamp": 500.0},  # newest
        }

        udp_client._cleanup_response_cache()

        # Newest entries should be preserved
        assert 5 in udp_client._response_cache


class TestAsyncCleanup:
    """Tests for async_cleanup method."""

    async def test_cleanup_clears_all_caches(self):
        """Test async_cleanup clears all internal caches."""
        client = MarstekUDPClient()

        # Populate caches
        client._pending_requests = {1: asyncio.Future(), 2: asyncio.Future()}
        client._response_cache = {1: {"response": {}}, 2: {"response": {}}}
        client._discovery_cache = [{"device": "test"}]
        client._last_request_time = {"192.168.1.1": 1000.0}
        client._rate_limit_locks = {"192.168.1.1": asyncio.Lock()}
        client._polling_paused = {"192.168.1.1": True}

        # Mock socket to avoid actual network operations
        client._socket = MagicMock()
        client._listen_task = None

        await client.async_cleanup()

        # All caches should be cleared
        assert client._pending_requests == {}
        assert client._response_cache == {}
        assert client._discovery_cache is None
        assert client._last_request_time == {}
        assert client._rate_limit_locks == {}
        assert client._polling_paused == {}
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
        client._max_tracked_ips = 2  # Low threshold to trigger cleanup

        # Add old entries that should be cleaned up
        client._last_request_time = {
            "192.168.1.1": 100.0,  # 900s old - stale
            "192.168.1.2": 200.0,  # 800s old - stale
            "192.168.1.3": 999.0,  # 1s old - fresh
        }
        client._rate_limit_locks = {
            "192.168.1.1": asyncio.Lock(),
            "192.168.1.2": asyncio.Lock(),
            "192.168.1.3": asyncio.Lock(),
        }

        await client._cleanup_rate_limit_tracking()

        # Stale IPs should be removed
        assert "192.168.1.1" not in client._last_request_time
        assert "192.168.1.2" not in client._last_request_time
        # Fresh IP should remain
        assert "192.168.1.3" in client._last_request_time
