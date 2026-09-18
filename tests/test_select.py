"""Tests for Marstek select platform."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.marstek.const import (
    DOMAIN,
    MODE_AI,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_PASSIVE,
    MODE_UPS,
    SELECTABLE_BASE_MODES,
)
from custom_components.marstek.firmware_profile import resolve_firmware_profile
from custom_components.marstek.helpers.select_descriptions import SELECT_ENTITIES
from custom_components.marstek.select import MarstekOperatingModeSelect, async_setup_entry


def _mock_client(status=None, setup_error=None):
    """Create a mock MarstekUDPClient factory fixture."""
    client = MagicMock()
    client.async_setup = AsyncMock(side_effect=setup_error)
    client.async_cleanup = AsyncMock(return_value=None)
    client.send_request = AsyncMock(return_value={"result": {}})
    client.is_polling_paused = MagicMock(return_value=False)
    client.pause_polling = AsyncMock(return_value=None)
    client.resume_polling = AsyncMock(return_value=None)
    if isinstance(status, Exception):
        client.get_device_status = AsyncMock(side_effect=status)
    else:
        default_status = {
            "battery_soc": 55,
            "pv1_power": 100,
            "device_mode": "auto",
        }
        client.get_device_status = AsyncMock(
            return_value=status if status is not None else default_status
        )
    return client


def _mock_scanner():
    """Create a mock MarstekScanner."""
    scanner = MagicMock()
    scanner.async_setup = AsyncMock(return_value=None)
    scanner.async_unload = AsyncMock(return_value=None)
    return scanner


@contextmanager
def _patch_all(client=None, scanner=None):
    """Patch MarstekUDPClient and MarstekScanner for tests."""
    client = client or _mock_client()
    scanner = scanner or _mock_scanner()
    # Reset scanner singleton to ensure fresh mock
    with (
        patch("custom_components.marstek.scanner.MarstekScanner._scanner", None),
        patch("custom_components.marstek.MarstekUDPClient", return_value=client),
        patch(
            "custom_components.marstek.pymarstek.MarstekUDPClient", return_value=client
        ),
        patch(
            "custom_components.marstek.scanner.MarstekScanner.async_get",
            return_value=scanner,
        ),
    ):
        yield client, scanner


async def test_select_entity_created(hass: HomeAssistant, mock_config_entry):
    """Test select entity is created."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "battery_soc": 55,
        "device_mode": "auto",
    }

    client = _mock_client(status=status)
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.LOADED
    state = hass.states.get("select.venus_operating_mode")
    assert state is not None
    assert state.state == "auto"


async def test_select_entity_options(hass: HomeAssistant, mock_config_entry):
    """Test select entity has correct options."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "battery_soc": 55,
        "device_mode": "auto",
    }

    client = _mock_client(status=status)
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("select.venus_operating_mode")
    assert state is not None
    # Check options attribute
    options = state.attributes.get("options")
    assert options is not None
    assert options == SELECTABLE_BASE_MODES
    assert MODE_UPS not in options


async def test_select_setup_missing_udp_client(
    hass: HomeAssistant, mock_config_entry, caplog
) -> None:
    """Test select setup handles missing UDP client."""
    hass.data.pop(DOMAIN, None)
    mock_config_entry.add_to_hass(hass)

    device_info = {
        **mock_config_entry.data,
        "ip": mock_config_entry.data["host"],
    }
    coordinator = MagicMock()
    coordinator.udp_client = None
    mock_config_entry.runtime_data = SimpleNamespace(
        coordinator=coordinator,
        device_info=device_info,
    )

    async_add_entities = MagicMock()

    caplog.set_level(logging.ERROR)
    await async_setup_entry(hass, mock_config_entry, async_add_entities)

    assert "UDP client not found for select entity setup" in caplog.text
    async_add_entities.assert_not_called()


@pytest.mark.parametrize(
    "mode,expected_config_key",
    [
        (MODE_AUTO, "auto_cfg"),
        (MODE_AI, "ai_cfg"),
    ],
)
async def test_select_mode_sends_command(
    hass: HomeAssistant, mock_config_entry, mode, expected_config_key
):
    """Test selecting a mode sends the correct command."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "battery_soc": 55,
        "device_mode": "auto",
    }

    client = _mock_client(status=status)
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Select a new mode
        await hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": "select.venus_operating_mode",
                "option": mode,
            },
            blocking=True,
        )

        # Verify command was sent
        assert client.pause_polling.call_count >= 1
        assert client.send_request.call_count >= 1
        assert client.resume_polling.call_count >= 1

        # Check the command payload contains the expected config
        call_args = client.send_request.call_args
        command = call_args[0][0] if call_args[0] else call_args.kwargs.get("command")
        # Verify the mode is in the command (JSON string)
        assert mode in str(command) or expected_config_key in str(command)


