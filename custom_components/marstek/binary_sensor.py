"""Binary sensor platform for Marstek devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MarstekConfigEntry
from .const import BAT_STATUS_KEYS
from .coordinator import MarstekDataUpdateCoordinator
from .device_info import build_device_info, get_device_identifier
from .helpers.binary_sensor_descriptions import (
    BINARY_SENSORS,
    MarstekBinarySensorEntityDescription,
)

# Read-only platform: every value comes from the shared coordinator, so
# Home Assistant never has to serialize updates for these entities.
# https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/parallel-updates
PARALLEL_UPDATES = 0


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
    data_for_exists = dict(data)

    for description in BINARY_SENSORS:
        data_for_exists.setdefault(description.key, None)

    entities: list[MarstekBinarySensor] = []
    for description in BINARY_SENSORS:
        if (
            description.key in BAT_STATUS_KEYS
            and coordinator.profile.openapi_reset_prone
        ):
            continue
        if description.exists_fn(data_for_exists):
            entities.append(
                MarstekBinarySensor(
                    coordinator, device_info, description, config_entry
                )
            )
    async_add_entities(entities)
