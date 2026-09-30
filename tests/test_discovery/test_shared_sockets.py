"""Broadcast discovery on a port a pooled client already owns.

Linux ``SO_REUSEPORT`` spreads datagrams across every socket bound to a port.
Binding a discovery socket next to a paused pooled client therefore lost the
replies hashed onto the pooled socket, so adding a device on port 30000 took
two or more attempts once another entry used that port. Discovery now reads
the paused pooled socket instead.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
import pytest_socket

from custom_components.marstek.discovery import discover_devices
from custom_components.marstek.helpers.udp_clients import paused_discovery_sockets
from custom_components.marstek.pymarstek.udp import MarstekUDPClient

_REPLY = {
    "id": 0,
    "src": "VenusE 3.0-02ee00000301",
    "result": {
        "device": "VenusE 3.0",
        "ver": 150,
        "ble_mac": "02ee00000301",
        "wifi_mac": "7483c2315cf8",
        "ip": "127.0.0.1",
    },
}


@pytest.fixture(autouse=True)
def _real_loopback_sockets() -> None:
    """These tests need real loopback UDP; the HA harness blocks it by default."""
    pytest_socket.enable_socket()
    pytest_socket.socket_allow_hosts(["127.0.0.1"], allow_unix_socket=True)


@pytest.fixture
def loopback_socket() -> Iterator[socket.socket]:
    """A bound, non-blocking loopback UDP socket, closed after the test."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.bind(("127.0.0.1", 0))
    yield sock
    sock.close()


async def test_discovery_reads_shared_socket_without_binding(
    loopback_socket: socket.socket,
) -> None:
    """A reply queued on the shared socket is found; no second socket is bound."""
    port = loopback_socket.getsockname()[1]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as device:
        device.sendto(json.dumps(_REPLY).encode(), ("127.0.0.1", port))
    await asyncio.sleep(0)

    with patch(
        "custom_components.marstek.discovery.create_udp_socket",
        side_effect=AssertionError("a shared port must not be bound again"),
    ):
        devices = await discover_devices(
            timeout=0.5,
            ports=[port],
            broadcast_addresses=["127.0.0.1"],
            shared_sockets={port: loopback_socket},
        )

    assert [device["ble_mac"] for device in devices] == ["02ee00000301"]
    # The pooled client still owns the socket, so discovery must not close it.
    assert loopback_socket.fileno() != -1


async def test_paused_socket_is_lent_only_while_paused(
    loopback_socket: socket.socket,
) -> None:
    """The listener and discovery must never read the socket at the same time."""
    client = MarstekUDPClient()
    client._socket = loopback_socket
    client._loop = asyncio.get_running_loop()
    port = loopback_socket.getsockname()[1]

    with patch.object(client, "_ensure_listener"):
        assert client.paused_socket() is None
        assert paused_discovery_sockets((client,)) == {}

        await client.async_pause_receiver()
        assert client.paused_socket() is loopback_socket
        assert paused_discovery_sockets((client,)) == {port: loopback_socket}

        await client.async_resume_receiver()
        assert client.paused_socket() is None


def test_paused_discovery_sockets_skips_clients_without_a_real_socket() -> None:
    """Mocks and closed sockets are not lent to discovery."""
    closed = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    closed.close()
    no_socket = MagicMock(spec=[])
    mock_socket = MagicMock()
    mock_socket.paused_socket.return_value = MagicMock()
    closed_client = MagicMock()
    closed_client.paused_socket.return_value = closed

    assert paused_discovery_sockets((no_socket, mock_socket, closed_client)) == {}
