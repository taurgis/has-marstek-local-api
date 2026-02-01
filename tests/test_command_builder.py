"""Tests for pymarstek command builder."""

from __future__ import annotations

import json

import pytest

from custom_components.marstek.pymarstek.command_builder import (
    build_command,
    discover,
    get_battery_status,
    get_em_status,
    get_es_mode,
    get_es_status,
    get_next_request_id,
    get_pv_status,
    get_wifi_status,
    reset_request_id,
    set_es_mode_manual_charge,
    set_es_mode_manual_discharge,
)
from custom_components.marstek.pymarstek.validators import ValidationError


class TestRequestIdManagement:
    """Tests for request ID management."""

    def test_get_next_request_id_increments(self) -> None:
        """Test that request ID increments."""
        reset_request_id()
        id1 = get_next_request_id()
        id2 = get_next_request_id()
        id3 = get_next_request_id()

        assert id2 == id1 + 1
        assert id3 == id2 + 1

    def test_reset_request_id(self) -> None:
        """Test that reset_request_id resets counter."""
        # Get some IDs
        for _ in range(5):
            get_next_request_id()

        # Reset
        reset_request_id()

        # Next ID should be 1
        assert get_next_request_id() == 1


class TestBuildCommand:
    """Tests for build_command function."""

    def setup_method(self) -> None:
        """Reset request ID before each test."""
        reset_request_id()

    def test_basic_command(self) -> None:
        """Test building a basic command."""
        result = build_command("ES.GetStatus", {"id": 0})

        parsed = json.loads(result)
        assert parsed["method"] == "ES.GetStatus"
        assert parsed["params"] == {"id": 0}
        assert "id" in parsed

    def test_command_without_params(self) -> None:
        """Test building a command without params."""
        result = build_command("Marstek.GetDevice")

        parsed = json.loads(result)
        assert parsed["method"] == "Marstek.GetDevice"
        assert parsed["params"] == {}

    def test_command_with_validation(self) -> None:
        """Test that validation occurs by default."""
        # Invalid method should fail
        with pytest.raises(ValidationError) as exc_info:
            build_command("Invalid.Method")

        assert "Unknown method" in str(exc_info.value)

    def test_command_without_validation(self) -> None:
        """Test that validation can be skipped."""
        # Invalid method should pass when validation is disabled
        result = build_command("Invalid.Method", validate=False)

        parsed = json.loads(result)
        assert parsed["method"] == "Invalid.Method"

    def test_command_increments_id(self) -> None:
        """Test that each command gets a new ID."""
        result1 = build_command("ES.GetStatus", {"id": 0})
        result2 = build_command("ES.GetStatus", {"id": 0})

        parsed1 = json.loads(result1)
        parsed2 = json.loads(result2)

        assert parsed2["id"] == parsed1["id"] + 1


class TestDiscoverCommand:
    """Tests for discover command builder."""

    def test_discover_format(self) -> None:
        """Test discover command format."""
        result = discover()

        parsed = json.loads(result)
        assert parsed["method"] == "Marstek.GetDevice"
        assert parsed["params"]["ble_mac"] == "0"


