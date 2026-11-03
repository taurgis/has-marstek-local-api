"""Turn polled battery watts into DC energy for the capacity estimator.

Reported battery power is the AC-side quantity the integration already
publishes (device ``bat_power`` when present, otherwise
``pv_power - ongrid_power - offgrid_power``). Nameplate ``bat_cap`` is DC
watt-hours. The one-way factors below convert the AC integral into an
estimate of stored energy so a charge window and a discharge window agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from .capacity_fields import MAX_ABS_POWER_W, finite

# They match the mock pack model (tools/mock_device/const.py). They are not a
# measurement from Marstek hardware; a different real efficiency shifts the
# absolute watt-hours and shows up as spread between charge and discharge.
CHARGE_EFFICIENCY = 0.93
DISCHARGE_EFFICIENCY = 0.93


@dataclass(frozen=True, slots=True)
class Sample:
    """One poll used by the open window."""

    timestamp: float
    soc: float
    power_w: float
    mode: str | None
    pv_w: float = 0.0
    power_derived: bool = False


def segment_power(earlier: Sample, later: Sample) -> float:
    """Return the trapezoidal average reported watts between two polls."""
    return (earlier.power_w + later.power_w) / 2.0


def segment_dc_power(earlier: Sample, later: Sample) -> float:
    """Return DC watts between two polls, positive when the pack discharges.

    Discharge puts more DC energy into the AC port than the port delivers, so
    that port's watts are divided by the discharge efficiency. Charge stores
    less than the port draws, so those watts are multiplied by the charge
    efficiency.

    A derived reading is ``battery_power = ongrid + offgrid - pv`` in the
    Home Assistant sign. PV is charging power on the battery side of the
    inverter (Open API ``pv_power``), so only ``battery_power + pv_power``
    is the AC port. A device ``bat_power`` is left on the whole-reading path.
    """
    battery = segment_power(earlier, later)
    if not (earlier.power_derived and later.power_derived):
        if battery >= 0.0:
            return battery / DISCHARGE_EFFICIENCY
        return battery * CHARGE_EFFICIENCY
    pv = max((earlier.pv_w + later.pv_w) / 2.0, 0.0)
    ac = battery + pv
    if ac >= 0.0:
        return ac / DISCHARGE_EFFICIENCY - pv
    return ac * CHARGE_EFFICIENCY - pv


def dc_energy_wh(samples: list[Sample]) -> float:
    """Integrate DC watts over the window. Positive energy leaves the pack."""
    energy = 0.0
    for earlier, later in pairwise(samples):
        hours = (later.timestamp - earlier.timestamp) / 3600.0
        energy += segment_dc_power(earlier, later) * hours
    return energy


def pv_share(samples: list[Sample]) -> float:
    """Return how much of the absolute battery-power integral PV wattage covers.

    0 means no PV, or a device ``bat_power`` that this correction does not
    split. 1 means PV watts were at least the battery watts across the window.
    """
    pv_wh = 0.0
    battery_wh = 0.0
    for earlier, later in pairwise(samples):
        hours = (later.timestamp - earlier.timestamp) / 3600.0
        battery_wh += abs(segment_power(earlier, later)) * hours
        if earlier.power_derived and later.power_derived:
            pv = max((earlier.pv_w + later.pv_w) / 2.0, 0.0)
            pv_wh += pv * hours
    if battery_wh <= 0.0:
        return 0.0
    return min(1.0, pv_wh / battery_wh)


def average_power_w(samples: list[Sample]) -> float:
    """Return the trapezoidal mean of reported battery power, signed."""
    duration = samples[-1].timestamp - samples[0].timestamp
    if duration <= 0.0:
        return 0.0
    apparent_wh = 0.0
    for earlier, later in pairwise(samples):
        hours = (later.timestamp - earlier.timestamp) / 3600.0
        apparent_wh += segment_power(earlier, later) * hours
    return apparent_wh / (duration / 3600.0)


def sample_pv(pv_w: float | None, power_derived: bool) -> float | None:
    """Return PV watts to store, or None when a derived poll's PV is unusable.

    Missing PV is zero. A device ``bat_power`` does not use PV, so a bad PV
    reading must not reject that poll. Negative PV is not generation.
    """
    if not power_derived:
        return 0.0
    if pv_w is None:
        return 0.0
    number = finite(pv_w)
    if number is None or number > MAX_ABS_POWER_W:
        return None
    return max(0.0, number)
