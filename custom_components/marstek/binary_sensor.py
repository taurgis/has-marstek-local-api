"""Binary sensor platform for Marstek devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MarstekConfigEntry
from .coordinator import MarstekDataUpdateCoordinator
from .device_info import build_device_info, get_device_identifier


@dataclass(kw_only=True)
class MarstekBinarySensorEntityDescription(BinarySensorEntityDescription):  # type: ignore[misc]
    """Marstek binary sensor entity description."""

    value_fn: Callable[[dict[str, Any]], bool | None]
    exists_fn: Callable[[dict[str, Any]], bool] = lambda data: True


def _exists_key_with_value(key: str, data: dict[str, Any]) -> bool:
    """Check if key exists and has a non-None value."""
    return key in data and data.get(key) is not None


BINARY_SENSORS: tuple[MarstekBinarySensorEntityDescription, ...] = (
    MarstekBinarySensorEntityDescription(
        key="ct_connection",
        translation_key="ct_connection",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.get("ct_connected"),
        exists_fn=lambda data: _exists_key_with_value("ct_connected", data),
    ),
    MarstekBinarySensorEntityDescription(
        key="bat_charg_flag",
        translation_key="charge_permission",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: (
            bool(data.get("bat_charg_flag"))
            if data.get("bat_charg_flag") is not None
            else None
        ),
        exists_fn=lambda data: _exists_key_with_value("bat_charg_flag", data),
    ),
    MarstekBinarySensorEntityDescription(
        key="bat_dischrg_flag",
        translation_key="discharge_permission",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: (
            bool(data.get("bat_dischrg_flag"))
            if data.get("bat_dischrg_flag") is not None
            else None
        ),
        exists_fn=lambda data: _exists_key_with_value("bat_dischrg_flag", data),
    ),
)


class MarstekBinarySensor(
    CoordinatorEntity[MarstekDataUpdateCoordinator], BinarySensorEntity
):
    """Representation of a Marstek binary sensor."""

    _attr_has_entity_name = True
    entity_description: MarstekBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        description: MarstekBinarySensorEntityDescription,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_info = device_info
        self._config_entry = config_entry

        device_identifier = get_device_identifier(device_info)
        self._attr_unique_id = f"{device_identifier}_{description.key}"
        self._attr_device_info = build_device_info(device_info)

    @property
    def is_on(self) -> bool | None:
        """Return the state of the binary sensor."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Marstek binary sensors based on a config entry."""
    coordinator = config_entry.runtime_data.coordinator
    device_info = config_entry.runtime_data.device_info
    data = coordinator.data or {}

    async_add_entities(
        MarstekBinarySensor(coordinator, device_info, description, config_entry)
        for description in BINARY_SENSORS
        if description.exists_fn(data)
    )
