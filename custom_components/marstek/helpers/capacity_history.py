"""Weekly snapshots of usable capacity, efficiency, and lifetime energy.

The rolling window list is capped. These points stay in the same Store file,
one per ISO week, so a pack's watt-hours and round-trip efficiency can be
compared across months and years. The open window is not part of this history.
"""

from __future__ import annotations

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

_CAPACITY_FIELDS: tuple[str, ...] = (
    "usable_wh",
    "discharge_median_wh",
    "charge_median_wh",
    "implied_round_trip_efficiency",
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


def card_history(points: list[dict[str, Any]]) -> list[dict[str, float | int]]:
    """Return the weekly usable watt-hours the cards draw, keyed by week start.

    The stored points also carry lifetime counters and a ``recorded_at`` that
    move every few minutes. Leaving them out keeps the sensor attribute small
    and unchanged until the estimate itself changes or a new week begins.
    """
    weeks: list[dict[str, float | int]] = []
    for point in points:
        usable = point.get("usable_wh")
        start = _week_start(point.get("period"))
        if isinstance(usable, int) and start is not None:
            weeks.append({"week_start": start, "usable_wh": usable})
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
    usable_wh: int | None,
    discharge_median_wh: int | None,
    charge_median_wh: int | None,
    implied_round_trip_efficiency: float | None,
    equivalent_cycles: float | None,
    totals: dict[str, float | None],
) -> bool:
    """Update this ISO week's point. Return whether the Store should catch up.

    The in-memory point always takes the latest numbers. Disk is asked to
    follow when the week is new, when a capacity field changes, or when a
    lifetime counter has moved by at least 50 Wh since the last write.
    """
    period = iso_week_period(timestamp)
    if period is None:
        return False
    point = _fresh_point(
        period=period,
        timestamp=timestamp,
        usable_wh=usable_wh,
        discharge_median_wh=discharge_median_wh,
        charge_median_wh=charge_median_wh,
        implied_round_trip_efficiency=implied_round_trip_efficiency,
        equivalent_cycles=equivalent_cycles,
        totals=totals,
    )
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


def _fresh_point(
    *,
    period: str,
    timestamp: float,
    usable_wh: int | None,
    discharge_median_wh: int | None,
    charge_median_wh: int | None,
    implied_round_trip_efficiency: float | None,
    equivalent_cycles: float | None,
    totals: dict[str, float | None],
) -> dict[str, Any] | None:
    """Return one weekly point, or None when every field is still unknown."""
    point: dict[str, Any] = {
        "period": period,
        "recorded_at": timestamp,
        "month": month_key(timestamp),
    }
    meaningful = False
    for key, value in (
        ("usable_wh", usable_wh),
        ("discharge_median_wh", discharge_median_wh),
        ("charge_median_wh", charge_median_wh),
    ):
        if isinstance(value, int):
            point[key] = value
            meaningful = True
    if implied_round_trip_efficiency is not None:
        point["implied_round_trip_efficiency"] = round(float(implied_round_trip_efficiency), 3)
        meaningful = True
    if equivalent_cycles is not None and equivalent_cycles >= 0.0:
        point["equivalent_cycles"] = round(float(equivalent_cycles), 3)
        meaningful = True
    for key in ENERGY_FIELDS:
        energy = totals.get(key)
        if energy is not None and energy >= 0.0:
            point[key] = round(energy, 1)
            meaningful = True
    if not meaningful:
        return None
    return point


def _point_from_dict(raw: object) -> dict[str, Any] | None:
    """Return a stored point, dropping fields that are not finite numbers."""
    if not isinstance(raw, dict):
        return None
    period = raw.get("period")
    recorded = _optional_number(raw.get("recorded_at"))
    if not isinstance(period, str) or not period or recorded is None or recorded < 0.0:
        return None
    point: dict[str, Any] = {
        "period": period,
        "recorded_at": recorded,
        "month": raw["month"] if isinstance(raw.get("month"), str) else month_key(recorded),
    }
    for key in ("usable_wh", "discharge_median_wh", "charge_median_wh"):
        value = _optional_number(raw.get(key))
        if value is not None:
            point[key] = round(value)
    rte = _optional_number(raw.get("implied_round_trip_efficiency"))
    if rte is not None:
        point["implied_round_trip_efficiency"] = round(rte, 3)
    cycles = _optional_number(raw.get("equivalent_cycles"))
    if cycles is not None and cycles >= 0.0:
        point["equivalent_cycles"] = round(cycles, 3)
    for key in ENERGY_FIELDS:
        value = _optional_number(raw.get(key))
        if value is not None and value >= 0.0:
            point[key] = round(value, 1)
    if not any(key in point for key in (*_CAPACITY_FIELDS, *ENERGY_FIELDS, "equivalent_cycles")):
        return None
    return point


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
    for key in _CAPACITY_FIELDS:
        if previous.get(key) != current.get(key):
            return True
    previous_cycles = previous.get("equivalent_cycles")
    current_cycles = current.get("equivalent_cycles")
    if previous_cycles is None or current_cycles is None:
        if previous_cycles != current_cycles:
            return True
    elif abs(float(previous_cycles) - float(current_cycles)) >= 0.01:
        return True
    for key in ENERGY_FIELDS:
        before = previous.get(key)
        after = current.get(key)
        if before is None or after is None:
            if before != after:
                return True
            continue
        if abs(float(before) - float(after)) >= _ENERGY_STEP_WH:
            return True
    return False
