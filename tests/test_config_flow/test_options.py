"""The options flow."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    CONF_ACTION_CHARGE_POWER,
    CONF_ACTION_DISCHARGE_POWER,
    CONF_BATTERY_LOW_FOLLOW_CUTOFF,
    CONF_BATTERY_LOW_SOC,
    CONF_CLAMP_NEGATIVE_OFFGRID_POWER,
    CONF_FAILURE_THRESHOLD,
    CONF_PARALLEL_API_REQUESTS,
    CONF_PLUG_ENERGY_IN,
    CONF_PLUG_ENERGY_OUT,
    CONF_POLL_INTERVAL_FAST,
    CONF_POLL_INTERVAL_MEDIUM,
    CONF_POLL_INTERVAL_SLOW,
    CONF_REQUEST_DELAY,
    CONF_REQUEST_TIMEOUT,
    CONF_SHOW_CAPACITY_DASHBOARD,
    CONF_SOCKET_LIMIT,
    DEFAULT_ACTION_CHARGE_POWER,
    DEFAULT_ACTION_DISCHARGE_POWER,
    DEFAULT_BATTERY_LOW_FOLLOW_CUTOFF,
    DEFAULT_BATTERY_LOW_SOC,
    DEFAULT_CLAMP_NEGATIVE_OFFGRID_POWER,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_PARALLEL_API_REQUESTS,
    DEFAULT_POLL_INTERVAL_FAST,
    DEFAULT_POLL_INTERVAL_MEDIUM,
    DEFAULT_POLL_INTERVAL_SLOW,
    DEFAULT_REQUEST_DELAY,
    DEFAULT_REQUEST_TIMEOUT,
)
from custom_components.marstek.helpers.flow_schemas import (
    build_network_schema,
    build_polling_schema,
)
from tests.conftest import (
    create_mock_client,
    patch_marstek_integration,
)


def _options_user_input(
    *,
    parallel_api_requests: bool = DEFAULT_PARALLEL_API_REQUESTS,
    request_delay: float = DEFAULT_REQUEST_DELAY,
    clamp_negative_offgrid_power: bool = DEFAULT_CLAMP_NEGATIVE_OFFGRID_POWER,
    socket_limit: bool = False,
) -> dict[str, Any]:
    """Build the nested options-flow payload."""
    return {
        "polling_settings": {
            CONF_POLL_INTERVAL_FAST: DEFAULT_POLL_INTERVAL_FAST,
            CONF_POLL_INTERVAL_MEDIUM: DEFAULT_POLL_INTERVAL_MEDIUM,
            CONF_POLL_INTERVAL_SLOW: DEFAULT_POLL_INTERVAL_SLOW,
        },
        "network_settings": {
            CONF_PARALLEL_API_REQUESTS: parallel_api_requests,
            CONF_REQUEST_DELAY: request_delay,
            CONF_REQUEST_TIMEOUT: DEFAULT_REQUEST_TIMEOUT,
            CONF_FAILURE_THRESHOLD: DEFAULT_FAILURE_THRESHOLD,
        },
        "battery_settings": {
            CONF_BATTERY_LOW_FOLLOW_CUTOFF: DEFAULT_BATTERY_LOW_FOLLOW_CUTOFF,
            CONF_BATTERY_LOW_SOC: DEFAULT_BATTERY_LOW_SOC,
        },
        "power_settings": {
            CONF_ACTION_CHARGE_POWER: DEFAULT_ACTION_CHARGE_POWER,
            CONF_ACTION_DISCHARGE_POWER: DEFAULT_ACTION_DISCHARGE_POWER,
            CONF_SOCKET_LIMIT: socket_limit,
            CONF_CLAMP_NEGATIVE_OFFGRID_POWER: clamp_negative_offgrid_power,
        },
        "dashboard_settings": {
            CONF_SHOW_CAPACITY_DASHBOARD: False,
        },
        "plug_meter_settings": {},
    }


async def test_options_flow_creates_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test options flow works."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    # Options flow uses sections - provide nested structure
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input=_options_user_input(),
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Verify data is flattened when stored
    assert result["data"][CONF_POLL_INTERVAL_FAST] == DEFAULT_POLL_INTERVAL_FAST
    assert result["data"][CONF_SOCKET_LIMIT] is False
    assert result["data"][CONF_CLAMP_NEGATIVE_OFFGRID_POWER] is False
    assert result["data"][CONF_BATTERY_LOW_FOLLOW_CUTOFF] is True
    assert result["data"][CONF_BATTERY_LOW_SOC] == DEFAULT_BATTERY_LOW_SOC


async def test_options_flow_socket_limit_default_by_model(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test socket limit default is enabled for Venus C/D/E models."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": "Venus C",
        },
    )

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    # Schema now uses sections - find power_settings section and check socket_limit default
    schema = result["data_schema"].schema
    power_section_key = next(
        key for key in schema if getattr(key, "schema", None) == "power_settings"
    )
    # The section value contains a schema - extract it
    power_schema = schema[power_section_key].schema.schema
    socket_key = next(
        key for key in power_schema if getattr(key, "schema", None) == CONF_SOCKET_LIMIT
    )
    assert socket_key.default() is True
    clamp_key = next(
        key
        for key in power_schema
        if getattr(key, "schema", None) == CONF_CLAMP_NEGATIVE_OFFGRID_POWER
    )
    assert clamp_key.default() is False


async def test_options_flow_keeps_request_delay_visible_when_parallel_enabled(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test request_delay field remains visible even when parallel mode is enabled."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            CONF_PARALLEL_API_REQUESTS: True,
            CONF_REQUEST_DELAY: 7.5,
        },
    )

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    schema = result["data_schema"].schema
    network_section_key = next(
        key for key in schema if getattr(key, "schema", None) == "network_settings"
    )
    network_schema = schema[network_section_key].schema.schema
    network_field_names = {getattr(key, "schema", None) for key in network_schema}
    assert CONF_REQUEST_DELAY in network_field_names
    assert CONF_PARALLEL_API_REQUESTS in network_field_names

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input=_options_user_input(
            parallel_api_requests=True,
            request_delay=7.5,
        ),
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_REQUEST_DELAY] == 7.5


async def test_options_flow_stores_clamp_negative_offgrid_power(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test the off-grid clamp option can be enabled and is stored flattened."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input=_options_user_input(clamp_negative_offgrid_power=True),
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CLAMP_NEGATIVE_OFFGRID_POWER] is True


async def test_options_flow_stores_capacity_dashboard(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The sidebar dashboard stays off unless the option is enabled."""
    mock_config_entry.add_to_hass(hass)

    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    schema = result["data_schema"].schema
    dashboard_key = next(
        key for key in schema if getattr(key, "schema", None) == "dashboard_settings"
    )
    dashboard_schema = schema[dashboard_key].schema.schema
    show_key = next(
        key
        for key in dashboard_schema
        if getattr(key, "schema", None) == CONF_SHOW_CAPACITY_DASHBOARD
    )
    assert show_key.default() is False

    user_input = _options_user_input()
    user_input["dashboard_settings"][CONF_SHOW_CAPACITY_DASHBOARD] = True
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SHOW_CAPACITY_DASHBOARD] is True


