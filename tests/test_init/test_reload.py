"""Reloads from options, reconfigure, reauth and discovery."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek import _async_update_listener
from custom_components.marstek.const import (
    DATA_SUPPRESS_RELOADS,
    DOMAIN,
)
from tests.conftest import (
    create_mock_client,
    patch_manual_connection,
    patch_marstek_integration,
)


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

        # The reload is the update listener's, because the flow changes the
        # entry data; the flow itself no longer schedules a second one.
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
                user_input={"host": "192.168.1.200", "port": 30000},
            )

            assert result["type"] == FlowResultType.ABORT
            assert result["reason"] == "reconfigure_successful"
            await hass.async_block_till_done()

        mock_reload.assert_called_once_with(mock_config_entry.entry_id)
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

        original_reload = hass.config_entries.async_reload

        async def _passthrough_reload(entry_id: str) -> bool:
            return await original_reload(entry_id)

        with (
            patch.object(
                hass.config_entries,
                "async_reload",
                AsyncMock(side_effect=_passthrough_reload),
            ) as mock_reload,
            patch.object(hass.config_entries, "async_schedule_reload") as mock_schedule_reload,
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "integration_discovery"},
                data=discovery_info,
            )

            assert result["type"] == FlowResultType.ABORT
            assert result["reason"] == "already_configured"
            await hass.async_block_till_done()

        # The update listener a loaded entry carries reloads it once. A second,
        # explicitly scheduled reload would set the device up twice.
        mock_reload.assert_called_once_with(mock_config_entry.entry_id)
        mock_schedule_reload.assert_not_called()
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

        original_reload = hass.config_entries.async_reload

        async def _passthrough_reload(entry_id: str) -> bool:
            return await original_reload(entry_id)

        with (
            patch.object(
                hass.config_entries,
                "async_reload",
                AsyncMock(side_effect=_passthrough_reload),
            ) as mock_reload,
            patch.object(hass.config_entries, "async_schedule_reload") as mock_schedule_reload,
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "dhcp"},
                data=discovery_info,
            )

            assert result["type"] == FlowResultType.ABORT
            assert result["reason"] == "already_configured"
            await hass.async_block_till_done()

        # The update listener a loaded entry carries reloads it once. A second,
        # explicitly scheduled reload would set the device up twice.
        mock_reload.assert_called_once_with(mock_config_entry.entry_id)
        mock_schedule_reload.assert_not_called()
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


async def test_reload_entry(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
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
        assert hass.states.get("sensor.venus_battery_level") is not None
        assert hass.services.has_service(DOMAIN, "set_passive_mode")
