"""Fixtures shared by the test_udp_client tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.marstek.pymarstek.udp import (
    MarstekUDPClient,
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
