"""JSON-RPC parse errors whose id is 0, as Control firmware and VNSEM-0 send them."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.marstek.pymarstek.response_router import ResponseRouter
from custom_components.marstek.pymarstek.udp import MarstekUDPClient

from ._helpers import _patch_sock_sendto

_HOST = "192.168.1.50"
_PARSE_ERROR = {
    "id": 0,
    "src": "VNSEM-0-aabbccddeeff",
    "error": {"code": -32700, "message": "Parse error", "data": 401},
}


def test_id_zero_parse_error_completes_one_opted_in_write() -> None:
    """Issue #104: the Passive reply uses id 0, not the request id."""
    router = ResponseRouter(time.monotonic)
    _wire_id, future = router.track(649, device_ip=_HOST, accept_orphan_parse_error=True)

    router.deliver(0, _PARSE_ERROR, (_HOST, 30000))

    assert future.done()
    assert future.result()["error"]["data"] == 401


def test_id_zero_parse_error_leaves_a_retransmitting_read_pending() -> None:
    """A busy parse error must not cancel the Wi-Fi copies still to be sent."""
    router = ResponseRouter(time.monotonic)
    _wire_id, future = router.track(1, device_ip=_HOST, accept_orphan_parse_error=False)

    router.deliver(
        0,
        {"id": 0, "error": {"code": -32700, "message": "Parse error", "data": 403}},
        (_HOST, 30000),
    )

    assert not future.done()


def test_id_zero_result_does_not_complete_another_request() -> None:
    """A GetDevice result that happens to use id 0 stays on the id-0 waiter."""
    router = ResponseRouter(time.monotonic)
    _wire_id, future = router.track(649, device_ip=_HOST, accept_orphan_parse_error=True)

    router.deliver(0, {"id": 0, "result": {"device": "VNSEM-0"}}, (_HOST, 30000))

    assert not future.done()


def test_two_opted_in_waiters_are_left_unmatched() -> None:
    """An id-0 error does not say which of two requests the firmware rejected."""
    router = ResponseRouter(time.monotonic)
    _first_id, first = router.track(1, device_ip=_HOST, accept_orphan_parse_error=True)
    _second_id, second = router.track(2, device_ip=_HOST, accept_orphan_parse_error=True)

    router.deliver(0, _PARSE_ERROR, (_HOST, 30000))

    assert not first.done()
    assert not second.done()


def test_getdevice_keeps_its_own_id_zero_error() -> None:
    """A real id-0 waiter, such as GetDevice, still wins over the fallback."""
    router = ResponseRouter(time.monotonic)
    _discovery_id, discovery = router.track(0, device_ip=_HOST)
    _write_id, write = router.track(649, device_ip=_HOST, accept_orphan_parse_error=True)

    router.deliver(0, _PARSE_ERROR, (_HOST, 30000))

    assert discovery.done()
    assert discovery.result()["error"]["code"] == -32700
    assert not write.done()


@asynccontextmanager
async def _listening_client(
    datagrams: list[tuple[bytes, tuple[str, int]]],
) -> AsyncIterator[MarstekUDPClient]:
    """Yield a client whose listener returns *datagrams*, then waits."""
    client = MarstekUDPClient()
    client._socket = MagicMock()
    client._loop = asyncio.get_running_loop()

    async def mock_recvfrom(_sock: Any, _bufsize: int) -> tuple[bytes, tuple[str, int]]:
        if datagrams:
            return datagrams.pop(0)
        await asyncio.sleep(3600)
        raise asyncio.CancelledError

    with (
        _patch_sock_sendto(),
        patch.object(client._loop, "sock_recvfrom", mock_recvfrom),
    ):
        try:
            yield client
        finally:
            task = client._listen_task
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


@pytest.mark.asyncio
async def test_setmode_returns_the_id_zero_parse_error() -> None:
    """ES.SetMode is one-shot, so the id-0 parse error is the answer."""
    queued = [(json.dumps(_PARSE_ERROR).encode(), (_HOST, 30000))]
    message = json.dumps(
        {
            "id": 649,
            "method": "ES.SetMode",
            "params": {
                "id": 0,
                "config": {"mode": "Passive", "passive_cfg": {"power": 0, "cd_time": 60}},
            },
        }
    )
    async with _listening_client(queued) as client:
        response = await client.send_request(message, _HOST, 30000, timeout=1.0)

    assert response["error"]["message"] == "Parse error"
    assert response["error"]["data"] == 401
    stats = client.get_command_stats_for_ip(_HOST)
    assert stats["ES.SetMode"]["total_timeouts"] == 0
    assert stats["ES.SetMode"]["total_success"] == 0


@pytest.mark.asyncio
async def test_retransmitting_read_ignores_id_zero_and_keeps_the_real_reply() -> None:
    """A read that will retransmit waits for the copy that echoes its own id."""
    success = {"id": 1, "result": {"bat_soc": 95}}
    queued = [
        (json.dumps(_PARSE_ERROR).encode(), (_HOST, 30000)),
        (json.dumps(success).encode(), (_HOST, 30000)),
    ]
    message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})
    async with _listening_client(queued) as client:
        client.set_openapi_retransmit_safe(_HOST, True)
        response = await client.send_request(message, _HOST, 30000, timeout=1.0)

    assert response["result"]["bat_soc"] == 95
