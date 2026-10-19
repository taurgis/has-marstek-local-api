"""Persist accepted usable-capacity windows across restarts."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION = 1


def usable_capacity_storage_key(entry_id: str) -> str:
    """Return the Store key for one config entry."""
    return f"{DOMAIN}.usable_capacity.{entry_id}"


class CapacityEstimateStore:
    """JSON store of accepted capacity windows for one device."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Create the store. Nothing is read until ``async_load``."""
        self._store: Store[dict[str, Any]] = Store(
            hass,
            _STORAGE_VERSION,
            usable_capacity_storage_key(entry_id),
        )

    async def async_load(self) -> dict[str, Any] | None:
        """Return the saved payload, or None when there is nothing usable."""
        try:
            data = await self._store.async_load()
        except (HomeAssistantError, OSError, TypeError, ValueError):
            _LOGGER.warning("Ignoring unreadable usable-capacity history for this device")
            return None
        if isinstance(data, dict):
            return data
        return None

    def delay_save(self, payload: dict[str, Any]) -> None:
        """Schedule a write. The payload is snapshotted by the caller."""
        self._store.async_delay_save(lambda: payload, delay=1.0)

    async def async_save(self, payload: dict[str, Any]) -> None:
        """Write immediately. Used when the entry unloads."""
        await self._store.async_save(payload)

    async def async_remove(self) -> None:
        """Delete the file. Used when the config entry is removed."""
        await self._store.async_remove()
