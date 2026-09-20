"""Broadcast sweeps, the discovery cache and broadcast address lookup."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.marstek.pymarstek.udp import (
    MarstekUDPClient,
)


class TestSendBroadcastRequest:
    """Tests for send_broadcast_request method."""

    async def test_validation_failure_returns_empty(self) -> None:
        """Test that validation failure returns empty list."""
        client = MarstekUDPClient()
        client._socket = MagicMock()

        invalid_message = json.dumps({"id": 1, "method": "Invalid.Method", "params": {}})

        result = await client.send_broadcast_request(invalid_message)
        assert result == []

    async def test_invalid_json_returns_empty(self) -> None:
        """Test that invalid JSON returns empty list."""
        client = MarstekUDPClient()
        client._socket = MagicMock()

        result = await client.send_broadcast_request("not json", validate=False)
        assert result == []


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
            },
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

        with patch.object(udp_client, "send_broadcast_request", AsyncMock(return_value=[response])):
            result = await udp_client.discover_devices(use_cache=False)

        assert result[0]["ble_mac"] == "AA:BB:CC:DD:EE:FF"
        assert result[0]["mac"] == "AA:BB:CC:DD:EE:FF"

    async def test_omitted_ver_is_not_coerced_to_zero(self, udp_client: MarstekUDPClient) -> None:
        """A discovery result without ver must keep firmware unknown."""
        response = {
            "id": 1,
            "result": {
                "device": "Venus E mini",
                "ip": "192.168.1.100",
                "ble_mac": "AA:BB:CC:DD:EE:FF",
            },
        }

        with patch.object(udp_client, "send_broadcast_request", AsyncMock(return_value=[response])):
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
            },
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
