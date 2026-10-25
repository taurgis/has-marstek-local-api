"""Smart plug between the battery and the mains.

The plug sees what the battery's AC cord carries: the inverter port plus the
unit's own standby draw, which the Open API never reports. Positive watts are
import (the battery takes energy from the mains), as a HomeWizard Energy
Socket or a Shelly plug reports them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PlugReading:
    """One snapshot of the plug's display."""

    power_w: int
    import_wh: float
    export_wh: float


class PlugMeter:
    """Lifetime import and export counters of the battery's plug."""

    def __init__(self) -> None:
        self.import_wh = 0.0
        self.export_wh = 0.0

    def accumulate(self, net_import_w: float, hours: float) -> None:
        """Add one tick of net import (negative is export)."""
        if net_import_w >= 0:
            self.import_wh += net_import_w * hours
        else:
            self.export_wh += -net_import_w * hours

    def reading(self, power_w: int) -> PlugReading:
        """Return the counters with the plug's current watts."""
        return PlugReading(power_w, self.import_wh, self.export_wh)

    def to_state(self) -> dict[str, float]:
        """Return the counters for the mock's state file."""
        return {"plug_import_energy": self.import_wh, "plug_export_energy": self.export_wh}

    def apply_state(self, state: dict[str, Any]) -> None:
        """Restore counters saved by :meth:`to_state`."""
        self.import_wh = float(state.get("plug_import_energy", self.import_wh))
        self.export_wh = float(state.get("plug_export_energy", self.export_wh))
