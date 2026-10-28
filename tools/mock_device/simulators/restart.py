"""Device restarts and Wi-Fi drops, so the integration can learn to tell them apart.

A restart takes the device off the network for a boot window and leaves the
traces ``custom_components/marstek/helpers/restart_detector.py`` looks for:

* Lifetime energy totals fall back to the last save. At boot the Control
  firmware writes its stored counters back to the inverter
  (``tools/firmware_emulator/README.md``, CAN cmd 0x16/0x17). How often a real
  unit saves them is not known; ``energy_save_s`` stands in for that.
* The CT link is down until the meter reconnects, and a Passive countdown is
  lost, so the mode falls back to Auto. Issue #14 reports a lost CT and a mode
  change after resets. Which mode a healthy 150 falls back to is not known.

A linked smart plug sees the self-test the unit runs as it boots: the standby
draw is gone, then about 2.5 kW of import for 20 s, then nothing until
standby returns (a user's plug history, 37 times in ten days). The plug
server shows that in place of the live watts.

A network reset is smaller. Control 150+ resets its own network chip while
cloud uploads fail (``firmware_quirks.upload_failure_resets``): the device
answers nothing for a few seconds, its self-use loop commands 0 W until the CT
data flows again and then ramps up from there, and the plug shows the same
start-up check. Energy totals, CT link and mode stay as they were.

A Wi-Fi drop loses one datagram at random and nothing else, which the
integration's retransmits recover inside the same poll.
"""

from __future__ import annotations

import random
import time
from typing import Any

from ..const import MODE_AUTO, MODE_PASSIVE

# A Venus E 3.0 on firmware 150 is back on the network 30-60 s after it restarts.
DEFAULT_BOOT_S = 45.0
DEFAULT_CT_RECONNECT_S = 60.0
DEFAULT_ENERGY_SAVE_S = 600.0
DEFAULT_NETWORK_RESET_S = 8.0
_TOTAL_KEYS = (
    "total_pv_energy",
    "total_grid_output_energy",
    "total_grid_input_energy",
    "total_load_energy",
)
# A scheduled restart lands within this share of the interval either side, so
# restarts in a long run do not line up with the poll interval.
_JITTER = 0.25
# The firmware's own timer sets the network reset period; only the task's
# pass time varies.
_NETWORK_JITTER = 0.05

# (until seconds after the restart, plug watts) for the boot self-test.
BOOT_PULSE = ((10.0, 0), (15.0, 2550), (30.0, 2200), (40.0, 0))

REBOOTING = "rebooting"
NETWORK_RESET = "network chip reset"
WIFI_DROP = "Wi-Fi drop"


