"""Switch platform for Marstek SYS configuration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import MarstekConfigEntry
from .coordinator import MarstekDataUpdateCoordinator
from .helpers.switch_descriptions import (
    SWITCH_ENTITIES,
    MarstekSwitchEntityDescription,
)
from .helpers.sys_entity import MarstekSysEntity
from .pymarstek import MarstekUDPClient

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Marstek switch entities based on a config entry."""
    coordinator = config_entry.runtime_data.coordinator
    device_info = config_entry.runtime_data.device_info
    udp_client = coordinator.udp_client
    if udp_client is None:
        _LOGGER.error("UDP client not found for switch entity setup")
        return

    profile = coordinator.profile
    async_add_entities(
        MarstekSysSwitch(
            coordinator=coordinator,
            device_info=device_info,
            description=description,
            udp_client=udp_client,
            config_entry=config_entry,
        )
        for description in SWITCH_ENTITIES
        if description.supported_fn(profile)
    )


class MarstekSysSwitch(MarstekSysEntity, SwitchEntity, RestoreEntity):
    """Optimistic switch entity for a write-only SYS setting."""

    entity_description: MarstekSwitchEntityDescription

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        description: MarstekSwitchEntityDescription,
        udp_client: MarstekUDPClient,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the SYS switch entity."""
        super().__init__(coordinator, device_info, description, udp_client, config_entry)
        self._attr_is_on = None

    async def async_added_to_hass(self) -> None:
        """Restore the last acknowledged on/off state when it is still valid."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None:
            return
        if last_state.state == STATE_ON:
            self._attr_is_on = True
        elif last_state.state == STATE_OFF:
            self._attr_is_on = False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the SYS switch using the documented on wire value."""
        await self._async_set_is_on(True, self.entity_description.on_value)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the SYS switch using the documented off wire value."""
        await self._async_set_is_on(False, self.entity_description.off_value)

    async def _async_set_is_on(self, is_on: bool, wire_value: int) -> None:
        """Send the SYS write and publish state after acknowledgement."""
        await self._async_sys_write(
            self.entity_description.method,
            {self.entity_description.param_key: wire_value},
        )
        self._attr_is_on = is_on
        self.async_write_ha_state()
