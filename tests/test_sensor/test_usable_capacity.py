"""Usable-capacity and state-of-health sensors."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import PERCENTAGE, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.coordinator import MarstekDataUpdateCoordinator
from custom_components.marstek.helpers.capacity_store import (
    CapacityEstimateStore,
    usable_capacity_storage_key,
)
from custom_components.marstek.sensor import MarstekSensor
from tests.conftest import create_mock_client, patch_marstek_integration

# Seconds for one state-of-charge point of a 4600 Wh pack at 2000 W and 93%.
_POINT_SECONDS = ((0.20 * 4600.0 * 0.93) / 2000.0) * 3600.0 / 20.0

_WINDOWS = [
    {
        "capacity_wh": 4500.0,
        "direction": "discharge",
        "soc_delta": 20.0,
        "closed_at": 1_700_000_000.0,
    },
    {
        "capacity_wh": 4600.0,
        "direction": "discharge",
        "soc_delta": 21.0,
        "closed_at": 1_700_003_600.0,
    },
    {
        "capacity_wh": 4700.0,
        "direction": "discharge",
        "soc_delta": 22.0,
        "closed_at": 1_700_007_200.0,
    },
]


async def _setup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    status: dict[str, Any],
) -> None:
    """Load the entry against a canned poll."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client(status=status)
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED


async def test_usable_capacity_is_unknown_until_windows_exist(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The entity exists immediately and does not invent a watt-hour value."""
    await _setup(
        hass,
        mock_config_entry,
        {"battery_soc": 55, "battery_power": 400, "device_mode": "auto", "bat_cap": 5120},
    )

    state = hass.states.get("sensor.venus_usable_capacity")
    assert state is not None
    assert state.state == "unknown"
    assert state.attributes["device_class"] == SensorDeviceClass.ENERGY_STORAGE
    assert state.attributes["state_class"] == SensorStateClass.MEASUREMENT
    assert state.attributes["unit_of_measurement"] == UnitOfEnergy.WATT_HOUR
    assert state.attributes["windows_accepted"] == 0
    assert state.attributes["confidence"] == "collecting"

    health = hass.states.get("sensor.venus_battery_state_of_health")
    assert health is not None
    assert health.state == "unknown"
    assert health.attributes["state_class"] == SensorStateClass.MEASUREMENT
    assert health.attributes["unit_of_measurement"] == PERCENTAGE
    assert "windows" in state.attributes
    assert not {"windows", "history", "active_window"} & set(health.attributes)


async def test_restored_windows_publish_median_and_state_of_health(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A restart shows the saved median before any new window closes."""
    mock_config_entry.add_to_hass(hass)
    store: Store[dict[str, Any]] = Store(
        hass, 1, usable_capacity_storage_key(mock_config_entry.entry_id)
    )
    await store.async_save({"windows": _WINDOWS})

    client = create_mock_client(
        status={
            "battery_soc": 55,
            "battery_power": 400,
            "device_mode": "auto",
            "bat_cap": 5120,
        }
    )
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.venus_usable_capacity")
    assert state is not None
    assert state.state == "4600"
    assert state.attributes["efficiency_correction"] == "discharge_median"
    assert state.attributes["discharge_median_wh"] == 4600
    assert "charge_median_wh" not in state.attributes
    assert "implied_round_trip_efficiency" not in state.attributes
    health = hass.states.get("sensor.venus_battery_state_of_health")
    assert health is not None
    assert health.state == "89.8"


async def test_state_of_health_is_absent_without_a_nameplate(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Venus E mini has no nameplate on the fast poll, so only watt-hours exist."""
    await _setup(
        hass,
        mock_config_entry,
        {"battery_soc": 40, "battery_power": -800, "device_mode": "auto", "bat_cap": None},
    )

    assert hass.states.get("sensor.venus_usable_capacity") is not None
    assert hass.states.get("sensor.venus_battery_state_of_health") is None


async def test_successive_polls_publish_and_unload_persists(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Fast-tier polls are enough. Unload writes the accepted windows."""
    status = {
        "battery_soc": 80,
        "battery_power": 2000.0,
        "device_mode": "auto",
        "bat_cap": 5120,
    }
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client(status=status)
    # A fresh dict each poll. Reusing one object makes the coordinator treat
    # the update as unchanged and skip entity listeners.
    client.get_device_status = AsyncMock(side_effect=lambda *_args, **_kwargs: dict(status))
    clock = {"t": 1_700_000_000.0}
    original = MarstekDataUpdateCoordinator._async_record_capacity

    async def record(
        self: MarstekDataUpdateCoordinator, device_status: dict[str, Any], **kwargs: Any
    ) -> None:
        clock["t"] += _POINT_SECONDS
        with patch("custom_components.marstek.coordinator.time.time", return_value=clock["t"]):
            await original(self, device_status, **kwargs)

    with (
        patch_marstek_integration(client=client),
        patch.object(MarstekDataUpdateCoordinator, "_async_record_capacity", record),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = mock_config_entry.runtime_data.coordinator
        for offset in range(1, 62):
            status["battery_soc"] = 80 - offset
            await coordinator.async_refresh()
            await hass.async_block_till_done()

        state = hass.states.get("sensor.venus_usable_capacity")
        assert state is not None
        assert state.state == "4600"
        health = hass.states.get("sensor.venus_battery_state_of_health")
        assert health is not None
        assert health.state == "89.8"

        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    store: Store[dict[str, Any]] = Store(
        hass, 1, usable_capacity_storage_key(mock_config_entry.entry_id)
    )
    saved = await store.async_load()
    assert isinstance(saved, dict)
    assert len(saved["windows"]) == 3
    assert saved["windows"][0]["start_soc"] == 79.0
    assert saved["windows"][0]["pv_share"] == 0.0
    assert saved["windows"][0]["mode"] == "auto"


async def test_unreadable_capacity_history_is_ignored(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A broken storage file does not block the sensors."""
    store = CapacityEstimateStore(hass, mock_config_entry.entry_id)
    with patch.object(store._store, "async_load", AsyncMock(side_effect=HomeAssistantError("bad"))):
        assert await store.async_load() is None
    store.delay_save({"windows": []})


def test_card_lists_stay_out_of_the_recorder() -> None:
    """The card's lists change every run, so the recorder must skip them."""
    unrecorded = MarstekSensor._Entity__combined_unrecorded_attributes  # type: ignore[attr-defined]
    assert {"windows", "history", "active_window"} <= unrecorded
