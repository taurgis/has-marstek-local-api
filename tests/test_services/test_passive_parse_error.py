"""Set passive mode reports the device's JSON-RPC error."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.helpers.device_lookup import async_lookup_device_by_identifier
from custom_components.marstek.services import (
    ATTR_DEVICE_ID,
    ATTR_POWER,
    SERVICE_SET_PASSIVE_MODE,
)
from tests.conftest import create_mock_client, patch_marstek_integration

DEVICE_IDENTIFIER = format_mac("AA:BB:CC:DD:EE:FF")
_PARSE_ERROR = {"id": 0, "error": {"code": -32700, "message": "Parse error", "data": 401}}


@pytest.mark.asyncio
async def test_passive_mode_surfaces_the_device_parse_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A delivered parse error is not turned into a successful service call."""
    mock_config_entry.add_to_hass(hass)
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        device = async_lookup_device_by_identifier(dr.async_get(hass), (DOMAIN, DEVICE_IDENTIFIER))
        assert device is not None
        client.send_request = AsyncMock(return_value=_PARSE_ERROR)
        with (
            patch("custom_components.marstek.helpers.command_retry.asyncio.sleep", AsyncMock()),
            pytest.raises(HomeAssistantError) as err,
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_PASSIVE_MODE,
                {ATTR_DEVICE_ID: device.id, ATTR_POWER: 0},
                blocking=True,
            )

    assert err.value.translation_key == "command_failed"
    assert err.value.translation_placeholders is not None
    assert err.value.translation_placeholders["error"] == (
        "JSON-RPC error -32700: Parse error (data 401)"
    )


@pytest.mark.asyncio
async def test_passive_mode_is_refused_on_vnsem_301_without_traffic(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """PMU 301 has no Passive branch, so the service never sends one."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "device_type": "VNSEM-0", "version": 301},
    )
    client = create_mock_client()
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        device = async_lookup_device_by_identifier(dr.async_get(hass), (DOMAIN, DEVICE_IDENTIFIER))
        assert device is not None
        client.send_request = AsyncMock(return_value=_PARSE_ERROR)
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_PASSIVE_MODE,
                {ATTR_DEVICE_ID: device.id, ATTR_POWER: 0},
                blocking=True,
            )

    assert err.value.translation_key == "mode_not_supported"
    client.send_request.assert_not_called()
