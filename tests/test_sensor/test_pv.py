"""PV entities and the channel sums behind their values."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.conftest import create_mock_client, patch_marstek_integration


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


async def test_e_mini_profile_omits_pv_entities_even_with_stale_data(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A non-PV profile does not create PV entities from stale coordinator keys."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus E mini",
            "version": 150,
        },
    )
    client = create_mock_client(
        status={
            "device_mode": "auto",
            "battery_soc": 55,
            "pv_power": 320,
            "pv1_power": 320,
            "total_pv_energy": 257420,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get("sensor.venus_e_mini_pv_power") is None
    assert hass.states.get("sensor.venus_e_mini_pv1_power") is None
    assert hass.states.get("sensor.venus_e_mini_total_solar_energy") is None


async def test_pv_power_overridden_when_api_returns_zero(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test pv_power is overridden with calculated sum when API returns 0.

    Venus A devices report pv_power=0 in ES.GetStatus but individual
    channels from PV.GetStatus have correct values. The integration should
    override pv_power with the calculated sum from channels.
    """
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "device_type": "VenusA", "version": 145},
    )

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
        state = hass.states.get("sensor.venus_a_pv_power")
        assert state is not None
        assert float(state.state) == 184.5


async def test_pv_power_partial_channels(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test pv_power with only some channels reporting."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "device_type": "VenusA", "version": 145},
    )

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
        state = hass.states.get("sensor.venus_a_pv_power")
        assert state is not None
        assert float(state.state) == 150.0
