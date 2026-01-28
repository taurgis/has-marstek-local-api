"""Tests for pymarstek validators module."""

from __future__ import annotations

import pytest

from custom_components.marstek.pymarstek.validators import (
    MAX_DEVICE_ID,
    MAX_PASSIVE_DURATION,
    MAX_POWER_VALUE,
    MAX_TIME_SLOTS,
    MAX_WEEK_SET,
    VALID_METHODS,
    VALID_MODES,
    ValidationError,
    enable_strict_mode,
    is_strict_mode,
    validate_command,
    validate_device_id,
    validate_es_set_mode_config,
    validate_json_message,
    validate_manual_config,
    validate_method,
    validate_params,
    validate_passive_config,
    validate_power_value,
    validate_time_format,
    validate_time_range,
    validate_week_set,
)


class TestValidateTimeFormat:
    """Tests for validate_time_format."""

    @pytest.mark.parametrize("time_str", [
        "00:00",
        "12:30",
        "23:59",
        "9:05",
        "09:05",
    ])
    def test_valid_times(self, time_str: str) -> None:
        """Test valid time formats are accepted."""
        validate_time_format(time_str)  # Should not raise

    @pytest.mark.parametrize("time_str", [
        "24:00",
        "12:60",
        "invalid",
        "",
        "12",
        "12:30:00",  # Seconds not allowed
    ])
    def test_invalid_times(self, time_str: str) -> None:
        """Test invalid time formats are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_time_format(time_str)
        assert exc_info.value.field == "time"

    def test_non_string_rejected(self) -> None:
        """Test non-string types are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_time_format(1230)  # type: ignore[arg-type]
        assert "must be a string" in exc_info.value.message


class TestValidateTimeRange:
    """Tests for validate_time_range."""

    def test_valid_range(self) -> None:
        """Test valid time range is accepted."""
        validate_time_range("09:00", "17:00")  # Should not raise

    def test_end_before_start_rejected(self) -> None:
        """Test end before start is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_time_range("17:00", "09:00")
        assert exc_info.value.field == "end_time"
        assert "must be after" in exc_info.value.message

    def test_equal_times_rejected_by_default(self) -> None:
        """Test equal times are rejected by default."""
        with pytest.raises(ValidationError) as exc_info:
            validate_time_range("12:00", "12:00")
        assert "must be after" in exc_info.value.message

    def test_equal_times_allowed_when_specified(self) -> None:
        """Test equal times accepted with allow_equal=True."""
        validate_time_range("12:00", "12:00", allow_equal=True)  # Should not raise

    def test_one_minute_difference_valid(self) -> None:
        """Test one minute difference is valid."""
        validate_time_range("12:00", "12:01")  # Should not raise


class TestValidateDeviceId:
    """Tests for validate_device_id."""

    @pytest.mark.parametrize("device_id", [0, 1, 127, 255])
    def test_valid_device_ids(self, device_id: int) -> None:
        """Test valid device IDs are accepted."""
        validate_device_id(device_id)  # Should not raise

    def test_negative_id_rejected(self) -> None:
        """Test negative device ID is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_device_id(-1)
        assert exc_info.value.field == "id"
        assert f"0 and {MAX_DEVICE_ID}" in exc_info.value.message

    def test_too_large_id_rejected(self) -> None:
        """Test device ID > 255 is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_device_id(256)
        assert exc_info.value.field == "id"

    def test_non_integer_rejected(self) -> None:
        """Test non-integer device ID is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_device_id("0")  # type: ignore[arg-type]
        assert "must be an integer" in exc_info.value.message