async def test_select_mode_command_failure_retries(
    hass: HomeAssistant, mock_config_entry
):
    """Test selecting mode retries on failure."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "battery_soc": 55,
        "device_mode": "auto",
    }

    client = _mock_client(status=status)
    # Setup succeeds (first call), then 2 failures + 1 success for retries
    client.send_request = AsyncMock(
        side_effect=[
            {"result": {}},  # Setup call succeeds
            TimeoutError("timeout"),  # First service attempt fails
            TimeoutError("timeout"),  # Second attempt fails
            {"result": {}},  # Third attempt succeeds
        ]
    )

    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Use AUTO mode since PASSIVE is blocked at select level
        await hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": "select.venus_operating_mode",
                "option": MODE_AUTO,
            },
            blocking=True,
        )

        # Should have called: 1 setup + 3 retries = 4 total
        assert client.send_request.call_count == 4


async def test_select_mode_all_retries_fail(hass: HomeAssistant, mock_config_entry):
    """Test selecting mode raises error when all retries fail."""
    mock_config_entry.add_to_hass(hass)

    status = {
        "battery_soc": 55,
        "device_mode": "auto",
    }

    call_count = 0

    async def send_request_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:  # First call is during setup
            return {"result": {}}
        raise TimeoutError("timeout")  # All service calls fail

    client = _mock_client(status=status)
    client.send_request = AsyncMock(side_effect=send_request_side_effect)

    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError, match="mode_change_failed|Failed to set"):
            await hass.services.async_call(
                "select",
                "select_option",
                {
                    "entity_id": "select.venus_operating_mode",
                    "option": MODE_AI,
                },
                blocking=True,
            )


async def test_select_entity_created_with_valid_data(
    hass: HomeAssistant, mock_config_entry
):
    """Test select entity is created with valid data."""
    mock_config_entry.add_to_hass(hass)

    # Return data with valid device_mode
    client = _mock_client(status={"battery_soc": 55, "device_mode": MODE_MANUAL})
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get("select.venus_operating_mode")
        assert state is not None
        assert state.state == MODE_MANUAL


async def test_select_invalid_mode(
    hass: HomeAssistant, mock_config_entry
):
    """Test select raises error for invalid operating mode."""
    mock_config_entry.add_to_hass(hass)

    client = _mock_client(status={"battery_soc": 55, "device_mode": MODE_AUTO})
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError, match="invalid_mode"):
            await hass.services.async_call(
                "select",
                "select_option",
                {
                    "entity_id": "select.venus_operating_mode",
                    "option": "invalid_mode_option",
                },
                blocking=True,
            )


@pytest.mark.parametrize(
    "mode,expected_message_part",
    [
        (MODE_PASSIVE, "Passive mode requires power and duration"),
        (MODE_MANUAL, "Manual mode requires schedule configuration"),
    ],
)
async def test_select_passive_manual_blocked(
    hass: HomeAssistant, mock_config_entry, mode, expected_message_part
):
    """Test selecting Passive/Manual modes raises error directing user to services."""
    mock_config_entry.add_to_hass(hass)

    client = _mock_client(status={"battery_soc": 55, "device_mode": MODE_AUTO})
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError, match=expected_message_part):
            await hass.services.async_call(
                "select",
                "select_option",
                {
                    "entity_id": "select.venus_operating_mode",
                    "option": mode,
                },
                blocking=True,
            )


async def test_select_no_host_configured() -> None:
    """Test selecting a mode raises error when host is missing."""
    coordinator = MagicMock()
    coordinator.async_add_listener.return_value = lambda: None
    coordinator.last_update_success = True
    coordinator.data = {"device_mode": MODE_AUTO}

    config_entry = MagicMock()
    config_entry.data = {}

    device_info = {
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus",
        "version": 3,
        "wifi_name": "marstek",
        "wifi_mac": "11:22:33:44:55:66",
    }

    entity = MarstekOperatingModeSelect(
        coordinator,
        device_info,
        SELECT_ENTITIES[0],
        MagicMock(),
        config_entry,
    )

    with pytest.raises(HomeAssistantError, match="no_host_configured"):
        await entity.async_select_option(MODE_AUTO)


def _operating_mode_state(hass: HomeAssistant):
    """Return the single operating-mode select state."""
    states = [
        state
        for state in hass.states.async_all("select")
        if state.entity_id.endswith("_operating_mode")
    ]
    assert len(states) == 1
    return states[0]


def _make_select_entity(
    *,
    device_type: str,
    version: object,
    udp_client: MagicMock | None = None,
) -> tuple[MarstekOperatingModeSelect, MagicMock]:
    """Build a select entity bound to a firmware profile."""
    coordinator = MagicMock()
    coordinator.async_add_listener.return_value = lambda: None
    coordinator.last_update_success = True
    coordinator.data = {"device_mode": MODE_AUTO}
    coordinator.profile = resolve_firmware_profile(device_type, version)

    config_entry = MagicMock()
    config_entry.data = {"host": "1.2.3.4", "port": 30000}

    client = udp_client or MagicMock()
    client.send_request = AsyncMock(return_value={"result": {}})
    client.pause_polling = AsyncMock(return_value=None)
    client.resume_polling = AsyncMock(return_value=None)

    entity = MarstekOperatingModeSelect(
        coordinator,
        {
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "device_type": device_type,
            "version": version,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
        SELECT_ENTITIES[0],
        client,
        config_entry,
    )
    return entity, client


def test_select_current_option_normalizes_open_api_auto() -> None:
    """Wire casing like Auto must stay inside HA 2026.9 select options."""
    entity, _client = _make_select_entity(device_type="VenusE 3.0", version=145)
    entity.coordinator.data = {"device_mode": "Auto"}
    assert entity.current_option == MODE_AUTO
    entity.coordinator.data = {"device_mode": "SelfUse"}
    assert entity.current_option is None


@pytest.mark.parametrize(
    ("device_type", "version", "expect_ups"),
    [
        ("VenusE", 145, False),
        ("VenusE", 150, True),
        ("Venus E mini", 150, True),
        ("Venus E mini", "not-a-version", False),
        ("Marstek Energy Storage", 150, False),
    ],
)
async def test_select_options_follow_firmware_profile(
    hass: HomeAssistant,
    mock_config_entry: Any,
    device_type: str,
    version: object,
    expect_ups: bool,
) -> None:
    """UPS appears on the existing select only for UPS-capable profiles."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            "device_type": device_type,
            "version": version,
        },
    )

    client = _mock_client(status={"battery_soc": 55, "device_mode": "auto"})
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    state = _operating_mode_state(hass)
    options = state.attributes.get("options")
    assert options is not None
    for mode in SELECTABLE_BASE_MODES:
        assert mode in options
    if expect_ups:
        assert MODE_UPS in options
    else:
        assert MODE_UPS not in options


