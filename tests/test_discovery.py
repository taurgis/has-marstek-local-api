"""Tests for the Marstek discovery module."""

from __future__ import annotations

import json
import socket
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class TestGetBroadcastAddresses:
    """Tests for _get_broadcast_addresses."""

    def test_without_psutil(self) -> None:
        """Test fallback when psutil is not available."""
        from custom_components.marstek.discovery import _get_broadcast_addresses
        
        # Patch the import to raise ImportError
        with patch.dict("sys.modules", {"psutil": None}):
            # Force ImportError by making psutil None in sys.modules
            import sys
            original = sys.modules.get("psutil")
            sys.modules["psutil"] = None  # type: ignore[assignment]
            try:
                # The function catches ImportError internally
                result = _get_broadcast_addresses()
                # Should fall back to global broadcast
                assert "255.255.255.255" in result
            finally:
                if original is not None:
                    sys.modules["psutil"] = original

    def test_with_psutil_basic(self) -> None:
        """Test normal psutil operation."""
        from custom_components.marstek.discovery import _get_broadcast_addresses
        
        mock_addr = MagicMock()
        mock_addr.family = socket.AF_INET
        mock_addr.address = "192.168.1.100"
        mock_addr.broadcast = "192.168.1.255"
        mock_addr.netmask = "255.255.255.0"
        
        with patch("psutil.net_if_addrs", return_value={"eth0": [mock_addr]}):
            result = _get_broadcast_addresses()
            
        assert "255.255.255.255" in result
        assert "192.168.1.255" in result
        assert len(result) >= 2

    def test_with_psutil_no_broadcast_attr(self) -> None:
        """Test fallback to netmask calculation when broadcast is None."""
        from custom_components.marstek.discovery import _get_broadcast_addresses
        
        mock_addr = MagicMock()
        mock_addr.family = socket.AF_INET
        mock_addr.address = "10.0.0.50"
        mock_addr.broadcast = None
        mock_addr.netmask = "255.255.255.0"
        
        with patch("psutil.net_if_addrs", return_value={"eth0": [mock_addr]}):
            result = _get_broadcast_addresses()
            
        assert "255.255.255.255" in result
        assert "10.0.0.255" in result

    def test_with_psutil_invalid_network(self) -> None:
        """Test handling of invalid network address."""
        from custom_components.marstek.discovery import _get_broadcast_addresses
        
        mock_addr = MagicMock()
        mock_addr.family = socket.AF_INET
        mock_addr.address = "invalid"
        mock_addr.broadcast = None
        mock_addr.netmask = "invalid"
        
        with patch("psutil.net_if_addrs", return_value={"eth0": [mock_addr]}):
            result = _get_broadcast_addresses()
            
        # Should still have global broadcast
        assert "255.255.255.255" in result

    def test_skips_loopback(self) -> None:
        """Test that loopback addresses are skipped."""
        from custom_components.marstek.discovery import _get_broadcast_addresses
        
        mock_addr = MagicMock()
        mock_addr.family = socket.AF_INET
        mock_addr.address = "127.0.0.1"
        mock_addr.broadcast = "127.255.255.255"
        mock_addr.netmask = "255.0.0.0"
        
        with patch("psutil.net_if_addrs", return_value={"lo": [mock_addr]}):
            result = _get_broadcast_addresses()
            
        assert "127.255.255.255" not in result

    def test_skips_ipv6(self) -> None:
        """Test that IPv6 addresses are skipped."""
        from custom_components.marstek.discovery import _get_broadcast_addresses
        
        mock_addr = MagicMock()
        mock_addr.family = socket.AF_INET6
        mock_addr.address = "::1"
        
        with patch("psutil.net_if_addrs", return_value={"lo": [mock_addr]}):
            result = _get_broadcast_addresses()
            
        # Only global broadcast
        assert "255.255.255.255" in result

    def test_removes_local_ips_from_broadcast(self) -> None:
        """Test that local IPs are removed from broadcast addresses."""
        from custom_components.marstek.discovery import _get_broadcast_addresses
        
        mock_addr = MagicMock()
        mock_addr.family = socket.AF_INET
        mock_addr.address = "192.168.1.100"
        mock_addr.broadcast = "192.168.1.100"  # Same as local IP (shouldn't happen but test it)
        mock_addr.netmask = "255.255.255.0"
        
        with patch("psutil.net_if_addrs", return_value={"eth0": [mock_addr]}):
            result = _get_broadcast_addresses()
            
        # Local IP should be removed from broadcast set
        assert "192.168.1.100" not in result

    def test_psutil_oserror(self) -> None:
        """Test handling of OSError from psutil."""
        from custom_components.marstek.discovery import _get_broadcast_addresses
        
        with patch("psutil.net_if_addrs", side_effect=OSError("Network error")):
            result = _get_broadcast_addresses()
            
        # Should fall back to global broadcast
        assert "255.255.255.255" in result