class TestStatusCommands:
    """Tests for status command builders."""

    def test_get_battery_status_default(self) -> None:
        """Test get_battery_status with default device ID."""
        result = get_battery_status()

        parsed = json.loads(result)
        assert parsed["method"] == "Bat.GetStatus"
        assert parsed["params"]["id"] == 0

    def test_get_battery_status_with_device_id(self) -> None:
        """Test get_battery_status with specific device ID."""
        result = get_battery_status(device_id=5)

        parsed = json.loads(result)
        assert parsed["params"]["id"] == 5

    def test_get_es_status_default(self) -> None:
        """Test get_es_status with default device ID."""
        result = get_es_status()

        parsed = json.loads(result)
        assert parsed["method"] == "ES.GetStatus"
        assert parsed["params"]["id"] == 0

    def test_get_es_status_with_device_id(self) -> None:
        """Test get_es_status with specific device ID."""
        result = get_es_status(device_id=3)

        parsed = json.loads(result)
        assert parsed["params"]["id"] == 3

    def test_get_es_mode_default(self) -> None:
        """Test get_es_mode with default device ID."""
        result = get_es_mode()

        parsed = json.loads(result)
        assert parsed["method"] == "ES.GetMode"
        assert parsed["params"]["id"] == 0

    def test_get_es_mode_with_device_id(self) -> None:
        """Test get_es_mode with specific device ID."""
        result = get_es_mode(device_id=2)

        parsed = json.loads(result)
        assert parsed["params"]["id"] == 2

    def test_get_pv_status_default(self) -> None:
        """Test get_pv_status with default device ID."""
        result = get_pv_status()

        parsed = json.loads(result)
        assert parsed["method"] == "PV.GetStatus"
        assert parsed["params"]["id"] == 0

    def test_get_pv_status_with_device_id(self) -> None:
        """Test get_pv_status with specific device ID."""
        result = get_pv_status(device_id=1)

        parsed = json.loads(result)
        assert parsed["params"]["id"] == 1

    def test_get_wifi_status_default(self) -> None:
        """Test get_wifi_status with default device ID."""
        result = get_wifi_status()

        parsed = json.loads(result)
        assert parsed["method"] == "Wifi.GetStatus"
        assert parsed["params"]["id"] == 0

    def test_get_wifi_status_with_device_id(self) -> None:
        """Test get_wifi_status with specific device ID."""
        result = get_wifi_status(device_id=4)

        parsed = json.loads(result)
        assert parsed["params"]["id"] == 4

    def test_get_em_status_default(self) -> None:
        """Test get_em_status with default device ID."""
        result = get_em_status()

        parsed = json.loads(result)
        assert parsed["method"] == "EM.GetStatus"
        assert parsed["params"]["id"] == 0

    def test_get_em_status_with_device_id(self) -> None:
        """Test get_em_status with specific device ID."""
        result = get_em_status(device_id=6)

        parsed = json.loads(result)
        assert parsed["params"]["id"] == 6

    def test_invalid_device_id(self) -> None:
        """Test that invalid device IDs raise ValidationError."""
        with pytest.raises(ValidationError):
            get_battery_status(device_id=300)

        with pytest.raises(ValidationError):
            get_es_status(device_id=-1)


class TestModeCommands:
    """Tests for ES.SetMode command builders."""

    def test_manual_charge_default(self) -> None:
        """Test set_es_mode_manual_charge with defaults."""
        result = set_es_mode_manual_charge()

        parsed = json.loads(result)
        assert parsed["method"] == "ES.SetMode"
        assert parsed["params"]["id"] == 0
        config = parsed["params"]["config"]
        assert config["mode"] == "Manual"
        assert config["manual_cfg"]["power"] == -1300
        assert config["manual_cfg"]["enable"] == 1
        assert config["manual_cfg"]["time_num"] == 0

    def test_manual_charge_custom_power(self) -> None:
        """Test set_es_mode_manual_charge with custom power."""
        result = set_es_mode_manual_charge(power=-500)

        parsed = json.loads(result)
        config = parsed["params"]["config"]
        assert config["manual_cfg"]["power"] == -500

    def test_manual_charge_custom_device_id(self) -> None:
        """Test set_es_mode_manual_charge with custom device ID."""
        result = set_es_mode_manual_charge(device_id=3)

        parsed = json.loads(result)
        assert parsed["params"]["id"] == 3

    def test_manual_discharge_default(self) -> None:
        """Test set_es_mode_manual_discharge with defaults."""
        result = set_es_mode_manual_discharge()

        parsed = json.loads(result)
        assert parsed["method"] == "ES.SetMode"
        config = parsed["params"]["config"]
        assert config["mode"] == "Manual"
        assert config["manual_cfg"]["power"] == 1300
        assert config["manual_cfg"]["enable"] == 1

    def test_manual_discharge_custom_power(self) -> None:
        """Test set_es_mode_manual_discharge with custom power."""
        result = set_es_mode_manual_discharge(power=800)

        parsed = json.loads(result)
        config = parsed["params"]["config"]
        assert config["manual_cfg"]["power"] == 800

    def test_manual_configs_have_time_settings(self) -> None:
        """Test that manual configs include time settings."""
        charge = json.loads(set_es_mode_manual_charge())
        discharge = json.loads(set_es_mode_manual_discharge())

        for config in [charge, discharge]:
            manual_cfg = config["params"]["config"]["manual_cfg"]
            assert manual_cfg["start_time"] == "00:00"
            assert manual_cfg["end_time"] == "23:59"
            assert manual_cfg["week_set"] == 127
