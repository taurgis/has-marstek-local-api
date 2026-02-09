"""Tests for Marstek integration setup/unload lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek import _async_update_listener
from custom_components.marstek.const import DATA_SUPPRESS_RELOADS, DATA_UDP_CLIENT, DOMAIN

from tests.conftest import (
    create_mock_client,
    patch_manual_connection,
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


async def test_update_listener_suppresses_reload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test update listener skips reload when suppression is set."""
    mock_config_entry.add_to_hass(hass)
    hass.data[DOMAIN] = {DATA_SUPPRESS_RELOADS: {mock_config_entry.entry_id}}

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await _async_update_listener(hass, mock_config_entry)

    mock_reload.assert_not_called()
    assert mock_config_entry.entry_id not in hass.data[DOMAIN][DATA_SUPPRESS_RELOADS]


async def test_update_listener_triggers_reload_when_not_suppressed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test update listener reloads entry when not suppressed."""
    mock_config_entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await _async_update_listener(hass, mock_config_entry)

    mock_reload.assert_called_once_with(mock_config_entry.entry_id)


async def test_options_update_reloads_and_keeps_services(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test options update reloads entry and services remain registered."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client(
        status={
            "device_mode": "auto",
            "battery_soc": 75,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        assert hass.services.has_service(DOMAIN, "set_passive_mode")


async def test_reconfigure_flow_reloads_and_keeps_services(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reconfigure flow reloads entry and services remain registered."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client(
        status={
            "device_mode": "auto",
            "battery_soc": 75,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        assert hass.services.has_service(DOMAIN, "set_passive_mode")

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": "reconfigure",
                "entry_id": mock_config_entry.entry_id,
            },
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure_confirm"

        device_info = {
            "ip": "192.168.1.200",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus",
            "version": "3.0",
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
            "model": "Venus",
            "firmware": "3.0",
        }

        original_schedule_reload = hass.config_entries.async_schedule_reload
        with (
            patch_manual_connection(device_info=device_info),
            patch.object(
                hass.config_entries,
                "async_schedule_reload",
                wraps=original_schedule_reload,
            ) as mock_schedule_reload,
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={"host": "192.168.1.200", "port": 30000},
            )

            assert result["type"] == FlowResultType.ABORT
            assert result["reason"] == "reconfigure_successful"
            await hass.async_block_till_done()

        mock_schedule_reload.assert_called_once_with(mock_config_entry.entry_id)
        assert (
            hass.config_entries.async_get_entry(mock_config_entry.entry_id).data["host"]
            == "192.168.1.200"
        )
        assert hass.services.has_service(DOMAIN, "set_passive_mode")


async def test_reauth_flow_reloads_and_keeps_services(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reauth flow reloads entry and services remain registered."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client(
        status={
            "device_mode": "auto",
            "battery_soc": 75,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        assert hass.services.has_service(DOMAIN, "set_passive_mode")

        result = await mock_config_entry.start_reauth_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        device_info = {
            "ip": "192.168.1.200",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus",
            "version": "3.0",
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
            "model": "Venus",
            "firmware": "3.0",
        }

        original_reload = hass.config_entries.async_reload

        async def _reload(entry_id: str) -> bool:
            return await original_reload(entry_id)

        with (
            patch_manual_connection(device_info=device_info),
            patch.object(
                hass.config_entries,
                "async_reload",
                AsyncMock(side_effect=_reload),
            ) as mock_reload,
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={"host": "192.168.1.200"},
            )

            assert result["type"] == FlowResultType.ABORT
            assert result["reason"] == "reauth_successful"
            await hass.async_block_till_done()

        assert mock_reload.call_count >= 1
        assert (
            hass.config_entries.async_get_entry(mock_config_entry.entry_id).data["host"]
            == "192.168.1.200"
        )
        assert hass.services.has_service(DOMAIN, "set_passive_mode")


async def test_integration_discovery_reloads_and_keeps_services(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test integration discovery updates IP, reloads entry, and keeps services."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client(
        status={
            "device_mode": "auto",
            "battery_soc": 75,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        assert hass.services.has_service(DOMAIN, "set_passive_mode")

        discovery_info = {
            "ip": "192.168.1.201",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "port": 30000,
        }

        original_schedule_reload = hass.config_entries.async_schedule_reload

        def _schedule_reload(entry_id: str) -> None:
            original_schedule_reload(entry_id)
            hass.async_create_task(hass.config_entries.async_reload(entry_id))

        with patch.object(
            hass.config_entries,
            "async_schedule_reload",
            side_effect=_schedule_reload,
        ) as mock_schedule_reload:
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "integration_discovery"},
                data=discovery_info,
            )

            assert result["type"] == FlowResultType.ABORT
            assert result["reason"] == "already_configured"
            await hass.async_block_till_done()

        mock_schedule_reload.assert_called_once_with(mock_config_entry.entry_id)
        assert (
            hass.config_entries.async_get_entry(mock_config_entry.entry_id).data["host"]
            == "192.168.1.201"
        )
        assert hass.services.has_service(DOMAIN, "set_passive_mode")


async def test_dhcp_discovery_reloads_and_keeps_services(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test DHCP discovery updates IP, reloads entry, and keeps services."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client(
        status={
            "device_mode": "auto",
            "battery_soc": 75,
        }
    )

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        assert hass.services.has_service(DOMAIN, "set_passive_mode")

        discovery_info = type(
            "DhcpInfo",
            (),
            {
                "ip": "192.168.1.202",
                "hostname": "marstek",
                "macaddress": "aabbccddeeff",
            },
        )

        original_schedule_reload = hass.config_entries.async_schedule_reload

        def _schedule_reload(entry_id: str) -> None:
            original_schedule_reload(entry_id)
            hass.async_create_task(hass.config_entries.async_reload(entry_id))

        with patch.object(
            hass.config_entries,
            "async_schedule_reload",
            side_effect=_schedule_reload,
        ) as mock_schedule_reload:
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "dhcp"},
                data=discovery_info,
            )

            assert result["type"] == FlowResultType.ABORT
            assert result["reason"] == "already_configured"
            await hass.async_block_till_done()

        mock_schedule_reload.assert_called_once_with(mock_config_entry.entry_id)
        assert (
            hass.config_entries.async_get_entry(mock_config_entry.entry_id).data["host"]
            == "192.168.1.202"
        )
        assert hass.services.has_service(DOMAIN, "set_passive_mode")

        original_reload = hass.config_entries.async_reload

        async def _reload(entry_id: str) -> bool:
            return await original_reload(entry_id)

        with patch.object(
            hass.config_entries,
            "async_reload",
            AsyncMock(side_effect=_reload),
        ) as mock_reload:
            hass.config_entries.async_update_entry(
                mock_config_entry,
                options={"poll_interval_fast": 31},
            )
            await hass.async_block_till_done()

        mock_reload.assert_called_once_with(mock_config_entry.entry_id)
        assert mock_config_entry.state == ConfigEntryState.LOADED
        assert hass.services.has_service(DOMAIN, "set_passive_mode")


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
        assert hass.services.has_service(DOMAIN, "set_passive_mode")

        # Reload
        await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_config_entry.state == ConfigEntryState.LOADED
        assert (
            hass.states.get("sensor.venus_battery_level") is not None
        )
        assert hass.services.has_service(DOMAIN, "set_passive_mode")


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
        # Services remain registered for the integration lifetime
        assert hass.services.has_service(DOMAIN, "set_passive_mode")


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