class RestartSimulator:
    """Decide which datagrams a restarting or flaky device never answers."""

    def __init__(
        self,
        battery: Any | None,
        *,
        every_s: float | None = None,
        boot_s: float = DEFAULT_BOOT_S,
        ct_reconnect_s: float = DEFAULT_CT_RECONNECT_S,
        energy_save_s: float = DEFAULT_ENERGY_SAVE_S,
        wifi_drop_rate: float = 0.0,
        network_reset_every_s: float | None = None,
        network_reset_s: float = DEFAULT_NETWORK_RESET_S,
        rng: random.Random | None = None,
    ) -> None:
        """Set up the schedule. ``battery`` is None without the simulation."""
        self._battery = battery
        self._every = every_s
        self._boot = boot_s
        self._ct_reconnect = ct_reconnect_s
        self._energy_save = energy_save_s
        self._drop_rate = wifi_drop_rate
        self._rng = rng or random.Random()
        self._booting_until = 0.0
        self._ct_back_at: float | None = None
        self._requested: float | None = None
        self._restarted_at: float | None = None
        self.restarts = 0
        self._network_every = network_reset_every_s
        self._network_offline = network_reset_s
        self._network_down_until = 0.0
        self._network_requested: float | None = None
        self._network_reset_at: float | None = None
        self.network_resets = 0
        now = time.time()
        self._next_restart = self._schedule(now, self._every)
        self._next_network_reset = self._schedule(now, network_reset_every_s, _NETWORK_JITTER)
        self._saved_totals = self._totals()
        self._saved_at = now

    def _schedule(self, now: float, every: float | None, jitter: float = _JITTER) -> float | None:
        if every is None:
            return None
        return now + every * self._rng.uniform(1 - jitter, 1 + jitter)

    def _totals(self) -> dict[str, float]:
        if self._battery is None:
            return {}
        return {key: float(getattr(self._battery, key)) for key in _TOTAL_KEYS}

    def request_restart(self) -> None:
        """Restart at the next datagram, as if the device crashed now.

        Safe from a signal handler: it only records the time.
        """
        self._requested = time.time()

    def request_network_reset(self) -> None:
        """Reset the network chip at the next datagram. Safe from a signal handler."""
        self._network_requested = time.time()

    def drop_reason(self, now: float) -> str | None:
        """Return why this datagram goes unanswered, or None to answer it."""
        self._tick(now)
        if now < self._booting_until:
            return REBOOTING
        if now < self._network_down_until:
            return NETWORK_RESET
        if self._drop_rate and self._rng.random() < self._drop_rate:
            return WIFI_DROP
        return None

    def plug_watts(self, now: float) -> int | None:
        """Return what the plug shows during the start-up check, else None.

        The check follows a restart and a network reset alike.
        """
        started = [at for at in (self._restarted_at, self._network_reset_at) if at is not None]
        if not started:
            return None
        elapsed = now - max(started)
        if elapsed < 0:
            return None
        return next((watts for until, watts in BOOT_PULSE if elapsed < until), None)

    def _tick(self, now: float) -> None:
        if self._requested is not None:
            started, self._requested = self._requested, None
            self.restart(started)
        elif self._next_restart is not None and now >= self._next_restart:
            self.restart(self._next_restart)
        if self._network_requested is not None:
            started, self._network_requested = self._network_requested, None
            self.network_reset(started)
        elif self._next_network_reset is not None and now >= self._next_network_reset:
            self.network_reset(self._next_network_reset)
        if self._battery is not None:
            self._battery.network_down = now < self._network_down_until
        if self._ct_back_at is not None and now >= self._ct_back_at:
            self._ct_back_at = None
            if self._battery is not None:
                self._battery.ct_connected = True
        if now < self._booting_until or now - self._saved_at < self._energy_save:
            return
        self._saved_totals = self._totals()
        self._saved_at = now

    def restart(self, started: float) -> None:
        """Reboot at *started*: go silent and lose what was only in RAM."""
        self.restarts += 1
        self._restarted_at = started
        self._booting_until = started + self._boot
        self._next_restart = self._schedule(started, self._every)
        battery = self._battery
        print(
            f"[{time.strftime('%H:%M:%S')}] Restart #{self.restarts}: silent for "
            f"{self._boot:.0f}s, energy totals back to the save "
            f"{max(0.0, started - self._saved_at):.0f}s earlier"
        )
        if battery is None:
            return
        for key, value in self._saved_totals.items():
            setattr(battery, key, value)
        if self._saved_totals:
            battery.pv_channels.total_pv_energy = self._saved_totals["total_pv_energy"]
        if battery.mode == MODE_PASSIVE:
            battery.mode = MODE_AUTO
            battery.target_power = 0
            battery.passive_end_time = None
        battery.ct_connected = False
        self._ct_back_at = self._booting_until + self._ct_reconnect
        self._saved_at = self._booting_until

    def network_reset(self, started: float) -> None:
        """Reset the network chip at *started*: silent for a few seconds, nothing lost."""
        self.network_resets += 1
        self._network_reset_at = started
        self._network_down_until = started + self._network_offline
        self._next_network_reset = self._schedule(started, self._network_every, _NETWORK_JITTER)
        print(
            f"[{time.strftime('%H:%M:%S')}] Network reset #{self.network_resets}: silent for "
            f"{self._network_offline:.0f}s, no cloud upload went through"
        )
