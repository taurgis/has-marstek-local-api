"""Entry setup, unload and service registration."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    DOMAIN,
)
from custom_components.marstek.helpers.device_lookup import async_lookup_device_by_identifier
from tests.conftest import (
    create_mock_client,
    patch_marstek_integration,
)


async def test_setup_and_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test setup creates coordinator, platforms, and successful unload."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client(
        status={
            "device_mode": "SelfUse",
            "battery_soc": 55,
            "battery_power": 120,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        assert (
            hass.states.get("sensor.venus_battery_level") is not None
        )

        # Unload
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.NOT_LOADED
    assert DOMAIN not in hass.data


async def test_setup_with_custom_port(
    hass: HomeAssistant,
) -> None:
    """Test setup passes custom port through the entire chain."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            "host": "1.2.3.4",
            "port": 30003,
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus",
            "version": 3,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)

    client = create_mock_client(
        status={
            "device_mode": "SelfUse",
            "battery_soc": 55,
            "battery_power": 120,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state == ConfigEntryState.LOADED

        # Verify connection was verified with custom port
        client.fetch_es_mode.assert_awaited()
        call_args = client.fetch_es_mode.await_args
        assert call_args is not None
        assert call_args.args[1] == 30003  # port argument

        # Verify coordinator uses custom port for polling
        coordinator = entry.runtime_data.coordinator
        assert coordinator.device_port == 30003

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_connection_failure_triggers_retry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test setup raises ConfigEntryNotReady on connection failure."""
    mock_config_entry.add_to_hass(hass)

    # Simulate timeout during initial connectivity check (send_request fails)
    client = create_mock_client(send_request_error=TimeoutError("timeout"))

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # ConfigEntryNotReady results in SETUP_RETRY, not SETUP_ERROR
    assert mock_config_entry.state == ConfigEntryState.SETUP_RETRY


async def test_services_registered_during_setup(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test that domain services are registered during async_setup."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # Verify services are registered
    assert hass.services.has_service(DOMAIN, "set_passive_mode")
    assert hass.services.has_service(DOMAIN, "set_manual_schedule")
    assert hass.services.has_service(DOMAIN, "clear_manual_schedules")


def test_every_platform_declares_parallel_updates() -> None:
    """Home Assistant needs the constant to know how to schedule updates.

    Coordinator-driven read-only platforms need no serialization (0); the
    write platforms keep one in-flight command at a time, because the device
    answers a single UDP request at a time.
    https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/parallel-updates
    """
    from custom_components.marstek import (
        binary_sensor,
        number,
        select,
        sensor,
        switch,
    )

    assert binary_sensor.PARALLEL_UPDATES == 0
    assert sensor.PARALLEL_UPDATES == 0
    assert number.PARALLEL_UPDATES == 1
    assert select.PARALLEL_UPDATES == 1
    assert switch.PARALLEL_UPDATES == 1


@pytest.mark.parametrize("device_type", ["Venus E2.0", "VenusE", "HMG-50"])
async def test_existing_venus_e2_entry_fails_setup(
    hass: HomeAssistant,
    device_type: str,
) -> None:
    """Migrated HMG-50 / Venus E2 entries must not start polling."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            "host": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": device_type,
            "version": 153,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)
    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )
    with patch_marstek_integration(client=client):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state == ConfigEntryState.SETUP_ERROR
    client.send_request.assert_not_called()


async def test_remove_entry_cleans_stale_device(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test removing the last entry deletes the device registry entry."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    formatted_mac = format_mac(mock_config_entry.data["ble_mac"])
    device = async_lookup_device_by_identifier(device_registry, (DOMAIN, formatted_mac))
    assert device is not None

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert (
        async_lookup_device_by_identifier(device_registry, (DOMAIN, formatted_mac))
        is None
    )
