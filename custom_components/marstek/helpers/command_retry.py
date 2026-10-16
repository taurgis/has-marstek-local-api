"""Shared retry loop for Marstek device writes."""

from __future__ import annotations

import asyncio
import logging

from ..pymarstek import MarstekUDPClient

# Retry configuration
MAX_RETRY_ATTEMPTS = 3
RETRY_TIMEOUT = 5.0
RETRY_DELAY = 1.0


def format_jsonrpc_error(error: object) -> str:
    """Return a short description of a JSON-RPC error object."""
    if not isinstance(error, dict):
        return str(error)
    code = error.get("code", "unknown")
    message = error.get("message", "unknown")
    text = f"JSON-RPC error {code}: {message}"
    if "data" in error:
        text = f"{text} (data {error['data']})"
    return text


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
    datagram, so a write is retried before it is called lost. A JSON-RPC
    error object is a delivered rejection, not a successful write: it is
    retried the same way, and the last error text is what the caller shows.

    Args:
        udp_client: Client owning the socket.
        command: JSON command string to send.
        host: Target device address.
        port: Target device port.
        description: What is being sent, for the log lines
            (e.g. ``"ES.SetMode command"``).
        logger: Logger of the calling module, so lines keep its name.

    Returns:
        ``None`` once the device answers without a JSON-RPC error, else the
        last error text.
    """
    last_error: str | None = None
    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        try:
            response = await udp_client.send_request(
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
        else:
            rpc_error = response.get("error") if isinstance(response, dict) else None
            if rpc_error is None:
                logger.info(
                    "Successfully sent %s (attempt %d/%d)",
                    description,
                    attempt,
                    MAX_RETRY_ATTEMPTS,
                )
                return None
            last_error = format_jsonrpc_error(rpc_error)
            logger.warning(
                "Device rejected %s (attempt %d/%d): %s",
                description,
                attempt,
                MAX_RETRY_ATTEMPTS,
                last_error,
            )
        if attempt < MAX_RETRY_ATTEMPTS:
            await asyncio.sleep(RETRY_DELAY)
    return last_error
