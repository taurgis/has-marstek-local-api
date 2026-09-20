"""Querying one device and turning its reply into device info."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ._helpers import (
    _run_in_executor,
)


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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop

                result = await get_device_info("192.168.1.100", timeout=0.5)

        assert result is not None
        assert result["ip"] == "192.168.1.100"
        assert result["device_type"] == "Venus"

    @pytest.mark.asyncio
    async def test_binds_to_target_port(self) -> None:
        """Test unicast GetDevice sends from the device Open API port."""
        from custom_components.marstek.discovery import get_device_info

        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 30003)

        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            raise TimeoutError()

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
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                await get_device_info("192.168.1.100", port=30003, timeout=1.0)

        mock_socket.bind.assert_called_with(("0.0.0.0", 30003))

    @pytest.mark.asyncio
    async def test_loopback_uses_ephemeral_bind(self) -> None:
        """Test localhost queries bind an ephemeral port to avoid colliding with the device."""
        from custom_components.marstek.discovery import get_device_info

        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 54321)

        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            raise TimeoutError()

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
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                await get_device_info("127.0.0.1", port=30000, timeout=1.0)

        mock_socket.bind.assert_called_with(("0.0.0.0", 0))

    @pytest.mark.asyncio
    async def test_falls_back_to_ephemeral_when_port_busy(self) -> None:
        """Test GetDevice still probes if the Open API port cannot be bound."""
        from custom_components.marstek.discovery import get_device_info

        mock_socket = MagicMock()
        mock_socket.bind.side_effect = [OSError("Address already in use"), None]
        mock_socket.getsockname.return_value = ("0.0.0.0", 54321)

        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            raise TimeoutError()

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
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop
                result = await get_device_info("192.168.1.100", timeout=1.0)

        assert result is None
        assert mock_socket.bind.call_args_list[0].args[0] == ("0.0.0.0", 30000)
        assert mock_socket.bind.call_args_list[1].args[0] == ("0.0.0.0", 0)

    @pytest.mark.asyncio
    async def test_venus_c_153_src_mac_without_result_mac(self) -> None:
        """Test firmware 153 GetDevice that only includes the MAC in src."""
        from custom_components.marstek.discovery import get_device_info

        device_response = {
            "id": 0,
            "src": "VenusC-AABBCCDDEEFF",
            "result": {
                "device": "VenusC",
                "ver": 153,
                "ip": "192.168.2.37",
            },
        }

        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 30000)

        call_count = 0

        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (json.dumps(device_response).encode(), ("192.168.2.37", 30000))
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
                result = await get_device_info("192.168.2.37", timeout=0.5)

        assert result is not None
        assert result["device_type"] == "VenusC"
        assert result["ble_mac"] == "AA:BB:CC:DD:EE:FF"
        assert result["ip"] == "192.168.2.37"

    @pytest.mark.asyncio
    async def test_normalizes_leading_zero_ip_from_device(self) -> None:
        """Test that device info normalizes device-reported IP addresses."""
        from custom_components.marstek.discovery import get_device_info

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

                result = await get_device_info("192.168.9.92", timeout=0.5)

        assert result is not None
        assert result["ip"] == "192.168.9.92"

    @pytest.mark.asyncio
    async def test_records_target_port_not_sender_port(self) -> None:
        """Record the target port we sent to, not the response sender port."""
        from custom_components.marstek.discovery import get_device_info

        device_response = {
            "id": 0,
            "result": {
                "device": "Venus",
                "ver": 3,
                "ip": "192.168.1.100",
                "ble_mac": "AA:BB:CC:DD:EE:FF",
            }
        }

        mock_socket = MagicMock()

        call_count = 0
        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Device responds from an ephemeral port (49152), NOT the target port (30003)
                return (json.dumps(device_response).encode(), ("192.168.1.100", 49152))
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

                result = await get_device_info("192.168.1.100", port=30003, timeout=0.5)

        assert result is not None
        # Port should be the target port (30003) we sent to, not the sender port (49152)
        assert result["port"] == 30003

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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
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

        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock(side_effect=OSError("Network error"))
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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
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

                result = await get_device_info("192.168.1.100", timeout=0.5)

        assert result is not None
        # Should use the host parameter, not sender IP
        assert result["ip"] == "192.168.1.100"

    @pytest.mark.asyncio
    async def test_ignores_getdevice_reply_from_other_host(self) -> None:
        """Unicast GetDevice must not accept another device's reply."""
        from custom_components.marstek.discovery import get_device_info

        other_device = {
            "id": 0,
            "result": {
                "device": "VenusE 3.0",
                "ble_mac": "AA:BB:CC:DD:EE:FF",
                "ip": "10.0.0.1",
            },
        }

        mock_socket = MagicMock()
        call_count = 0

        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (json.dumps(other_device).encode(), ("10.0.0.1", 30000))
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

                result = await get_device_info("192.168.1.100", timeout=0.5)

        assert result is None

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
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
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

        with patch("socket.socket", return_value=mock_socket):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock(side_effect=OSError("Connection refused"))
                loop.time.return_value = 0
                mock_loop.return_value = loop

                result = await get_device_info("192.168.1.100")

        # Socket error should return None
        assert result is None
        mock_socket.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_provided_udp_client_instead_of_binding(self) -> None:
        """Pooled-client GetDevice must not open a second SO_REUSEPORT socket."""
        from custom_components.marstek.discovery import get_device_info

        client = AsyncMock()
        client.send_request = AsyncMock(
            return_value={
                "id": 7,
                "src": "VenusC-AABBCCDDEEFF",
                "result": {
                    "device": "VenusC",
                    "ver": 153,
                    "ip": "172.28.0.26",
                },
            }
        )

        with patch("socket.socket") as mock_socket:
            result = await get_device_info(
                "172.28.0.26",
                port=30000,
                timeout=5.0,
                udp_client=client,
            )

        mock_socket.assert_not_called()
        client.send_request.assert_awaited_once()
        assert client.send_request.await_args is not None
        sent_args = client.send_request.await_args.args
        sent_kwargs = client.send_request.await_args.kwargs
        assert sent_args[1] == "172.28.0.26"
        assert sent_args[2] == 30000
        payload = json.loads(sent_args[0])
        assert payload["method"] == "Marstek.GetDevice"
        assert sent_kwargs["bypass_rate_limit"] is True
        assert result is not None
        assert result["device_type"] == "VenusC"
        assert result["ble_mac"] == "AA:BB:CC:DD:EE:FF"
        assert result["ip"] == "172.28.0.26"
        assert result["port"] == 30000

    @pytest.mark.asyncio
    async def test_udp_client_timeout_returns_none(self) -> None:
        """Timeout on the pooled client is cannot_connect, not a crash."""
        from custom_components.marstek.discovery import get_device_info

        client = AsyncMock()
        client.send_request = AsyncMock(side_effect=TimeoutError("timeout"))

        result = await get_device_info("172.28.0.20", udp_client=client)

        assert result is None

    @pytest.mark.asyncio
    async def test_udp_client_invalid_response_returns_none(self) -> None:
        """Invalid GetDevice payloads from the pooled client are ignored."""
        from custom_components.marstek.discovery import get_device_info

        client = AsyncMock()
        client.send_request = AsyncMock(
            return_value={"id": 1, "result": {"unknown": "value"}}
        )

        result = await get_device_info("172.28.0.20", udp_client=client)

        assert result is None

    @pytest.mark.asyncio
    async def test_udp_client_oserror_returns_none(self) -> None:
        """Socket errors on the pooled client return None."""
        from custom_components.marstek.discovery import get_device_info

        client = AsyncMock()
        client.send_request = AsyncMock(side_effect=OSError("network down"))

        result = await get_device_info("172.28.0.20", udp_client=client)

        assert result is None


