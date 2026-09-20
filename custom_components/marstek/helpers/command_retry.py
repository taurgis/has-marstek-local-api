"""Shared retry loop for Marstek device writes."""

from __future__ import annotations

import asyncio
import logging

from ..pymarstek import MarstekUDPClient

# Retry configuration
MAX_RETRY_ATTEMPTS = 3
RETRY_TIMEOUT = 5.0
RETRY_DELAY = 1.0


async def send_command_with_retries(
    udp_client: MarstekUDPClient,
    command: str,
    host: str,
    port: int,
    *,
    description: str,
    logger: logging.Logger,
) -> str | None:
    """Send *command* to a device, retrying while the transport fails.

    UDP gives no delivery guarantee and Wi-Fi devices drop the occasional
    datagram, so a write is retried before it is called lost.

    Args:
        udp_client: Client owning the socket.
        command: JSON command string to send.
        host: Target device address.
        port: Target device port.
        description: What is being sent, for the log lines
            (e.g. ``"ES.SetMode command"``).
        logger: Logger of the calling module, so lines keep its name.

    Returns:
        ``None`` once the device answers, else the last error text.
    """
    last_error: str | None = None
    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        try:
            await udp_client.send_request(
                command,
                host,
                port,
                timeout=RETRY_TIMEOUT,
            )
        except (TimeoutError, OSError, ValueError) as err:
            logger.warning(
                "Failed to send %s (attempt %d/%d): %s",
                description,
                attempt,
                MAX_RETRY_ATTEMPTS,
                err,
            )
            last_error = str(err)
            if attempt < MAX_RETRY_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY)
        else:
            logger.info(
                "Successfully sent %s (attempt %d/%d)",
                description,
                attempt,
                MAX_RETRY_ATTEMPTS,
            )
            return None
    return last_error