class TestIsEchoResponse:
    """Tests for _is_echo_response."""

    def test_echo_response(self) -> None:
        """Test detection of echoed requests."""
        from custom_components.marstek.discovery import _is_echo_response
        
        echo = {"method": "Marstek.GetDevice", "params": {"ble_mac": "0"}}
        assert _is_echo_response(echo) is True

    def test_valid_response(self) -> None:
        """Test detection of valid device responses."""
        from custom_components.marstek.discovery import _is_echo_response
        
        valid = {"result": {"device": "Venus", "ip": "192.168.1.100"}}
        assert _is_echo_response(valid) is False

    def test_response_with_result_and_method(self) -> None:
        """Test response that has both result and method."""
        from custom_components.marstek.discovery import _is_echo_response
        
        # Has result, so should not be echo even if has method
        response = {
            "result": {"device": "Venus"},
            "method": "Marstek.GetDevice",
            "params": {}
        }
        assert _is_echo_response(response) is False


class TestIsValidDeviceResponse:
    """Tests for _is_valid_device_response."""

    def test_valid_with_device(self) -> None:
        """Test valid response with device field."""
        from custom_components.marstek.discovery import _is_valid_device_response
        
        response = {"result": {"device": "Venus"}}
        assert _is_valid_device_response(response) is True

    def test_valid_with_ip(self) -> None:
        """Test valid response with ip field."""
        from custom_components.marstek.discovery import _is_valid_device_response
        
        response = {"result": {"ip": "192.168.1.100"}}
        assert _is_valid_device_response(response) is True

    def test_valid_with_ble_mac(self) -> None:
        """Test valid response with ble_mac field."""
        from custom_components.marstek.discovery import _is_valid_device_response
        
        response = {"result": {"ble_mac": "AA:BB:CC:DD:EE:FF"}}
        assert _is_valid_device_response(response) is True

    def test_valid_with_wifi_mac(self) -> None:
        """Test valid response with wifi_mac field."""
        from custom_components.marstek.discovery import _is_valid_device_response
        
        response = {"result": {"wifi_mac": "11:22:33:44:55:66"}}
        assert _is_valid_device_response(response) is True

    def test_invalid_no_result(self) -> None:
        """Test invalid response without result."""
        from custom_components.marstek.discovery import _is_valid_device_response
        
        response = {"method": "Marstek.GetDevice"}
        assert _is_valid_device_response(response) is False

    def test_invalid_result_not_dict(self) -> None:
        """Test invalid response with non-dict result."""
        from custom_components.marstek.discovery import _is_valid_device_response
        
        response = {"result": "not a dict"}
        assert _is_valid_device_response(response) is False

    def test_invalid_no_identifiers(self) -> None:
        """Test invalid response without any identifiers."""
        from custom_components.marstek.discovery import _is_valid_device_response
        
        response = {"result": {"unknown_field": "value"}}
        assert _is_valid_device_response(response) is False


