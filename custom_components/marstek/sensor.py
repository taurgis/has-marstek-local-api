"""Sensor platform for Marstek devices."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MarstekConfigEntry
from .const import device_supports_pv
from .coordinator import MarstekDataUpdateCoordinator
from .device_info import build_device_info, get_device_identifier
from .helpers.sensor_descriptions import (
    API_STABILITY_SENSORS,
    PV_SENSORS,
    SENSORS,
    MarstekSensorEntityDescription,
)

_LOGGER = logging.getLogger(__name__)


class MarstekSensor(CoordinatorEntity[MarstekDataUpdateCoordinator], SensorEntity):
    """Representation of a Marstek sensor."""

    _attr_has_entity_name = True
    entity_description: MarstekSensorEntityDescription

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        description: MarstekSensorEntityDescription,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_info = device_info
        self._config_entry = config_entry
        device_identifier = get_device_identifier(device_info)
        self._attr_unique_id = f"{device_identifier}_{description.key}"
        self._attr_device_info = build_device_info(device_info)

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(
            self.coordinator, self._device_info, self._config_entry
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes for the sensor."""
        if not self.entity_description.attributes_fn:
            return None
        return self.entity_description.attributes_fn(
            self.coordinator, self._device_info, self._config_entry
        )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Marstek sensors based on a config entry."""
    coordinator = config_entry.runtime_data.coordinator
    device_info = config_entry.runtime_data.device_info
    device_ip = device_info["ip"]
    _LOGGER.info("Setting up Marstek sensors: %s", device_ip)

    data = coordinator.data or {}
    data_for_exists = dict(data)
    if device_supports_pv(device_info.get("device_type")):
        data_for_exists.setdefault("pv_power", None)
        data_for_exists.setdefault("total_pv_energy", None)
        for pv_channel in range(1, 5):
            for metric in ("power", "voltage", "current", "state"):
                data_for_exists.setdefault(f"pv{pv_channel}_{metric}", None)
    sensors: list[MarstekSensor] = []
    for description in (*SENSORS, *PV_SENSORS, *API_STABILITY_SENSORS):
        if description.exists_fn(data_for_exists):
            sensors.append(
                MarstekSensor(
                    coordinator,
                    device_info,
                    description,
                    config_entry,
                )
            )

    _LOGGER.info("Device %s sensors set up, total %d", device_ip, len(sensors))
    async_add_entities(sensors)
