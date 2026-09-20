"""Reject impossible lifetime energy totals before they reach Home Assistant.

Home Assistant ``total_increasing`` statistics add every upward jump to
``sum`` and treat a decrease as a new meter cycle. The recorder does not
drop spikes; the integration must not publish them.

https://developers.home-assistant.io/docs/core/entity/sensor/
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Any, Final

_LOGGER = logging.getLogger(__name__)

# Coordinator keys for lifetime energy totals (native Wh).
ENERGY_TOTAL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "total_grid_input_energy",
        "total_grid_output_energy",
        "total_pv_energy",
        "total_load_energy",
        "em_input_energy",
        "em_output_energy",
    }
)

# 1 GWh. Continuous 5 kW for about 22 years. Real Venus lifetime totals are
# far below this. ``2**32 - 1`` Wh (uint32 sentinel) and issue #70's
# ~4.29e15 Wh are above it. Venus A 149 solar x10 corrections stay well under it.
MAX_PLAUSIBLE_ENERGY_WH: Final[float] = 1_000_000_000.0

# 5 MWh in one update is not a home-storage increment (5 kW for 41 days).
# The firmware-149 solar encoding correction is about 0.23 MWh, and a scale
# change reloads the entry anyway (FirmwareProfile.setup_reload_signature), so
# the rescaled total arrives with no previous reading to be measured against.
MAX_PLAUSIBLE_ENERGY_JUMP_WH: Final[float] = 5_000_000.0


def energy_total_is_plausible(value: Any) -> bool:
    """Return whether a lifetime energy total can be published as Wh."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        return False
    return numeric <= MAX_PLAUSIBLE_ENERGY_WH


def without_implausible_energy_totals(
    status: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a sanitized copy of a *previous* status snapshot.

    The previous snapshot belongs to the coordinator, so this copies rather
    than mutating. Clearing its garbage first stops a bad reading from
    becoming the baseline the new reading is judged against. The companion
    ``apply_energy_total_guard`` does mutate, because the status it fixes up
    is the one being built.
    """
    if status is None:
        return None
    cleaned = dict(status)
    for key in ENERGY_TOTAL_KEYS:
        if key in cleaned and not energy_total_is_plausible(cleaned[key]):
            cleaned[key] = None
    return cleaned


def apply_energy_total_guard(
    status: dict[str, Any],
    previous_status: Mapping[str, Any] | None,
) -> None:
    """Drop spikes and garbage on every lifetime energy total in ``status``."""
    previous = previous_status or {}
    for key in ENERGY_TOTAL_KEYS:
        current = status.get(key)
        if current is None:
            continue
        previous_total = previous.get(key)
        previous_ok = energy_total_is_plausible(previous_total)
        if energy_total_is_plausible(current):
            if (
                previous_ok
                and isinstance(previous_total, (int, float))
                and isinstance(current, (int, float))
                and current > previous_total
                and (current - previous_total) > MAX_PLAUSIBLE_ENERGY_JUMP_WH
            ):
                _LOGGER.warning(
                    "Ignoring %s spike from %s Wh to %s Wh",
                    key,
                    previous_total,
                    current,
                )
                status[key] = previous_total
            continue
        if previous_ok:
            _LOGGER.warning(
                "Ignoring implausible %s reading %s; keeping %s Wh",
                key,
                current,
                previous_total,
            )
            status[key] = previous_total
        else:
            _LOGGER.warning("Ignoring implausible %s reading %s", key, current)
            status[key] = None
