"""Per-device pacing and serialization for Marstek Open API traffic.

Marstek devices are sensitive to request bursts, so every unicast waits out a
minimum gap since the last one to the same IP. The per-IP bookkeeping — last
send time and the two locks a device needs — lives here, along with the
pruning that keeps it from growing for every address the client ever touched.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)


class DeviceThrottle:
    """Minimum-interval pacing and per-device locks, keyed by IP."""

    def __init__(
        self,
        clock: Callable[[], float],
        *,
        max_tracked_ips: int = 100,
        stale_after: float = 300.0,
    ) -> None:
        self._clock = clock
        self.last_request_time: dict[str, float] = {}
        self.rate_limit_locks: dict[str, asyncio.Lock] = {}
        self.device_io_locks: dict[str, asyncio.Lock] = {}
        self.max_tracked_ips = max_tracked_ips
        self.stale_after = stale_after
        self._meta_lock = asyncio.Lock()

    async def rate_limit_lock(self, device_ip: str) -> asyncio.Lock:
        """Get or create the pacing lock for one device."""
        async with self._meta_lock:
            return self.rate_limit_locks.setdefault(device_ip, asyncio.Lock())

    async def io_lock(self, device_ip: str) -> asyncio.Lock:
        """Get or create the lock that serializes reset-prone exchanges."""
        async with self._meta_lock:
            return self.device_io_locks.setdefault(device_ip, asyncio.Lock())

    def note_sent(self, device_ip: str) -> None:
        """Stamp a send so the next request to this device waits its turn."""
        self.last_request_time[device_ip] = self._clock()

    def is_crowded(self) -> bool:
        """Return True once more IPs are tracked than the cap allows."""
        return len(self.last_request_time) > self.max_tracked_ips

    async def wait_turn(self, device_ip: str, min_interval: float) -> None:
        """Sleep until *min_interval* has passed since the last request.

        Per-IP locks mean a slow device never holds up traffic to another one.
        """
        ip_lock = await self.rate_limit_lock(device_ip)
        async with ip_lock:
            elapsed = self._clock() - self.last_request_time.get(device_ip, 0)
            if elapsed < min_interval:
                wait_time = min_interval - elapsed
                _LOGGER.debug(
                    "Rate limiting: waiting %.2fs before request to %s",
                    wait_time,
                    device_ip,
                )
                await asyncio.sleep(wait_time)
            self.note_sent(device_ip)

    async def prune(self) -> list[str]:
        """Forget devices quiet for longer than ``stale_after``.

        Returns the IPs dropped so the caller can release whatever else it
        tracks per device. Does nothing while the cap is not exceeded.
        """
        current_time = self._clock()
        async with self._meta_lock:
            if len(self.last_request_time) <= self.max_tracked_ips:
                return []

            stale_ips = [
                device_ip
                for device_ip, last_time in self.last_request_time.items()
                if current_time - last_time > self.stale_after
            ]
            for device_ip in stale_ips:
                self.last_request_time.pop(device_ip, None)
                self.rate_limit_locks.pop(device_ip, None)
                self.device_io_locks.pop(device_ip, None)

            if stale_ips:
                _LOGGER.debug("Cleaned up rate limit tracking for %d stale IPs", len(stale_ips))
            return stale_ips

    def clear(self) -> None:
        """Forget every tracked device."""
        self.last_request_time.clear()
        self.rate_limit_locks.clear()
        self.device_io_locks.clear()
