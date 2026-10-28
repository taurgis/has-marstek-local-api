"""Restarts sensor: device restarts told apart from network dropouts."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import CONF_PLUG_ENERGY_IN, CONF_PLUG_ENERGY_OUT
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


async def test_plug_self_test_without_a_failed_poll_is_a_network_reset(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A linked plug's self-test with every poll answered is logged, not counted.

    The unit only reset its network chip (Control 150 does every 30 min while
    its cloud uploads fail), so the Restarts count stays at zero.
    """
    plug_entry = MockConfigEntry(domain="homewizard")
    plug_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=plug_entry.entry_id, identifiers={("homewizard", "plug")}
    )
    registry = er.async_get(hass)
    ids = [
        registry.async_get_or_create(
            "sensor",
            "homewizard",
            key,
            device_id=device.id,
            suggested_object_id=key,
            original_device_class=device_class,
        ).entity_id
        for key, device_class in (
            ("plug_import", "energy"),
            ("plug_export", "energy"),
            ("plug_power", "power"),
        )
    ]
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={CONF_PLUG_ENERGY_IN: ids[0], CONF_PLUG_ENERGY_OUT: ids[1]},
    )
    status = dict(_STATUS)
    client = create_mock_client(status=status)
    client.get_device_status = AsyncMock(side_effect=lambda *_a, **_k: dict(status))
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = mock_config_entry.runtime_data.coordinator
        await coordinator.async_refresh()

        for watts in ("8", "0", "2550", "2200", "1", "8"):
            hass.states.async_set(ids[2], watts, {"unit_of_measurement": "W"})
            await hass.async_block_till_done()
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        state = hass.states.get("sensor.venus_restarts")
        assert state is not None
        assert state.state == "0"
        assert state.attributes["outages"][-1]["kind"] == "network_reset"
        assert state.attributes["outages"][-1]["evidence"] == ["boot_pulse"]
        assert state.attributes["network_resets_30d"] == 1

        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()


def test_restart_log_stays_out_of_the_recorder() -> None:
    """The outage list is for the card, not for history."""
    unrecorded = MarstekSensor._Entity__combined_unrecorded_attributes  # type: ignore[attr-defined]
    assert "outages" in unrecorded
