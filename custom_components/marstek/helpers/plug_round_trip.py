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

The same counters give the standby draw, which is the usual power the plug
shows while the battery is idle, not the average of every watt-hour in that
stretch. A wake pulse (the boot self-test, a fan) or a short charge between
polls adds energy the battery never reports as power. On a Venus E 3.0 that
average then reads about twice the 9 W the plug shows the rest of the time.
The 1 Wh steps of a kWh counter are not that gap: a steady 9 W still totals
about 9 W.

Idle is three hours or more, with 5 W or less of reported battery power and
no energy out. When the plug has a power sensor on the same device, the
figure is the median of those readings, which is what the plug shows most of
the time. Without one, each ten-minute block of the energy counter is a rate,
blocks that hold a pulse or a charge are set aside, and the mean of the rest
is published. A stored average from before this rule is dropped. The figure
is published when the stretch ends, and once a day while it lasts. Anything
above 100 W is dropped.

Load stays low: the meter states, and the plug's power when it has one, are
read from memory once per poll the coordinator already makes. The boot
self-test is the only state listener, and the Store is asked to write only
when a published number changes (at most daily). Anchors ride along with
those writes; losing some to a restart only delays the next measurement.
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
    SOC_UNCONFIRMED_KEY,
)
from ..const import CONF_PLUG_ENERGY_IN, CONF_PLUG_ENERGY_OUT
from .boot_pulse import entity_power_w, plug_power_entity

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
# A stored standby from before this version is an average that counted wake
# pulses. It is dropped so the usual reading can take its place.
STANDBY_VERSION = 2
# Ten minutes keeps a 1 Wh counter step under 6 W, and a wake pulse out of
# most blocks. A 30 second poll is too short: one step would look like 120 W.
STANDBY_BUCKET_SECONDS = 600
# How far a block may sit above the quieter half and still be the continuous
# draw. A kilowatt pulse in one block is well past this.
PULSE_MARGIN_W = 12.0
# Median of the plug's own watts once this many idle readings exist.
MIN_POWER_SAMPLES = 5
# A hole longer than this starts a new stretch, so a missed charge is not standby.
MAX_IDLE_GAP_SECONDS = 2 * 3_600


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


