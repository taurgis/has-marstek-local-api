"""Number platform for Marstek SYS configuration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MarstekConfigEntry
from .const import DOMAIN
from .coordinator import MarstekDataUpdateCoordinator
from .helpers.number_descriptions import (
    NUMBER_ENTITIES,
    MarstekNumberEntityDescription,
)
from .helpers.sys_entity import MarstekSysEntity
from .pymarstek import MarstekUDPClient

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


def _parse_dod_int(value: Any, description: MarstekNumberEntityDescription) -> int | None:
    """Return a DOD integer in range, or None when the value is unusable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not float(value).is_integer():
        return None
    int_value = int(value)
    min_value = int(description.native_min_value or 0)
    max_value = int(description.native_max_value or 0)
    if int_value < min_value or int_value > max_value:
        return None
    return int_value


def _coerce_dod_int(value: float, description: MarstekNumberEntityDescription) -> int:
    """Convert a Home Assistant number value into a valid DOD integer."""
    int_value = _parse_dod_int(value, description)
    if int_value is None:
        min_value = int(description.native_min_value or 0)
        max_value = int(description.native_max_value or 0)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="sys_write_invalid",
            translation_placeholders={
                "error": (
                    f"value must be an integer between {min_value} and {max_value} (got {value})"
                )
            },
        )
    return int_value


def _restored_dod_value(
    native_value: Any, description: MarstekNumberEntityDescription
) -> int | None:
    """Return a restored DOD integer, or None when the stored value is unusable."""
    return _parse_dod_int(native_value, description)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Marstek number entities based on a config entry."""
    coordinator = config_entry.runtime_data.coordinator
    device_info = config_entry.runtime_data.device_info
    udp_client = coordinator.udp_client
    if udp_client is None:
        _LOGGER.error("UDP client not found for number entity setup")
        return

    profile = coordinator.profile
    async_add_entities(
        MarstekSysNumber(
            coordinator=coordinator,
            device_info=device_info,
            description=description,
            udp_client=udp_client,
            config_entry=config_entry,
        )
        for description in NUMBER_ENTITIES
        if description.supported_fn(profile)
    )


class MarstekSysNumber(MarstekSysEntity, RestoreNumber):
    """Optimistic number entity for a write-only SYS setting."""

    _attr_suggested_display_precision = 0
    entity_description: MarstekNumberEntityDescription

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        description: MarstekNumberEntityDescription,
        udp_client: MarstekUDPClient,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the SYS number entity."""
        super().__init__(coordinator, device_info, description, udp_client, config_entry)
        self._attr_native_value = float(description.default_value)

    @property
    def native_value(self) -> float | None:
        """Return the assumed depth-of-discharge percentage."""
        return self._attr_native_value

    async def async_added_to_hass(self) -> None:
        """Restore the last acknowledged native value when it is still valid."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data is None:
            return
        restored = _restored_dod_value(last_data.native_value, self.entity_description)
        if restored is not None:
            self._attr_native_value = float(restored)

    async def async_set_native_value(self, value: float) -> None:
        """Send DOD.SET and publish the requested value after acknowledgement."""
        int_value = _coerce_dod_int(value, self.entity_description)
        await self._async_sys_write(self.entity_description.method, {"value": int_value})
        self._attr_native_value = float(int_value)
        self.async_write_ha_state()
