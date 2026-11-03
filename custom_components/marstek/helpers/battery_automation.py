"""Battery-low and charging decisions for Home Assistant battery automations.

Home Assistant's battery conditions and triggers match a binary sensor with
device class ``battery`` (on means low) or ``battery_charging`` (on means
charging), and a sensor with device class ``battery`` for the level. See
https://www.home-assistant.io/integrations/battery/ and
https://github.com/home-assistant/core/blob/dev/homeassistant/components/binary_sensor/const.py.

This module is pure: the entity layer supplies the state of charge, the
battery power, and the depth of discharge.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..const import (
    CONF_BATTERY_LOW_FOLLOW_CUTOFF,
    CONF_BATTERY_LOW_SOC,
    DEFAULT_BATTERY_LOW_FOLLOW_CUTOFF,
    DEFAULT_BATTERY_LOW_SOC,
)
from ..pymarstek.const import DOD_MAX_VALUE, DOD_MIN_VALUE

# Warn this many percentage points before the pack hits its discharge floor,
# so an automation can run while the battery can still deliver power.
BATTERY_LOW_MARGIN: int = 5
# Stay low until the state of charge rises this far above the trip level.
BATTERY_LOW_HYSTERESIS: int = 5
# Idle noise around 0 W must not flip Charging. Same width the stop action
# treats as "near zero" (device_action.STOP_POWER_THRESHOLD).
BATTERY_CHARGING_DEADBAND_W: float = 50.0
BATTERY_LOW_SOC_MIN: int = 5
BATTERY_LOW_SOC_MAX: int = 80
_AUTO_THRESHOLD_MIN: int = 1
_AUTO_THRESHOLD_MAX: int = 95


def finite_number(value: Any) -> float | None:
    """Return a finite float, or None for missing and non-numeric values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def parse_dod_percent(value: Any) -> int | None:
    """Return a depth-of-discharge percent inside the Open API range."""
    number = finite_number(value)
    if number is None or not number.is_integer():
        return None
    dod = int(number)
    if dod < DOD_MIN_VALUE or dod > DOD_MAX_VALUE:
        return None
    return dod


def parse_dod_state(state: str | None) -> int | None:
    """Return a depth of discharge parsed from a number-entity state."""
    if state is None:
        return None
    try:
        return parse_dod_percent(float(state))
    except ValueError:
        return None


def discharge_floor_soc(dod: int) -> int:
    """Return the state of charge where discharging stops.

    Depth of discharge is how far below 100% the pack may go. A DOD of 88
    stops discharge at 12% (Open API ``DOD.SET``, device default 88).
    """
    return 100 - dod


def auto_low_threshold(dod: int | None) -> int:
    """Return the low-battery trip level derived from the discharge cutoff."""
    if dod is None:
        return DEFAULT_BATTERY_LOW_SOC
    return _clamp(
        discharge_floor_soc(dod) + BATTERY_LOW_MARGIN, _AUTO_THRESHOLD_MIN, _AUTO_THRESHOLD_MAX
    )


def resolve_battery_low_threshold(
    options: Mapping[str, Any],
    *,
    supports_dod: bool,
    dod: int | None,
) -> int:
    """Return the state of charge at which Battery low turns on.

    With follow-cutoff on, a device that exposes depth of discharge uses its
    floor plus ``BATTERY_LOW_MARGIN``. Every other device uses 15%. With
    follow-cutoff off, the options number is the trip level.
    """
    follow = options.get(CONF_BATTERY_LOW_FOLLOW_CUTOFF, DEFAULT_BATTERY_LOW_FOLLOW_CUTOFF)
    if follow:
        return auto_low_threshold(dod if supports_dod else None)
    manual = finite_number(options.get(CONF_BATTERY_LOW_SOC, DEFAULT_BATTERY_LOW_SOC))
    if manual is None:
        return DEFAULT_BATTERY_LOW_SOC
    return _clamp(int(manual), BATTERY_LOW_SOC_MIN, BATTERY_LOW_SOC_MAX)


def battery_low_state(
    previous: bool | None,
    soc: float,
    threshold: float,
    hysteresis: float,
) -> bool:
    """Return whether the battery is low, holding the previous state inside the band.

    A fresh or cleared sensor trips at ``threshold``. Once low, it stays low
    until the state of charge reaches ``threshold + hysteresis``, so a reading
    that jitters around the trip level does not flap.
    """
    if previous is True:
        return soc < threshold + hysteresis
    return soc <= threshold


def battery_is_charging(
    power_w: Any, *, deadband: float = BATTERY_CHARGING_DEADBAND_W
) -> bool | None:
    """Return whether the battery is charging.

    The integration's sign is positive while discharging and negative while
    charging (``data_parser`` negates the Open API ``bat_power``). Power inside
    the deadband, including idle and light discharge, is not charging. Missing
    power is unknown.
    """
    power = finite_number(power_w)
    if power is None:
        return None
    return power <= -deadband


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))
