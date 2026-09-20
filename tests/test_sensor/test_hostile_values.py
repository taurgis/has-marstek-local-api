"""Non-numeric wire values must not reach a numeric sensor state.

Firmware that cannot read a field has been seen answering with a placeholder
string or a bool where the Open API documents a number. Home Assistant raises
on a non-numeric state for a sensor carrying a unit or a state class, which
loses the reading and writes one traceback per poll.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.device_info import get_device_identifier
from tests.conftest import create_mock_client, patch_marstek_integration

# Values a real device has been seen to send in place of a number.
PLACEHOLDERS = ["", "N/A", "--", "abc", True, False]


def _state_of(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str | None:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{get_device_identifier(entry.data)}_{key}"
    )
    assert entity_id is not None, f"{key} sensor was not created"
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


@pytest.mark.parametrize("placeholder", PLACEHOLDERS)
@pytest.mark.parametrize("key", ["battery_soc", "battery_power", "ongrid_power"])
async def test_non_numeric_reading_becomes_unknown(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    key: str,
    placeholder: object,
) -> None:
    """A placeholder or bool reading is reported as unknown, not published."""
    mock_config_entry.add_to_hass(hass)
    status = {
        "device_mode": "Auto",
        "battery_soc": 55,
        "battery_power": 120,
        "ongrid_power": 30,
    }
    status[key] = placeholder
    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert _state_of(hass, mock_config_entry, key) == "unknown"


async def test_numeric_readings_still_published(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The guard does not swallow ordinary numbers, including zero."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client(
        status={
            "device_mode": "Auto",
            "battery_soc": 0,
            "battery_power": -250.5,
            "ongrid_power": 0,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert _state_of(hass, mock_config_entry, "battery_soc") == "0"
        assert _state_of(hass, mock_config_entry, "battery_power") == "-250.5"
        assert _state_of(hass, mock_config_entry, "ongrid_power") == "0"


async def test_textual_sensors_keep_their_strings(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Sensors without a unit or state class are unaffected by the guard."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client(
        status={"device_mode": "Auto", "battery_soc": 55, "battery_power": 10}
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert _state_of(hass, mock_config_entry, "device_mode") == "auto"


async def test_non_numeric_reading_warns_once_per_value(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A device stuck on a placeholder does not write one warning per poll."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client(
        status={"device_mode": "Auto", "battery_soc": "N/A", "battery_power": 10}
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        caplog.clear()
        coordinator = mock_config_entry.runtime_data.coordinator
        for _ in range(3):
            await coordinator.async_refresh()
            await hass.async_block_till_done()

        warnings = [
            record for record in caplog.records if "non-numeric battery_soc" in record.getMessage()
        ]
        assert warnings == []


@pytest.mark.parametrize(
    ("quoted", "expected"),
    [("0", "0.0"), ("55", "55.0"), ("-250.5", "-250.5")],
)
async def test_quoted_numbers_are_still_readings(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    quoted: str,
    expected: str,
) -> None:
    """A number the device quoted is published, as it was before the guard."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client(
        status={"device_mode": "Auto", "battery_soc": 55, "battery_power": quoted}
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert _state_of(hass, mock_config_entry, "battery_power") == expected


@pytest.mark.parametrize("hostile", ["nan", "inf", "-inf", "1e400"])
async def test_quoted_non_finite_numbers_are_rejected(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, hostile: str
) -> None:
    """``float()`` parses "nan" and "inf", so the guard checks finiteness too."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client(
        status={"device_mode": "Auto", "battery_soc": 55, "battery_power": hostile}
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert _state_of(hass, mock_config_entry, "battery_power") == "unknown"
