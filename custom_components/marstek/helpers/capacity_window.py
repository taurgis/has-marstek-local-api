"""One accepted usable-capacity run, as stored and as shown on the card.

A window is a stretch of one direction, one operating mode, and no long gap
that moved state of charge at least ``MIN_SOC_DELTA`` points. Its capacity is
the DC watt-hours it moved per 100 points of reported state of charge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .capacity_fields import (
    finite,
    optional_duration,
    optional_power,
    optional_share,
    optional_soc,
)

MIN_SOC_DELTA = 20.0
ABS_MIN_CAPACITY_WH = 300.0
ABS_MAX_CAPACITY_WH = 30_000.0


@dataclass(frozen=True, slots=True)
class CapacityWindow:
    """One accepted capacity observation.

    Windows saved before the detail fields existed load without them. A
    missing field stays unknown; it is not invented on the way back out.
    """

    capacity_wh: float
    direction: str
    soc_delta: float
    closed_at: float
    start_soc: float | None = None
    end_soc: float | None = None
    average_power_w: float | None = None
    duration_s: float | None = None
    mode: str | None = None
    pv_share: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form stored across restarts."""
        payload: dict[str, Any] = {
            "capacity_wh": self.capacity_wh,
            "direction": self.direction,
            "soc_delta": self.soc_delta,
            "closed_at": self.closed_at,
        }
        if self.start_soc is not None:
            payload["start_soc"] = self.start_soc
        if self.end_soc is not None:
            payload["end_soc"] = self.end_soc
        if self.average_power_w is not None:
            payload["average_power_w"] = self.average_power_w
        if self.duration_s is not None:
            payload["duration_s"] = self.duration_s
        if self.mode is not None:
            payload["mode"] = self.mode
        if self.pv_share is not None:
            payload["pv_share"] = self.pv_share
        return payload


def window_from_dict(raw: object) -> CapacityWindow | None:
    """Parse one persisted window, or return None when it is not usable.

    Detail fields added later are optional. A store written by an older
    version still contributes its capacity to the median.
    """
    if not isinstance(raw, dict):
        return None
    capacity = finite(raw.get("capacity_wh"))
    soc_delta = finite(raw.get("soc_delta"))
    closed_at = finite(raw.get("closed_at"))
    direction = raw.get("direction")
    if capacity is None or soc_delta is None or closed_at is None:
        return None
    if direction not in ("charge", "discharge"):
        return None
    if not ABS_MIN_CAPACITY_WH <= capacity <= ABS_MAX_CAPACITY_WH:
        return None
    if abs(soc_delta) < MIN_SOC_DELTA:
        return None
    return CapacityWindow(
        capacity_wh=capacity,
        direction=direction,
        soc_delta=soc_delta,
        closed_at=closed_at,
        start_soc=optional_soc(raw, "start_soc"),
        end_soc=optional_soc(raw, "end_soc"),
        average_power_w=optional_power(raw),
        duration_s=optional_duration(raw),
        mode=mode_text(raw.get("mode")) if "mode" in raw else None,
        pv_share=optional_share(raw),
    )


def mode_text(mode: str | None) -> str | None:
    """Normalize a mode name. Empty and non-strings carry no mode information."""
    if not isinstance(mode, str):
        return None
    text = mode.strip().casefold()
    return text or None
