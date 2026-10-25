"""A third-party style smart plug for the Marstek dev setup.

It reads a Shelly-style ``/rpc/Switch.GetStatus`` from the mock battery's plug
server every 10 seconds and reports energy in watt-hours, so the Marstek
energy-meter option can be tried with something that is not HomeWizard.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

DOMAIN = "mock_energy_plug"
PLATFORMS = [Platform.SENSOR]
_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=5)


class PlugCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the plug's switch status."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"Mock plug {entry.data[CONF_HOST]}",
            update_interval=timedelta(seconds=10),
            config_entry=entry,
        )
        self._url = f"http://{entry.data[CONF_HOST]}/rpc/Switch.GetStatus?id=0"

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            async with async_get_clientsession(self.hass).get(self._url, timeout=TIMEOUT) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise UpdateFailed(f"Plug did not answer: {err}") from err
        if not isinstance(data, dict):
            raise UpdateFailed("Plug sent no status object")
        return data


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start polling and add the sensors."""
    coordinator = PlugCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove the sensors."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