async def test_select_ups_sends_exact_payload_and_pauses_polling(
    hass: HomeAssistant, mock_config_entry: Any
) -> None:
    """Selecting UPS sends ES.SetMode with ups_cfg.enable = 1 and pauses polling."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "device_type": "VenusE", "version": 150},
    )

    client = _mock_client(status={"battery_soc": 55, "device_mode": "auto"})
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        pause_before = client.pause_polling.call_count
        resume_before = client.resume_polling.call_count
        requests_before = client.send_request.call_count

        entity_id = _operating_mode_state(hass).entity_id
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": entity_id, "option": MODE_UPS},
            blocking=True,
        )

        assert client.pause_polling.call_count == pause_before + 1
        assert client.resume_polling.call_count == resume_before + 1
        assert client.send_request.call_count == requests_before + 1

        command = client.send_request.call_args[0][0]
        payload = json.loads(command)
        assert payload["method"] == "ES.SetMode"
        assert payload["params"]["config"] == {
            "mode": "UPS",
            "ups_cfg": {"enable": 1},
        }


async def test_select_ups_resumes_polling_after_failure(
    hass: HomeAssistant, mock_config_entry: Any
) -> None:
    """UPS selection resumes polling when every retry fails."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "device_type": "VenusE", "version": 150},
    )

    call_count = 0

    async def send_request_side_effect(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"result": {}}
        raise TimeoutError("timeout")

    client = _mock_client(status={"battery_soc": 55, "device_mode": "auto"})
    client.send_request = AsyncMock(side_effect=send_request_side_effect)

    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        resume_before = client.resume_polling.call_count
        entity_id = _operating_mode_state(hass).entity_id
        with pytest.raises(HomeAssistantError, match="mode_change_failed|Failed to set"):
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": MODE_UPS},
                blocking=True,
            )

        assert client.resume_polling.call_count == resume_before + 1


