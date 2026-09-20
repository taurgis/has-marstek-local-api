"""Diagnostic sensors: Wi-Fi, battery detail and API stability."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    DOMAIN,
)
from custom_components.marstek.device_info import get_device_identifier
from custom_components.marstek.helpers.sensor_descriptions import _api_success_rate_sensor
from custom_components.marstek.helpers.sensor_stats import (
    command_stats_attributes,
    command_success_rate,
    overall_command_stats_attributes,
    overall_command_success_rate,
)
from tests.conftest import create_mock_client, patch_marstek_integration


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
        device_identifier = get_device_identifier(mock_config_entry.data)
        entity_keys = [
            "api_success_rate_overall",
            "api_success_rate_es_get_mode",
            "api_success_rate_es_get_status",
            "api_success_rate_em_get_status",
            "api_success_rate_pv_get_status",
            "api_success_rate_wifi_get_status",
            "api_success_rate_bat_get_status",
            "api_success_rate_es_set_mode",
        ]
        for key in entity_keys:
            unique_id = f"{device_identifier}_{key}"
            entity_id = entity_registry.async_get_entity_id(
                "sensor", DOMAIN, unique_id
            )
            assert entity_id is not None
            entry = entity_registry.async_get(entity_id)
            assert entry is not None
            assert entry.disabled_by is not None


async def test_battery_detail_sensors_disabled_by_default(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test Bat.GetStatus sensors are created but disabled by default."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": 120,
        "bat_temp": 27.5,
        "bat_capacity": 2508,
        "bat_rated_capacity": 2560,
    }

    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        # Disabled by default (Bat.GetStatus can reset the device, issue #14)
        # and diagnostic, per the HA quality scale pairing guidance
        entity_registry = er.async_get(hass)
        device_identifier = get_device_identifier(mock_config_entry.data)
        for domain, key in (
            ("sensor", "bat_temp"),
            ("sensor", "bat_capacity"),
            ("sensor", "bat_rated_capacity"),
            ("binary_sensor", "bat_charg_flag"),
            ("binary_sensor", "bat_dischrg_flag"),
        ):
            unique_id = f"{device_identifier}_{key}"
            entity_id = entity_registry.async_get_entity_id(
                domain, DOMAIN, unique_id
            )
            assert entity_id is not None
            entry = entity_registry.async_get(entity_id)
            assert entry is not None
            assert entry.disabled_by is not None
            assert entry.entity_category is EntityCategory.DIAGNOSTIC


async def test_battery_detail_sensors_omitted_on_reset_prone_firmware(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Reset-prone firmware does not create Bat.GetStatus entities."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "VenusE 3.0",
            "version": 147,
        },
    )

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": 120,
        "bat_temp": 27.5,
        "bat_capacity": 2508,
        "bat_rated_capacity": 2560,
        "bat_charg_flag": 1,
        "bat_dischrg_flag": 1,
    }
    client = create_mock_client(status=status)

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    device_identifier = get_device_identifier(mock_config_entry.data)
    for domain, key in (
        ("sensor", "bat_temp"),
        ("sensor", "bat_capacity"),
        ("sensor", "bat_rated_capacity"),
        ("binary_sensor", "bat_charg_flag"),
        ("binary_sensor", "bat_dischrg_flag"),
    ):
        unique_id = f"{device_identifier}_{key}"
        assert entity_registry.async_get_entity_id(domain, DOMAIN, unique_id) is None


async def test_battery_detail_sensor_states_when_enabled(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test Bat.GetStatus sensors report their state once enabled."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "device_mode": "auto",
        "battery_soc": 55,
        "battery_power": 120,
        "bat_temp": 27.5,
        "bat_capacity": 2508,
        "bat_rated_capacity": 2560,
    }

    client = create_mock_client(status=status)

    # Pre-register the entities as enabled (simulates a user who opted in)
    entity_registry = er.async_get(hass)
    device_identifier = get_device_identifier(mock_config_entry.data)
    expected_states = {
        "bat_temp": "27.5",
        "bat_capacity": "2508",
        "bat_rated_capacity": "2560",
    }
    registry_entries = {
        key: entity_registry.async_get_or_create(
            domain="sensor",
            platform=DOMAIN,
            unique_id=f"{device_identifier}_{key}",
            config_entry=mock_config_entry,
        )
        for key in expected_states
    }

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        for key, expected in expected_states.items():
            state = hass.states.get(registry_entries[key].entity_id)
            assert state is not None
            assert state.state == expected


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

    rate = command_success_rate(coordinator, "ES.GetStatus")
    assert rate == 75.0

    attrs = command_stats_attributes(coordinator, "ES.GetStatus")
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

    rate = command_success_rate(coordinator, "ES.GetStatus")
    assert rate is None

    attrs = command_stats_attributes(coordinator, "ES.GetStatus")
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

    rate = overall_command_success_rate(coordinator)
    assert rate == 80.0

    attrs = overall_command_stats_attributes(coordinator)
    assert attrs == {
        "total_attempts": 10,
        "total_success": 8,
        "total_timeouts": 0,
        "total_failures": 0,
    }


async def test_all_new_sensors_with_full_status(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test all new sensors are created when full device status is available."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "device_type": "VenusA", "version": 150},
    )

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
                "sensor.venus_a_wifi_signal_strength"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "sensor.venus_a_wi_fi_ip_address"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "sensor.venus_a_wi_fi_gateway"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "sensor.venus_a_wi_fi_subnet_mask"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "sensor.venus_a_wi_fi_dns"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "binary_sensor.venus_a_ct_connection"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "binary_sensor.venus_a_charge_permission"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "binary_sensor.venus_a_discharge_permission"
            )
            is not None
        )

        # Battery detail sensors are disabled by default (issue #14)
        assert (
            entity_registry.async_get(
                "sensor.venus_a_battery_temperature"
            )
            is not None
        )
        # Grid power is enabled
        assert (
            hass.states.get("sensor.venus_a_total_power")
            is not None
        )
        assert hass.states.get("sensor.venus_a_on_grid_power") is not None
        assert hass.states.get("sensor.venus_a_off_grid_power") is not None
        # PV power (overridden from calculated sum when API returns 0)
        pv_power = hass.states.get("sensor.venus_a_pv_power")
        assert pv_power is not None
        assert float(pv_power.state) == 320.0  # 100 + 120 + 50 + 50
        assert (
            entity_registry.async_get(
                "sensor.venus_a_battery_remaining_capacity"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "sensor.venus_a_battery_rated_capacity"
            )
            is not None
        )
        assert (
            entity_registry.async_get(
                "sensor.venus_a_battery_total_capacity"
            )
            is not None
        )

        # Phase sensors (entity_id uses em_X_power)
        assert hass.states.get("sensor.venus_a_phase_a_power") is not None
        assert hass.states.get("sensor.venus_a_phase_b_power") is not None
        assert hass.states.get("sensor.venus_a_phase_c_power") is not None
