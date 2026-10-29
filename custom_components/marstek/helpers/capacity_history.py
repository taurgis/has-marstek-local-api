"""Weekly snapshots of usable capacity, efficiency, and lifetime energy.

The rolling window list is capped. These points stay in the same Store file,
one per ISO week, so a pack's watt-hours and round-trip efficiency can be
compared across months and years. The open window is not part of this history.

Each point also notes the nameplate and the efficiency treatment it was
published under. They are labels, not measurements: a week with only a
nameplate is not a point, and the card compares only weeks that share them.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

MAX_HISTORY_POINTS = 156
_ENERGY_STEP_WH = 50.0

ENERGY_FIELDS: tuple[str, ...] = (
    "total_grid_input_energy",
    "total_grid_output_energy",
    "total_pv_energy",
    "total_load_energy",
)

_WH_FIELDS: tuple[str, ...] = ("usable_wh", "discharge_median_wh", "charge_median_wh")
_TAG_FIELDS: tuple[str, ...] = ("rated_wh", "method")
_METHODS = frozenset({"discharge_median", "charge_median", "charge_discharge_geometric_mean"})
_CAPACITY_FIELDS: tuple[str, ...] = (
    *_WH_FIELDS,
    "implied_round_trip_efficiency",
    *_TAG_FIELDS,
)


def iso_week_period(timestamp: float) -> str | None:
    """Return ``YYYY-Www`` for a unix time, or None when it is not usable."""
    if timestamp != timestamp or timestamp in (float("inf"), float("-inf")) or timestamp < 0:
        return None
    moment = datetime.fromtimestamp(timestamp, UTC)
    year, week, _day = moment.isocalendar()
    return f"{year}-W{week:02d}"


def month_key(timestamp: float) -> str:
    """Return ``YYYY-MM`` for a unix time that already passed ``iso_week_period``."""
    moment = datetime.fromtimestamp(timestamp, UTC)
    return f"{moment.year:04d}-{moment.month:02d}"


def restore_history(raw: object) -> list[dict[str, Any]]:
    """Return stored weekly points. Unreadable items are dropped."""
    if not isinstance(raw, list):
        return []
    points = [point for item in raw if (point := _point_from_dict(item)) is not None]
    points.sort(key=lambda item: float(item["recorded_at"]))
    return points[-MAX_HISTORY_POINTS:]


def card_history(points: list[dict[str, Any]]) -> list[dict[str, float | int | str]]:
    """Return the weekly usable watt-hours the cards draw, keyed by week start.

    The stored points also carry lifetime counters and a ``recorded_at`` that
    move every few minutes. Leaving them out keeps the sensor attribute small
    and unchanged until the estimate itself changes or a new week begins. The
    nameplate and method ride along so the card can skip weeks of another
    pack or another efficiency treatment.
    """
    weeks: list[dict[str, float | int | str]] = []
    for point in points:
        usable = point.get("usable_wh")
        start = _week_start(point.get("period"))
        if isinstance(usable, int) and start is not None:
            week: dict[str, float | int | str] = {"week_start": start, "usable_wh": usable}
            week.update({key: point[key] for key in _TAG_FIELDS if key in point})
            weeks.append(week)
    return weeks


def _week_start(period: object) -> float | None:
    """Return the unix time of Monday 00:00 UTC for ``YYYY-Www``."""
    if not isinstance(period, str):
        return None
    year, _, week = period.partition("-W")
    try:
        monday = datetime.fromisocalendar(int(year), int(week), 1)
    except ValueError:
        return None
    return monday.replace(tzinfo=UTC).timestamp()


def empty_lifetime() -> dict[str, float | None]:
    """Return lifetime counters with nothing observed yet."""
    totals: dict[str, float | None] = dict.fromkeys(ENERGY_FIELDS)
    return totals


def lifetime_totals(status: dict[str, Any] | Any) -> dict[str, float | None]:
    """Copy non-negative lifetime watt-hours off a coordinator payload."""
    totals = empty_lifetime()
    if not isinstance(status, dict):
        return totals
    for key in ENERGY_FIELDS:
        value = _optional_number(status.get(key))
        if value is not None and value >= 0.0:
            totals[key] = value
    return totals


def fold_weekly_history(
    history: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
    *,
    timestamp: float,
    measures: dict[str, float | None],
    tags: Mapping[str, float | str | None] | None = None,
) -> bool:
    """Update this ISO week's point. Return whether the Store should catch up.

    ``measures`` maps point fields (usable and median watt-hours, implied
    round-trip efficiency, equivalent cycles, lifetime counters) to their
    current values; unknown values are None. The in-memory point always takes
    the latest numbers. Disk is asked to follow when the week is new, when a
    capacity field changes, or when a lifetime counter has moved by at least
    50 Wh since the last write. ``tags`` carries the nameplate and the
    efficiency treatment; they are stored with a point but never make one.
    """
    period = iso_week_period(timestamp)
    if period is None:
        return False
    point = _point(period, timestamp, month_key(timestamp), {**measures, **(tags or {})})
    if point is None:
        return False
    for index, existing in enumerate(history):
        if existing.get("period") != period:
            continue
        history[index] = point
        reference = next((item for item in baseline if item.get("period") == period), None)
        if reference is None:
            return True
        return _materially_different(reference, point)
    history.append(point)
    history.sort(key=lambda item: float(item["recorded_at"]))
    del history[:-MAX_HISTORY_POINTS]
    return True


def _point_from_dict(raw: object) -> dict[str, Any] | None:
    """Return a stored point, dropping fields that are not finite numbers."""
    if not isinstance(raw, dict):
        return None
    period = raw.get("period")
    recorded = _optional_number(raw.get("recorded_at"))
    if not isinstance(period, str) or not period or recorded is None or recorded < 0.0:
        return None
    month = raw["month"] if isinstance(raw.get("month"), str) else month_key(recorded)
    return _point(period, recorded, month, raw)


def _point(
    period: str, recorded_at: float, month: str, fields: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Return one weekly point, or None when no measured field is usable.

    New and restored points go through the same rounding: watt-hour fields to
    whole numbers, efficiency and cycles to three decimals, lifetime counters
    to one. Negative cycles and counters are dropped.
    """
    measured: dict[str, Any] = {}
    for key in _WH_FIELDS:
        value = _optional_number(fields.get(key))
        if value is not None:
            measured[key] = round(value)
    rte = _optional_number(fields.get("implied_round_trip_efficiency"))
    if rte is not None:
        measured["implied_round_trip_efficiency"] = round(rte, 3)
    cycles = _optional_number(fields.get("equivalent_cycles"))
    if cycles is not None and cycles >= 0.0:
        measured["equivalent_cycles"] = round(cycles, 3)
    for key in ENERGY_FIELDS:
        energy = _optional_number(fields.get(key))
        if energy is not None and energy >= 0.0:
            measured[key] = round(energy, 1)
    if not measured:
        return None
    return {
        "period": period,
        "recorded_at": recorded_at,
        "month": month,
        **measured,
        **_tags(fields),
    }


def _tags(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Return the nameplate and method labels that are usable."""
    tags: dict[str, Any] = {}
    rated = _optional_number(fields.get("rated_wh"))
    if rated is not None and rated > 0.0:
        tags["rated_wh"] = round(rated)
    method = fields.get("method")
    if method in _METHODS:
        tags["method"] = method
    return tags


def _optional_number(value: object) -> float | None:
    """Return a finite real number, rejecting booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _materially_different(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    """Return whether this week moved enough to be worth another Store write."""
    if any(previous.get(key) != current.get(key) for key in _CAPACITY_FIELDS):
        return True
    if _moved(previous.get("equivalent_cycles"), current.get("equivalent_cycles"), 0.01):
        return True
    return any(
        _moved(previous.get(key), current.get(key), _ENERGY_STEP_WH) for key in ENERGY_FIELDS
    )


def _moved(before: float | None, after: float | None, step: float) -> bool:
    """Return whether a value appeared, vanished, or changed by at least ``step``."""
    if before is None or after is None:
        return before != after
    return abs(float(before) - float(after)) >= step