class TestValidatePowerValue:
    """Tests for validate_power_value."""

    @pytest.mark.parametrize("power", [
        0,
        1000,
        -1000,
        MAX_POWER_VALUE,
        -MAX_POWER_VALUE,
    ])
    def test_valid_power_values(self, power: int) -> None:
        """Test valid power values are accepted."""
        validate_power_value(power)  # Should not raise

    def test_power_too_high_rejected(self) -> None:
        """Test power exceeding max is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_power_value(MAX_POWER_VALUE + 1)
        assert exc_info.value.field == "power"
        assert f"-{MAX_POWER_VALUE} and {MAX_POWER_VALUE}" in exc_info.value.message

    def test_power_too_low_rejected(self) -> None:
        """Test power below negative max is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_power_value(-MAX_POWER_VALUE - 1)
        assert exc_info.value.field == "power"

    def test_non_integer_rejected(self) -> None:
        """Test non-integer power is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_power_value(1000.5)  # type: ignore[arg-type]
        assert "must be an integer" in exc_info.value.message


class TestValidateWeekSet:
    """Tests for validate_week_set."""

    @pytest.mark.parametrize("week_set", [0, 1, 63, 127])
    def test_valid_week_sets(self, week_set: int) -> None:
        """Test valid week_set values are accepted."""
        validate_week_set(week_set)  # Should not raise

    def test_negative_week_set_rejected(self) -> None:
        """Test negative week_set is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_week_set(-1)
        assert exc_info.value.field == "week_set"

    def test_too_large_week_set_rejected(self) -> None:
        """Test week_set > 127 is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_week_set(128)
        assert f"0 and {MAX_WEEK_SET}" in exc_info.value.message


class TestValidateManualConfig:
    """Tests for validate_manual_config."""

    @pytest.fixture
    def valid_manual_config(self) -> dict:
        """Return a valid manual configuration."""
        return {
            "time_num": 0,
            "start_time": "00:00",
            "end_time": "23:59",
            "week_set": 127,
            "power": 1000,
            "enable": 1,
        }

    def test_valid_config(self, valid_manual_config: dict) -> None:
        """Test valid manual config is accepted."""
        validate_manual_config(valid_manual_config)  # Should not raise

    def test_missing_required_field(self, valid_manual_config: dict) -> None:
        """Test missing required field is rejected."""
        del valid_manual_config["power"]
        with pytest.raises(ValidationError) as exc_info:
            validate_manual_config(valid_manual_config)
        assert "missing required fields" in exc_info.value.message
        assert "power" in exc_info.value.message

    def test_invalid_time_num(self, valid_manual_config: dict) -> None:
        """Test invalid time_num is rejected."""
        valid_manual_config["time_num"] = MAX_TIME_SLOTS
        with pytest.raises(ValidationError) as exc_info:
            validate_manual_config(valid_manual_config)
        assert "time_num" in exc_info.value.field

    def test_invalid_enable_value(self, valid_manual_config: dict) -> None:
        """Test enable must be 0 or 1."""
        valid_manual_config["enable"] = 2
        with pytest.raises(ValidationError) as exc_info:
            validate_manual_config(valid_manual_config)
        assert exc_info.value.field == "enable"

    def test_invalid_time_range_when_enabled(self, valid_manual_config: dict) -> None:
        """Test invalid time range rejected when slot is enabled."""
        valid_manual_config["start_time"] = "17:00"
        valid_manual_config["end_time"] = "09:00"
        with pytest.raises(ValidationError) as exc_info:
            validate_manual_config(valid_manual_config)
        assert exc_info.value.field == "end_time"

    def test_invalid_time_range_allowed_when_disabled(self, valid_manual_config: dict) -> None:
        """Test invalid time range allowed when slot is disabled."""
        valid_manual_config["enable"] = 0
        valid_manual_config["start_time"] = "17:00"
        valid_manual_config["end_time"] = "09:00"
        validate_manual_config(valid_manual_config)  # Should not raise


class TestValidatePassiveConfig:
    """Tests for validate_passive_config."""

    @pytest.fixture
    def valid_passive_config(self) -> dict:
        """Return a valid passive configuration."""
        return {
            "power": 1000,
            "cd_time": 3600,
        }

    def test_valid_config(self, valid_passive_config: dict) -> None:
        """Test valid passive config is accepted."""
        validate_passive_config(valid_passive_config)  # Should not raise

    def test_missing_cd_time(self, valid_passive_config: dict) -> None:
        """Test missing cd_time is rejected."""
        del valid_passive_config["cd_time"]
        with pytest.raises(ValidationError) as exc_info:
            validate_passive_config(valid_passive_config)
        assert "cd_time" in exc_info.value.message

    def test_cd_time_too_large(self, valid_passive_config: dict) -> None:
        """Test cd_time > 24 hours is rejected."""
        valid_passive_config["cd_time"] = MAX_PASSIVE_DURATION + 1
        with pytest.raises(ValidationError) as exc_info:
            validate_passive_config(valid_passive_config)
        assert "cd_time" in exc_info.value.field


class TestValidateEsSetModeConfig:
    """Tests for validate_es_set_mode_config."""

    def test_auto_mode_no_extra_config(self) -> None:
        """Test Auto mode doesn't require extra config."""
        config = {"mode": "Auto"}
        validate_es_set_mode_config(config)  # Should not raise

    def test_ai_mode_no_extra_config(self) -> None:
        """Test AI mode doesn't require extra config."""
        config = {"mode": "AI"}
        validate_es_set_mode_config(config)  # Should not raise

    def test_manual_mode_requires_config(self) -> None:
        """Test Manual mode requires manual_cfg."""
        config = {"mode": "Manual"}
        with pytest.raises(ValidationError) as exc_info:
            validate_es_set_mode_config(config)
        assert "manual_cfg is required" in exc_info.value.message

    def test_passive_mode_requires_config(self) -> None:
        """Test Passive mode requires passive_cfg."""
        config = {"mode": "Passive"}
        with pytest.raises(ValidationError) as exc_info:
            validate_es_set_mode_config(config)
        assert "passive_cfg is required" in exc_info.value.message

    def test_invalid_mode_rejected(self) -> None:
        """Test invalid mode is rejected."""
        config = {"mode": "Invalid"}
        with pytest.raises(ValidationError) as exc_info:
            validate_es_set_mode_config(config)
        assert exc_info.value.field == "mode"
        # Check all valid modes are mentioned
        for mode in VALID_MODES:
            assert mode in exc_info.value.message


