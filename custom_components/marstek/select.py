"""Select platform for Marstek devices."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from .pymarstek import MarstekUDPClient, build_command

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MarstekConfigEntry
from .const import (
    CMD_ES_SET_MODE,
    DATA_UDP_CLIENT,
    DEFAULT_UDP_PORT,
    DOMAIN,
    MODE_MANUAL,
    MODE_PASSIVE,
    OPERATING_MODES,
)
from .coordinator import MarstekDataUpdateCoordinator
from .device_info import build_device_info, get_device_identifier
from .mode_config import build_mode_config

_LOGGER = logging.getLogger(__name__)

# Retry configuration
MAX_RETRY_ATTEMPTS = 3
RETRY_TIMEOUT = 5.0
RETRY_DELAY = 1.0


@dataclass(kw_only=True)
class MarstekSelectEntityDescription(SelectEntityDescription):  # type: ignore[misc]
    """Marstek select entity description."""

    options_fn: Callable[[], list[str]]
    value_fn: Callable[[dict[str, Any]], str | None]


SELECT_ENTITIES: tuple[MarstekSelectEntityDescription, ...] = (
    MarstekSelectEntityDescription(
        key="operating_mode",
        translation_key="operating_mode",
        options_fn=lambda: OPERATING_MODES,
        value_fn=lambda data: data.get("device_mode"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Marstek select entities based on a config entry."""
    coordinator = config_entry.runtime_data.coordinator
    device_info = config_entry.runtime_data.device_info
    # Get shared UDP client from hass.data
    udp_client = hass.data.get(DOMAIN, {}).get(DATA_UDP_CLIENT)
    if not udp_client:
        _LOGGER.error("Shared UDP client not found for select entity setup")
        return

    async_add_entities(
        MarstekOperatingModeSelect(
            coordinator=coordinator,
            device_info=device_info,
            description=description,
            udp_client=udp_client,
            config_entry=config_entry,
        )
        for description in SELECT_ENTITIES
    )


class MarstekOperatingModeSelect(
    CoordinatorEntity[MarstekDataUpdateCoordinator], SelectEntity
):
    """Select entity for Marstek operating mode."""

    _attr_has_entity_name = True
    entity_description: MarstekSelectEntityDescription

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        description: MarstekSelectEntityDescription,
        udp_client: MarstekUDPClient,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_info_dict = device_info
        self._udp_client = udp_client
        self._config_entry = config_entry

        self._device_identifier = get_device_identifier(device_info)
        self._attr_unique_id = f"{self._device_identifier}_{description.key}"
        self._attr_device_info = build_device_info(device_info)

    @property
    def options(self) -> list[str]:
        """Return the list of available options."""
        return self.entity_description.options_fn()

    @property
    def current_option(self) -> str | None:
        """Return the current operating mode."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_select_option(self, option: str) -> None:
        """Change the operating mode."""
        if option not in OPERATING_MODES:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_mode",
                translation_placeholders={"mode": option},
            )

        # Block Passive/Manual selection - these require parameters via services
        if option == MODE_PASSIVE:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="passive_mode_requires_service",
            )
        if option == MODE_MANUAL:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="manual_mode_requires_service",
            )

        host = self._config_entry.data.get(CONF_HOST)
        port = self._config_entry.data.get(CONF_PORT, DEFAULT_UDP_PORT)
        if not host:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="no_host_configured",
            )

        # Build mode configuration
        config = build_mode_config(option)

        # Build command
        command = build_command(CMD_ES_SET_MODE, {"id": 0, "config": config})

        # Pause polling while sending command
        await self._udp_client.pause_polling(host)

        try:
            success = False
            last_error: str | None = None

            for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
                try:
                    await self._udp_client.send_request(
                        command,
                        host,
                        port,
                        timeout=RETRY_TIMEOUT,
                    )
                    _LOGGER.info(
                        "Successfully set operating mode to %s (attempt %d/%d)",
                        option,
                        attempt,
                        MAX_RETRY_ATTEMPTS,
                    )
                    success = True
                    break
                except (TimeoutError, OSError, ValueError) as err:
                    last_error = str(err)
                    _LOGGER.warning(
                        "Failed to set operating mode to %s (attempt %d/%d): %s",
                        option,
                        attempt,
                        MAX_RETRY_ATTEMPTS,
                        err,
                    )
                    if attempt < MAX_RETRY_ATTEMPTS:
                        await asyncio.sleep(RETRY_DELAY)

            if not success:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="mode_change_failed",
                    translation_placeholders={"mode": option, "error": last_error or "Unknown error"},
                )

            # Request coordinator refresh to update state
            await self.coordinator.async_request_refresh()

        finally:
            await self._udp_client.resume_polling(host)

    
