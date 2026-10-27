"""Spot the self-test a Marstek unit runs at its mains plug when it boots.

A linked energy meter sees what the battery's AC cord carries. When a Venus E
3.0 restarts, that plug shows the same few seconds every time: the standby
draw (about 8 W) is gone, then the unit imports about 2.5 kW for 5 to 35
seconds, then the plug is quiet again before standby returns. Sometimes a
short export of about 1.25 kW comes first, and then the plug need not be
quiet after the burst: the unit can go straight back to charging. A user's
plug history showed this 37 times in ten days, about every five hours, by day
and by night.

Normal charging never starts or ends from a reading below standby, and it
lasts longer than a minute, so the quiet readings on both sides and the
short burst tell the self-test apart from a charge. No request goes to the
device: the plug's power sensor already updates Home Assistant.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from homeassistant.const import UnitOfPower
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util.unit_conversion import PowerConverter

# Below the unit's standby draw: the cord carries nothing.
QUIET_W = 3.0
# The burst holds above this; its first reading reaches PEAK_W.
BURST_W = 1500.0
PEAK_W = 2000.0
MAX_BURST_S = 60.0
# How close the quiet readings must sit to the burst on either side.
BRACKET_S = 30.0
# The export some self-tests start with, before the quiet reading.
PRE_PULSE_W = -1000.0
PRE_PULSE_S = 40.0


class BootPulseDetector:
    """Feed plug watts in time order; get the burst's start when one completes.

    Positive watts are import, as HomeWizard and Shelly plugs report them.
    A state change only arrives when the value moves, so the detector keeps
    whether the last reading was quiet rather than a stream of samples.
    """

    def __init__(self) -> None:
        """Start without history."""
        self._quiet_at: float | None = None
        self._quiet_now = False
        self._export_at: float | None = None
        self._burst_start: float | None = None
        self._pre_pulse = False
        self._peak = 0.0
        # A burst that ended, waiting for the quiet reading after it.
        self._ended: tuple[float, float, float] | None = None
        # A burst too long or not preceded by quiet: wait until it ends.
        self._skip = False

    def feed(self, at: float, watts: float) -> float | None:
        """Take one reading and return the self-test's start time when it closes."""
        if watts >= BURST_W:
            self._quiet_now = False
            self._on_burst(at, watts)
            return None
        if watts <= PRE_PULSE_W:
            self._export_at = at
        if self._burst_start is not None:
            start, self._burst_start = self._burst_start, None
            if self._pre_pulse:
                # Export, quiet, burst: nothing else looks like this.
                self._skip = False
                self._quiet_now, self._quiet_at = abs(watts) <= QUIET_W, at
                return start
            self._ended = (start, at, self._peak)
        self._skip = False
        quiet = abs(watts) <= QUIET_W
        found = self._close(at) if quiet else None
        if self._ended is not None and at - self._ended[1] > BRACKET_S:
            self._ended = None
        self._quiet_now = quiet
        if quiet:
            self._quiet_at = at
        return found

    def _on_burst(self, at: float, watts: float) -> None:
        if self._skip:
            return
        if self._burst_start is None:
            recent = self._quiet_at is not None and at - self._quiet_at <= BRACKET_S
            if not (self._quiet_now or recent) or watts < PEAK_W:
                self._skip = True
                return
            self._burst_start, self._peak, self._ended = at, watts, None
            self._pre_pulse = self._export_at is not None and at - self._export_at <= PRE_PULSE_S
            return
        self._peak = max(self._peak, watts)
        if at - self._burst_start > MAX_BURST_S:
            self._burst_start, self._skip = None, True

    def _close(self, at: float) -> float | None:
        ended, self._ended = self._ended, None
        if ended is None:
            return None
        start, end, peak = ended
        if at - end > BRACKET_S or peak < PEAK_W:
            return None
        return start


def plug_power_entity(hass: HomeAssistant, energy_entity_id: str | None) -> str | None:
    """Return the power sensor on the same device as a linked energy sensor.

    The options link the plug's energy counters; its watts live on a sibling
    sensor. A per-phase sensor repeats the total under a longer id, so the
    shortest id wins, as on the battery health card.
    """
    registry = er.async_get(hass)
    energy = registry.async_get(energy_entity_id) if energy_entity_id else None
    if energy is None or energy.device_id is None:
        return None
    powers = sorted(
        (
            entry.entity_id
            for entry in er.async_entries_for_device(registry, energy.device_id)
            if entry.domain == "sensor"
            and entry.disabled_by is None
            and (entry.device_class or entry.original_device_class) == "power"
        ),
        key=lambda entity_id: (len(entity_id), entity_id),
    )
    return powers[0] if powers else None


def _watts(event: Event[EventStateChangedData]) -> float | None:
    state = event.data["new_state"]
    if state is None:
        return None
    try:
        value = PowerConverter.convert(
            float(state.state),
            state.attributes.get("unit_of_measurement", UnitOfPower.WATT),
            UnitOfPower.WATT,
        )
    except (HomeAssistantError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def async_track_boot_pulses(
    hass: HomeAssistant, power_entity_id: str, on_pulse: Callable[[float], None]
) -> CALLBACK_TYPE:
    """Call ``on_pulse`` with the start time of each self-test the plug shows."""
    detector = BootPulseDetector()

    @callback
    def _changed(event: Event[EventStateChangedData]) -> None:
        state = event.data["new_state"]
        watts = _watts(event)
        if state is None or watts is None:
            return
        start = detector.feed(state.last_updated_timestamp, watts)
        if start is not None:
            on_pulse(start)

    return async_track_state_change_event(hass, [power_entity_id], _changed)
