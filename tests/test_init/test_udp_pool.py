"""Sharing and releasing the pooled UDP client per port."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import (
    issue_registry as ir,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    DATA_ENTRY_BIND_PORTS,
    DATA_UDP_CLIENTS,
    DOMAIN,
)
from tests.conftest import (
    create_mock_client,
    create_mock_scanner,
    patch_marstek_integration,
)


async def test_setup_binds_shared_udp_client_to_entry_port(
    hass: HomeAssistant,
) -> None:
    """Test the shared UDP client binds to the config entry Open API port."""
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
    scanner = create_mock_scanner()

    with (
        patch("custom_components.marstek.scanner.MarstekScanner._scanner", None),
        patch("custom_components.marstek.MarstekUDPClient", return_value=client) as mock_udp,
        patch("custom_components.marstek.pymarstek.MarstekUDPClient", return_value=client),
        patch(
            "custom_components.marstek.scanner.MarstekScanner.async_get",
            return_value=scanner,
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        mock_udp.assert_called_once_with(port=30003, bind_port=30003)
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_loopback_uses_ephemeral_bind_port(
    hass: HomeAssistant,
) -> None:
    """Test loopback devices bind an ephemeral port so they do not collide locally."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            "host": "127.0.0.1",
            "port": 30000,
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
    scanner = create_mock_scanner()

    with (
        patch("custom_components.marstek.scanner.MarstekScanner._scanner", None),
        patch("custom_components.marstek.MarstekUDPClient", return_value=client) as mock_udp,
        patch("custom_components.marstek.pymarstek.MarstekUDPClient", return_value=client),
        patch(
            "custom_components.marstek.scanner.MarstekScanner.async_get",
            return_value=scanner,
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        mock_udp.assert_called_once_with(port=30000, bind_port=0)
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


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
        assert DATA_UDP_CLIENTS in hass.data[DOMAIN]

        # Store reference to the shared client
        shared_client = hass.data[DOMAIN][DATA_UDP_CLIENTS][30000]

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
        assert hass.data[DOMAIN][DATA_UDP_CLIENTS][30000] is shared_client

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
        shared_client = hass.data[DOMAIN][DATA_UDP_CLIENTS][30000]

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
        assert DATA_UDP_CLIENTS in hass.data[DOMAIN]
        assert hass.data[DOMAIN][DATA_UDP_CLIENTS][30000] is shared_client

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
        assert DATA_UDP_CLIENTS in hass.data[DOMAIN]

        # Unload last entry
        await hass.config_entries.async_unload(second_entry.entry_id)
        await hass.async_block_till_done()

        # UDP client should be cleaned up (either key removed or no client in it)
        marstek_data = hass.data.get(DOMAIN)
        if marstek_data is not None:
            assert DATA_UDP_CLIENTS not in marstek_data
        # Services remain registered for the integration lifetime
        assert hass.services.has_service(DOMAIN, "set_passive_mode")


async def test_entries_on_different_ports_use_separate_udp_clients(
    hass: HomeAssistant,
) -> None:
    """Test that devices on distinct Open API ports get distinct UDP sockets."""
    first_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            "host": "1.2.3.4",
            "port": 30000,
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus",
            "version": 3,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
    )
    second_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Custom Port Device",
        unique_id="bb:cc:dd:ee:ff:00",
        data={
            "host": "5.6.7.8",
            "port": 30003,
            "ble_mac": "BB:CC:DD:EE:FF:00",
            "device_type": "Venus v3",
            "version": 145,
        },
    )
    first_entry.add_to_hass(hass)

    def _make_client(*_args: object, **_kwargs: object) -> object:
        return create_mock_client(
            status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
        )

    scanner = create_mock_scanner()
    with (
        patch("custom_components.marstek.scanner.MarstekScanner._scanner", None),
        patch("custom_components.marstek.MarstekUDPClient", side_effect=_make_client) as mock_udp,
        patch(
            "custom_components.marstek.pymarstek.MarstekUDPClient",
            side_effect=_make_client,
        ),
        patch(
            "custom_components.marstek.scanner.MarstekScanner.async_get",
            return_value=scanner,
        ),
    ):
        await hass.config_entries.async_setup(first_entry.entry_id)
        await hass.async_block_till_done()

        second_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(second_entry.entry_id)
        await hass.async_block_till_done()

        assert first_entry.state == ConfigEntryState.LOADED
        assert second_entry.state == ConfigEntryState.LOADED
        pool = hass.data[DOMAIN][DATA_UDP_CLIENTS]
        assert set(pool) == {30000, 30003}
        first_client = pool[30000]
        second_client = pool[30003]
        assert first_client is not second_client
        mock_udp.assert_any_call(port=30000, bind_port=30000)
        mock_udp.assert_any_call(port=30003, bind_port=30003)

        await hass.config_entries.async_unload(second_entry.entry_id)
        await hass.async_block_till_done()

        assert 30003 not in hass.data[DOMAIN][DATA_UDP_CLIENTS]
        assert hass.data[DOMAIN][DATA_UDP_CLIENTS][30000] is first_client
        second_client.async_cleanup.assert_awaited()

        await hass.config_entries.async_unload(first_entry.entry_id)
        await hass.async_block_till_done()


async def test_remove_setup_retry_entry_releases_udp_client(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Deleting a retrying entry must release the socket HA never unloaded."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client(send_request_error=TimeoutError("timeout"))

    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert mock_config_entry.state == ConfigEntryState.SETUP_RETRY
        assert DATA_UDP_CLIENTS in hass.data.get(DOMAIN, {})

        await hass.config_entries.async_remove(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    client.async_cleanup.assert_awaited()
    assert DOMAIN not in hass.data or DATA_UDP_CLIENTS not in hass.data.get(DOMAIN, {})


async def test_setup_error_after_lease_releases_udp_client(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """SETUP_ERROR after the socket is leased must not keep the bind port."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client()

    with (
        patch_marstek_integration(client=client),
        patch(
            "custom_components.marstek._async_verify_device_connection",
            AsyncMock(side_effect=ConfigEntryError("broken")),
        ),
    ):
        assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.SETUP_ERROR
    client.async_cleanup.assert_awaited()
    domain_data = hass.data.get(DOMAIN, {})
    assert not domain_data.get(DATA_UDP_CLIENTS)
    assert not domain_data.get(DATA_ENTRY_BIND_PORTS)


async def test_failed_unload_keeps_reset_prone_protection(
    hass: HomeAssistant,
) -> None:
    """A failed platform unload must not drop firmware-reset protections."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            "host": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "VenusE 3.0",
            "version": 147,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)
    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        issue_registry = ir.async_get(hass)
        issue_id = f"openapi_reset_prone_{entry.entry_id}"
        assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=False),
        ):
            unloaded = await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()

        assert unloaded is False
        assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None
        client.clear_openapi_reset_prone.assert_not_called()

        # FAILED_UNLOAD cannot be unloaded again; stop the coordinator timer.
        await entry.runtime_data.coordinator.async_shutdown()
