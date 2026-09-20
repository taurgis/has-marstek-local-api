"""Base entities shared by every Marstek platform.

Home Assistant expects an integration's common entity wiring to live in
``entity.py`` so a reader knows where to look for it:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/common-modules
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import MarstekDataUpdateCoordinator
from .device_info import build_device_info, get_device_identifier
from .helpers.sys_write import (
    async_send_sys_write,
    sys_write_target,
    sys_write_timeout,
)
from .pymarstek import MarstekUDPClient, build_command


class MarstekEntity(CoordinatorEntity[MarstekDataUpdateCoordinator]):
    """Identity and device wiring shared by every Marstek entity.

    Every entity belongs to one device and takes its unique id from that
    device's MAC, so unique ids survive an IP change and the entity stays
    attached to the same device across a re-add.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        description: EntityDescription,
    ) -> None:
        """Initialize the shared entity attributes."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_info = device_info
        self._device_identifier = get_device_identifier(device_info)
        self._attr_unique_id = f"{self._device_identifier}_{description.key}"
        self._attr_device_info = build_device_info(device_info)


class MarstekSysEntity(MarstekEntity):
    """Wiring shared by the SYS number and switch platforms.

    SYS settings cannot be read back over the Open API, so these entities
    carry an assumed state and only publish a new value once the device has
    acknowledged the write.
    """

    _attr_assumed_state = True

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        description: EntityDescription,
        udp_client: MarstekUDPClient,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the shared SYS entity attributes."""
        super().__init__(coordinator, device_info, description)
        self._udp_client = udp_client
        self._config_entry = config_entry

    async def _async_sys_write(self, method: str, params: dict[str, Any]) -> None:
        """Send a SYS write for this entity and require its acknowledgement."""
        host, port = sys_write_target(self._config_entry)
        await async_send_sys_write(
            self._udp_client,
            build_command(method, params),
            host,
            port,
            sys_write_timeout(self._config_entry),
        )
