"""The broadcast sweep itself and the addresses it targets."""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from ._helpers import (
    _run_in_executor,
)


class TestGetBroadcastAddresses:
    """Tests for get_broadcast_addresses."""

    def test_without_psutil(self) -> None:
        """Test fallback when psutil is not available."""
        from custom_components.marstek.pymarstek.network import get_broadcast_addresses

        result = get_broadcast_addresses(allow_import=False)

        assert "255.255.255.255" in result

    def test_with_psutil_basic(self) -> None:
        """Test normal psutil operation."""
        from custom_components.marstek.pymarstek.network import get_broadcast_addresses

        mock_addr = MagicMock()
        mock_addr.family = socket.AF_INET
        mock_addr.address = "192.168.1.100"
        mock_addr.broadcast = "192.168.1.255"
        mock_addr.netmask = "255.255.255.0"

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = {"eth0": [mock_addr]}

        result = get_broadcast_addresses(psutil_module=mock_psutil)

        assert "255.255.255.255" in result
        assert "192.168.1.255" in result
        assert len(result) >= 2

    def test_with_psutil_no_broadcast_attr(self) -> None:
        """Test fallback to netmask calculation when broadcast is None."""
        from custom_components.marstek.pymarstek.network import get_broadcast_addresses

        mock_addr = MagicMock()
        mock_addr.family = socket.AF_INET
        mock_addr.address = "10.0.0.50"
        mock_addr.broadcast = None
        mock_addr.netmask = "255.255.255.0"

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = {"eth0": [mock_addr]}

        result = get_broadcast_addresses(psutil_module=mock_psutil)

        assert "255.255.255.255" in result
        assert "10.0.0.255" in result

    def test_with_psutil_invalid_network(self) -> None:
        """Test handling of invalid network address."""
        from custom_components.marstek.pymarstek.network import get_broadcast_addresses

        mock_addr = MagicMock()
        mock_addr.family = socket.AF_INET
        mock_addr.address = "invalid"
        mock_addr.broadcast = None
        mock_addr.netmask = "invalid"

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = {"eth0": [mock_addr]}

        result = get_broadcast_addresses(psutil_module=mock_psutil)

        assert "255.255.255.255" in result

    def test_skips_loopback(self) -> None:
        """Test that loopback addresses are skipped."""
        from custom_components.marstek.pymarstek.network import get_broadcast_addresses

        mock_addr = MagicMock()
        mock_addr.family = socket.AF_INET
        mock_addr.address = "127.0.0.1"
        mock_addr.broadcast = "127.255.255.255"
        mock_addr.netmask = "255.0.0.0"

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = {"lo": [mock_addr]}

        result = get_broadcast_addresses(psutil_module=mock_psutil)

        assert "127.255.255.255" not in result

    def test_skips_ipv6(self) -> None:
        """Test that IPv6 addresses are skipped."""
        from custom_components.marstek.pymarstek.network import get_broadcast_addresses

        mock_addr = MagicMock()
        mock_addr.family = socket.AF_INET6
        mock_addr.address = "::1"

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = {"lo": [mock_addr]}

        result = get_broadcast_addresses(psutil_module=mock_psutil)

        assert "255.255.255.255" in result

    def test_removes_local_ips_from_broadcast(self) -> None:
        """Test that local IPs are removed from broadcast addresses."""
        from custom_components.marstek.pymarstek.network import get_broadcast_addresses

        mock_addr = MagicMock()
        mock_addr.family = socket.AF_INET
        mock_addr.address = "192.168.1.100"
        mock_addr.broadcast = "192.168.1.100"
        mock_addr.netmask = "255.255.255.0"

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.return_value = {"eth0": [mock_addr]}

        result = get_broadcast_addresses(psutil_module=mock_psutil)

        assert "192.168.1.100" not in result

    def test_psutil_oserror(self) -> None:
        """Test handling of OSError from psutil."""
        from custom_components.marstek.pymarstek.network import get_broadcast_addresses

        mock_psutil = MagicMock()
        mock_psutil.net_if_addrs.side_effect = OSError("Network error")

        result = get_broadcast_addresses(psutil_module=mock_psutil)

        assert "255.255.255.255" in result


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
    async def test_discovery_binds_to_open_api_port(self) -> None:
        """Test discovery binds the local socket to the Open API listen port."""
        from custom_components.marstek.discovery import discover_devices

        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 30000)

        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            raise TimeoutError()

        time_calls = [0.0]

        def advancing_time() -> float:
            time_calls[0] += 1.0
            return time_calls[0]

        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
                loop.time.side_effect = advancing_time
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                with patch(
                    "custom_components.marstek.discovery._get_broadcast_addresses",
                    return_value=["255.255.255.255"],
                ):
                    await discover_devices(timeout=0.5)

        mock_socket.bind.assert_called_with(("0.0.0.0", 30000))

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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
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

                with patch(
                    "custom_components.marstek.discovery._get_broadcast_addresses",
                    return_value=["255.255.255.255"],
                ):
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
            },
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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop

                with patch(
                    "custom_components.marstek.discovery._get_broadcast_addresses",
                    return_value=["255.255.255.255"],
                ):
                    result = await discover_devices(timeout=0.5)

        assert len(result) == 1
        assert result[0]["ip"] == "192.168.1.100"
        assert result[0]["port"] == 30000
        assert result[0]["device_type"] == "Venus"
        assert result[0]["ble_mac"] == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_discovery_normalizes_leading_zero_ip(self) -> None:
        """Test that discovery normalizes device-reported IP addresses."""
        from custom_components.marstek.discovery import discover_devices

        device_response = {
            "id": 0,
            "result": {
                "device": "Venus",
                "ver": 3,
                "wifi_name": "TestNet",
                "ip": "192.168.09.92",
                "wifi_mac": "11:22:33:44:55:66",
                "ble_mac": "AA:BB:CC:DD:EE:FF",
            },
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
                return (json.dumps(device_response).encode(), ("192.168.9.92", 30000))
            raise TimeoutError()

        time_calls = [0.0]

        def time_side_effect() -> float:
            time_calls[0] += 0.1
            return time_calls[0]

        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop

                with patch(
                    "custom_components.marstek.discovery._get_broadcast_addresses",
                    return_value=["255.255.255.255"],
                ):
                    result = await discover_devices(timeout=0.5)

        assert len(result) == 1
        assert result[0]["ip"] == "192.168.9.92"

    @pytest.mark.asyncio
    async def test_discovery_does_not_serialize_quiet_ports(self) -> None:
        """A reply on one port is handled while another port stays silent.

        Sockets are listened to together. Walking them in turn made a quiet
        port hold up a reply already queued on a later one, and overran the
        caller's timeout by that wait for every extra port.
        """
        from custom_components.marstek import discovery
        from custom_components.marstek.discovery import discover_devices

        sockets = [MagicMock(), MagicMock()]
        sockets[0].getsockname.return_value = ("0.0.0.0", 30000)
        sockets[1].getsockname.return_value = ("0.0.0.0", 30003)

        device_response = {
            "id": 0,
            "src": "VenusE-AABBCCDDEEFF",
            "result": {
                "device": "VenusE 3.0",
                "ver": 150,
                "ip": "192.168.9.92",
                "ble_mac": "AA:BB:CC:DD:EE:FF",
                "wifi_mac": "11:22:33:44:55:66",
                "wifi_name": "net",
            },
        }
        answered = False

        async def mock_recvfrom(sock: Any, _bufsize: int) -> Any:
            nonlocal answered
            if sock is sockets[0]:
                # The port bound first never hears anything.
                await asyncio.sleep(3600)
            if answered:
                await asyncio.sleep(3600)
            answered = True
            return (
                json.dumps(device_response).encode(),
                ("192.168.9.92", 30003),
            )

        loop = asyncio.get_running_loop()
        started = loop.time()
        handled_at: list[float] = []
        real_build = discovery._build_device_info

        def timed_build(*args: Any, **kwargs: Any) -> dict[str, Any]:
            handled_at.append(loop.time() - started)
            return real_build(*args, **kwargs)

        with (
            patch("socket.socket", side_effect=sockets),
            patch.object(loop, "sock_sendto", AsyncMock()),
            patch.object(loop, "sock_recvfrom", mock_recvfrom),
            patch.object(discovery, "_build_device_info", timed_build),
            patch(
                "custom_components.marstek.discovery._get_broadcast_addresses",
                return_value=["255.255.255.255"],
            ),
        ):
            result = await discover_devices(timeout=1.0, ports=[30000, 30003])

        assert len(result) == 1
        assert result[0]["ip"] == "192.168.9.92"
        # Serial polling spent its first half-second window on the silent
        # socket before ever reading the second one.
        assert handled_at[0] < 0.4

    @pytest.mark.asyncio
    async def test_discovery_scans_multiple_ports(self) -> None:
        """Test discovery sends broadcast probes to every requested UDP port."""
        from custom_components.marstek.discovery import discover_devices

        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 12345)

        time_calls = [0.0]

        def time_side_effect() -> float:
            time_calls[0] += 0.2
            return time_calls[0]

        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = AsyncMock(side_effect=TimeoutError())
                mock_loop.return_value = loop

                with patch(
                    "custom_components.marstek.discovery._get_broadcast_addresses",
                    return_value=["255.255.255.255"],
                ):
                    await discover_devices(timeout=0.5, ports=[30000, 30003])

        loop.sock_sendto.assert_any_await(mock_socket, ANY, ("255.255.255.255", 30000))
        loop.sock_sendto.assert_any_await(mock_socket, ANY, ("255.255.255.255", 30003))
        bind_ports = [call.args[0][1] for call in mock_socket.bind.call_args_list]
        assert 30000 in bind_ports
        assert 30003 in bind_ports

    @pytest.mark.asyncio
    async def test_discovery_filters_echo(self) -> None:
        """Test that echoed requests are filtered."""
        from custom_components.marstek.discovery import discover_devices

        echo_response = {"id": 0, "method": "Marstek.GetDevice", "params": {"ble_mac": "0"}}

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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop

                with patch(
                    "custom_components.marstek.discovery._get_broadcast_addresses",
                    return_value=["255.255.255.255"],
                ):
                    result = await discover_devices(timeout=1.0)

        assert result == []

    @pytest.mark.asyncio
    async def test_discovery_skips_reply_with_a_non_finite_number(self) -> None:
        """A GetDevice reply carrying NaN is not a device we can trust."""
        from custom_components.marstek.discovery import discover_devices

        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 12345)

        poisoned = (
            b'{"id": 0, "result": {"device": "VenusE 3.0", "ver": NaN, '
            b'"ble_mac": "009b08a5aa39", "wifi_mac": "7483c2315cf8", '
            b'"ip": "192.168.1.1"}}'
        )

        call_count = 0

        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (poisoned, ("192.168.1.1", 30000))
            raise TimeoutError()

        time_calls = [0.0]

        def time_side_effect() -> float:
            time_calls[0] += 0.1
            return time_calls[0]

        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop

                with patch(
                    "custom_components.marstek.discovery._get_broadcast_addresses",
                    return_value=["255.255.255.255"],
                ):
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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop

                with patch(
                    "custom_components.marstek.discovery._get_broadcast_addresses",
                    return_value=["255.255.255.255"],
                ):
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
            },
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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop

                with patch(
                    "custom_components.marstek.discovery._get_broadcast_addresses",
                    return_value=["255.255.255.255"],
                ):
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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
                loop.time.return_value = 0
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop

                with patch(
                    "custom_components.marstek.discovery._get_broadcast_addresses",
                    return_value=["255.255.255.255"],
                ):
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
            },
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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop

                with patch(
                    "custom_components.marstek.discovery._get_broadcast_addresses",
                    return_value=["255.255.255.255"],
                ):
                    result = await discover_devices(timeout=0.5)

        assert len(result) == 1
        assert result[0]["ip"] == "10.0.0.50"


