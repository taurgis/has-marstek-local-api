"""Parse numbers the capacity estimator stores and reads back.

Stored windows and corrections come from a JSON Store file that older
versions wrote, or that was edited by hand. A field that is absent or junk
stays unknown rather than failing the whole restore.
"""

from __future__ import annotations

import math
from typing import Any

# Reported battery power above this is a glitch, not a reading.
MAX_ABS_POWER_W = 30_000.0


def finite(value: object) -> float | None:
    """Return a finite real number, rejecting booleans and non-numeric values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def optional_soc(raw: dict[Any, Any], key: str) -> float | None:
    """Return a stored state of charge, or None when the field is absent or junk."""
    if key not in raw:
        return None
    value = finite(raw.get(key))
    if value is None or not 0.0 <= value <= 100.0:
        return None
    return value


def optional_power(raw: dict[Any, Any]) -> float | None:
    """Return a stored average power, or None when it is absent or absurd."""
    if "average_power_w" not in raw:
        return None
    value = finite(raw.get("average_power_w"))
    if value is None or abs(value) > MAX_ABS_POWER_W:
        return None
    return value


def optional_duration(raw: dict[Any, Any]) -> float | None:
    """Return a stored duration in seconds, or None when it is absent or not positive."""
    if "duration_s" not in raw:
        return None
    value = finite(raw.get("duration_s"))
    if value is None or value <= 0.0:
        return None
    return value


def optional_share(raw: dict[Any, Any]) -> float | None:
    """Return a stored PV share in 0-1, or None when it is absent or outside that."""
    if "pv_share" not in raw:
        return None
    value = finite(raw.get("pv_share"))
    if value is None or not 0.0 <= value <= 1.0:
        return None
    return value
