"""Entity creation, mode mapping, availability and restored totals."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components.sensor import SensorExtraStoredData
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    MODE_AUTO,
    MODE_UPS,
    OPERATING_MODES,
    ha_operating_mode,
)
from custom_components.marstek.device_info import get_device_identifier
from custom_components.marstek.pymarstek.data_parser import merge_device_status
from custom_components.marstek.sensor import MarstekSensor
from tests.conftest import create_mock_client, patch_marstek_integration


def test_ha_operating_mode_maps_open_api_wire_names() -> None:
    """HA 2026.9 enum sensors only accept OPERATING_MODES values."""
    assert ha_operating_mode("Auto") == MODE_AUTO
    assert ha_operating_mode("auto") == MODE_AUTO
    assert ha_operating_mode("UPS") == MODE_UPS
    assert ha_operating_mode(0) == MODE_AUTO
    assert ha_operating_mode("SelfUse") is None
    assert ha_operating_mode("selfuse") is None
    assert ha_operating_mode(None) is None


async def test_coordinator_success_creates_entities(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test coordinator successfully fetches data and creates sensor entities."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "device_mode": "SelfUse",
        "battery_soc": 55,
        "battery_power": 120,
        "pv1_power": 300,
    }

    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        state = hass.states.get("sensor.venus_battery_level")
        assert state is not None
        assert state.state == "55"


async def test_device_mode_enum_accepts_ups(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The device-mode enum includes ups rather than treating it as unknown."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client(
        status={"device_mode": MODE_UPS, "battery_soc": 55, "battery_power": 0}
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.venus_device_mode")
    assert state is not None
    assert state.state == MODE_UPS
    assert state.attributes.get("options") == OPERATING_MODES
    assert MODE_UPS in state.attributes["options"]


async def test_device_mode_enum_normalizes_open_api_auto(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Wire casing like Auto must not raise on HA 2026.9 enum sensors."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client(
        status={"device_mode": "Auto", "battery_soc": 55, "battery_power": 0}
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert mock_config_entry.state == ConfigEntryState.LOADED

    state = hass.states.get("sensor.venus_device_mode")
    assert state is not None
    assert state.state == MODE_AUTO


async def test_device_mode_enum_unknown_mode_is_unknown(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Unrecognized modes become unknown instead of crashing Core 2026.9."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client(
        status={"device_mode": "SelfUse", "battery_soc": 55, "battery_power": 0}
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert mock_config_entry.state == ConfigEntryState.LOADED

    state = hass.states.get("sensor.venus_device_mode")
    assert state is not None
    assert state.state == "unknown"


async def test_coordinator_failure_marks_entities_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test coordinator failure raises UpdateFailed, entities become unavailable."""
    mock_config_entry.add_to_hass(hass)
    # Set failure threshold to 1 so entities become unavailable immediately
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"failure_threshold": 1}
    )

    client = create_mock_client(status=TimeoutError("poll failed"))

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.venus_battery_level")
    # Entity may not exist if coordinator failed on first refresh
    # or should be unavailable if it was created
    if state:
        assert state.state == "unavailable"


async def test_entities_recover_after_unavailable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test entities recover after a failed refresh."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"failure_threshold": 1}
    )

    good_status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": -250,
    }

    client = create_mock_client(status=good_status)
    client.get_device_status = AsyncMock(
        side_effect=[
            good_status,
            TimeoutError("timeout"),
            good_status,
        ]
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get("sensor.venus_battery_level")
        assert state is not None
        assert state.state == "55"

        coordinator = mock_config_entry.runtime_data.coordinator
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        state = hass.states.get("sensor.venus_battery_level")
        assert state is not None
        assert state.state == "unavailable"

        await coordinator.async_refresh()
        await hass.async_block_till_done()

        state = hass.states.get("sensor.venus_battery_level")
        assert state is not None
        assert state.state == "55"


async def test_grid_input_total_restores_corrected_value_after_restart(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test restored grid totals seed the fallback baseline after restart."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": -250,
        "ongrid_power": -360,
        "total_grid_input_energy": 1000.0,
        "total_grid_output_energy": 500.0,
    }

    client = create_mock_client(status=status)

    async def _get_device_status(*_args, **kwargs):
        previous_status = kwargs.get("previous_status")
        last_update = 160.0 if previous_status else 100.0
        return merge_device_status(
            es_status_data=status,
            last_update=last_update,
            previous_status=previous_status,
        )

    client.get_device_status = AsyncMock(side_effect=_get_device_status)

    async def _restore_sensor_data(
        self: MarstekSensor,
    ) -> SensorExtraStoredData | None:
        if self.entity_description.key == "total_grid_input_energy":
            return SensorExtraStoredData(
                native_value=1100.0,
                native_unit_of_measurement="Wh",
            )
        return None

    with (
        patch.object(
            MarstekSensor,
            "async_get_last_sensor_data",
            _restore_sensor_data,
        ),
        patch_marstek_integration(client=client),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get("sensor.venus_total_grid_input_energy")
        assert state is not None
        assert float(state.state) == 1100.0

        coordinator = mock_config_entry.runtime_data.coordinator
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        state = hass.states.get("sensor.venus_total_grid_input_energy")
        assert state is not None
        assert float(state.state) == 1106.0


async def test_implausible_restored_energy_totals_are_not_applied(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A garbage restored floor must not override a sane device reading."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": -250,
        "ongrid_power": -360,
        "total_grid_input_energy": 267386.0,
        "total_grid_output_energy": 217251.0,
        "total_pv_energy": 0.0,
        "total_load_energy": 0.0,
    }

    client = create_mock_client(status=status)

    async def _get_device_status(*_args, **kwargs):
        previous_status = kwargs.get("previous_status")
        last_update = 160.0 if previous_status else 100.0
        return merge_device_status(
            es_status_data=status,
            last_update=last_update,
            previous_status=previous_status,
        )

    client.get_device_status = AsyncMock(side_effect=_get_device_status)

    async def _restore_sensor_data(
        self: MarstekSensor,
    ) -> SensorExtraStoredData | None:
        if self.entity_description.key in {
            "total_grid_input_energy",
            "total_grid_output_energy",
            "total_pv_energy",
            "total_load_energy",
        }:
            return SensorExtraStoredData(
                native_value=4_294_967_295_267.680,
                native_unit_of_measurement="Wh",
            )
        return None

    with (
        patch.object(
            MarstekSensor,
            "async_get_last_sensor_data",
            _restore_sensor_data,
        ),
        patch_marstek_integration(client=client),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        grid_in = hass.states.get("sensor.venus_total_grid_input_energy")
        grid_out = hass.states.get("sensor.venus_total_grid_output_energy")
        assert grid_in is not None
        assert grid_out is not None
        assert float(grid_in.state) == 267386.0
        assert float(grid_out.state) == 217251.0


async def test_total_solar_energy_keeps_stable_unique_id_and_wh(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The existing solar energy entity keeps its BLE-MAC unique ID and Wh unit."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "device_type": "VenusA", "version": 149},
    )
    client = create_mock_client(
        status={
            "device_mode": "auto",
            "battery_soc": 55,
            "battery_power": 120,
            "total_pv_energy": 257420,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get("sensor.venus_a_total_solar_energy")
        assert state is not None
        assert state.state == "257420"
        assert state.attributes["unit_of_measurement"] == "Wh"

        entity_registry = er.async_get(hass)
        device_identifier = get_device_identifier(mock_config_entry.data)
        entry = entity_registry.async_get("sensor.venus_a_total_solar_energy")
        assert entry is not None
        assert entry.unique_id == f"{device_identifier}_total_pv_energy"