class TestDiscoverDevicesEdgeCases:
    """Additional edge case tests for discover_devices."""

    @pytest.mark.asyncio
    async def test_broadcast_send_oserror(self) -> None:
        """Test handling of OSError when sending to broadcast address."""
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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock(side_effect=OSError("Network unreachable"))
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop

                with patch(
                    "custom_components.marstek.discovery._get_broadcast_addresses",
                    return_value=["10.0.0.255"],
                ):
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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop

                with patch(
                    "custom_components.marstek.discovery._get_broadcast_addresses",
                    return_value=["255.255.255.255"],
                ):
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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop

                with patch(
                    "custom_components.marstek.discovery._get_broadcast_addresses",
                    return_value=["255.255.255.255", "192.168.1.255", "10.0.0.255"],
                ):
                    await discover_devices(timeout=0.5)

        # Should have sent to all broadcast addresses
        assert loop.sock_sendto.call_count == 3

    def test_psutil_import_error(self) -> None:
        """Test handling when psutil module import raises ImportError."""
        # Simulate psutil module raising ImportError when accessed
        import sys

        from custom_components.marstek.discovery import _get_broadcast_addresses

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

    def test_local_ip_filter_uses_one_interface_snapshot(self) -> None:
        """Broadcasts and local IPs come from a single net_if_addrs() call.

        Two snapshots can disagree when an interface appears or disappears
        between them, which would leave a local address in the broadcast list.
        """
        from custom_components.marstek.discovery import _get_broadcast_addresses

        call_count = 0

        def mock_net_if_addrs() -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            mock_addr = MagicMock()
            mock_addr.family = socket.AF_INET
            mock_addr.address = "192.168.1.100"
            mock_addr.broadcast = "192.168.1.255"
            mock_addr.netmask = "255.255.255.0"
            return {"eth0": [mock_addr]}

        with patch("psutil.net_if_addrs", side_effect=mock_net_if_addrs):
            result = _get_broadcast_addresses()

        assert call_count == 1
        assert "255.255.255.255" in result
        assert "192.168.1.255" in result
        # The interface's own address is not a broadcast target.
        assert "192.168.1.100" not in result


