"""Binary sensor descriptions for Marstek devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory


@dataclass(kw_only=True)
class MarstekBinarySensorEntityDescription(BinarySensorEntityDescription):  # type: ignore[misc]
    """Marstek binary sensor entity description."""

    value_fn: Callable[[dict[str, Any]], bool | None]
    exists_fn: Callable[[dict[str, Any]], bool] = lambda data: True


def _exists_key(key: str, data: dict[str, Any]) -> bool:
    """Check if key exists in data."""
    return key in data


BINARY_SENSORS: tuple[MarstekBinarySensorEntityDescription, ...] = (
    MarstekBinarySensorEntityDescription(
        key="ct_connection",
        translation_key="ct_connection",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.get("ct_connected"),
        exists_fn=lambda data: _exists_key("ct_connected", data),
    ),
    MarstekBinarySensorEntityDescription(
        key="bat_charg_flag",
        translation_key="charge_permission",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: (
            bool(data.get("bat_charg_flag")) if data.get("bat_charg_flag") is not None else None
        ),
        exists_fn=lambda data: _exists_key("bat_charg_flag", data),
    ),
    MarstekBinarySensorEntityDescription(
        key="bat_dischrg_flag",
        translation_key="discharge_permission",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: (
            bool(data.get("bat_dischrg_flag")) if data.get("bat_dischrg_flag") is not None else None
        ),
        exists_fn=lambda data: _exists_key("bat_dischrg_flag", data),
    ),
)
