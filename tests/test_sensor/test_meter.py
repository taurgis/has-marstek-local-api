"""CT and energy meter sensors, gated on firmware."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    DOMAIN,
)
from custom_components.marstek.device_info import get_device_identifier
from tests.conftest import create_mock_client, patch_marstek_integration

from ._helpers import (
    _as_meter_client,
    _em_state,
)


async def test_ct_connection_sensor_created(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test CT connection binary sensor is created when data is available (disabled by default)."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": 120,
        "ct_state": 1,
        "ct_connected": True,
    }

    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        # Sensor is disabled by default, check entity registry instead of state
        entity_registry = er.async_get(hass)
        entry = entity_registry.async_get("binary_sensor.venus_ct_connection")
        assert entry is not None
        assert entry.disabled_by is not None  # Disabled by default


async def test_ct_connection_sensor_created_when_value_missing(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test CT connection binary sensor is created even if value is missing."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": 120,
        "ct_state": None,
        "ct_connected": None,
    }

    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_registry = er.async_get(hass)
        entry = entity_registry.async_get("binary_sensor.venus_ct_connection")
        assert entry is not None
        assert entry.disabled_by is not None  # Disabled by default


async def test_ct_connection_sensor_disconnected(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test CT connection binary sensor shows disconnected state (disabled by default)."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": 120,
        "ct_state": 0,
        "ct_connected": False,
    }

    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Sensor is disabled by default, check entity registry instead of state
        entity_registry = er.async_get(hass)
        entry = entity_registry.async_get("binary_sensor.venus_ct_connection")
        assert entry is not None
        assert entry.disabled_by is not None  # Disabled by default


async def test_em_sensors_omitted_when_firmware_is_not_a_meter_client(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """HMG-50 below 155 never answers EM.GetStatus, so it gets no EM entities.

    The power entities are skipped by the profile gate; the energy counters
    fall out of their own exists_fn, because a call that never runs cannot
    report them.
    """
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "VenusC",
            "version": 153,
        },
    )

    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 55, "battery_power": 120}
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    device_identifier = get_device_identifier(mock_config_entry.data)
    for key in (
        "em_total_power",
        "em_a_power",
        "em_b_power",
        "em_c_power",
        "em_input_energy",
        "em_output_energy",
    ):
        unique_id = f"{device_identifier}_{key}"
        assert entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None, (
            f"{key} must not be created on a non-meter firmware"
        )


async def test_em_power_sensors_created_when_firmware_is_a_meter_client(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A meter-client firmware still gets its EM power entities."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "VenusE 3.0",
            "version": 150,
        },
    )

    client = create_mock_client(
        status={
            "device_mode": "auto",
            "battery_soc": 55,
            "battery_power": 120,
            "em_total_power": -230,
            "em_a_power": -230,
            "em_b_power": 0,
            "em_c_power": 0,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    device_identifier = get_device_identifier(mock_config_entry.data)
    for key in ("em_total_power", "em_a_power", "em_b_power", "em_c_power"):
        unique_id = f"{device_identifier}_{key}"
        assert entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id) is not None, (
            f"{key} must exist on a meter-client firmware"
        )


async def test_meter_energy_sensors_survive_pre_150_firmware(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """supports_em_energy picks the wire scale; it must not hide the counters.

    Venus E 144/145 report the EM lifetime counters unscaled, so gating the
    entities on that flag would drop sensors those devices really have.
    """
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "VenusE 3.0",
            "version": 145,
        },
    )

    client = create_mock_client(
        status={
            "device_mode": "auto",
            "battery_soc": 55,
            "battery_power": 120,
            "em_input_energy": 0.0,
            "em_output_energy": 0.0,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    device_identifier = get_device_identifier(mock_config_entry.data)
    for key in ("em_input_energy", "em_output_energy"):
        unique_id = f"{device_identifier}_{key}"
        assert entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id) is not None, (
            f"{key} must survive on firmware that reports it unscaled"
        )


async def test_grid_total_power_sensor_created(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test grid total power sensor is created when EM data is available."""
    mock_config_entry.add_to_hass(hass)
    _as_meter_client(hass, mock_config_entry)

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": 120,
        "em_total_power": 360,
        "em_a_power": 120,
        "em_b_power": 115,
        "em_c_power": 125,
    }

    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        assert _em_state(hass, mock_config_entry, "em_total_power") == "360"


async def test_phase_power_sensors_created(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test phase power sensors are created for 3-phase systems."""
    mock_config_entry.add_to_hass(hass)
    _as_meter_client(hass, mock_config_entry)

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": 120,
        "em_a_power": 120,
        "em_b_power": 115,
        "em_c_power": 125,
    }

    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        assert _em_state(hass, mock_config_entry, "em_a_power") == "120"
        assert _em_state(hass, mock_config_entry, "em_b_power") == "115"
        assert _em_state(hass, mock_config_entry, "em_c_power") == "125"


async def test_em_energy_sensors_created_when_values_present(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Meter energy sensors exist when coordinator values are present, including zero."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": 120,
        "em_input_energy": 0,
        "em_output_energy": 308632,
    }
    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        input_state = hass.states.get("sensor.venus_meter_input_energy")
        output_state = hass.states.get("sensor.venus_meter_output_energy")
        assert input_state is not None
        assert output_state is not None
        assert input_state.state == "0"
        assert output_state.state == "308632"
        assert input_state.attributes["unit_of_measurement"] == "Wh"
        assert input_state.attributes["device_class"] == "energy"
        assert input_state.attributes["state_class"] == "total_increasing"

        entity_registry = er.async_get(hass)
        device_identifier = get_device_identifier(mock_config_entry.data)
        input_entry = entity_registry.async_get("sensor.venus_meter_input_energy")
        output_entry = entity_registry.async_get("sensor.venus_meter_output_energy")
        assert input_entry is not None
        assert output_entry is not None
        assert input_entry.unique_id == f"{device_identifier}_em_input_energy"
        assert output_entry.unique_id == f"{device_identifier}_em_output_energy"


async def test_em_energy_sensors_omitted_when_values_missing(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Meter energy sensors are not created when coordinator values are absent."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client(
        status={
            "device_mode": "auto",
            "battery_soc": 55,
            "battery_power": 120,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert hass.states.get("sensor.venus_meter_input_energy") is None
        assert hass.states.get("sensor.venus_meter_output_energy") is None
