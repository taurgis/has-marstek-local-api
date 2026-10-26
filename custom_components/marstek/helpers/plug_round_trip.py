"""Round-trip efficiency measured by an energy meter between the pack and the mains.

The capacity estimator only has the battery's own AC power reading, so its
round trip is implied by two medians and leaves out the power the battery
draws while idle. A smart plug between the battery and the mains counts the
energy that really went in and came out.

Between two moments with the same reported state of charge, the pack holds
the same energy, so energy out divided by energy in is the round trip with
no efficiency assumption and no stored-energy correction. Standby draw and
self-discharge in between are part of it. Each whole state-of-charge level
keeps the oldest meter reading of the last 30 days. When the pack is back at
a level after at least one full capacity went in, the quotient is published,
at most once a day.

Energy that reaches the pack without crossing the plug (DC solar input, the
off-grid socket) would push the quotient towards or over 100%, so such a
poll starts the measurement again. So does a counter that went down or a
different pair of meter entities. A BMS that corrects its own percentage
(the jump to 100 after a drifted charge) breaks "same percentage, same
energy", so the level readings start again from that poll too.

The same counters give the standby draw: over an idle stretch of at least
three hours, with no battery power reported and no energy out, the energy in
divided by the hours is what the battery uses to stay awake. At 8 W that is
24 Wh, so the 1 Wh steps of a kWh counter with three decimals stay near 4%.

Load stays low: the two sensor states are read from memory once per poll the
coordinator already makes, with no state listener, and the Store is asked to
write only when a published number changes (at most daily). Anchors ride along
with those writes; losing some to a restart only delays the next measurement.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.unit_conversion import EnergyConverter

from ..capacity_estimator import (
    CAPACITY_ATTR_KEY,
    CAPACITY_DATA_KEY,
    ES_STATUS_FRESH_KEY,
    SOC_CORRECTED_KEY,
)
from ..const import CONF_PLUG_ENERGY_IN, CONF_PLUG_ENERGY_OUT

PLUG_STORE_KEY = "plug_round_trip"

HORIZON_SECONDS = 30 * 86_400
REFRESH_SECONDS = 86_400
# Watts of solar input or off-grid output above this bypass the plug.
BYPASS_W = 20.0
MIN_EFFICIENCY = 0.5
MAX_EFFICIENCY = 1.0
# Reported battery watts at or under this count as idle.
IDLE_W = 5.0
MIN_IDLE_SECONDS = 3 * 3_600
MAX_STANDBY_W = 100.0


def energy_wh(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Return an energy sensor's state in watt-hours, or None when it is not usable."""
    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return None
    try:
        value = float(state.state)
        unit = state.attributes.get("unit_of_measurement")
        converted = EnergyConverter.convert(value, unit, UnitOfEnergy.WATT_HOUR)
    except (HomeAssistantError, TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) and converted >= 0.0 else None


@dataclass(frozen=True)
class _Reading:
    at: float
    energy_in_wh: float
    energy_out_wh: float


@dataclass(frozen=True)
class _Measurement:
    efficiency: float
    since: float
    at: float
    energy_in_wh: float
    energy_out_wh: float


def _fields(raw: object, names: tuple[str, ...]) -> list[float] | None:
    """Return the named finite numbers of a stored dict, or None if any is missing."""
    if not isinstance(raw, dict):
        return None
    values = [_finite_or_none(raw.get(name)) for name in names]
    return None if None in values else [value for value in values if value is not None]


def _reading_from(raw: object) -> _Reading | None:
    values = _fields(raw, ("at", "energy_in_wh", "energy_out_wh"))
    return None if values is None else _Reading(*values)


def _measurement_from(raw: object) -> _Measurement | None:
    values = _fields(raw, ("efficiency", "since", "at", "energy_in_wh", "energy_out_wh"))
    if values is None or not MIN_EFFICIENCY <= values[0] <= MAX_EFFICIENCY:
        return None
    return _Measurement(*values)


def _finite_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).isoformat()


