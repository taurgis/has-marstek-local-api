"""Confidence label for the usable-capacity estimate, and what holds it down.

The label starts from how many runs entered the published value. Each check
below can only lower it, and a check that does is named in ``limits`` so the
card can say why. None of them is a calibrated error bar.

The thresholds come from a simulation campaign of 782 published states across
device families, seasons, inverter curves, poll faults, and BMS and meter
errors. Each one moves a set of states whose error the run count alone
labelled too confidently:

- Scatter. A BMS whose state of charge is not linear in stored charge makes
  each 20-point run measure a different slice of the curve. Realistic packs
  stayed under a 7.4% median deviation within one direction; a curved
  percentage reached 10-18% and a published error near 10%.
- Power asymmetry. The geometric mean cancels the fixed one-way factor only
  when both directions run at the same real efficiency. Inverters lose more
  at low power, so a pack discharged at 300 W and charged at 2500 W keeps a
  residual: 2 to 3% on a typical inverter and about 5% on a poor one, whose
  implied round trip sits near 0.76.
- One direction. A single median still carries the assumed 0.93 one-way
  factor. Independent tests put Marstek round trips at 77-85%, about 0.88 to
  0.92 one way, so it reads a few percent off before the other direction
  arrives.
- Solar charging. A derived reading adds PV watts on the DC side with no
  conversion loss, so a PV meter that reads high passes straight into charge
  runs: 5% high gave about +4%.
- Counting drift. ``cap_confidence`` lowers the label when the BMS corrected
  its percentage by more than the medians resolve.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Protocol

from .soc_corrections import cap_confidence

COLLECTING = "collecting"
_ORDER = ("low", "medium", "high")
MIN_RUNS = 3
_MEDIUM_RUNS = 6
_HIGH_RUNS = 12

SCATTER_MEDIUM_PCT = 5.0
SCATTER_LOW_PCT = 10.0
POWER_RATIO_MEDIUM = 4.0
POWER_RATIO_LOW = 6.0
# Below this implied round trip a 6:1 power ratio left about 5% in simulation.
POWER_RATIO_LOW_RTE = 0.80
# A charge run whose battery watts PV covers at least this share is a solar run.
SOLAR_RUN_SHARE = 0.5

GEOMETRIC_MEAN = "charge_discharge_geometric_mean"

SINGLE_DIRECTION = "single_direction"
RUN_SCATTER = "run_scatter"
POWER_ASYMMETRY = "power_asymmetry"
SOLAR_CHARGING = "solar_charging"
SOC_DRIFT = "soc_drift"


class Run(Protocol):
    """The parts of an accepted window the label looks at."""

    @property
    def capacity_wh(self) -> float:
        """Watt-hours per 100 points this run measured."""

    @property
    def direction(self) -> str:
        """``charge`` or ``discharge``."""

    @property
    def average_power_w(self) -> float | None:
        """Signed mean battery watts, or None for runs saved without it."""

    @property
    def pv_share(self) -> float | None:
        """Share of the battery watts PV covered, or None when unknown."""


def by_direction(runs: Sequence[Run]) -> dict[str, list[Run]]:
    """Split runs by direction, keeping their order."""
    split: dict[str, list[Run]] = {"discharge": [], "charge": []}
    for run in runs:
        split.setdefault(run.direction, []).append(run)
    return split


def scatter_pct(capacities: Sequence[float]) -> float | None:
    """Return the median absolute deviation as a percentage of the median.

    Half the runs lie within this of their median. The range would grow with
    every run kept and let one outlier set the label.
    """
    if len(capacities) < MIN_RUNS:
        return None
    middle = statistics.median(capacities)
    deviation = statistics.median(abs(value - middle) for value in capacities)
    return deviation / middle * 100.0


def run_scatter_pct(runs: Sequence[Run]) -> float | None:
    """Return the larger scatter of the two directions among ``runs``.

    Each direction is measured against its own median. In the geometric mean
    the gap between the two medians is a systematic offset, not scatter.
    """
    values = [
        scatter
        for group in by_direction(runs).values()
        if (scatter := scatter_pct([run.capacity_wh for run in group])) is not None
    ]
    return max(values, default=None)


def spread_wh(runs: Sequence[Run], published: float) -> float | None:
    """Return the larger range within one direction, scaled to ``published``.

    A single direction's range is its runs' own range. In the geometric mean
    each direction's range is taken relative to its median, so the charge and
    discharge offset does not count as disagreement.
    """
    ranges = []
    for group in by_direction(runs).values():
        capacities = [run.capacity_wh for run in group]
        if not capacities:
            continue
        ranges.append((max(capacities) - min(capacities)) / statistics.median(capacities))
    if not ranges:
        return None
    return max(ranges) * published


def power_ratio(runs: Sequence[Run]) -> float | None:
    """Return how many times faster one direction ran than the other.

    Medians of each direction's average watts. None without three runs that
    know their watts in each direction.
    """
    medians = []
    for group in by_direction(runs).values():
        watts = [abs(run.average_power_w) for run in group if run.average_power_w is not None]
        if len(watts) < MIN_RUNS:
            return None
        medians.append(statistics.median(watts))
    low, high = min(medians), max(medians)
    if low <= 0.0:
        return None
    return high / low


def _solar_charged(runs: Sequence[Run]) -> bool:
    """Return whether most charge runs that entered the value ran on PV."""
    shares = [run.pv_share for run in by_direction(runs)["charge"] if run.pv_share is not None]
    if len(shares) < MIN_RUNS:
        return False
    return statistics.median(shares) >= SOLAR_RUN_SHARE


def _lower(label: str, cap: str) -> str:
    """Return the lower of two labels."""
    return _ORDER[min(_ORDER.index(label), _ORDER.index(cap))]


def assess(
    runs: Sequence[Run],
    method: str,
    round_trip: float | None,
    bound_pct: float | None,
) -> tuple[str, list[str]]:
    """Return the label and the checks that lowered it.

    ``runs`` are the windows that entered the published value, ``method`` its
    efficiency treatment. Nothing published is ``collecting``, whatever the
    run count. ``round_trip`` is the implied round trip once both directions
    have their median.
    """
    if len(runs) < MIN_RUNS:
        return COLLECTING, []
    count = len(runs)
    label = "low" if count < _MEDIUM_RUNS else "medium" if count < _HIGH_RUNS else "high"
    caps: list[tuple[str, str]] = []
    if method != GEOMETRIC_MEAN:
        caps.append((SINGLE_DIRECTION, "medium"))
    scatter = run_scatter_pct(runs)
    if scatter is not None and scatter >= SCATTER_LOW_PCT:
        caps.append((RUN_SCATTER, "low"))
    elif scatter is not None and scatter >= SCATTER_MEDIUM_PCT:
        caps.append((RUN_SCATTER, "medium"))
    ratio = power_ratio(runs) if method == GEOMETRIC_MEAN else None
    poor_curve = round_trip is not None and round_trip < POWER_RATIO_LOW_RTE
    if ratio is not None and ratio >= POWER_RATIO_LOW and poor_curve:
        caps.append((POWER_ASYMMETRY, "low"))
    elif ratio is not None and ratio >= POWER_RATIO_MEDIUM:
        caps.append((POWER_ASYMMETRY, "medium"))
    if _solar_charged(runs):
        caps.append((SOLAR_CHARGING, "medium"))
    drift = cap_confidence("high", bound_pct)
    if drift != "high":
        caps.append((SOC_DRIFT, drift))
    limits = [name for name, cap in caps if _lower(label, cap) != label]
    for _name, cap in caps:
        label = _lower(label, cap)
    return label, limits
