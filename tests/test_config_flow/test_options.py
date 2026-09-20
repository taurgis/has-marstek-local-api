"""The options flow."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    CONF_ACTION_CHARGE_POWER,
    CONF_ACTION_DISCHARGE_POWER,
    CONF_FAILURE_THRESHOLD,
    CONF_PARALLEL_API_REQUESTS,
    CONF_POLL_INTERVAL_FAST,
    CONF_POLL_INTERVAL_MEDIUM,
    CONF_POLL_INTERVAL_SLOW,
    CONF_REQUEST_DELAY,
    CONF_REQUEST_TIMEOUT,
    CONF_SOCKET_LIMIT,
    DEFAULT_ACTION_CHARGE_POWER,
    DEFAULT_ACTION_DISCHARGE_POWER,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_PARALLEL_API_REQUESTS,
    DEFAULT_POLL_INTERVAL_FAST,
    DEFAULT_POLL_INTERVAL_MEDIUM,
    DEFAULT_POLL_INTERVAL_SLOW,
    DEFAULT_REQUEST_DELAY,
    DEFAULT_REQUEST_TIMEOUT,
)
from tests.conftest import (
    create_mock_client,
    patch_marstek_integration,
)


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
        user_input={
            "polling_settings": {
                CONF_POLL_INTERVAL_FAST: DEFAULT_POLL_INTERVAL_FAST,
                CONF_POLL_INTERVAL_MEDIUM: DEFAULT_POLL_INTERVAL_MEDIUM,
                CONF_POLL_INTERVAL_SLOW: DEFAULT_POLL_INTERVAL_SLOW,
            },
            "network_settings": {
                CONF_PARALLEL_API_REQUESTS: DEFAULT_PARALLEL_API_REQUESTS,
                CONF_REQUEST_DELAY: DEFAULT_REQUEST_DELAY,
                CONF_REQUEST_TIMEOUT: DEFAULT_REQUEST_TIMEOUT,
                CONF_FAILURE_THRESHOLD: DEFAULT_FAILURE_THRESHOLD,
            },
            "power_settings": {
                CONF_ACTION_CHARGE_POWER: DEFAULT_ACTION_CHARGE_POWER,
                CONF_ACTION_DISCHARGE_POWER: DEFAULT_ACTION_DISCHARGE_POWER,
                CONF_SOCKET_LIMIT: False,
            },
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Verify data is flattened when stored
    assert result["data"][CONF_POLL_INTERVAL_FAST] == DEFAULT_POLL_INTERVAL_FAST
    assert result["data"][CONF_SOCKET_LIMIT] is False


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
        user_input={
            "polling_settings": {
                CONF_POLL_INTERVAL_FAST: DEFAULT_POLL_INTERVAL_FAST,
                CONF_POLL_INTERVAL_MEDIUM: DEFAULT_POLL_INTERVAL_MEDIUM,
                CONF_POLL_INTERVAL_SLOW: DEFAULT_POLL_INTERVAL_SLOW,
            },
            "network_settings": {
                CONF_PARALLEL_API_REQUESTS: True,
                CONF_REQUEST_DELAY: 7.5,
                CONF_REQUEST_TIMEOUT: DEFAULT_REQUEST_TIMEOUT,
                CONF_FAILURE_THRESHOLD: DEFAULT_FAILURE_THRESHOLD,
            },
            "power_settings": {
                CONF_ACTION_CHARGE_POWER: DEFAULT_ACTION_CHARGE_POWER,
                CONF_ACTION_DISCHARGE_POWER: DEFAULT_ACTION_DISCHARGE_POWER,
                CONF_SOCKET_LIMIT: False,
            },
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_REQUEST_DELAY] == 7.5
