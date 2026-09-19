"""Select entity descriptions for Marstek devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntityDescription

from ..const import ha_operating_mode, selectable_operating_modes
from ..firmware_profile import FirmwareProfile


@dataclass(kw_only=True)
class MarstekSelectEntityDescription(SelectEntityDescription):  # type: ignore[misc]
    """Marstek select entity description."""

    options_fn: Callable[[FirmwareProfile], list[str]]
    value_fn: Callable[[dict[str, Any]], str | None]


SELECT_ENTITIES: tuple[MarstekSelectEntityDescription, ...] = (
    MarstekSelectEntityDescription(
        key="operating_mode",
        translation_key="operating_mode",
        options_fn=selectable_operating_modes,
        value_fn=lambda data: ha_operating_mode(data.get("device_mode")),
    ),
)
