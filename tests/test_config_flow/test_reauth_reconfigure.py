"""Reauth and reconfigure flows."""

from __future__ import annotations

from unittest.mock import patch

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    DOMAIN,
)
from tests.conftest import (
    patch_manual_connection,
    patch_marstek_integration,
)

from ._helpers import (
    _get_schema_field_default,
)


async def test_reauth_flow_success(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Test successful reauth flow."""
    mock_config_entry.add_to_hass(hass)

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

    with patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.200"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert hass.config_entries.async_entries(DOMAIN)[0].data["host"] == "192.168.1.200"


async def test_reauth_confirm_form_snapshot(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, snapshot
) -> None:
    """Test reauth confirm form structure snapshot."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    schema = result["data_schema"].schema
    fields: dict[str, dict[str, object]] = {}
    for key in schema:
        default = getattr(key, "default", None)
        if default is vol.UNDEFINED:
            default = None
        fields[str(key)] = {
            "required": getattr(key, "required", False),
            "default": default,
        }

    snapshot_data = {
        "type": result["type"],
        "step_id": result["step_id"],
        "errors": result.get("errors"),
        "description_placeholders": result.get("description_placeholders"),
        "fields": fields,
    }

    assert snapshot_data == snapshot


async def test_reauth_flow_cannot_connect(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reauth flow with connection failure."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch_manual_connection(error=TimeoutError("Connection timeout")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.200"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"]["base"] == "cannot_connect"


async def test_reauth_flow_device_returns_none(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reauth flow when device returns None."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM

    with patch_manual_connection(device_info=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.200"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_reauth_flow_empty_host(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reauth flow with empty host shows error."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    # Submit empty host - should show form with error
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"host": ""},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_reconfigure_flow_success(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test successful reconfigure flow."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": mock_config_entry.entry_id},
        data=None,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"

    device_info = {
        "ip": "192.168.1.201",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus",
        "version": "3.0",
        "wifi_name": "marstek",
        "wifi_mac": "11:22:33:44:55:66",
        "model": "Venus",
        "firmware": "3.0",
    }

    with patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.201", "port": 30000},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    updated_entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert updated_entry.data["host"] == "192.168.1.201"
    assert updated_entry.data["port"] == 30000
    assert updated_entry.data["version"] == "3.0"
    assert updated_entry.data["device_type"] == "Venus"


async def test_reconfigure_wifi_unique_id_matches_ble_device(
    hass: HomeAssistant,
) -> None:
    """Reconfigure must not fail when the entry unique_id is the Wi-Fi MAC."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="11:22:33:44:55:66",
        data={
            "host": "1.2.3.4",
            "wifi_mac": "11:22:33:44:55:66",
            "device_type": "Venus C",
            "version": 153,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
        data=None,
    )
    assert result["step_id"] == "reconfigure_confirm"

    device_info = {
        "ip": "192.168.1.201",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus C",
        "version": 153,
        "wifi_mac": "11:22:33:44:55:66",
    }

    with patch_manual_connection(device_info=device_info):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.201", "port": 30000},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    updated = hass.config_entries.async_entries(DOMAIN)[0]
    assert updated.unique_id == "11:22:33:44:55:66"
    assert updated.data["host"] == "192.168.1.201"


async def test_reconfigure_unchanged_entry_still_schedules_a_reload(
    hass: HomeAssistant,
) -> None:
    """Resubmitting the same host means "retry this device", so reload it.

    The reload is scheduled by the entry's update listener when the data
    changes. An unchanged entry never reaches that listener, so the flow has
    to schedule the reload itself.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="AA:BB:CC:DD:EE:FF",
        data={
            "host": "192.168.1.201",
            "port": 30000,
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus C",
            "version": 153,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
        data=None,
    )

    device_info = {
        "ip": "192.168.1.201",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus C",
        "version": 153,
    }

    with (
        patch_manual_connection(device_info=device_info),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_schedule_reload"
        ) as schedule_reload,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.201", "port": 30000},
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    schedule_reload.assert_called_once_with(entry.entry_id)


async def test_reconfigure_changed_entry_leaves_the_reload_to_the_listener(
    hass: HomeAssistant,
) -> None:
    """A changed entry must not be reloaded twice.

    async_update_entry notifies the update listener, which reloads. Asking
    Home Assistant to schedule a second reload is what it reports as
    deprecated and stops honouring in 2026.12.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="AA:BB:CC:DD:EE:FF",
        data={
            "host": "192.168.1.200",
            "port": 30000,
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus C",
            "version": 153,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
        data=None,
    )

    device_info = {
        "ip": "192.168.1.201",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus C",
        "version": 153,
    }

    with (
        patch_marstek_integration(),
        patch_manual_connection(device_info=device_info),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        with patch(
            "homeassistant.config_entries.ConfigEntries.async_schedule_reload"
        ) as schedule_reload:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={"host": "192.168.1.201", "port": 30000},
            )
            await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert hass.config_entries.async_entries(DOMAIN)[0].data["host"] == ("192.168.1.201")
    schedule_reload.assert_not_called()


async def test_reconfigure_unloaded_entry_schedules_the_reload(
    hass: HomeAssistant,
) -> None:
    """An entry that is not loaded carries no update listener.

    Nothing would pick the corrected host up until Home Assistant's own
    setup-retry backoff came round, which grows to ten minutes, so the flow
    has to ask for the reload itself.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="AA:BB:CC:DD:EE:FF",
        data={
            "host": "192.168.1.200",
            "port": 30000,
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus C",
            "version": 153,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
        data=None,
    )

    device_info = {
        "ip": "192.168.1.201",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus C",
        "version": 153,
    }

    with (
        patch_manual_connection(device_info=device_info),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_schedule_reload"
        ) as schedule_reload,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.201", "port": 30000},
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.data["host"] == "192.168.1.201"
    schedule_reload.assert_called_once_with(entry.entry_id)


async def test_reconfigure_confirm_form_snapshot(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, snapshot
) -> None:
    """Test reconfigure confirm form structure snapshot."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": mock_config_entry.entry_id},
        data=None,
    )
    assert result["step_id"] == "reconfigure_confirm"

    schema = result["data_schema"].schema
    fields: dict[str, dict[str, object]] = {}
    for key in schema:
        default = getattr(key, "default", None)
        if default is vol.UNDEFINED:
            default = None
        fields[str(key)] = {
            "required": getattr(key, "required", False),
            "default": default,
        }

    snapshot_data = {
        "type": result["type"],
        "step_id": result["step_id"],
        "errors": result.get("errors"),
        "description_placeholders": result.get("description_placeholders"),
        "fields": fields,
    }

    assert snapshot_data == snapshot


async def test_reconfigure_flow_cannot_connect(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reconfigure flow with connection failure."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": mock_config_entry.entry_id},
        data=None,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"

    with patch_manual_connection(error=TimeoutError("Connection timeout")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.201", "port": 30000},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"
    assert result["errors"]["base"] == "cannot_connect"


async def test_reconfigure_flow_keeps_custom_port_after_failure(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reconfigure form keeps custom port on failed reconnect attempt."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": mock_config_entry.entry_id},
        data=None,
    )
    assert result["step_id"] == "reconfigure_confirm"

    with patch_manual_connection(error=TimeoutError("Connection timeout")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.201", "port": 30030},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"
    assert result["errors"]["base"] == "cannot_connect"
    assert _get_schema_field_default(result, CONF_PORT) == 30030


async def test_reconfigure_flow_empty_host(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reconfigure flow with empty host shows error."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": mock_config_entry.entry_id},
        data=None,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"

    # Submit empty host - should show form with error
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"host": "", "port": 30000},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_reconfigure_flow_device_returns_none(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reconfigure flow when device returns None shows error."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": mock_config_entry.entry_id},
        data=None,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"

    with patch_manual_connection(device_info=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.1.201", "port": 30000},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"
