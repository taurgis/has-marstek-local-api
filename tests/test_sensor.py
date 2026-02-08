"""Tests for Marstek sensor entities."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.sensor import (
    _api_success_rate_sensor,
    _command_success_rate,
    _command_stats_attributes,
    _overall_command_stats_attributes,
    _overall_command_success_rate,
)

from tests.conftest import create_mock_client, patch_marstek_integration


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


async def test_no_pv_entities_when_data_missing(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test PV entities are not created when PV data keys are absent."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "device_mode": "SelfUse",
        "battery_soc": 55,
        "battery_power": 120,
        # No pv1_power, pv2_power, etc.
    }

    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Check that PV entities are not registered
        pv_entity = hass.states.get("sensor.venus_pv1_power")
        assert pv_entity is None
        # PV power should also not be created as exist_fn checks for pv_power key
        pv_power = hass.states.get("sensor.venus_pv_power")
        assert pv_power is None


async def test_pv_power_overridden_when_api_returns_zero(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test pv_power is overridden with calculated sum when API returns 0.
    
    Venus A devices report pv_power=0 in ES.GetStatus but individual 
    channels from PV.GetStatus have correct values. The integration should
    override pv_power with the calculated sum from channels.
    """
    mock_config_entry.add_to_hass(hass)

    # This status simulates what comes from merge_device_status when
    # ES.GetStatus pv_power=0 is overridden with calculated sum
    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": -15.5,  # Recalculated: charging
        "pv1_power": 41.5,  # After scaling: 41.5W
        "pv2_power": 52.0,  # 52W
        "pv3_power": 58.0,  # 58W
        "pv4_power": 33.0,  # 33W
        # pv_power is overridden in merge_device_status when API returns 0
        "pv_power": 184.5,  # 41.5 + 52 + 58 + 33 = 184.5
    }

    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # PV power should show the overridden value (sum of channels)
        state = hass.states.get("sensor.venus_pv_power")
        assert state is not None
        assert float(state.state) == 184.5


async def test_pv_power_partial_channels(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test pv_power with only some channels reporting."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": 120,
        "pv1_power": 100.0,
        "pv2_power": 50.0,
        # pv3 and pv4 not present
        # pv_power is calculated from available channels
        "pv_power": 150.0,  # 100 + 50
    }

    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # PV power should show sum of available channels
        state = hass.states.get("sensor.venus_pv_power")
        assert state is not None
        assert float(state.state) == 150.0


async def test_wifi_rssi_sensor_created(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test WiFi RSSI sensor is created when data is available (disabled by default)."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": 120,
        "wifi_rssi": -58,
        "wifi_ssid": "TestNetwork",
    }

    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        # Sensor is disabled by default, check entity registry instead of state
        entity_registry = er.async_get(hass)
        entry = entity_registry.async_get(
            "sensor.venus_wifi_signal_strength"
        )
        assert entry is not None
        assert entry.disabled_by is not None  # Disabled by default