class TestDiscoverDevices:
    """Tests for discover_devices function."""

    @pytest.mark.asyncio
    async def test_socket_bind_error(self) -> None:
        """Test handling of socket bind error."""
        from custom_components.marstek.discovery import discover_devices
        
        mock_socket = MagicMock()
        mock_socket.bind.side_effect = OSError("Address already in use")
        
        with patch("socket.socket", return_value=mock_socket):
            with pytest.raises(OSError, match="Address already in use"):
                await discover_devices()
        
        mock_socket.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_discovery_timeout(self) -> None:
        """Test discovery completes after timeout with no devices."""
        from custom_components.marstek.discovery import discover_devices
        
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 12345)
        mock_socket.setblocking = MagicMock()
        mock_socket.setsockopt = MagicMock()
        
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            raise TimeoutError()
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.return_value = 0
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                # Make time advance on each call
                call_count = 0
                def advancing_time() -> float:
                    nonlocal call_count
                    call_count += 1
                    return float(call_count * 2)  # Advances by 2 seconds each call
                loop.time.side_effect = advancing_time
                
                with patch("custom_components.marstek.discovery._get_broadcast_addresses", return_value=["255.255.255.255"]):
                    result = await discover_devices(timeout=0.5)
        
        assert result == []
        mock_socket.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_discovery_finds_device(self) -> None:
        """Test successful device discovery."""
        from custom_components.marstek.discovery import discover_devices
        
        device_response = {
            "id": 0,
            "result": {
                "device": "Venus",
                "ver": 3,
                "wifi_name": "TestNet",
                "ip": "192.168.1.100",
                "wifi_mac": "11:22:33:44:55:66",
                "ble_mac": "AA:BB:CC:DD:EE:FF",
            }
        }
        
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 12345)
        mock_socket.setblocking = MagicMock()
        mock_socket.setsockopt = MagicMock()
        
        call_count = 0
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (json.dumps(device_response).encode(), ("192.168.1.100", 30000))
            raise TimeoutError()
        
        time_calls = [0]
        def time_side_effect() -> float:
            time_calls[0] += 0.1
            return time_calls[0]
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                with patch("custom_components.marstek.discovery._get_broadcast_addresses", return_value=["255.255.255.255"]):
                    result = await discover_devices(timeout=0.5)
        
        assert len(result) == 1
        assert result[0]["ip"] == "192.168.1.100"
        assert result[0]["device_type"] == "Venus"
        assert result[0]["ble_mac"] == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_discovery_filters_echo(self) -> None:
        """Test that echoed requests are filtered."""
        from custom_components.marstek.discovery import discover_devices
        
        echo_response = {
            "id": 0,
            "method": "Marstek.GetDevice",
            "params": {"ble_mac": "0"}
        }
        
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 12345)
        
        call_count = 0
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (json.dumps(echo_response).encode(), ("192.168.1.1", 30000))
            raise TimeoutError()
        
        time_calls = [0.0]
        def time_side_effect() -> float:
            time_calls[0] += 0.1
            return time_calls[0]
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                with patch("custom_components.marstek.discovery._get_broadcast_addresses", return_value=["255.255.255.255"]):
                    result = await discover_devices(timeout=1.0)
        
        assert result == []

    @pytest.mark.asyncio
    async def test_discovery_handles_invalid_json(self) -> None:
        """Test handling of invalid JSON responses."""
        from custom_components.marstek.discovery import discover_devices
        
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 12345)
        
        call_count = 0
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (b"not valid json", ("192.168.1.1", 30000))
            # Keep returning invalid JSON to ensure we hit the continue path
            if call_count < 5:
                raise TimeoutError()  # Next recv attempt times out
            raise TimeoutError()
        
        time_calls = [0.0]
        def time_side_effect() -> float:
            time_calls[0] += 0.1  # Small increments to keep loop running
            return time_calls[0]
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                with patch("custom_components.marstek.discovery._get_broadcast_addresses", return_value=["255.255.255.255"]):
                    result = await discover_devices(timeout=1.0)
        
        assert result == []

    @pytest.mark.asyncio
    async def test_discovery_skips_duplicates(self) -> None:
        """Test that duplicate devices are skipped."""
        from custom_components.marstek.discovery import discover_devices
        
        device_response = {
            "id": 0,
            "result": {
                "device": "Venus",
                "ip": "192.168.1.100",
                "ble_mac": "AA:BB:CC:DD:EE:FF",
            }
        }
        
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 12345)
        
        call_count = 0
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # Return same device twice
                return (json.dumps(device_response).encode(), ("192.168.1.100", 30000))
            raise TimeoutError()
        
        time_calls = [0]
        def time_side_effect() -> float:
            time_calls[0] += 0.2
            return time_calls[0]
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                with patch("custom_components.marstek.discovery._get_broadcast_addresses", return_value=["255.255.255.255"]):
                    result = await discover_devices(timeout=0.5)
        
        # Should only have one device
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_discovery_socket_error(self) -> None:
        """Test handling of socket error during receive."""
        from custom_components.marstek.discovery import discover_devices
        
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 12345)
        
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            raise OSError("Network unreachable")
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.return_value = 0
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                with patch("custom_components.marstek.discovery._get_broadcast_addresses", return_value=["255.255.255.255"]):
                    result = await discover_devices(timeout=0.1)
        
        assert result == []

    @pytest.mark.asyncio
    async def test_discovery_uses_sender_ip_fallback(self) -> None:
        """Test that sender IP is used when response doesn't contain IP."""
        from custom_components.marstek.discovery import discover_devices
        
        device_response = {
            "id": 0,
            "result": {
                "device": "Venus",
                "ble_mac": "AA:BB:CC:DD:EE:FF",
                # No "ip" field
            }
        }
        
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 12345)
        
        call_count = 0
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (json.dumps(device_response).encode(), ("10.0.0.50", 30000))
            raise TimeoutError()
        
        time_calls = [0]
        def time_side_effect() -> float:
            time_calls[0] += 0.1
            return time_calls[0]
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                with patch("custom_components.marstek.discovery._get_broadcast_addresses", return_value=["255.255.255.255"]):
                    result = await discover_devices(timeout=0.5)
        
        assert len(result) == 1
        assert result[0]["ip"] == "10.0.0.50"