class PlugRoundTrip:
    """Round trip from a linked meter's cumulative energy-in and energy-out sensors."""

    def __init__(self, meter: tuple[str, str]) -> None:
        """Start with no readings for this pair of entity ids."""
        self._meter = meter
        self._anchors: dict[int, _Reading] = {}
        self._measured: _Measurement | None = None
        self._idle_start: _Reading | None = None
        self._idle_last: _Reading | None = None
        self._standby: tuple[float, float] | None = None
        self._dirty = False

    @classmethod
    def from_dict(cls, raw: object, meter: tuple[str, str]) -> PlugRoundTrip:
        """Restore stored readings. A different meter starts from nothing."""
        plug = cls(meter)
        if not isinstance(raw, dict) or raw.get("meter") != list(meter):
            return plug
        anchors = raw.get("anchors")
        if isinstance(anchors, dict):
            for level, item in anchors.items():
                reading = _reading_from(item)
                if reading is not None and str(level).isdigit() and int(level) <= 100:
                    plug._anchors[int(level)] = reading
        plug._measured = _measurement_from(raw.get("measured"))
        standby = raw.get("standby")
        if isinstance(standby, list) and len(standby) == 2:
            watts, at = (_finite_or_none(item) for item in standby)
            if watts is not None and at is not None and 0.0 <= watts <= MAX_STANDBY_W:
                plug._standby = (watts, at)
        return plug

    def to_dict(self) -> dict[str, Any]:
        """Return the state for the Store file."""
        return {
            "meter": list(self._meter),
            "anchors": {str(level): asdict(reading) for level, reading in self._anchors.items()},
            "measured": None if self._measured is None else asdict(self._measured),
            "standby": None if self._standby is None else list(self._standby),
        }

    @property
    def meter(self) -> tuple[str, str]:
        """Return the energy-in and energy-out entity ids."""
        return self._meter

    def consume_dirty(self) -> bool:
        """Return whether a published number changed since the last call."""
        dirty, self._dirty = self._dirty, False
        return dirty

    def attributes(self) -> dict[str, Any]:
        """Return the published measurement for the usable capacity sensor."""
        attrs: dict[str, Any] = {}
        measured = self._measured
        if measured is not None:
            attrs["plug_round_trip_efficiency"] = round(measured.efficiency, 3)
            attrs["plug_round_trip_since"] = _iso(measured.since)
            attrs["plug_round_trip_at"] = _iso(measured.at)
            attrs["plug_round_trip_energy_in_wh"] = round(measured.energy_in_wh)
            attrs["plug_round_trip_energy_out_wh"] = round(measured.energy_out_wh)
        if self._standby is not None:
            attrs["plug_standby_w"] = round(self._standby[0], 1)
            attrs["plug_standby_at"] = _iso(self._standby[1])
        return attrs

    def observe(
        self,
        *,
        timestamp: float,
        soc: float | None,
        energy_in_wh: float | None,
        energy_out_wh: float | None,
        capacity_wh: float | None,
        power_w: float | None,
        bypass_w: float,
        soc_corrected: bool = False,
    ) -> None:
        """Fold one poll in. Missing values are skipped; the counters catch up later.

        ``soc_corrected`` drops the level readings taken before this poll. The
        standby stretch does not depend on state of charge and continues.
        """
        if bypass_w > BYPASS_W:
            self._restart()
            return
        if soc_corrected:
            self._anchors.clear()
        if soc is None or energy_in_wh is None or energy_out_wh is None:
            return
        reading = _Reading(timestamp, energy_in_wh, energy_out_wh)
        if any(
            old.energy_in_wh > energy_in_wh or old.energy_out_wh > energy_out_wh
            for old in [*self._anchors.values(), *filter(None, [self._idle_start])]
        ):
            self._restart()
        self._track_idle(reading, power_w is not None and abs(power_w) <= IDLE_W)
        level = round(soc)
        anchor = self._anchors.get(level)
        if anchor is None or timestamp - anchor.at > HORIZON_SECONDS:
            self._anchors[level] = reading
            return
        self._measure(anchor, reading, capacity_wh)

    def _measure(self, anchor: _Reading, reading: _Reading, capacity_wh: float | None) -> None:
        """Publish out/in since ``anchor`` once a full capacity went in."""
        energy_in = reading.energy_in_wh - anchor.energy_in_wh
        energy_out = reading.energy_out_wh - anchor.energy_out_wh
        if capacity_wh is None or capacity_wh <= 0.0 or energy_in < capacity_wh:
            return
        if self._measured is not None and reading.at - self._measured.at < REFRESH_SECONDS:
            return
        efficiency = energy_out / energy_in
        if not MIN_EFFICIENCY <= efficiency <= MAX_EFFICIENCY:
            return
        self._measured = _Measurement(efficiency, anchor.at, reading.at, energy_in, energy_out)
        self._dirty = True

    def _track_idle(self, reading: _Reading, idle: bool) -> None:
        """Publish an idle stretch's standby watts when it ends, and daily while it lasts."""
        start = self._idle_start
        if idle and reading.energy_out_wh == (start or reading).energy_out_wh:
            self._idle_start = start or reading
            self._idle_last = reading
            if self._standby is None or reading.at - self._standby[1] >= REFRESH_SECONDS:
                self._publish_standby(self._idle_start, reading)
            return
        last = self._idle_last
        self._idle_start = self._idle_last = reading if idle else None
        if start is not None and last is not None:
            self._publish_standby(start, last)

    def _publish_standby(self, start: _Reading, last: _Reading) -> None:
        """Keep energy in per hour over a long enough idle stretch."""
        hours = (last.at - start.at) / 3_600
        if hours * 3_600 < MIN_IDLE_SECONDS:
            return
        watts = (last.energy_in_wh - start.energy_in_wh) / hours
        if 0.0 <= watts <= MAX_STANDBY_W and self._standby != (watts, last.at):
            self._standby = (watts, last.at)
            self._dirty = True

    def _restart(self) -> None:
        """Forget the readings. The last published measurements stay."""
        self._idle_start = self._idle_last = None
        self._anchors.clear()


