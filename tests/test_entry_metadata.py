"""Unicast firmware metadata refresh helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.helpers.entry_metadata import async_refresh_entry_from_unicast


async def test_unicast_refresh_missing_host(hass: HomeAssistant) -> None:
    """An entry without a host is skipped without querying the device."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id="aa:bb:cc:dd:ee:ff", data={})
    entry.add_to_hass(hass)

    result = await async_refresh_entry_from_unicast(hass, entry, MagicMock())

    assert result == {"entry_id": entry.entry_id, "status": "missing_host"}


async def test_unicast_refresh_no_reply(hass: HomeAssistant) -> None:
    """A silent GetDevice leaves stored firmware in place."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={"host": "1.2.3.4", "version": 148, "ble_mac": "AA:BB:CC:DD:EE:FF"},
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)

    with patch(
        "custom_components.marstek.helpers.entry_metadata.get_device_info",
        AsyncMock(return_value=None),
    ):
        result = await async_refresh_entry_from_unicast(hass, entry, MagicMock())

    assert result["status"] == "no_reply"
    assert entry.data["version"] == 148