class TestGetDeviceInfo:
    """Tests for get_device_info function."""

    @pytest.mark.asyncio
    async def test_successful_query(self) -> None:
        """Test successful device info query."""
        from custom_components.marstek.discovery import get_device_info
        
        device_response = {
            "id": 0,
            "result": {
                "device": "Venus",
                "ver": 3,
                "wifi_name": "TestNet",
                "ip": "192.168.1.100",
                "wifi_mac": "11:22:33:44:55:66",
                "ble_mac": "AA:BB:CC:DD:EE:FF",
            }
        }
        
        mock_socket = MagicMock()
        
        call_count = 0
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (json.dumps(device_response).encode(), ("192.168.1.100", 30000))
            raise TimeoutError()
        
        time_calls = [0]
        def time_side_effect() -> float:
            time_calls[0] += 0.1
            return time_calls[0]
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                result = await get_device_info("192.168.1.100", timeout=0.5)
        
        assert result is not None
        assert result["ip"] == "192.168.1.100"
        assert result["device_type"] == "Venus"

    @pytest.mark.asyncio
    async def test_no_response_timeout(self) -> None:
        """Test timeout when device doesn't respond."""
        from custom_components.marstek.discovery import get_device_info
        
        mock_socket = MagicMock()
        
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            raise TimeoutError()

        time_calls = [0.0]
        def time_side_effect() -> float:
            time_calls[0] += 0.2
            return time_calls[0]
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                result = await get_device_info("192.168.1.100", timeout=1.0)
        
        assert result is None

    @pytest.mark.asyncio
    async def test_socket_error(self) -> None:
        """Test handling of socket error."""
        from custom_components.marstek.discovery import get_device_info
        
        mock_socket = MagicMock()
        mock_socket.sendto.side_effect = OSError("Network error")
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.return_value = 0
                mock_loop.return_value = loop
                
                result = await get_device_info("192.168.1.100")
        
        assert result is None

    @pytest.mark.asyncio
    async def test_filters_echo_response(self) -> None:
        """Test that echo responses are filtered."""
        from custom_components.marstek.discovery import get_device_info
        
        echo_response = {
            "id": 0,
            "method": "Marstek.GetDevice",
            "params": {"ble_mac": "0"}
        }
        
        mock_socket = MagicMock()
        
        call_count = 0
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (json.dumps(echo_response).encode(), ("192.168.1.100", 30000))
            raise TimeoutError()
        
        time_calls = [0.0]
        def time_side_effect() -> float:
            time_calls[0] += 0.1
            return time_calls[0]
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                result = await get_device_info("192.168.1.100", timeout=1.0)
        
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_invalid_json(self) -> None:
        """Test handling of invalid JSON response."""
        from custom_components.marstek.discovery import get_device_info
        
        mock_socket = MagicMock()
        
        call_count = 0
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (b"not json", ("192.168.1.100", 30000))
            raise TimeoutError()
        
        time_calls = [0.0]
        def time_side_effect() -> float:
            time_calls[0] += 0.1
            return time_calls[0]
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                result = await get_device_info("192.168.1.100", timeout=1.0)
        
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_host_as_fallback_ip(self) -> None:
        """Test that host parameter is used when response has no IP."""
        from custom_components.marstek.discovery import get_device_info
        
        device_response = {
            "id": 0,
            "result": {
                "device": "Venus",
                "ble_mac": "AA:BB:CC:DD:EE:FF",
            }
        }
        
        mock_socket = MagicMock()
        
        call_count = 0
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (json.dumps(device_response).encode(), ("10.0.0.1", 30000))
            raise TimeoutError()
        
        time_calls = [0]
        def time_side_effect() -> float:
            time_calls[0] += 0.1
            return time_calls[0]
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                result = await get_device_info("192.168.1.100", timeout=0.5)
        
        assert result is not None
        # Should use the host parameter, not sender IP
        assert result["ip"] == "192.168.1.100"

    @pytest.mark.asyncio
    async def test_handles_invalid_device_response(self) -> None:
        """Test handling of invalid device response (missing identifiers)."""
        from custom_components.marstek.discovery import get_device_info
        
        # Response with result but no valid identifiers
        invalid_response = {
            "id": 0,
            "result": {"unknown": "value"}
        }
        
        mock_socket = MagicMock()
        
        call_count = 0
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (json.dumps(invalid_response).encode(), ("192.168.1.100", 30000))
            raise TimeoutError()
        
        time_calls = [0.0]
        def time_side_effect() -> float:
            time_calls[0] += 0.1
            return time_calls[0]
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                result = await get_device_info("192.168.1.100", timeout=1.0)
        
        # Should return None since response is invalid
        assert result is None

    @pytest.mark.asyncio
    async def test_sendto_oserror(self) -> None:
        """Test handling of OSError during sendto."""
        from custom_components.marstek.discovery import get_device_info
        
        mock_socket = MagicMock()
        # First call to sendto fails, triggering the socket error path
        mock_socket.sendto.side_effect = OSError("Connection refused")
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.return_value = 0
                mock_loop.return_value = loop
                
                result = await get_device_info("192.168.1.100")
        
        # Socket error should return None
        assert result is None
        mock_socket.close.assert_called_once()