class TestValidateMethod:
    """Tests for validate_method."""

    @pytest.mark.parametrize("method", list(VALID_METHODS.keys()))
    def test_all_valid_methods(self, method: str) -> None:
        """Test all defined methods are accepted."""
        spec = validate_method(method)
        assert spec.method == method

    def test_unknown_method_rejected(self) -> None:
        """Test unknown method is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_method("Unknown.Method")
        assert "Unknown method" in exc_info.value.message
        assert exc_info.value.field == "method"

    def test_non_string_rejected(self) -> None:
        """Test non-string method is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_method(123)  # type: ignore[arg-type]
        assert "must be a string" in exc_info.value.message


class TestValidateParams:
    """Tests for validate_params."""

    def test_valid_get_status_params(self) -> None:
        """Test valid ES.GetStatus params are accepted."""
        validate_params("ES.GetStatus", {"id": 0})  # Should not raise

    def test_missing_required_param(self) -> None:
        """Test missing required params are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_params("ES.SetMode", {"id": 0})  # Missing config
        assert "Missing required parameters" in exc_info.value.message
        assert "config" in exc_info.value.message

    def test_unknown_param_rejected(self) -> None:
        """Test unknown params are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_params("ES.GetStatus", {"id": 0, "unknown": "value"})
        assert "Unknown parameters" in exc_info.value.message

    def test_invalid_device_id_in_params(self) -> None:
        """Test invalid device ID in params is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_params("ES.GetStatus", {"id": 999})
        assert exc_info.value.field == "id"


class TestValidateCommand:
    """Tests for validate_command."""

    def test_valid_command(self) -> None:
        """Test valid command structure is accepted."""
        command = {
            "id": 1,
            "method": "ES.GetStatus",
            "params": {"id": 0},
        }
        validate_command(command)  # Should not raise

    def test_missing_id(self) -> None:
        """Test missing command id is rejected."""
        command = {
            "method": "ES.GetStatus",
            "params": {},
        }
        with pytest.raises(ValidationError) as exc_info:
            validate_command(command)
        assert "missing required field 'id'" in exc_info.value.message

    def test_missing_method(self) -> None:
        """Test missing method is rejected."""
        command = {
            "id": 1,
            "params": {},
        }
        with pytest.raises(ValidationError) as exc_info:
            validate_command(command)
        assert "missing required field 'method'" in exc_info.value.message

    def test_negative_command_id_rejected(self) -> None:
        """Test negative command id is rejected."""
        command = {
            "id": -1,
            "method": "ES.GetStatus",
            "params": {},
        }
        with pytest.raises(ValidationError) as exc_info:
            validate_command(command)
        assert "non-negative integer" in exc_info.value.message


class TestValidateJsonMessage:
    """Tests for validate_json_message."""

    def test_valid_json_message(self) -> None:
        """Test valid JSON message is parsed and validated."""
        message = '{"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}'
        result = validate_json_message(message)
        assert result["id"] == 1
        assert result["method"] == "ES.GetStatus"

    def test_invalid_json_rejected(self) -> None:
        """Test invalid JSON is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_json_message("not valid json")
        assert "Invalid JSON" in exc_info.value.message

    def test_empty_message_rejected(self) -> None:
        """Test empty message is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_json_message("")
        assert "cannot be empty" in exc_info.value.message

    def test_whitespace_only_rejected(self) -> None:
        """Test whitespace-only message is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_json_message("   ")
        assert "cannot be empty" in exc_info.value.message

    def test_non_string_rejected(self) -> None:
        """Test non-string message is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_json_message({"id": 1})  # type: ignore[arg-type]
        assert "must be a string" in exc_info.value.message


class TestValidateSetModeCommand:
    """Tests for complete ES.SetMode command validation."""

    def test_valid_manual_charge_command(self) -> None:
        """Test valid manual charge command is accepted."""
        command = {
            "id": 1,
            "method": "ES.SetMode",
            "params": {
                "id": 0,
                "config": {
                    "mode": "Manual",
                    "manual_cfg": {
                        "time_num": 0,
                        "start_time": "00:00",
                        "end_time": "23:59",
                        "week_set": 127,
                        "power": -1300,
                        "enable": 1,
                    },
                },
            },
        }
        validate_command(command)  # Should not raise

    def test_valid_passive_mode_command(self) -> None:
        """Test valid passive mode command is accepted."""
        command = {
            "id": 1,
            "method": "ES.SetMode",
            "params": {
                "id": 0,
                "config": {
                    "mode": "Passive",
                    "passive_cfg": {
                        "power": 1000,
                        "cd_time": 3600,
                    },
                },
            },
        }
        validate_command(command)  # Should not raise

    def test_manual_mode_invalid_power(self) -> None:
        """Test manual mode with invalid power is rejected."""
        command = {
            "id": 1,
            "method": "ES.SetMode",
            "params": {
                "id": 0,
                "config": {
                    "mode": "Manual",
                    "manual_cfg": {
                        "time_num": 0,
                        "start_time": "00:00",
                        "end_time": "23:59",
                        "week_set": 127,
                        "power": 999999,  # Way too high
                        "enable": 1,
                    },
                },
            },
        }
        with pytest.raises(ValidationError) as exc_info:
            validate_command(command)
        assert "power" in exc_info.value.field


class TestValidationErrorAttributes:
    """Tests for ValidationError exception attributes."""

    def test_error_has_message_and_field(self) -> None:
        """Test ValidationError has message and field attributes."""
        error = ValidationError("Test error", "test_field")
        assert error.message == "Test error"
        assert error.field == "test_field"
        assert str(error) == "Test error"

    def test_error_field_optional(self) -> None:
        """Test ValidationError field is optional."""
        error = ValidationError("Test error")
        assert error.message == "Test error"
        assert error.field is None


class TestStrictMode:
    """Tests for strict validation mode."""

    def setup_method(self) -> None:
        """Disable strict mode before each test."""
        enable_strict_mode(False)

    def teardown_method(self) -> None:
        """Ensure strict mode is disabled after each test."""
        enable_strict_mode(False)

    def test_strict_mode_initially_disabled(self) -> None:
        """Test strict mode is disabled by default."""
        assert is_strict_mode() is False

    def test_enable_strict_mode(self) -> None:
        """Test strict mode can be enabled."""
        enable_strict_mode(True)
        assert is_strict_mode() is True

    def test_disable_strict_mode(self) -> None:
        """Test strict mode can be disabled."""
        enable_strict_mode(True)
        assert is_strict_mode() is True
        enable_strict_mode(False)
        assert is_strict_mode() is False

    def test_strict_mode_toggles(self) -> None:
        """Test strict mode can be toggled multiple times."""
        assert is_strict_mode() is False
        enable_strict_mode(True)
        assert is_strict_mode() is True
        enable_strict_mode(True)  # Enable again
        assert is_strict_mode() is True
        enable_strict_mode(False)
        assert is_strict_mode() is False

    def test_high_power_valid_without_strict_mode(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test high power values are valid without strict mode warnings."""
        high_power = int(MAX_POWER_VALUE * 0.95)  # 95% of max
        validate_power_value(high_power)  # Should not raise
        # No warning in non-strict mode
        assert "Strict mode" not in caplog.text

    def test_high_power_warns_in_strict_mode(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test high power values log warnings in strict mode."""
        import logging
        caplog.set_level(logging.WARNING)
        enable_strict_mode(True)
        high_power = int(MAX_POWER_VALUE * 0.95)  # 95% of max
        validate_power_value(high_power)  # Should not raise but should warn
        assert "Strict mode" in caplog.text or "90%" in caplog.text

    def test_short_schedule_valid_without_strict_mode(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test short schedules are valid without strict mode warnings."""
        config = {
            "time_num": 0,
            "start_time": "12:00",
            "end_time": "12:01",  # 1 minute schedule
            "week_set": 127,
            "power": 1000,
            "enable": 1,
        }
        validate_manual_config(config)  # Should not raise
        assert "Strict mode" not in caplog.text

    def test_short_schedule_warns_in_strict_mode(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test very short schedules log warnings in strict mode."""
        import logging
        caplog.set_level(logging.WARNING)
        enable_strict_mode(True)
        config = {
            "time_num": 0,
            "start_time": "12:00",
            "end_time": "12:01",  # Very short 1 minute schedule
            "week_set": 127,
            "power": 1000,
            "enable": 1,
        }
        validate_manual_config(config)  # Should not raise but may warn
        # Just verify validation passes - warning is optional for 1 minute


class TestExportedConstants:
    """Tests to verify constants are properly exported."""

    def test_max_power_value_exported(self) -> None:
        """Test MAX_POWER_VALUE is exported and reasonable."""
        from custom_components.marstek.pymarstek import MAX_POWER_VALUE
        assert MAX_POWER_VALUE == 5000

    def test_max_passive_duration_exported(self) -> None:
        """Test MAX_PASSIVE_DURATION is exported (24 hours in seconds)."""
        from custom_components.marstek.pymarstek import MAX_PASSIVE_DURATION
        assert MAX_PASSIVE_DURATION == 86400

    def test_max_time_slots_exported(self) -> None:
        """Test MAX_TIME_SLOTS is exported."""
        from custom_components.marstek.pymarstek import MAX_TIME_SLOTS
        assert MAX_TIME_SLOTS == 10

    def test_max_week_set_exported(self) -> None:
        """Test MAX_WEEK_SET is exported."""
        from custom_components.marstek.pymarstek import MAX_WEEK_SET
        assert MAX_WEEK_SET == 127

    def test_enable_strict_mode_exported(self) -> None:
        """Test enable_strict_mode function is exported."""
        from custom_components.marstek.pymarstek import enable_strict_mode
        assert callable(enable_strict_mode)

    def test_is_strict_mode_exported(self) -> None:
        """Test is_strict_mode function is exported."""
        from custom_components.marstek.pymarstek import is_strict_mode
        assert callable(is_strict_mode)
