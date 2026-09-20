"""Select platform for Marstek devices."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MarstekConfigEntry
from .const import (
    CMD_ES_SET_MODE,
    DEFAULT_UDP_PORT,
    DOMAIN,
    MODE_MANUAL,
    MODE_PASSIVE,
    OPERATING_MODES,
)
from .coordinator import MarstekDataUpdateCoordinator
from .entity import MarstekEntity
from .helpers.command_retry import send_command_with_retries
from .helpers.polling import polling_paused
from .helpers.select_descriptions import (
    SELECT_ENTITIES,
    MarstekSelectEntityDescription,
)
from .mode_config import build_mode_config
from .pymarstek import MarstekUDPClient, build_command

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Marstek select entities based on a config entry."""
    coordinator = config_entry.runtime_data.coordinator
    device_info = config_entry.runtime_data.device_info
    udp_client = coordinator.udp_client
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


class MarstekOperatingModeSelect(MarstekEntity, SelectEntity):
    """Select entity for Marstek operating mode."""

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
        super().__init__(coordinator, device_info, description)
        self._udp_client = udp_client
        self._config_entry = config_entry

    @property
    def options(self) -> list[str]:
        """Return the list of available options."""
        return self.entity_description.options_fn(self.coordinator.profile)

    @property
    def current_option(self) -> str | None:
        """Return the current operating mode."""
        if not self.coordinator.data:
            return None
        option = self.entity_description.value_fn(self.coordinator.data)
        if option is not None and option in self.options:
            return option
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the operating mode.

        A mode this device cannot take is the caller's mistake, so it raises
        ServiceValidationError; only a failed exchange with the device is a
        HomeAssistantError.
        https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/action-exceptions
        """
        if option not in OPERATING_MODES:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_mode",
                translation_placeholders={"mode": option},
            )
        if option not in self.options:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="mode_not_supported",
                translation_placeholders={"mode": option},
            )

        # Block Passive/Manual selection - these require parameters via services
        if option == MODE_PASSIVE:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="passive_mode_requires_service",
            )
        if option == MODE_MANUAL:
            raise ServiceValidationError(
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
        async with polling_paused(self._udp_client, host):
            last_error = await send_command_with_retries(
                self._udp_client,
                command,
                host,
                port,
                description=f"mode command for {option}",
                logger=_LOGGER,
            )

        if last_error is not None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="mode_change_failed",
                translation_placeholders={
                    "mode": option,
                    "error": last_error,
                },
            )

        # Refresh after polling resumes so begin_poll_cycle is not skipped.
        await self.coordinator.async_request_refresh()
