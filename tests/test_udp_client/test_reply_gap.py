"""The quiet gap a device gets after it answers."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.marstek.pymarstek.throttle import DeviceThrottle
from custom_components.marstek.pymarstek.udp import (
    MIN_REQUEST_INTERVAL,
    POST_REPLY_QUIET_INTERVAL,
    MarstekUDPClient,
)

_IP = "192.168.1.100"


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def test_wait_turn_waits_out_the_reply_gap() -> None:
    """A slow reply restarts the wait even when the send interval has passed."""
    clock = _Clock(100.0)
    throttle = DeviceThrottle(clock)
    throttle.note_sent(_IP)
    clock.now = 101.2  # a write answered 1.2 s after it went out
    throttle.note_reply(_IP)
    clock.now = 101.3

    with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        await throttle.wait_turn(_IP, MIN_REQUEST_INTERVAL, POST_REPLY_QUIET_INTERVAL)

    mock_sleep.assert_awaited_once()
    assert mock_sleep.call_args[0][0] == pytest.approx(POST_REPLY_QUIET_INTERVAL - 0.1)


async def test_wait_turn_does_not_wait_once_the_gap_has_passed() -> None:
    """Polls spaced further apart than the gap never sleep."""
    clock = _Clock(100.0)
    throttle = DeviceThrottle(clock)
    throttle.note_sent(_IP)
    throttle.note_reply(_IP)
    clock.now = 100.0 + POST_REPLY_QUIET_INTERVAL

    with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        await throttle.wait_turn(_IP, MIN_REQUEST_INTERVAL, POST_REPLY_QUIET_INTERVAL)

    mock_sleep.assert_not_awaited()


async def test_wait_turn_without_reply_keeps_send_interval() -> None:
    """A device that never answered only gets the send-based interval."""
    clock = _Clock(100.0)
    throttle = DeviceThrottle(clock)
    throttle.note_sent(_IP)
    clock.now = 100.1

    with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        await throttle.wait_turn(_IP, MIN_REQUEST_INTERVAL, POST_REPLY_QUIET_INTERVAL)

    assert mock_sleep.call_args[0][0] == pytest.approx(MIN_REQUEST_INTERVAL - 0.1)


async def test_prune_ages_out_reply_stamps() -> None:
    """Stamps from hosts that only ever sent to us cannot grow without bound."""
    clock = _Clock(1000.0)
    throttle = DeviceThrottle(clock, max_tracked_ips=2, stale_after=300.0)
    for index in range(3):
        throttle.note_reply(f"10.0.0.{index}")
    clock.now = 1400.0
    throttle.note_reply("10.0.0.9")

    assert throttle.is_crowded()
    await throttle.prune()

    assert throttle.last_reply_time == {"10.0.0.9": 1400.0}
    throttle.clear()
    assert throttle.last_reply_time == {}


async def test_listener_stamps_every_datagram() -> None:
    """Any datagram from a device starts its quiet gap, error replies included."""
    client = MarstekUDPClient()
    client._socket = MagicMock()
    client._loop = asyncio.get_running_loop()
    datagrams = [
        (b'{"id":0,"error":{"code":-32700,"message":"Parse error","data":403}}', (_IP, 30000)),
    ]

    async def mock_recvfrom(sock: Any, bufsize: int) -> tuple[bytes, tuple[str, int]]:
        if datagrams:
            return datagrams.pop()
        raise asyncio.CancelledError

    with patch.object(client._loop, "sock_recvfrom", mock_recvfrom):
        await client._listen_for_responses()

    assert _IP in client._throttle.last_reply_time
