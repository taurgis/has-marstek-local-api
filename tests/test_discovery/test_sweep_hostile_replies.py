"""What the broadcast sweep does with replies it should not trust.

Anything on the LAN can put a datagram on the Open API port, so the sweep sees
payloads that are not Marstek replies at all alongside the ones that are.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ._helpers import (
    _run_in_executor,
)


class TestSweepSurvivesNonObjectDatagram:
    """A stray scalar datagram must not cost the sweep its real devices.

    Anything on the LAN can put a datagram on the Open API port, and JSON-RPC
    2.0 says nothing about payloads that are not objects, so firmware and
    unrelated software both produce them. This used to raise ``TypeError`` out
    of ``discover_devices``: the scanner caught it as "Scanner discovery
    failed" and lost every device in that pass, and the config flow's
    discovery step -- which only catches OSError/TimeoutError/ValueError --
    let it escape to Home Assistant's flow engine.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("noise", [b"5", b"null", b"true", b'"text"', b"-1.5"])
    async def test_scalar_datagram_is_ignored(self, noise: bytes) -> None:
        """The device that answered after the noise is still discovered."""
        from custom_components.marstek.discovery import discover_devices

        device_response = {
            "id": 0,
            "result": {
                "device": "VenusE 3.0",
                "ver": 150,
                "ip": "192.168.1.100",
                "ble_mac": "AA:BB:CC:DD:EE:FF",
            },
        }

        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 12345)

        replies = [
            (noise, ("192.168.1.77", 30000)),
            (json.dumps(device_response).encode(), ("192.168.1.100", 30000)),
        ]

        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            if replies:
                return replies.pop(0)
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

        assert [device["ble_mac"] for device in result] == ["AA:BB:CC:DD:EE:FF"]


class TestSweepSkipsRepliesWithoutIdentity:
    """A reply that cannot become a config entry must not reach the picker."""

    @pytest.mark.asyncio
    async def test_identity_less_reply_is_not_offered(self) -> None:
        """Unrelated LAN traffic showed up as "Unknown vNone ()" in the picker."""
        from custom_components.marstek.discovery import discover_devices

        noise = json.dumps({"result": {"ip": "192.168.1.77", "ble_mac": 7}}).encode()
        real = json.dumps(
            {
                "id": 0,
                "src": "VenusC-02deadbeef06",
                "result": {"device": "VenusC", "ver": 153, "ip": "192.168.1.26"},
            }
        ).encode()

        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("0.0.0.0", 12345)

        replies = [
            (noise, ("192.168.1.77", 30000)),
            (real, ("192.168.1.26", 30000)),
        ]

        async def mock_recvfrom(*args: Any) -> tuple[bytes, tuple[str, int]]:
            if replies:
                return replies.pop(0)
            raise TimeoutError()

        time_calls = [0.0]

        def time_side_effect() -> float:
            time_calls[0] += 0.1
            return time_calls[0]

        with patch("socket.socket", return_value=mock_socket):
            with patch(
                "custom_components.marstek.discovery._get_broadcast_addresses",
                return_value=["192.168.1.255"],
            ):
                with patch("asyncio.get_running_loop") as mock_loop:
                    loop = MagicMock()
                    loop.run_in_executor = _run_in_executor
                    loop.sock_sendto = AsyncMock()
                    loop.time.side_effect = time_side_effect
                    loop.sock_recvfrom = mock_recvfrom
                    mock_loop.return_value = loop

                    result = await discover_devices(timeout=1.0)

        # The Venus C omits its MACs from ``result`` but carries one in ``src``
        # (issue #60), so it is kept; the identity-less reply is not.
        assert [device["ble_mac"] for device in result] == ["02:de:ad:be:ef:06"]
