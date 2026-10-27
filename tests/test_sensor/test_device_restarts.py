"""Restarts sensor: device restarts told apart from network dropouts."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.helpers.capacity_store import usable_capacity_storage_key
from custom_components.marstek.sensor import MarstekSensor
from tests.conftest import create_mock_client, patch_marstek_integration

_STATUS = {
    "battery_soc": 55,
    "battery_power": 0.0,
    "device_mode": "Auto",
    "bat_cap": 5120,
    "ct_connected": True,
}


def _store(hass: HomeAssistant, entry: MockConfigEntry) -> Store[dict[str, Any]]:
    return Store(hass, 1, usable_capacity_storage_key(entry.entry_id))


async def test_failed_poll_then_boot_trace_counts_a_restart(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A failed poll alone is a dropout; with the CT lost on return it is a restart."""
    status = dict(_STATUS)
    replies: list[dict[str, Any] | Exception] = []

    async def poll(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        reply = replies.pop(0) if replies else dict(status)
        if isinstance(reply, Exception):
            raise reply
        return reply

    mock_config_entry.add_to_hass(hass)
    client = create_mock_client(status=status)
    client.get_device_status = AsyncMock(side_effect=poll)
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = mock_config_entry.runtime_data.coordinator

        state = hass.states.get("sensor.venus_restarts")
        assert state is not None
        assert state.state == "0"
        assert state.attributes["state_class"] == SensorStateClass.TOTAL_INCREASING

        replies[:] = [TimeoutError("no reply"), dict(status)]
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        state = hass.states.get("sensor.venus_restarts")
        assert state is not None
        assert state.state == "0"
        assert state.attributes["dropouts_30d"] == 1

        replies[:] = [TimeoutError("no reply"), {**status, "ct_connected": False}]
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        state = hass.states.get("sensor.venus_restarts")
        assert state is not None
        assert state.state == "1"
        assert state.attributes["restarts_30d"] == 1
        assert "last_restart_at" in state.attributes
        assert [item["kind"] for item in state.attributes["outages"]] == ["dropout", "restart"]
        assert state.attributes["outages"][-1]["evidence"] == ["ct_lost"]

        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    saved = await _store(hass, mock_config_entry).async_load()
    assert isinstance(saved, dict)
    assert saved["restarts"]["restarts"] == 1
    assert len(saved["restarts"]["events"]) == 2


async def test_stored_restarts_survive_a_reload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The count and log come back from the store before any new outage."""
    await _store(hass, mock_config_entry).async_save(
        {
            "windows": [],
            "restarts": {
                "restarts": 4,
                "events": [
                    {
                        "at": 1_700_000_000.0,
                        "kind": "restart",
                        "offline_s": 60.0,
                        "failed_polls": 1,
                        "evidence": ["energy_rollback"],
                    }
                ],
            },
        }
    )
    mock_config_entry.add_to_hass(hass)
    with patch_marstek_integration(client=create_mock_client(status=dict(_STATUS))):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.venus_restarts")
    assert state is not None
    assert state.state == "4"
    assert state.attributes["last_restart_at"] == "2023-11-14T22:13:20+00:00"


def test_restart_log_stays_out_of_the_recorder() -> None:
    """The outage list is for the card, not for history."""
    unrecorded = MarstekSensor._Entity__combined_unrecorded_attributes  # type: ignore[attr-defined]
    assert "outages" in unrecorded
