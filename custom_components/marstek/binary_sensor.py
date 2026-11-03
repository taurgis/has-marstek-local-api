"""Binary sensor platform for Marstek devices."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MarstekConfigEntry
from .const import BAT_STATUS_KEYS, DOMAIN
from .coordinator import MarstekDataUpdateCoordinator
from .entity import MarstekEntity
from .helpers.battery_automation import (
    BATTERY_LOW_HYSTERESIS,
    battery_is_charging,
    battery_low_state,
    finite_number,
    parse_dod_state,
    resolve_battery_low_threshold,
)
from .helpers.binary_sensor_descriptions import (
    BINARY_SENSORS,
    MarstekBinarySensorEntityDescription,
)
from .pymarstek.const import DOD_DEFAULT_VALUE

# Read-only platform: every value comes from the shared coordinator, so
# Home Assistant never has to serialize updates for these entities.
# https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/parallel-updates
PARALLEL_UPDATES = 0


class MarstekBinarySensor(MarstekEntity, BinarySensorEntity):
    """Representation of a Marstek binary sensor."""

    entity_description: MarstekBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        description: MarstekBinarySensorEntityDescription,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device_info, description)
        self._config_entry = config_entry

    @property
    def is_on(self) -> bool | None:
        """Return the state of the binary sensor."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


class MarstekBatteryLowBinarySensor(MarstekBinarySensor):
    """Battery-low sensor with a trip level and hysteresis.

    Unavailable while the state of charge is missing. The latched state is kept
    across that gap so the next reading still uses the clear level.
    """

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        description: MarstekBinarySensorEntityDescription,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the latched low state."""
        super().__init__(coordinator, device_info, description, config_entry)
        self._latched_low: bool | None = None

    @property
    def available(self) -> bool:
        """Unavailable while the state of charge is missing."""
        soc = finite_number((self.coordinator.data or {}).get("battery_soc"))
        return super().available and soc is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the state of charge is at or under the low level."""
        soc = finite_number((self.coordinator.data or {}).get("battery_soc"))
        if soc is None:
            return None
        state = battery_low_state(
            self._latched_low,
            soc,
            self._threshold(),
            BATTERY_LOW_HYSTERESIS,
        )
        self._latched_low = state
        return state

    def _threshold(self) -> int:
        """Return the trip level from options and the depth of discharge."""
        entry = self.coordinator.config_entry
        options: Mapping[str, Any] = {} if entry is None else entry.options
        return resolve_battery_low_threshold(
            options,
            supports_dod=self.coordinator.profile.supports_sys_dod,
            dod=self._configured_dod(),
        )

    def _configured_dod(self) -> int | None:
        """Return the depth of discharge the number entity last acknowledged.

        The Open API has no DOD read. The number entity stores the last write
        and starts at the device default of 88.
        """
        if not self.coordinator.profile.supports_sys_dod:
            return None
        entity_id = er.async_get(self.hass).async_get_entity_id(
            NUMBER_DOMAIN,
            DOMAIN,
            f"{self._device_identifier}_depth_of_discharge",
        )
        if entity_id is not None:
            state = self.hass.states.get(entity_id)
            parsed = parse_dod_state(state.state if state is not None else None)
            if parsed is not None:
                return parsed
        return DOD_DEFAULT_VALUE


class MarstekBatteryChargingBinarySensor(MarstekBinarySensor):
    """Charging sensor. Unavailable while battery power is missing."""

    @property
    def available(self) -> bool:
        """Unavailable while battery power is missing."""
        power = (self.coordinator.data or {}).get("battery_power")
        return super().available and battery_is_charging(power) is not None


def _entity_class(key: str) -> type[MarstekBinarySensor]:
    """Return the entity class for a binary sensor key."""
    if key == "battery_low":
        return MarstekBatteryLowBinarySensor
    if key == "battery_charging":
        return MarstekBatteryChargingBinarySensor
    return MarstekBinarySensor


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
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
        if description.key in BAT_STATUS_KEYS and coordinator.profile.openapi_reset_prone:
            continue
        if description.exists_fn(data_for_exists):
            entity_cls = _entity_class(description.key)
            entities.append(entity_cls(coordinator, device_info, description, config_entry))
    async_add_entities(entities)
