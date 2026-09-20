"""Helpers shared by the test_sensor tests."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    DOMAIN,
)
from custom_components.marstek.device_info import get_device_identifier


def _as_meter_client(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Make the entry a firmware that answers EM.GetStatus.

    The shared fixture is an unknown family, and the coordinator does not
    send EM.GetStatus to one, so EM entities are not created for it either.
    """
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, "device_type": "VenusE 3.0", "version": 150},
    )


def _em_state(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str | None:
    """Return the state of the EM sensor with this description key."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{get_device_identifier(entry.data)}_{key}"
    )
    assert entity_id is not None, f"{key} sensor was not created"
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state
