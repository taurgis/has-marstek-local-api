"""Tests for Marstek integration setup/unload lifecycle."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DATA_UDP_CLIENT, DOMAIN

from tests.conftest import create_mock_client, patch_marstek_integration


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


async def test_reload_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reloading the integration re-establishes coordinator."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client(
        status={
            "device_mode": "auto",
            "battery_soc": 75,
        }
    )

    with patch_marstek_integration(client=client):
        # Initial setup
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert mock_config_entry.state == ConfigEntryState.LOADED

        # Reload
        await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        assert (
            hass.states.get("sensor.venus_battery_level") is not None
        )


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


async def test_multiple_entries_share_udp_client(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test that multiple config entries share a single UDP client."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )

    with patch_marstek_integration(client=client):
        # Setup first entry
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        assert DOMAIN in hass.data
        assert DATA_UDP_CLIENT in hass.data[DOMAIN]

        # Store reference to the shared client
        shared_client = hass.data[DOMAIN][DATA_UDP_CLIENT]

        # Create and add second entry AFTER first is setup
        second_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Second Device",
            unique_id="bb:cc:dd:ee:ff:00",
            data={
                "host": "5.6.7.8",
                "ble_mac": "BB:CC:DD:EE:FF:00",
                "device_type": "Venus v3",
                "version": 145,
            },
        )
        second_entry.add_to_hass(hass)

        # Setup second entry
        await hass.config_entries.async_setup(second_entry.entry_id)
        await hass.async_block_till_done()

        assert second_entry.state == ConfigEntryState.LOADED
        # Verify both entries use the SAME UDP client instance
        assert hass.data[DOMAIN][DATA_UDP_CLIENT] is shared_client

        # Cleanup
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.config_entries.async_unload(second_entry.entry_id)
        await hass.async_block_till_done()


async def test_partial_unload_preserves_shared_client(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test unloading one entry while another exists preserves the shared client."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )

    with patch_marstek_integration(client=client):
        # Setup first entry
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Store reference to verify it persists
        shared_client = hass.data[DOMAIN][DATA_UDP_CLIENT]

        # Create and add second entry AFTER first is setup
        second_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Second Device",
            unique_id="bb:cc:dd:ee:ff:00",
            data={
                "host": "5.6.7.8",
                "ble_mac": "BB:CC:DD:EE:FF:00",
                "device_type": "Venus v3",
                "version": 145,
            },
        )
        second_entry.add_to_hass(hass)

        # Setup second entry
        await hass.config_entries.async_setup(second_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        assert second_entry.state == ConfigEntryState.LOADED

        # Unload first entry only
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.NOT_LOADED
        assert second_entry.state == ConfigEntryState.LOADED

        # Shared client should still exist for the remaining entry
        assert DOMAIN in hass.data
        assert DATA_UDP_CLIENT in hass.data[DOMAIN]
        assert hass.data[DOMAIN][DATA_UDP_CLIENT] is shared_client

        # Services should still be registered (other entry still loaded)
        assert hass.services.has_service(DOMAIN, "set_passive_mode")

        # Cleanup
        await hass.config_entries.async_unload(second_entry.entry_id)
        await hass.async_block_till_done()


async def test_last_entry_unload_cleans_up_shared_client(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test that unloading the last entry cleans up the shared UDP client."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )

    with patch_marstek_integration(client=client):
        # Setup first entry
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Create and add second entry AFTER first is setup
        second_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Second Device",
            unique_id="bb:cc:dd:ee:ff:00",
            data={
                "host": "5.6.7.8",
                "ble_mac": "BB:CC:DD:EE:FF:00",
                "device_type": "Venus v3",
                "version": 145,
            },
        )
        second_entry.add_to_hass(hass)

        # Setup second entry
        await hass.config_entries.async_setup(second_entry.entry_id)
        await hass.async_block_till_done()

        # Unload first entry
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Client should still exist
        assert DOMAIN in hass.data
        assert DATA_UDP_CLIENT in hass.data[DOMAIN]

        # Unload last entry
        await hass.config_entries.async_unload(second_entry.entry_id)
        await hass.async_block_till_done()

        # UDP client should be cleaned up (either key removed or no client in it)
        marstek_data = hass.data.get(DOMAIN)
        if marstek_data is not None:
            assert DATA_UDP_CLIENT not in marstek_data
        # Services should be unregistered
        assert not hass.services.has_service(DOMAIN, "set_passive_mode")


async def test_repair_issue_created_on_failure(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test connection repair issue is created on setup failure."""
    mock_config_entry.add_to_hass(hass)

    failing_client = create_mock_client(send_request_error=TimeoutError("timeout"))
    with patch_marstek_integration(client=failing_client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    issue_registry = ir.async_get(hass)
    issue_id = f"cannot_connect_{mock_config_entry.entry_id}"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None


async def test_repair_issue_cleared_on_success(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test connection repair issue is cleared on successful setup."""
    mock_config_entry.add_to_hass(hass)

    issue_registry = ir.async_get(hass)
    issue_id = f"cannot_connect_{mock_config_entry.entry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="cannot_connect",
        translation_placeholders={"host": "1.2.3.4", "error": "timeout"},
        data={"entry_id": mock_config_entry.entry_id},
    )

    working_client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )
    with patch_marstek_integration(client=working_client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


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
    device = device_registry.async_get_device(
        identifiers={(DOMAIN, formatted_mac)}
    )
    assert device is not None

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert (
        device_registry.async_get_device(
            identifiers={(DOMAIN, formatted_mac)}
        )
        is None
    )