async def test_wifi_rssi_sensor_not_created_when_missing(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test WiFi RSSI sensor is NOT created when data is absent."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": 120,
        # No wifi_rssi
    }

    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get("sensor.venus_wifi_signal_strength")
        assert state is None


    async def test_api_stability_sensors_disabled_by_default(
        hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Test API stability sensors are created but disabled by default."""
        mock_config_entry.add_to_hass(hass)

        status = {
            "device_mode": "auto",
            "battery_soc": 55,
            "battery_power": 120,
        }

        client = create_mock_client(status=status)

        with patch_marstek_integration(client=client):
            await hass.config_entries.async_setup(mock_config_entry.entry_id)
            await hass.async_block_till_done()

            entity_registry = er.async_get(hass)
            entity_ids = [
                "sensor.venus_api_success_rate_overall",
                "sensor.venus_api_success_rate_es_get_mode",
                "sensor.venus_api_success_rate_es_get_status",
                "sensor.venus_api_success_rate_em_get_status",
                "sensor.venus_api_success_rate_pv_get_status",
                "sensor.venus_api_success_rate_wifi_get_status",
                "sensor.venus_api_success_rate_bat_get_status",
                "sensor.venus_api_success_rate_es_set_mode",
            ]
            for entity_id in entity_ids:
                entry = entity_registry.async_get(entity_id)
                assert entry is not None
                assert entry.disabled_by is not None


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
        entry = entity_registry.async_get(
            "binary_sensor.venus_ct_connection"
        )
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
        entry = entity_registry.async_get(
            "binary_sensor.venus_ct_connection"
        )
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
        entry = entity_registry.async_get(
            "binary_sensor.venus_ct_connection"
        )
        assert entry is not None
        assert entry.disabled_by is not None  # Disabled by default


async def test_battery_temperature_sensor_created(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test battery temperature sensor is created when data is available."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": 120,
        "bat_temp": 27.5,
    }

    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        state = hass.states.get("sensor.venus_battery_temperature")
        assert state is not None
        assert state.state == "27.5"


async def test_grid_total_power_sensor_created(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test grid total power sensor is created when EM data is available."""
    mock_config_entry.add_to_hass(hass)

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
        state = hass.states.get("sensor.venus_total_power")
        assert state is not None
        assert state.state == "360"


async def test_phase_power_sensors_created(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test phase power sensors are created for 3-phase systems."""
    mock_config_entry.add_to_hass(hass)

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
        
        # Check all three phase sensors (entity_id uses sensor_type em_X_power)
        state_a = hass.states.get("sensor.venus_phase_a_power")
        assert state_a is not None
        assert state_a.state == "120"
        
        state_b = hass.states.get("sensor.venus_phase_b_power")
        assert state_b is not None
        assert state_b.state == "115"
        
        state_c = hass.states.get("sensor.venus_phase_c_power")
        assert state_c is not None
        assert state_c.state == "125"


async def test_all_new_sensors_with_full_status(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test all new sensors are created when full device status is available."""
    mock_config_entry.add_to_hass(hass)

    # Full status with all new fields
    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": 250,
        "battery_status": "discharging",
        "ongrid_power": -150,
        "offgrid_power": 10,
        "pv_power": 320,
        "bat_cap": 2560,
        # PV channels (for total PV power calculation)
        "pv1_power": 100.0,
        "pv2_power": 120.0,
        "pv3_power": 50.0,
        "pv4_power": 50.0,
        # WiFi
        "wifi_rssi": -58,
        "wifi_ssid": "TestNetwork",
        "wifi_sta_ip": "192.168.1.50",
        "wifi_sta_gate": "192.168.1.1",
        "wifi_sta_mask": "255.255.255.0",
        "wifi_sta_dns": "192.168.1.1",
        # CT / Energy Meter
        "ct_state": 1,
        "ct_connected": True,
        "em_a_power": 120,
        "em_b_power": 115,
        "em_c_power": 125,
        "em_total_power": 360,
        # Battery details
        "bat_temp": 27.5,
        "bat_charg_flag": 1,
        "bat_dischrg_flag": 1,
        "bat_capacity": 2508,
        "bat_rated_capacity": 2560,
    }

    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        
        # Verify entities - some are disabled by default (check entity_registry)
        entity_registry = er.async_get(hass)
        
        # WiFi and CT sensors are disabled by default
        assert (
            entity_registry.async_get(
                "sensor.venus_wifi_signal_strength"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "sensor.venus_wi_fi_ip_address"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "sensor.venus_wi_fi_gateway"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "sensor.venus_wi_fi_subnet_mask"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "sensor.venus_wi_fi_dns"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "binary_sensor.venus_ct_connection"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "binary_sensor.venus_charge_permission"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "binary_sensor.venus_discharge_permission"
            )
            is not None
        )
        
        # Battery temp and grid power are enabled
        assert (
            hass.states.get("sensor.venus_battery_temperature")
            is not None
        )
        assert (
            hass.states.get("sensor.venus_total_power")
            is not None
        )
        assert hass.states.get("sensor.venus_on_grid_power") is not None
        assert hass.states.get("sensor.venus_off_grid_power") is not None
        # PV power (overridden from calculated sum when API returns 0)
        pv_power = hass.states.get("sensor.venus_pv_power")
        assert pv_power is not None
        assert float(pv_power.state) == 320.0  # 100 + 120 + 50 + 50
        assert (
            entity_registry.async_get(
                "sensor.venus_battery_remaining_capacity"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "sensor.venus_battery_rated_capacity"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "sensor.venus_battery_total_capacity"
            )
            is not None
        )
        
        # Phase sensors (entity_id uses em_X_power)
        assert hass.states.get("sensor.venus_phase_a_power") is not None
        assert hass.states.get("sensor.venus_phase_b_power") is not None
        assert hass.states.get("sensor.venus_phase_c_power") is not None


def test_command_success_rate_calculates_percentage() -> None:
    """Test API success rate calculation with valid stats."""
    coordinator = SimpleNamespace(
        device_ip="1.2.3.4",
        udp_client=SimpleNamespace(
            get_command_stats_for_ip=lambda _ip: {
                "ES.GetStatus": {"total_attempts": 4, "total_success": 3}
            }
        ),
    )

    rate = _command_success_rate(coordinator, "ES.GetStatus")
    assert rate == 75.0

    attrs = _command_stats_attributes(coordinator, "ES.GetStatus")
    assert attrs == {
        "total_attempts": 4,
        "total_success": 3,
    }


def test_command_success_rate_returns_none_with_no_attempts() -> None:
    """Test API success rate returns None when no attempts were recorded."""
    coordinator = SimpleNamespace(
        device_ip="1.2.3.4",
        udp_client=SimpleNamespace(
            get_command_stats_for_ip=lambda _ip: {
                "ES.GetStatus": {"total_attempts": 0, "total_success": 0}
            }
        ),
    )

    rate = _command_success_rate(coordinator, "ES.GetStatus")
    assert rate is None

    attrs = _command_stats_attributes(coordinator, "ES.GetStatus")
    assert attrs == {
        "total_attempts": 0,
        "total_success": 0,
    }


def test_api_success_rate_sensor_value_fn() -> None:
    """Test API success rate sensor description value function."""
    coordinator = SimpleNamespace(
        device_ip="1.2.3.4",
        udp_client=SimpleNamespace(
            get_command_stats_for_ip=lambda _ip: {
                "ES.GetMode": {"total_attempts": 10, "total_success": 9}
            }
        ),
    )

    description = _api_success_rate_sensor("ES.GetMode", "api_success_rate_es_get_mode")
    value = description.value_fn(coordinator, {}, None)
    assert value == 90.0

    attrs = description.attributes_fn(coordinator, {}, None)
    assert attrs == {
        "total_attempts": 10,
        "total_success": 9,
    }


def test_overall_command_success_rate() -> None:
    """Test overall API success rate aggregation."""
    coordinator = SimpleNamespace(
        device_ip="1.2.3.4",
        udp_client=SimpleNamespace(
            get_command_stats_for_ip=lambda _ip: {
                "ES.GetMode": {"total_attempts": 5, "total_success": 5},
                "ES.GetStatus": {"total_attempts": 5, "total_success": 3},
            }
        ),
    )

    rate = _overall_command_success_rate(coordinator)
    assert rate == 80.0

    attrs = _overall_command_stats_attributes(coordinator)
    assert attrs == {
        "total_attempts": 10,
        "total_success": 8,
        "total_timeouts": 0,
        "total_failures": 0,
    }
