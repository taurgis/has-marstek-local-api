"""Binary sensor platform for Marstek devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

try:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
except ImportError:
    from collections.abc import Iterable
    from typing import Protocol

    class AddConfigEntryEntitiesCallback(Protocol):
        def __call__(
            self,
            new_entities: Iterable,
            update_before_add: bool = False,
        ) -> None:
            """Define add_entities type."""

from . import MarstekConfigEntry
from .const import DOMAIN
from .coordinator import MarstekDataUpdateCoordinator
from .device_info import build_device_info, get_device_identifier


class MarstekCTConnectionBinarySensor(
    CoordinatorEntity[MarstekDataUpdateCoordinator], BinarySensorEntity
):
    """Representation of a Marstek CT connection binary sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = "ct_connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the CT connection binary sensor."""
        super().__init__(coordinator)
        self._device_info = device_info
        self._config_entry = config_entry

        device_identifier = get_device_identifier(device_info)
        self._attr_unique_id = f"{device_identifier}_ct_connection"
        self._attr_device_info = build_device_info(device_info)

    @property
    def is_on(self) -> bool | None:
        """Return true if CT is connected."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("ct_connected")


class MarstekBatteryPermissionBinarySensor(
    CoordinatorEntity[MarstekDataUpdateCoordinator], BinarySensorEntity
):
    """Representation of Marstek battery permission binary sensors."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None,
        *,
        key: str,
        translation_key: str,
    ) -> None:
        """Initialize the battery permission binary sensor."""
        super().__init__(coordinator)
        self._device_info = device_info
        self._config_entry = config_entry
        self._value_key = key
        self._attr_translation_key = translation_key

        device_identifier = get_device_identifier(device_info)
        self._attr_unique_id = f"{device_identifier}_{key}"
        self._attr_device_info = build_device_info(device_info)

    @property
    def is_on(self) -> bool | None:
        """Return true if the permission flag is enabled."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get(self._value_key)
        if value is None:
            return None
        return bool(value)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Marstek binary sensors based on a config entry."""
    coordinator = config_entry.runtime_data.coordinator
    device_info = config_entry.runtime_data.device_info

    entities: list[BinarySensorEntity] = [
        MarstekCTConnectionBinarySensor(coordinator, device_info, config_entry),
        MarstekBatteryPermissionBinarySensor(
            coordinator,
            device_info,
            config_entry,
            key="bat_charg_flag",
            translation_key="charge_permission",
        ),
        MarstekBatteryPermissionBinarySensor(
            coordinator,
            device_info,
            config_entry,
            key="bat_dischrg_flag",
            translation_key="discharge_permission",
        ),
    ]

    async_add_entities(entities)
