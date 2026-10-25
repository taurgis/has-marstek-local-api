"""Power and lifetime energy of the mock plug, in watts and watt-hours."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, PlugCoordinator

# (key, name, device class, unit, state class, path into Switch.GetStatus)
_SENSORS = (
    (
        "power",
        "Power",
        SensorDeviceClass.POWER,
        UnitOfPower.WATT,
        SensorStateClass.MEASUREMENT,
        ("apower",),
    ),
    (
        "energy",
        "Energy",
        SensorDeviceClass.ENERGY,
        UnitOfEnergy.WATT_HOUR,
        SensorStateClass.TOTAL_INCREASING,
        ("aenergy", "total"),
    ),
    (
        "returned_energy",
        "Returned energy",
        SensorDeviceClass.ENERGY,
        UnitOfEnergy.WATT_HOUR,
        SensorStateClass.TOTAL_INCREASING,
        ("ret_aenergy", "total"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the three plug sensors."""
    coordinator: PlugCoordinator = entry.runtime_data
    async_add_entities(PlugSensor(coordinator, entry, *spec) for spec in _SENSORS)


class PlugSensor(CoordinatorEntity[PlugCoordinator], SensorEntity):
    """One number from the plug's switch status."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PlugCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        device_class: SensorDeviceClass,
        unit: str,
        state_class: SensorStateClass,
        path: tuple[str, ...],
    ) -> None:
        super().__init__(coordinator)
        self._path = path
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name="Mock energy plug",
            manufacturer="Marstek dev setup",
            model="Shelly-style plug",
        )

    @property
    def native_value(self) -> float | None:
        value: Any = self.coordinator.data
        for key in self._path:
            value = value.get(key) if isinstance(value, dict) else None
        return float(value) if isinstance(value, (int, float)) else None
