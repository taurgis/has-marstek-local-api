"""Per-command diagnostics counters for the UDP client.

Every exchange records one outcome per method and device IP, which is what
the diagnostics download and the command-statistics sensors read back.
"""

from __future__ import annotations

import time
from typing import Any


def _new_bucket() -> dict[str, Any]:
    """Create an empty counter bucket."""
    return {
        "total_attempts": 0,
        "total_success": 0,
        "total_timeouts": 0,
        "total_failures": 0,
        "total_retransmits": 0,
        "last_success": None,
        "last_latency": None,
        "last_timeout": None,
        "last_error": None,
        "last_updated": None,
    }


class CommandStats:
    """Command outcome counters, per method and device IP."""

    def __init__(self) -> None:
        self._by_ip: dict[str, dict[str, dict[str, Any]]] = {}

    def record(
        self,
        method: str,
        *,
        device_ip: str,
        success: bool,
        timeout: bool,
        latency: float | None,
        error: str | None,
        retransmitted: bool = False,
    ) -> None:
        """Record one command outcome against its method and device."""
        bucket = self._by_ip.setdefault(device_ip, {}).setdefault(method, _new_bucket())
        bucket["total_attempts"] += 1
        if success:
            bucket["total_success"] += 1
        elif timeout:
            bucket["total_timeouts"] += 1
        else:
            bucket["total_failures"] += 1
        if retransmitted:
            bucket["total_retransmits"] += 1

        bucket["last_success"] = success
        bucket["last_latency"] = latency
        bucket["last_timeout"] = timeout
        bucket["last_error"] = error
        bucket["last_updated"] = time.time()

    def snapshot_for_ip(self, device_ip: str) -> dict[str, dict[str, Any]]:
        """Return a copy of the counters recorded for one device IP."""
        return {
            method: dict(stats)
            for method, stats in self._by_ip.get(device_ip, {}).items()
        }

    def forget_ip(self, device_ip: str) -> None:
        """Drop the per-device counters for one IP the client stopped tracking."""
        self._by_ip.pop(device_ip, None)

    def clear(self) -> None:
        """Drop every recorded counter."""
        self._by_ip.clear()
