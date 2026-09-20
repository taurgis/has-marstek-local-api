"""Helpers shared by the test_udp_client tests."""

from __future__ import annotations

import asyncio
from itertools import product
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.marstek.pymarstek.udp import (
    MarstekUDPClient,
)


def _complete_first_pending(
    client: MarstekUDPClient, response: dict[str, Any] | None = None
) -> None:
    """Complete the first unfinished pending unicast future."""
    payload = response if response is not None else {"id": 1, "result": {}}
    for future in client._router.pending.values():
        if not future.done():
            future.set_result(payload)
            return


def _patch_sock_sendto() -> Any:
    """Patch ``loop.sock_sendto`` for tests that drive a mocked socket.

    ``_send_udp_message`` puts datagrams on the wire through the event loop
    because the Open API socket is non-blocking. A ``MagicMock`` socket is not
    a real one, so the loop call has to be intercepted.
    """
    return patch.object(asyncio.get_running_loop(), "sock_sendto", AsyncMock())


def _unicast_test_client() -> MarstekUDPClient:
    """Return a UDP client with a mocked socket ready for send_request tests."""
    client = MarstekUDPClient()
    client._socket = MagicMock()
    client._loop = asyncio.get_running_loop()
    client._listen_task = MagicMock()
    client._listen_task.done.return_value = False
    return client


_STATUS_COMBINATION_LABELS = (
    "es_mode",
    "es_status",
    "em",
    "pv",
    "wifi",
    "bat",
)
_STATUS_COMBINATIONS = list(product([True, False], repeat=len(_STATUS_COMBINATION_LABELS)))
