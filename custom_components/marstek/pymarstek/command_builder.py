"""Command builder utilities for pymarstek.

All commands are validated before being built to protect devices from
malformed requests. See validators.py for validation rules.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .const import (
    CMD_BATTERY_STATUS,
    CMD_BLE_ADV,
    CMD_DISCOVER,
    CMD_DOD_SET,
    CMD_EM_STATUS,
    CMD_ES_MODE,
    CMD_ES_SET_MODE,
    CMD_ES_STATUS,
    CMD_LED_CTRL,
    CMD_PV_GET_STATUS,
    CMD_WIFI_STATUS,
)
from .validators import MAX_JSON_RPC_ID, ValidationError, validate_command

_LOGGER = logging.getLogger(__name__)

_request_id = 0


def get_next_request_id() -> int:
    """Get the next request identifier.

    Control firmware stores JSON-RPC ``id`` as uint16. Wrapping through 0
    collides with parse-error replies (``id: 0``, code -32700), so the
    generator cycles ``1..65535`` and never emits 0.
    """
    global _request_id
    _request_id += 1
    if _request_id > MAX_JSON_RPC_ID:
        _request_id = 1
    return _request_id


def reset_request_id() -> None:
    """Reset the request identifier counter."""
    global _request_id
    _request_id = 0


def build_command(
    method: str, params: dict[str, Any] | None = None, *, validate: bool = True
) -> str:
    """Construct a JSON command payload.

    Args:
        method: API method name (e.g., "ES.GetStatus")
        params: Optional parameters dictionary
        validate: Whether to validate the command (default True). Only set to
            False if validation was already performed upstream.

    Returns:
        JSON string of the command

    Raises:
        ValidationError: If command validation fails and validate=True
    """
    command = {
        "id": get_next_request_id(),
        "method": method,
        "params": params or {},
    }

    if validate:
        try:
            validate_command(command)
        except ValidationError as err:
            _LOGGER.error("Command validation failed: %s", err.message)
            raise

    return json.dumps(command)


def discover() -> str:
    """Create a GetDevice command with JSON-RPC id 0.

    Control firmware echoes ``Marstek.GetDevice`` on id 0. Broadcast discovery
    already sends that id; pooled unicast GetDevice (manual add, confirm,
    reconfigure, repairs) must use the same wire id. Other methods still skip
    0 so they cannot collide with parse-error replies.
    """
    command: dict[str, Any] = {
        "id": 0,
        "method": CMD_DISCOVER,
        "params": {"ble_mac": "0"},
    }
    validate_command(command)
    return json.dumps(command)


def get_battery_status(device_id: int = 0) -> str:
    """Create a battery status command.

    Args:
        device_id: Device identifier (0-255)

    Returns:
        JSON command string

    Raises:
        ValidationError: If device_id is invalid
    """
    # Validation happens in build_command via validate_command
    return build_command(CMD_BATTERY_STATUS, {"id": device_id})


def get_es_status(device_id: int = 0) -> str:
    """Create an ES status command.

    Args:
        device_id: Device identifier (0-255)

    Returns:
        JSON command string

    Raises:
        ValidationError: If device_id is invalid
    """
    # Validation happens in build_command via validate_command
    return build_command(CMD_ES_STATUS, {"id": device_id})


def get_es_mode(device_id: int = 0) -> str:
    """Create an ES mode command.

    Args:
        device_id: Device identifier (0-255)

    Returns:
        JSON command string

    Raises:
        ValidationError: If device_id is invalid
    """
    # Validation happens in build_command via validate_command
    return build_command(CMD_ES_MODE, {"id": device_id})


def get_pv_status(device_id: int = 0) -> str:
    """Create a PV status command.

    Args:
        device_id: Device identifier (0-255)

    Returns:
        JSON command string

    Raises:
        ValidationError: If device_id is invalid
    """
    # Validation happens in build_command via validate_command
    return build_command(CMD_PV_GET_STATUS, {"id": device_id})


def set_es_mode_manual_charge(device_id: int = 0, power: int = -1300) -> str:
    """Create a manual charge command."""
    config = {
        "mode": "Manual",
        "manual_cfg": {
            "time_num": 0,
            "start_time": "00:00",
            "end_time": "23:59",
            "week_set": 127,
            "power": power,
            "enable": 1,
        },
    }
    return build_command(CMD_ES_SET_MODE, {"id": device_id, "config": config})


def set_es_mode_manual_discharge(device_id: int = 0, power: int = 1300) -> str:
    """Create a manual discharge command."""
    config = {
        "mode": "Manual",
        "manual_cfg": {
            "time_num": 0,
            "start_time": "00:00",
            "end_time": "23:59",
            "week_set": 127,
            "power": power,
            "enable": 1,
        },
    }
    return build_command(CMD_ES_SET_MODE, {"id": device_id, "config": config})


def get_wifi_status(device_id: int = 0) -> str:
    """Create a WiFi status command.

    Args:
        device_id: Device identifier (0-255)

    Returns:
        JSON command string

    Raises:
        ValidationError: If device_id is invalid
    """
    # Validation happens in build_command via validate_command
    return build_command(CMD_WIFI_STATUS, {"id": device_id})


def get_em_status(device_id: int = 0) -> str:
    """Create an Energy Meter (CT) status command.

    Args:
        device_id: Device identifier (0-255)

    Returns:
        JSON command string

    Raises:
        ValidationError: If device_id is invalid
    """
    # Validation happens in build_command via validate_command
    return build_command(CMD_EM_STATUS, {"id": device_id})


def set_dod(value: int) -> str:
    """Create a DOD.SET command."""
    return build_command(CMD_DOD_SET, {"value": value})


def set_ble_advertising(enable: int) -> str:
    """Create a Ble.Adv command. 0 enables advertising, 1 disables it."""
    return build_command(CMD_BLE_ADV, {"enable": enable})


def set_led(state: int) -> str:
    """Create a Led.Ctrl command. 1 turns the panel LED on, 0 turns it off."""
    return build_command(CMD_LED_CTRL, {"state": state})