@pytest.mark.asyncio
async def test_discover_devices_accepts_injected_broadcast_addresses() -> None:
    """The Home Assistant layer knows the adapters the user enabled.

    When it passes them in, discovery must sweep exactly those and never read
    the interface table, which blocks.
    """
    from custom_components.marstek.discovery import discover_devices

    mock_socket = MagicMock()
    mock_socket.getsockname.return_value = ("0.0.0.0", 12345)

    async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
        raise TimeoutError()

    times = iter(range(100))
    sent_to: list[str] = []

    async def record_sendto(_sock: Any, _data: bytes, addr: tuple[str, int]) -> None:
        sent_to.append(addr[0])

    with (
        patch("socket.socket", return_value=mock_socket),
        patch("asyncio.get_running_loop") as mock_loop,
        patch(
            "custom_components.marstek.discovery._get_broadcast_addresses",
            side_effect=AssertionError("the interface table must not be read"),
        ),
    ):
        loop = MagicMock()
        loop.run_in_executor = _run_in_executor
        loop.sock_sendto = AsyncMock(side_effect=record_sendto)
        loop.time.side_effect = lambda: float(next(times))
        loop.sock_recvfrom = mock_recvfrom
        mock_loop.return_value = loop

        await discover_devices(timeout=0.5, broadcast_addresses=["10.0.0.255", "192.168.1.255"])

    assert set(sent_to) == {"10.0.0.255", "192.168.1.255"}