def _median(values: list[float]) -> float:
    """Return the middle value, or the mean of the two middle values."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _standby_pair(raw: object) -> tuple[float, float] | None:
    """Return a stored standby pair that sits inside the published range."""
    if not isinstance(raw, list) or len(raw) != 2:
        return None
    watts, at = (_finite_or_none(item) for item in raw)
    if watts is None or at is None or not 0.0 <= watts <= MAX_STANDBY_W:
        return None
    return (watts, at)


def _usable_plug_w(plug_w: float | None) -> float | None:
    """Keep an import reading. Export and missing readings are not standby samples."""
    if plug_w is None or not math.isfinite(plug_w) or plug_w < 0.0:
        return None
    return plug_w


@dataclass(frozen=True)
class _IdleSample:
    at: float
    energy_in_wh: float
    energy_out_wh: float
    plug_w: float | None


def _bucket_rates(samples: list[_IdleSample]) -> list[float]:
    """Return the watt rate of each block of at least ten minutes."""
    rates: list[float] = []
    start = 0
    for index in range(1, len(samples)):
        span = samples[index].at - samples[start].at
        if span < STANDBY_BUCKET_SECONDS:
            continue
        hours = span / 3_600
        delta = samples[index].energy_in_wh - samples[start].energy_in_wh
        if hours > 0 and delta >= 0:
            rates.append(delta / hours)
        start = index
    return rates


def _continuous_rate(rates: list[float]) -> float | None:
    """Return the mean of the blocks that sit with the quieter half.

    That half is the continuous draw. A block that also holds a wake pulse or
    a short charge sits well above it and is left out. With no pulses every
    block is the same rate, so the mean is that rate.
    """
    if not rates:
        return None
    ordered = sorted(rates)
    lower_count = max(1, (len(ordered) + 1) // 2)
    anchor = _median(ordered[:lower_count])
    ceiling = max(anchor * 2.0, anchor + PULSE_MARGIN_W)
    # The quieter half is at or under the ceiling, so at least one block remains.
    kept = [rate for rate in ordered if rate <= ceiling]
    return sum(kept) / len(kept)


def _typical_watts(samples: list[_IdleSample]) -> float | None:
    """Return the usual idle watts, or None while the stretch is under three hours."""
    if len(samples) < 2 or samples[-1].at - samples[0].at < MIN_IDLE_SECONDS:
        return None
    powers = [sample.plug_w for sample in samples if sample.plug_w is not None]
    # The plug's own watts match its display. The counter is the fallback.
    if len(powers) >= MIN_POWER_SAMPLES and len(powers) * 2 >= len(samples):
        return _median(powers)
    return _continuous_rate(_bucket_rates(samples))


class PlugRoundTrip:
    """Round trip from a linked meter's cumulative energy-in and energy-out sensors."""

    def __init__(self, meter: tuple[str, str]) -> None:
        """Start with no readings for this pair of entity ids."""
        self._meter = meter
        self._anchors: dict[int, _Reading] = {}
        self._measured: _Measurement | None = None
        self._idle: list[_IdleSample] = []
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
        standby = _standby_pair(raw.get("standby"))
        if standby is not None and raw.get("standby_version") == STANDBY_VERSION:
            plug._standby = standby
        elif standby is not None:
            # The old number is an average. Drop it so the next idle stretch
            # can publish the usual reading, and rewrite the file without it.
            plug._dirty = True
        return plug

    def to_dict(self) -> dict[str, Any]:
        """Return the state for the Store file."""
        return {
            "meter": list(self._meter),
            "anchors": {str(level): asdict(reading) for level, reading in self._anchors.items()},
            "measured": None if self._measured is None else asdict(self._measured),
            "standby_version": STANDBY_VERSION,
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
        plug_w: float | None = None,
    ) -> None:
        """Fold one poll in. Missing meter values are skipped; the counters catch up later.

        ``soc_corrected`` drops the level readings taken before this poll. The
        standby stretch does not depend on state of charge. Unknown battery
        power leaves that stretch open instead of ending it. ``plug_w`` is the
        plug's own power reading when the caller has one.
        """
        if bypass_w > BYPASS_W:
            self._restart()
            return
        if soc_corrected:
            self._anchors.clear()
        if energy_in_wh is None or energy_out_wh is None:
            return
        reading = _Reading(timestamp, energy_in_wh, energy_out_wh)
        if self._counters_reversed(reading):
            self._restart()
        if power_w is not None:
            self._track_idle(reading, abs(power_w) <= IDLE_W, plug_w)
        if soc is None:
            return
        level = round(soc)
        anchor = self._anchors.get(level)
        if anchor is None or timestamp - anchor.at > HORIZON_SECONDS:
            self._anchors[level] = reading
            return
        self._measure(anchor, reading, capacity_wh)

    def _counters_reversed(self, reading: _Reading) -> bool:
        """Return whether a stored reading is ahead of this one."""
        held = list(self._anchors.values())
        if self._idle:
            first = self._idle[0]
            held.append(_Reading(first.at, first.energy_in_wh, first.energy_out_wh))
        return any(
            old.energy_in_wh > reading.energy_in_wh or old.energy_out_wh > reading.energy_out_wh
            for old in held
        )

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

    def _track_idle(self, reading: _Reading, idle: bool, plug_w: float | None) -> None:
        """Publish an idle stretch's usual watts when it ends, and daily while it lasts."""
        exporting = bool(self._idle) and reading.energy_out_wh != self._idle[0].energy_out_wh
        gap = bool(self._idle) and reading.at - self._idle[-1].at > MAX_IDLE_GAP_SECONDS
        if exporting or not idle or gap:
            # A reported charge ends the stretch before this poll. Energy leaving
            # the pack does too. A long hole ends it before this poll as well, so
            # the missed energy stays out. This poll starts the next stretch only
            # while the battery is idle again.
            self._end_idle(reading if idle else None, plug_w)
            return
        self._idle.append(
            _IdleSample(
                reading.at,
                reading.energy_in_wh,
                reading.energy_out_wh,
                _usable_plug_w(plug_w),
            )
        )
        if self._standby is None or reading.at - self._standby[1] >= REFRESH_SECONDS:
            self._publish_standby(self._idle)

    def _end_idle(self, restart_at: _Reading | None, plug_w: float | None) -> None:
        """Publish the open stretch, then start a new one when this poll is still idle."""
        samples = self._idle
        self._idle = []
        self._publish_standby(samples)
        if restart_at is not None:
            self._idle.append(
                _IdleSample(
                    restart_at.at,
                    restart_at.energy_in_wh,
                    restart_at.energy_out_wh,
                    _usable_plug_w(plug_w),
                )
            )

    def _publish_standby(self, samples: list[_IdleSample]) -> None:
        """Keep the usual idle watts over a long enough stretch."""
        watts = _typical_watts(samples)
        if watts is None:
            return
        rounded = round(watts, 1)
        taken_at = samples[-1].at
        if 0.0 <= rounded <= MAX_STANDBY_W and self._standby != (rounded, taken_at):
            self._standby = (rounded, taken_at)
            self._dirty = True

    def _restart(self) -> None:
        """Forget the readings. The last published measurements stay."""
        self._idle = []
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
    # A step the estimator holds for confirmation may be a glitch: no level to anchor.
    soc_known = fresh and status.get(SOC_UNCONFIRMED_KEY) is not True
    energy_in_id, energy_out_id = plug.meter
    capacity = _finite_or_none(status.get(CAPACITY_DATA_KEY))
    bypass = [_finite_or_none(status.get(key)) for key in ("pv_power", "offgrid_power")]
    plug.observe(
        timestamp=timestamp,
        soc=_finite_or_none(status.get("battery_soc")) if soc_known else None,
        energy_in_wh=energy_wh(hass, energy_in_id),
        energy_out_wh=energy_wh(hass, energy_out_id),
        capacity_wh=capacity if capacity is not None else _finite_or_none(status.get("bat_cap")),
        power_w=_finite_or_none(status.get("battery_power")) if fresh else None,
        bypass_w=max((abs(watts) for watts in bypass if watts is not None), default=0.0),
        soc_corrected=status.get(SOC_CORRECTED_KEY) is True,
        plug_w=entity_power_w(hass, plug_power_entity(hass, energy_in_id)),
    )
    attributes = status.get(CAPACITY_ATTR_KEY)
    # The entity ids let the cards find the plug's live watts on the same device.
    status[CAPACITY_ATTR_KEY] = {
        **(attributes if isinstance(attributes, dict) else {}),
        "plug_energy_entities": list(plug.meter),
        **plug.attributes(),
    }