def _suggested(result: Any, section_name: str, field: str) -> Any:
    """Return the suggested value a form field shows."""
    schema = result["data_schema"].schema
    section_key = next(key for key in schema if getattr(key, "schema", None) == section_name)
    fields = schema[section_key].schema.schema
    key = next(key for key in fields if getattr(key, "schema", None) == field)
    return (key.description or {}).get("suggested_value")


async def test_options_flow_links_and_unlinks_an_energy_meter(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Both meter fields are stored, shown again, and emptied to unlink."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
        assert _suggested(result, "plug_meter_settings", CONF_PLUG_ENERGY_IN) is None
        user_input = _options_user_input()
        user_input["plug_meter_settings"] = {
            CONF_PLUG_ENERGY_IN: "sensor.plug_energy_import",
            CONF_PLUG_ENERGY_OUT: "sensor.plug_energy_export",
        }
        result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_PLUG_ENERGY_IN] == "sensor.plug_energy_import"
        assert result["data"][CONF_PLUG_ENERGY_OUT] == "sensor.plug_energy_export"
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
        assert (
            _suggested(result, "plug_meter_settings", CONF_PLUG_ENERGY_OUT)
            == "sensor.plug_energy_export"
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _options_user_input()
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert CONF_PLUG_ENERGY_IN not in result["data"]
        await hass.async_block_till_done()


async def test_options_flow_rejects_an_incomplete_or_doubled_meter(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """One field alone, or the same entity twice, keeps the form open with the input."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    mock_config_entry_options = {
        **mock_config_entry.options,
        CONF_PLUG_ENERGY_IN: "sensor.old_import",
        CONF_PLUG_ENERGY_OUT: "sensor.old_export",
    }
    hass.config_entries.async_update_entry(mock_config_entry, options=mock_config_entry_options)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    user_input = _options_user_input()
    user_input["plug_meter_settings"] = {CONF_PLUG_ENERGY_IN: "sensor.plug_energy_import"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "plug_meter_incomplete"}
    assert (
        _suggested(result, "plug_meter_settings", CONF_PLUG_ENERGY_IN)
        == "sensor.plug_energy_import"
    )
    # The emptied field stays empty instead of showing the stored entity again.
    assert _suggested(result, "plug_meter_settings", CONF_PLUG_ENERGY_OUT) is None

    user_input["plug_meter_settings"] = {
        CONF_PLUG_ENERGY_IN: "sensor.plug_energy_import",
        CONF_PLUG_ENERGY_OUT: "sensor.plug_energy_import",
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "plug_meter_same_entity"}


async def test_options_flow_stores_battery_low_level(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A fixed low-battery level is stored when the discharge cutoff is not followed."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    user_input = _options_user_input()
    user_input["battery_settings"] = {
        CONF_BATTERY_LOW_FOLLOW_CUTOFF: False,
        CONF_BATTERY_LOW_SOC: 12,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BATTERY_LOW_FOLLOW_CUTOFF] is False
    assert result["data"][CONF_BATTERY_LOW_SOC] == 12


def _selector_config(schema: vol.Schema, field: str) -> dict[str, Any]:
    """Return the number-selector config for one options field."""
    key = next(marker for marker in schema.schema if getattr(marker, "schema", None) == field)
    config = schema.schema[key].config
    assert isinstance(config, dict)
    return config


def test_every_polling_interval_accepts_five_seconds() -> None:
    """Fast, medium and slow can be 5 seconds. Request delay keeps its own floor."""
    polling = build_polling_schema(
        current_fast=DEFAULT_POLL_INTERVAL_FAST,
        current_medium=DEFAULT_POLL_INTERVAL_MEDIUM,
        current_slow=DEFAULT_POLL_INTERVAL_SLOW,
    )
    expected = {
        CONF_POLL_INTERVAL_FAST: (5, 300, DEFAULT_POLL_INTERVAL_FAST),
        CONF_POLL_INTERVAL_MEDIUM: (5, 86400, DEFAULT_POLL_INTERVAL_MEDIUM),
        CONF_POLL_INTERVAL_SLOW: (5, 86400, DEFAULT_POLL_INTERVAL_SLOW),
    }
    for field, (lower, upper, default) in expected.items():
        config = _selector_config(polling, field)
        assert config["min"] == lower
        assert config["max"] == upper
        assert config["step"] == 5
        marker = next(item for item in polling.schema if getattr(item, "schema", None) == field)
        assert marker.default() == default

    accepted = polling(
        {
            CONF_POLL_INTERVAL_FAST: 5,
            CONF_POLL_INTERVAL_MEDIUM: 5,
            CONF_POLL_INTERVAL_SLOW: 5,
        }
    )
    assert accepted[CONF_POLL_INTERVAL_FAST] == 5
    assert accepted[CONF_POLL_INTERVAL_MEDIUM] == 5
    assert accepted[CONF_POLL_INTERVAL_SLOW] == 5

    for field in expected:
        too_low = {
            CONF_POLL_INTERVAL_FAST: 5,
            CONF_POLL_INTERVAL_MEDIUM: 5,
            CONF_POLL_INTERVAL_SLOW: 5,
            field: 4,
        }
        with pytest.raises(vol.Invalid):
            polling(too_low)

    network = build_network_schema(
        current_parallel_requests=DEFAULT_PARALLEL_API_REQUESTS,
        current_delay=DEFAULT_REQUEST_DELAY,
        current_timeout=DEFAULT_REQUEST_TIMEOUT,
        current_failure_threshold=DEFAULT_FAILURE_THRESHOLD,
    )
    delay = _selector_config(network, CONF_REQUEST_DELAY)
    assert delay["min"] == 1.0
    assert delay["max"] == 30.0


async def test_options_flow_saves_five_second_polls_and_uses_them(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Saved 5 second intervals reload onto the coordinator and leave request spacing."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = mock_config_entry.runtime_data.coordinator
        assert coordinator.update_interval == timedelta(seconds=DEFAULT_POLL_INTERVAL_FAST)
        assert coordinator._get_medium_interval() == DEFAULT_POLL_INTERVAL_MEDIUM
        assert coordinator._get_slow_interval() == DEFAULT_POLL_INTERVAL_SLOW

        result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
        for field in (
            CONF_POLL_INTERVAL_FAST,
            CONF_POLL_INTERVAL_MEDIUM,
            CONF_POLL_INTERVAL_SLOW,
        ):
            rejected = _options_user_input()
            rejected["polling_settings"][field] = 4
            with pytest.raises(vol.Invalid):
                await hass.config_entries.options.async_configure(result["flow_id"], rejected)
            assert field not in mock_config_entry.options

        result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
        accepted = _options_user_input()
        accepted["polling_settings"][CONF_POLL_INTERVAL_FAST] = 5
        accepted["polling_settings"][CONF_POLL_INTERVAL_MEDIUM] = 5
        accepted["polling_settings"][CONF_POLL_INTERVAL_SLOW] = 5
        result = await hass.config_entries.options.async_configure(result["flow_id"], accepted)
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_POLL_INTERVAL_FAST] == 5
    assert result["data"][CONF_POLL_INTERVAL_MEDIUM] == 5
    assert result["data"][CONF_POLL_INTERVAL_SLOW] == 5
    assert result["data"][CONF_REQUEST_DELAY] == DEFAULT_REQUEST_DELAY
    assert result["data"][CONF_PARALLEL_API_REQUESTS] is DEFAULT_PARALLEL_API_REQUESTS
    coordinator = mock_config_entry.runtime_data.coordinator
    assert coordinator.update_interval == timedelta(seconds=5)
    assert coordinator._get_medium_interval() == 5
    assert coordinator._get_slow_interval() == 5
    assert coordinator._get_request_delay() == DEFAULT_REQUEST_DELAY
