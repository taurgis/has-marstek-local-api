"""Tests for Marstek diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.marstek import MarstekConfigEntry
from custom_components.marstek.diagnostics import async_get_config_entry_diagnostics
from custom_components.marstek.const import DATA_UDP_CLIENT, DOMAIN


@pytest.fixture
def mock_config_entry() -> MagicMock:
    """Create a mock config entry."""
    entry = MagicMock(spec=MarstekConfigEntry)
    entry.title = "Test Marstek Device"
    entry.data = {
        "host": "192.168.1.100",
        "mac": "AA:BB:CC:DD:EE:FF",
        "ble_mac": "11:22:33:44:55:66",
        "wifi_mac": "77:88:99:AA:BB:CC",
        "wifi_name": "TestNetwork",
        "device_type": "Venus",
        "version": 1,
    }
    entry.options = {"poll_interval_fast": 30}
    return entry


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.data = {
        "battery_level": 75,
        "grid_power": 500,
        "device_mode": "Auto",
        "ip": "192.168.1.100",
        "bleMac": "11:22:33:44:55:66",
        "SSID": "TestNetwork",
    }
    coordinator.last_update_success = True
    coordinator.last_exception = None
    coordinator.last_update_success_time = datetime(2026, 1, 27, 10, 30, 0, tzinfo=timezone.utc)
    coordinator.last_update_attempt_time = datetime(2026, 1, 27, 10, 30, 0, tzinfo=timezone.utc)
    coordinator.consecutive_failures = 0
    return coordinator


@pytest.fixture
def mock_runtime_data(mock_coordinator: MagicMock) -> MagicMock:
    """Create mock runtime data."""
    runtime_data = MagicMock()
    runtime_data.coordinator = mock_coordinator
    runtime_data.device_info = {
        "ip": "192.168.1.100",
        "mac": "AA:BB:CC:DD:EE:FF",
        "ble_mac": "11:22:33:44:55:66",
        "wifi_mac": "77:88:99:AA:BB:CC",
        "wifi_name": "TestNetwork",
        "device_type": "Venus",
        "version": 1,
    }
    return runtime_data


async def test_async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    mock_config_entry: MagicMock,
    mock_runtime_data: MagicMock,
) -> None:
    """Test diagnostics returns correct structure."""
    mock_config_entry.runtime_data = mock_runtime_data

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    # Verify structure
    assert "entry" in result
    assert "device_info" in result
    assert "polling_config" in result
    assert "coordinator" in result
    assert "coordinator_data" in result
    assert "last_exception" in result

    # Verify entry data
    assert result["entry"]["title"] == "Test Marstek Device"
    assert result["coordinator"]["last_update_success"] is True
    assert result["last_exception"] is None

    # Verify polling_config has expected keys with defaults
    assert "poll_interval_fast" in result["polling_config"]
    assert "poll_interval_medium" in result["polling_config"]
    assert "poll_interval_slow" in result["polling_config"]
    assert "request_delay" in result["polling_config"]
    assert "request_timeout" in result["polling_config"]

    # Verify coordinator section has timestamps
    assert "last_update_success_time" in result["coordinator"]
    assert "time_since_last_success" in result["coordinator"]
    assert "last_update_attempt_time" in result["coordinator"]
    assert "time_since_last_attempt" in result["coordinator"]
    assert "consecutive_failures" in result["coordinator"]
    assert "diagnostics_generated_at" in result["coordinator"]
    assert result["coordinator"]["consecutive_failures"] == 0


async def test_diagnostics_redacts_sensitive_data(
    hass: HomeAssistant,
    mock_config_entry: MagicMock,
    mock_runtime_data: MagicMock,
) -> None:
    """Test that sensitive data is redacted."""
    mock_config_entry.runtime_data = mock_runtime_data

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    # Check that sensitive fields are redacted in entry data
    entry_data = result["entry"]["data"]
    assert entry_data.get("host") == "**REDACTED**"
    assert entry_data.get("mac") == "**REDACTED**"
    assert entry_data.get("ble_mac") == "**REDACTED**"
    assert entry_data.get("wifi_mac") == "**REDACTED**"
    assert entry_data.get("wifi_name") == "**REDACTED**"

    # Check that sensitive fields are redacted in device_info
    device_info = result["device_info"]
    assert device_info.get("ip") == "**REDACTED**"
    assert device_info.get("mac") == "**REDACTED**"
    assert device_info.get("ble_mac") == "**REDACTED**"

    # Check that sensitive fields are redacted in coordinator_data
    coordinator_data = result["coordinator_data"]
    assert coordinator_data.get("ip") == "**REDACTED**"
    assert coordinator_data.get("bleMac") == "**REDACTED**"
    assert coordinator_data.get("SSID") == "**REDACTED**"


async def test_diagnostics_with_exception(
    hass: HomeAssistant,
    mock_config_entry: MagicMock,
    mock_runtime_data: MagicMock,
) -> None:
    """Test diagnostics when coordinator has an exception."""
    # Create an exception with a cause (chained exception)
    original_error = OSError("Network unreachable")
    update_failed = Exception("Polling failed for 192.168.1.100")
    update_failed.__cause__ = original_error

    mock_runtime_data.coordinator.last_update_success = False
    mock_runtime_data.coordinator.last_exception = update_failed
    mock_runtime_data.coordinator.consecutive_failures = 5
    mock_config_entry.runtime_data = mock_runtime_data

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert result["coordinator"]["last_update_success"] is False
    assert result["coordinator"]["consecutive_failures"] == 5

    # Verify exception structure
    exc_info = result["last_exception"]
    assert exc_info is not None
    assert exc_info["type"] == "Exception"
    assert "Polling failed" in exc_info["message"]
    assert "traceback" in exc_info
    assert isinstance(exc_info["traceback"], list)

    # Verify cause is included
    assert "cause" in exc_info
    assert exc_info["cause"]["type"] == "OSError"
    assert "Network unreachable" in exc_info["cause"]["message"]


async def test_diagnostics_with_simple_exception(
    hass: HomeAssistant,
    mock_config_entry: MagicMock,
    mock_runtime_data: MagicMock,
) -> None:
    """Test diagnostics when coordinator has a simple exception without cause."""
    mock_runtime_data.coordinator.last_update_success = False
    mock_runtime_data.coordinator.last_exception = TimeoutError("Connection timeout")
    mock_config_entry.runtime_data = mock_runtime_data

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert result["coordinator"]["last_update_success"] is False

    exc_info = result["last_exception"]
    assert exc_info is not None
    assert exc_info["type"] == "TimeoutError"
    assert "Connection timeout" in exc_info["message"]
    assert "traceback" in exc_info
    # No cause for simple exception
    assert "cause" not in exc_info


async def test_diagnostics_with_empty_coordinator_data(
    hass: HomeAssistant,
    mock_config_entry: MagicMock,
    mock_runtime_data: MagicMock,
) -> None:
    """Test diagnostics when coordinator has no data."""
    mock_runtime_data.coordinator.data = None
    mock_config_entry.runtime_data = mock_runtime_data

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert result["coordinator_data"] == {}


async def test_diagnostics_includes_command_stats(
    hass: HomeAssistant,
    mock_config_entry: MagicMock,
    mock_runtime_data: MagicMock,
) -> None:
    """Test diagnostics include command stats when UDP client exposes them."""
    mock_runtime_data.coordinator.device_ip = "192.168.1.100"
    mock_config_entry.runtime_data = mock_runtime_data

    udp_client = MagicMock()
    udp_client.get_command_stats_for_ip.return_value = {
        "ES.GetStatus": {
            "total_attempts": 2,
            "total_success": 1,
            "total_timeouts": 1,
            "total_failures": 0,
            "last_success": False,
            "last_latency": None,
            "last_timeout": True,
            "last_error": "timeout",
            "last_updated": 1738170001.0,
        }
    }

    hass.data.setdefault(DOMAIN, {})[DATA_UDP_CLIENT] = udp_client

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert "command_stats" in result
    stats = result["command_stats"]["ES.GetStatus"]

    assert stats["total_attempts"] == 2
    assert stats["success_rate"] == 0.5
    assert stats["timeout_rate"] == 0.5


async def test_diagnostics_without_last_update_time(
    hass: HomeAssistant,
    mock_config_entry: MagicMock,
    mock_runtime_data: MagicMock,
) -> None:
    """Test diagnostics when coordinator has no last_update_success_time."""
    # Simulate coordinator without the attributes (e.g., older coordinator version)
    del mock_runtime_data.coordinator.last_update_success_time
    del mock_runtime_data.coordinator.last_update_attempt_time
    del mock_runtime_data.coordinator.consecutive_failures
    mock_config_entry.runtime_data = mock_runtime_data

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert result["coordinator"]["last_update_success_time"] is None
    assert result["coordinator"]["time_since_last_success"] is None
    assert result["coordinator"]["last_update_attempt_time"] is None
    assert result["coordinator"]["time_since_last_attempt"] is None
    assert result["coordinator"]["consecutive_failures"] == 0
