"""Number entity descriptions for Marstek SYS controls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import NumberEntityDescription, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory

from ..firmware_profile import FirmwareProfile
from ..pymarstek.const import CMD_DOD_SET, DOD_DEFAULT_VALUE, DOD_MAX_VALUE, DOD_MIN_VALUE


@dataclass(kw_only=True)
class MarstekNumberEntityDescription(NumberEntityDescription):  # type: ignore[misc]
    """Marstek number entity description."""

    method: str
    default_value: int
    supported_fn: Callable[[FirmwareProfile], bool]


NUMBER_ENTITIES: tuple[MarstekNumberEntityDescription, ...] = (
    MarstekNumberEntityDescription(
        key="depth_of_discharge",
        translation_key="depth_of_discharge",
        native_min_value=float(DOD_MIN_VALUE),
        native_max_value=float(DOD_MAX_VALUE),
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        method=CMD_DOD_SET,
        default_value=DOD_DEFAULT_VALUE,
        supported_fn=lambda profile: profile.supports_sys_dod,
    ),
)
