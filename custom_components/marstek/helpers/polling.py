"""Polling-pause helper for Marstek write paths."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ..pymarstek import MarstekUDPClient


@asynccontextmanager
async def polling_paused(
    udp_client: MarstekUDPClient, host: str
) -> AsyncIterator[None]:
    """Hold polling for *host* while the wrapped block runs.

    Polls and writes share one UDP socket and one device, so a poll landing
    mid-write can cost the write its reply. Pauses are reference counted:
    every pause taken here is released exactly once, so a nested block never
    resumes polling while an outer one still needs it held.
    """
    await udp_client.pause_polling(host)
    try:
        yield
    finally:
        await udp_client.resume_polling(host)
