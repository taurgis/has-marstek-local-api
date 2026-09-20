"""Helpers shared by the test_discovery tests."""

from __future__ import annotations

from typing import Any


async def _run_in_executor(_executor: Any, func: Any, *args: Any) -> Any:
    """Stand in for ``loop.run_in_executor`` on a hand-built mock loop.

    Discovery hops to the executor to read the interface table, because reading
    it on the event loop is a blocking call. These tests replace the running
    loop with a ``MagicMock``, so the hop has to be spelled out or the
    production code would await a ``MagicMock``.
    """
    return func(*args)
