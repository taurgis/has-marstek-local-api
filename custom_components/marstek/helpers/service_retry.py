"""Service retry helpers for Marstek integration."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from ..const import CMD_ES_SET_MODE, DOMAIN
from ..pymarstek import MarstekUDPClient, build_command
from .command_retry import send_command_with_retries
from .polling import polling_paused


async def send_mode_command_with_retries(
    udp_client: MarstekUDPClient,
    host: str,
    port: int,
    config: dict[str, Any],
    *,
    pause_polling: bool = True,
    logger: logging.Logger,
) -> None:
    """Send an ES.SetMode command with retries, raising on failure.

    Callers that send a batch of commands pause polling once around the whole
    batch and pass ``pause_polling=False`` here.
    """
    command = build_command(CMD_ES_SET_MODE, {"id": 0, "config": config})

    last_error: str | None = None
    async with AsyncExitStack() as stack:
        if pause_polling:
            await stack.enter_async_context(polling_paused(udp_client, host))
        last_error = await send_command_with_retries(
            udp_client,
            command,
            host,
            port,
            description="mode command",
            logger=logger,
        )

    if last_error is not None:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"error": last_error},
        )