def plug_from_options(options: Mapping[str, Any], stored: object) -> PlugRoundTrip | None:
    """Return the tracker for the linked meter, or None when no meter is linked."""
    energy_in = options.get(CONF_PLUG_ENERGY_IN)
    energy_out = options.get(CONF_PLUG_ENERGY_OUT)
    if not isinstance(energy_in, str) or not isinstance(energy_out, str):
        return None
    if not energy_in or not energy_out:
        return None
    raw = stored.get(PLUG_STORE_KEY) if isinstance(stored, dict) else None
    return PlugRoundTrip.from_dict(raw, (energy_in, energy_out))


def apply_plug_round_trip(
    plug: PlugRoundTrip, hass: HomeAssistant, status: dict[str, Any], timestamp: float
) -> None:
    """Fold this poll and the meter's counters in, and add the results to the attributes.

    Runs after ``apply_capacity_estimate``, whose estimate sets how much energy
    must go in before a quotient counts.
    """
    fresh = status.get(ES_STATUS_FRESH_KEY, True) is not False
    energy_in_id, energy_out_id = plug.meter
    capacity = _finite_or_none(status.get(CAPACITY_DATA_KEY))
    bypass = [_finite_or_none(status.get(key)) for key in ("pv_power", "offgrid_power")]
    plug.observe(
        timestamp=timestamp,
        soc=_finite_or_none(status.get("battery_soc")) if fresh else None,
        energy_in_wh=energy_wh(hass, energy_in_id),
        energy_out_wh=energy_wh(hass, energy_out_id),
        capacity_wh=capacity if capacity is not None else _finite_or_none(status.get("bat_cap")),
        power_w=_finite_or_none(status.get("battery_power")) if fresh else None,
        bypass_w=max((abs(watts) for watts in bypass if watts is not None), default=0.0),
        soc_corrected=status.get(SOC_CORRECTED_KEY) is True,
    )
    attributes = status.get(CAPACITY_ATTR_KEY)
    # The entity ids let the cards find the plug's live watts on the same device.
    status[CAPACITY_ATTR_KEY] = {
        **(attributes if isinstance(attributes, dict) else {}),
        "plug_energy_entities": list(plug.meter),
        **plug.attributes(),
    }
