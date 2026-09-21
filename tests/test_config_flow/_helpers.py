"""Helpers shared by the test_config_flow tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import voluptuous as vol
from homeassistant.core import HomeAssistant

from custom_components.marstek.helpers.domain_data import domain_data


def _get_schema_field_default(result: dict[str, Any], field_name: str) -> Any:
    """Extract default value for a field in a flow form schema."""
    schema = result["data_schema"].schema
    key = next(key for key in schema if getattr(key, "schema", None) == field_name)
    default = getattr(key, "default", vol.UNDEFINED)
    if default is vol.UNDEFINED:
        return None
    return default() if callable(default) else default


def _pooled_udp_client(hass: HomeAssistant) -> MagicMock:
    """Install a pooled UDP client so config-flow pause/resume is observable."""
    client = MagicMock()
    client.async_pause_receiver = AsyncMock()
    client.async_resume_receiver = AsyncMock()
    domain_data(hass).udp_clients[30000] = client
    return client