class TestDiscoverDevicesEdgeCases:
    """Additional edge case tests for discover_devices."""

    @pytest.mark.asyncio
    async def test_broadcast_send_oserror(self) -> None:
        """Test handling of OSError when sending to broadcast address."""
        from custom_components.marstek.discovery import discover_devices
        
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 12345)
        # sendto fails with OSError
        mock_socket.sendto.side_effect = OSError("Network unreachable")
        
        time_calls = [0]
        def time_side_effect() -> float:
            time_calls[0] += 0.6
            return time_calls[0]
        
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            raise TimeoutError()
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                with patch("custom_components.marstek.discovery._get_broadcast_addresses", return_value=["10.0.0.255"]):
                    result = await discover_devices(timeout=0.5)
        
        # Should complete with empty list even if send failed
        assert result == []

    @pytest.mark.asyncio
    async def test_discover_filters_invalid_response(self) -> None:
        """Test that invalid device responses are skipped."""
        from custom_components.marstek.discovery import discover_devices
        
        # Response with result that has no identifiers (invalid)
        invalid_response = {"id": 0, "result": {"unknown_field": "value"}}
        
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 12345)
        
        call_count = 0
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (json.dumps(invalid_response).encode(), ("192.168.1.100", 30000))
            raise TimeoutError()
        
        time_calls = [0.0]
        def time_side_effect() -> float:
            time_calls[0] += 0.1
            return time_calls[0]
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                with patch("custom_components.marstek.discovery._get_broadcast_addresses", return_value=["255.255.255.255"]):
                    result = await discover_devices(timeout=1.0)
        
        assert result == []

    @pytest.mark.asyncio
    async def test_discover_with_multiple_broadcasts(self) -> None:
        """Test discovery sends to multiple broadcast addresses."""
        from custom_components.marstek.discovery import discover_devices
        
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 12345)
        
        time_calls = [0]
        def time_side_effect() -> float:
            time_calls[0] += 0.6
            return time_calls[0]
        
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            raise TimeoutError()
        
        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                
                with patch("custom_components.marstek.discovery._get_broadcast_addresses", return_value=["255.255.255.255", "192.168.1.255", "10.0.0.255"]):
                    await discover_devices(timeout=0.5)
        
        # Should have sent to all broadcast addresses
        assert mock_socket.sendto.call_count == 3

    def test_psutil_import_error(self) -> None:
        """Test handling when psutil module import raises ImportError."""
        from custom_components.marstek.discovery import _get_broadcast_addresses
        
        # Simulate psutil module raising ImportError when accessed
        import sys
        original_psutil = sys.modules.get("psutil")
        
        class MockPsutilRaiser:
            """Mock module that raises ImportError on any attribute access."""
            def __getattr__(self, name: str) -> Any:
                raise ImportError("No module named 'psutil'")
        
        try:
            sys.modules["psutil"] = MockPsutilRaiser()  # type: ignore[assignment]
            result = _get_broadcast_addresses()
            # Should fall back to global broadcast only
            assert "255.255.255.255" in result
        finally:
            if original_psutil is not None:
                sys.modules["psutil"] = original_psutil
            elif "psutil" in sys.modules:
                del sys.modules["psutil"]

    def test_local_ip_oserror_in_filter(self) -> None:
        """Test handling of OSError when filtering local IPs."""
        from custom_components.marstek.discovery import _get_broadcast_addresses
        
        call_count = 0
        
        def mock_net_if_addrs() -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call returns normal result
                mock_addr = MagicMock()
                mock_addr.family = socket.AF_INET
                mock_addr.address = "192.168.1.100"
                mock_addr.broadcast = "192.168.1.255"
                mock_addr.netmask = "255.255.255.0"
                return {"eth0": [mock_addr]}
            # Second call (for local IP filtering) raises OSError
            raise OSError("Network error")
        
        with patch("psutil.net_if_addrs", side_effect=mock_net_if_addrs):
            result = _get_broadcast_addresses()
        
        # Should still have results (OSError is caught)
        assert "255.255.255.255" in result