def test_discovery_omitted_ver_stays_unknown() -> None:
    """A GetDevice payload without ver must not be stored as firmware 0."""
    from custom_components.marstek.discovery import _build_device_info
    from custom_components.marstek.firmware_profile import resolve_firmware_profile

    info = _build_device_info(
        {"device": "Venus E mini", "ble_mac": "aabbccddeeff"},
        "192.168.1.50",
        30000,
    )

    assert info["version"] is None
    profile = resolve_firmware_profile(info["device_type"], info["version"])
    assert profile.firmware_known is False
    assert profile.supports_sys_dod is False


def test_build_device_info_uses_src_mac_when_result_omits_mac() -> None:
    """Venus C firmware 153 may omit ble_mac while still putting it in src."""
    from custom_components.marstek.discovery import _build_device_info

    info = _build_device_info(
        {"device": "VenusC", "ver": 153, "ip": "192.168.2.37"},
        "192.168.2.37",
        30000,
        src="VenusC-AABBCCDDEEFF",
    )

    assert info["ble_mac"] == "AA:BB:CC:DD:EE:FF"
    assert info["mac"] == "AA:BB:CC:DD:EE:FF"
    assert info["device_type"] == "VenusC"
    assert info["version"] == 153


def test_build_device_info_prefers_result_ble_mac() -> None:
    """Result ble_mac wins over a MAC parsed from src."""
    from custom_components.marstek.discovery import _build_device_info

    info = _build_device_info(
        {"device": "VenusC", "ble_mac": "11:22:33:44:55:66"},
        "192.168.1.1",
        30000,
        src="VenusC-AABBCCDDEEFF",
    )

    assert info["ble_mac"] == "11:22:33:44:55:66"


