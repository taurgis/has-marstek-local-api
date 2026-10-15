"""Off-grid power sensor: optional clamp of negative readings to 0 W."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    CONF_CLAMP_NEGATIVE_OFFGRID_POWER,
    DOMAIN,
)
from custom_components.marstek.device_info import get_device_identifier
from tests.conftest import create_mock_client, patch_marstek_integration


def _state_of(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str | None:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{get_device_identifier(entry.data)}_{key}"
    )
    assert entity_id is not None, f"{key} sensor was not created"
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


@pytest.mark.parametrize(
    ("enabled", "offgrid_power", "expected"),
    [
        (False, -10, "-10"),
        (False, 80, "80"),
        (False, 0, "0"),
        (True, -10, "0"),
        (True, -20, "0"),
        (True, 80, "80"),
        (True, 0, "0"),
    ],
)
async def test_offgrid_power_sensor_clamp_option(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    enabled: bool,
    offgrid_power: int,
    expected: str,
) -> None:
    """The sensor honours the opt-in clamp; default off keeps negatives visible."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_CLAMP_NEGATIVE_OFFGRID_POWER: enabled},
    )
    client = create_mock_client(
        status={
            "device_mode": "Auto",
            "battery_soc": 55,
            "battery_power": -1866,
            "ongrid_power": -1856,
            "offgrid_power": offgrid_power,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert _state_of(hass, mock_config_entry, "offgrid_power") == expected


async def test_offgrid_clamp_does_not_change_coordinator_or_battery_power(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Clamping is display-only: coordinator data and battery power stay signed."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_CLAMP_NEGATIVE_OFFGRID_POWER: True},
    )
    client = create_mock_client(
        status={
            "device_mode": "Auto",
            "battery_soc": 55,
            "battery_power": -1866,
            "ongrid_power": -1856,
            "offgrid_power": -10,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert _state_of(hass, mock_config_entry, "offgrid_power") == "0"
        assert _state_of(hass, mock_config_entry, "battery_power") == "-1866"
        assert mock_config_entry.runtime_data.coordinator.data["offgrid_power"] == -10
