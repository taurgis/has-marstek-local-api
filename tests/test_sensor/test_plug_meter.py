"""A linked energy meter adds its round trip and standby draw to usable capacity."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import CONF_PLUG_ENERGY_IN, CONF_PLUG_ENERGY_OUT
from custom_components.marstek.helpers.capacity_store import usable_capacity_storage_key
from custom_components.marstek.helpers.plug_round_trip import PLUG_STORE_KEY
from tests.conftest import create_mock_client, patch_marstek_integration

_METER = ["sensor.plug_import", "sensor.plug_export"]
_STATUS = {"battery_soc": 55, "battery_power": 400, "device_mode": "auto", "bat_cap": 5120}


def _store(hass: HomeAssistant, entry: MockConfigEntry) -> Store[dict[str, Any]]:
    return Store(hass, 1, usable_capacity_storage_key(entry.entry_id))


async def _setup_with_meter(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    hass.config_entries.async_update_entry(
        entry, options={CONF_PLUG_ENERGY_IN: _METER[0], CONF_PLUG_ENERGY_OUT: _METER[1]}
    )
    hass.states.async_set(_METER[0], "6.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(_METER[1], "5.1", {"unit_of_measurement": "kWh"})
    client = create_mock_client(status=_STATUS)
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_meter_round_trip_is_published_and_saved(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Back at a stored state of charge after one capacity in, the quotient shows up."""
    mock_config_entry.add_to_hass(hass)
    anchor = {"at": time.time() - 86_400, "energy_in_wh": 0.0, "energy_out_wh": 0.0}
    await _store(hass, mock_config_entry).async_save(
        {PLUG_STORE_KEY: {"meter": _METER, "anchors": {"55": anchor}}}
    )

    await _setup_with_meter(hass, mock_config_entry)

    state = hass.states.get("sensor.venus_usable_capacity")
    assert state is not None
    assert state.attributes["plug_energy_entities"] == _METER
    assert state.attributes["plug_round_trip_efficiency"] == 0.85
    assert state.attributes["plug_round_trip_energy_in_wh"] == 6_000

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    saved = await _store(hass, mock_config_entry).async_load()
    assert saved is not None
    assert saved[PLUG_STORE_KEY]["meter"] == _METER
    assert saved[PLUG_STORE_KEY]["measured"]["efficiency"] == 0.85


async def test_without_a_meter_nothing_is_added(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """No linked meter means no meter attributes and no meter state on disk."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client(status=_STATUS)
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.venus_usable_capacity")
    assert state is not None
    assert not [key for key in state.attributes if key.startswith("plug_")]

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    saved = await _store(hass, mock_config_entry).async_load()
    assert saved is not None
    assert PLUG_STORE_KEY not in saved