class TestUnusableClaimedIp:
    """A reply that cannot supply a usable address falls back to the socket."""

    @pytest.mark.parametrize(
        "claimed",
        [12345, None, ["192.168.1.7"], {"addr": "192.168.1.7"}, True, 1.5],
    )
    def test_non_string_ip_does_not_raise(self, claimed: object) -> None:
        """A non-string ip used to raise AttributeError out of the config flow."""
        from custom_components.marstek.discovery import _device_info_from_response

        info = _device_info_from_response(
            {"result": {"ble_mac": "AA:BB:CC:DD:EE:FF", "ip": claimed}},
            "192.168.1.9",
            30000,
        )

        assert info is not None
        assert info["ip"] == "192.168.1.9"

    @pytest.mark.parametrize("claimed", ["0.0.0.0", "", "   "])
    def test_placeholder_ip_falls_back_to_the_probed_host(self, claimed: str) -> None:
        """A device without a lease reports 0.0.0.0; nothing can poll that."""
        from custom_components.marstek.discovery import _device_info_from_response

        info = _device_info_from_response(
            {"result": {"ble_mac": "AA:BB:CC:DD:EE:FF", "ip": claimed}},
            "192.168.1.9",
            30000,
        )

        assert info is not None
        assert info["ip"] == "192.168.1.9"

    def test_usable_claimed_ip_still_wins_and_is_normalized(self) -> None:
        """The claimed address remains authoritative when it is usable."""
        from custom_components.marstek.discovery import _device_info_from_response

        info = _device_info_from_response(
            {"result": {"ble_mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.09.92"}},
            "192.168.1.9",
            30000,
        )

        assert info is not None
        assert info["ip"] == "192.168.9.92"


class TestOversizedReply:
    """A reply longer than the read buffer is truncated and lost by the kernel."""

    @pytest.mark.asyncio
    async def test_read_buffer_fits_any_datagram_a_device_can_send(self) -> None:
        """A 6 KB schedule reply used to decode as broken JSON and time out."""
        from custom_components.marstek.discovery import get_device_info

        slots = [
            {"id": index, "start_time": 0, "end_time": 1440, "power": -2500,
             "label": "x" * 380}
            for index in range(12)
        ]
        device_response = {
            "id": 0,
            "result": {
                "device": "VenusE 3.0",
                "ver": 150,
                "ip": "192.168.1.100",
                "ble_mac": "AA:BB:CC:DD:EE:FF",
                "manual_cfg": slots,
            },
        }
        payload = json.dumps(device_response).encode()
        assert len(payload) > 4096

        requested_sizes: list[int] = []
        call_count = 0

        async def mock_recvfrom(_sock: Any, size: int) -> tuple[bytes, tuple[str, int]]:
            nonlocal call_count
            requested_sizes.append(size)
            call_count += 1
            if call_count == 1:
                return (payload, ("192.168.1.100", 30000))
            raise TimeoutError

        time_calls = [0.0]

        def time_side_effect() -> float:
            time_calls[0] += 0.1
            return time_calls[0]

        with patch("socket.socket", return_value=MagicMock()):
            with patch("asyncio.get_running_loop") as mock_loop:
                loop = MagicMock()
                loop.run_in_executor = _run_in_executor
                loop.sock_sendto = AsyncMock()
                loop.time.side_effect = time_side_effect
                loop.sock_recvfrom = mock_recvfrom
                mock_loop.return_value = loop

                result = await get_device_info("192.168.1.100", timeout=0.5)

        assert result is not None
        assert result["ble_mac"] == "AA:BB:CC:DD:EE:FF"
        assert all(size >= len(payload) for size in requested_sizes)