async def test_unsupported_direct_ups_selection_sends_no_request() -> None:
    """A profile without UPS cannot transmit UPS even if invoked directly."""
    entity, client = _make_select_entity(device_type="VenusE", version=145)

    with pytest.raises(HomeAssistantError, match="mode_not_supported"):
        await entity.async_select_option(MODE_UPS)

    client.send_request.assert_not_called()
    client.pause_polling.assert_not_called()
    client.resume_polling.assert_not_called()


async def test_unknown_profile_direct_ups_selection_sends_no_request() -> None:
    """Unknown family firmware cannot transmit UPS."""
    entity, client = _make_select_entity(
        device_type="Marstek Energy Storage", version=150
    )

    with pytest.raises(HomeAssistantError, match="mode_not_supported"):
        await entity.async_select_option(MODE_UPS)

    client.send_request.assert_not_called()


@pytest.mark.parametrize("mode", [MODE_AUTO, MODE_AI])
async def test_auto_and_ai_remain_selectable_without_ups_profile(
    hass: HomeAssistant, mock_config_entry: Any, mode: str
) -> None:
    """Auto and AI still send commands on profiles that omit UPS."""
    mock_config_entry.add_to_hass(hass)

    client = _mock_client(status={"battery_soc": 55, "device_mode": "auto"})
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        requests_before = client.send_request.call_count
        await hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": "select.venus_operating_mode",
                "option": mode,
            },
            blocking=True,
        )
        assert client.send_request.call_count > requests_before


@pytest.mark.parametrize(
    ("mode", "expected_message_part"),
    [
        (MODE_PASSIVE, "Passive mode requires power and duration"),
        (MODE_MANUAL, "Manual mode requires schedule configuration"),
    ],
)
async def test_manual_and_passive_remain_blocked_on_ups_capable_profile(
    hass: HomeAssistant,
    mock_config_entry: Any,
    mode: str,
    expected_message_part: str,
) -> None:
    """Manual and Passive stay service-only even when UPS is offered."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "device_type": "VenusE", "version": 150},
    )

    client = _mock_client(status={"battery_soc": 55, "device_mode": "auto"})
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        requests_before = client.send_request.call_count
        entity_id = _operating_mode_state(hass).entity_id
        options = hass.states.get(entity_id)
        assert options is not None
        assert MODE_MANUAL in options.attributes["options"]
        assert MODE_PASSIVE in options.attributes["options"]
        assert MODE_UPS in options.attributes["options"]

        with pytest.raises(HomeAssistantError, match=expected_message_part):
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": mode},
                blocking=True,
            )

        assert client.send_request.call_count == requests_before


async def test_select_reports_ups_state(
    hass: HomeAssistant, mock_config_entry: Any
) -> None:
    """ES.GetMode UPS is represented as Home Assistant state ups."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "device_type": "VenusE", "version": 150},
    )

    client = _mock_client(status={"battery_soc": 55, "device_mode": MODE_UPS})
    with _patch_all(client=client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    state = _operating_mode_state(hass)
    assert state.state == MODE_UPS
