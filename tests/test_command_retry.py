"""Write retries surface a JSON-RPC error instead of treating it as success."""

from __future__ import annotations

import logging
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.marstek.helpers.command_retry import (
    MAX_RETRY_ATTEMPTS,
    format_jsonrpc_error,
    send_command_with_retries,
)
from custom_components.marstek.pymarstek import MarstekUDPClient

_PARSE_ERROR = {"id": 0, "error": {"code": -32700, "message": "Parse error", "data": 401}}


def test_format_jsonrpc_error_includes_the_device_data_code() -> None:
    """The text the user sees keeps the code, message and data field."""
    assert format_jsonrpc_error(_PARSE_ERROR["error"]) == (
        "JSON-RPC error -32700: Parse error (data 401)"
    )


@pytest.mark.asyncio
async def test_jsonrpc_error_is_retried_and_then_reported() -> None:
    """Three parse errors become the device's error text, not a success."""
    client = cast(MarstekUDPClient, AsyncMock())
    client.send_request = AsyncMock(return_value=_PARSE_ERROR)

    with patch("custom_components.marstek.helpers.command_retry.asyncio.sleep", AsyncMock()):
        error = await send_command_with_retries(
            client,
            '{"id":649,"method":"ES.SetMode"}',
            "192.168.1.50",
            30000,
            description="mode command",
            logger=logging.getLogger("test"),
        )

    assert client.send_request.await_count == MAX_RETRY_ATTEMPTS
    assert error == "JSON-RPC error -32700: Parse error (data 401)"


@pytest.mark.asyncio
async def test_jsonrpc_error_then_success_returns_none() -> None:
    """A later acknowledgement still counts as the write landing."""
    client = cast(MarstekUDPClient, AsyncMock())
    client.send_request = AsyncMock(
        side_effect=[
            _PARSE_ERROR,
            {"id": 2, "result": {"id": 0, "set_result": True}},
        ]
    )

    with patch("custom_components.marstek.helpers.command_retry.asyncio.sleep", AsyncMock()):
        error = await send_command_with_retries(
            client,
            '{"id":2,"method":"ES.SetMode"}',
            "192.168.1.50",
            30000,
            description="mode command",
            logger=logging.getLogger("test"),
        )

    assert error is None
    assert client.send_request.await_count == 2


@pytest.mark.asyncio
async def test_timeout_text_is_unchanged_when_nothing_answers() -> None:
    """Silence is still reported as the transport timeout."""
    client = cast(MarstekUDPClient, AsyncMock())
    client.send_request = AsyncMock(
        side_effect=TimeoutError("Request timeout to 192.168.1.50:30000")
    )

    with patch("custom_components.marstek.helpers.command_retry.asyncio.sleep", AsyncMock()):
        error = await send_command_with_retries(
            client,
            '{"id":2,"method":"ES.SetMode"}',
            "192.168.1.50",
            30000,
            description="mode command",
            logger=logging.getLogger("test"),
        )

    assert error == "Request timeout to 192.168.1.50:30000"


def test_format_jsonrpc_error_accepts_a_non_object() -> None:
    """A non-object error member is still shown rather than dropped."""
    assert format_jsonrpc_error("Parse error") == "Parse error"
    assert format_jsonrpc_error({"code": -32601, "message": "Method not found"}) == (
        "JSON-RPC error -32601: Method not found"
    )
