"""Per-device gate that keeps coordinator polls and writes off the wire together.

Control firmware answers one Open API request at a time. A writer therefore
pauses polling for its device, waits for the in-flight poll cycle to drain,
runs its exchange and resumes. Pauses are reference counted so two writers
cannot un-pause each other halfway through.
"""

from __future__ import annotations

import asyncio


class PollGate:
    """Reference-counted pause/resume state for one UDP client."""

    def __init__(self) -> None:
        self._paused: dict[str, bool] = {}
        self._pause_counts: dict[str, int] = {}
        self._cycle_counts: dict[str, int] = {}
        self._cycle_idle: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    def _idle_event(self, device_ip: str) -> asyncio.Event:
        """Return the "no poll cycle running" event for a device."""
        event = self._cycle_idle.get(device_ip)
        if event is None:
            event = asyncio.Event()
            event.set()
            self._cycle_idle[device_ip] = event
        return event

    async def begin_cycle(self, device_ip: str) -> bool:
        """Mark a poll cycle as running, or return False while paused."""
        async with self._lock:
            if self._paused.get(device_ip, False):
                return False
            self._cycle_counts[device_ip] = self._cycle_counts.get(device_ip, 0) + 1
            self._idle_event(device_ip).clear()
            return True

    async def end_cycle(self, device_ip: str) -> None:
        """Mark a poll cycle finished so paused writers can proceed."""
        async with self._lock:
            count = self._cycle_counts.get(device_ip, 0) - 1
            if count <= 0:
                self._cycle_counts.pop(device_ip, None)
                self._idle_event(device_ip).set()
                return
            self._cycle_counts[device_ip] = count

    async def pause(self, device_ip: str) -> None:
        """Hold off new poll cycles and wait for the running one to finish."""
        async with self._lock:
            self._pause_counts[device_ip] = self._pause_counts.get(device_ip, 0) + 1
            self._paused[device_ip] = True
            idle = self._idle_event(device_ip)
        try:
            await idle.wait()
        except BaseException:
            await self.resume(device_ip)
            raise

    async def resume(self, device_ip: str) -> None:
        """Drop one pause; polling restarts once the last one is released."""
        async with self._lock:
            count = self._pause_counts.get(device_ip, 0) - 1
            if count <= 0:
                self._pause_counts.pop(device_ip, None)
                self._paused[device_ip] = False
                return
            self._pause_counts[device_ip] = count

    def is_paused(self, device_ip: str) -> bool:
        """Return True while at least one writer holds this device paused."""
        return self._paused.get(device_ip, False)

    def clear(self) -> None:
        """Forget every device, releasing anyone waiting for an idle cycle."""
        self._paused.clear()
        self._pause_counts.clear()
        self._cycle_counts.clear()
        for idle in self._cycle_idle.values():
            idle.set()
        self._cycle_idle.clear()
