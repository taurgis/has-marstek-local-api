"""Tests for mode_config module."""

from __future__ import annotations

import pytest

from custom_components.marstek.const import (
    MODE_AI,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_PASSIVE,
    MODE_TO_API,
    MODE_UPS,
    WEEKDAYS_ALL,
)
from custom_components.marstek.mode_config import (
    MANUAL_SET_CHARGE,
    MANUAL_SET_DISABLE,
    MANUAL_SET_DISCHARGE,
    build_manual_mode_config,
    build_mode_config,
    manual_set_for_slot,
)


class TestBuildModeConfig:
    """Tests for build_mode_config function."""

    def test_auto_mode(self) -> None:
        """Test building Auto mode configuration."""
        result = build_mode_config(MODE_AUTO)

        assert result["mode"] == MODE_TO_API[MODE_AUTO]
        assert result["auto_cfg"]["enable"] == 1

    def test_ai_mode(self) -> None:
        """Test building AI mode configuration."""
        result = build_mode_config(MODE_AI)

        assert result["mode"] == MODE_TO_API[MODE_AI]
        assert result["ai_cfg"]["enable"] == 1

    def test_manual_mode(self) -> None:
        """Test building Manual mode configuration."""
        result = build_mode_config(MODE_MANUAL)

        assert result["mode"] == MODE_TO_API[MODE_MANUAL]
        manual_cfg = result["manual_cfg"]
        assert manual_cfg["time_num"] == 0
        assert manual_cfg["start_time"] == "00:00"
        assert manual_cfg["end_time"] == "23:59"
        assert manual_cfg["week_set"] == WEEKDAYS_ALL
        assert manual_cfg["power"] == 0
        assert manual_cfg["enable"] == 0
        assert "manual_set" not in manual_cfg

    def test_passive_mode(self) -> None:
        """Test building Passive mode configuration."""
        result = build_mode_config(MODE_PASSIVE)

        assert result["mode"] == MODE_TO_API[MODE_PASSIVE]
        passive_cfg = result["passive_cfg"]
        assert passive_cfg["power"] == 0
        assert passive_cfg["cd_time"] == 3600

    def test_ups_mode(self) -> None:
        """Test building UPS mode configuration."""
        result = build_mode_config(MODE_UPS)

        assert result == {"mode": "UPS", "ups_cfg": {"enable": 1}}
        assert list(result.keys()) == ["mode", "ups_cfg"]

    def test_unknown_mode_raises(self) -> None:
        """Test that unknown mode raises ValueError."""
        with pytest.raises(ValueError, match="Unknown mode: invalid_mode"):
            build_mode_config("invalid_mode")

    def test_all_modes_return_dict(self) -> None:
        """Test that all valid modes return dictionaries."""
        for mode in [MODE_AUTO, MODE_AI, MODE_MANUAL, MODE_PASSIVE, MODE_UPS]:
            result = build_mode_config(mode)
            assert isinstance(result, dict)
            assert "mode" in result

    def test_mode_values_match_api_constants(self) -> None:
        """Test that mode values match API constants."""
        from custom_components.marstek.const import (
            API_MODE_AI,
            API_MODE_AUTO,
            API_MODE_MANUAL,
            API_MODE_PASSIVE,
            API_MODE_UPS,
        )

        auto_config = build_mode_config(MODE_AUTO)
        assert auto_config["mode"] == API_MODE_AUTO

        ai_config = build_mode_config(MODE_AI)
        assert ai_config["mode"] == API_MODE_AI

        manual_config = build_mode_config(MODE_MANUAL)
        assert manual_config["mode"] == API_MODE_MANUAL

        passive_config = build_mode_config(MODE_PASSIVE)
        assert passive_config["mode"] == API_MODE_PASSIVE

        ups_config = build_mode_config(MODE_UPS)
        assert ups_config["mode"] == API_MODE_UPS


class TestManualSetForSlot:
    """Rev 3.1 Venus E mini ``manual_set`` is derived from signed user watts."""

    def test_disable_when_slot_is_off(self) -> None:
        """A disabled slot always sends 0, even if power is set."""
        assert manual_set_for_slot(power=-500, enable=False) == MANUAL_SET_DISABLE
        assert manual_set_for_slot(power=800, enable=False) == MANUAL_SET_DISABLE

    def test_charge_from_negative_power(self) -> None:
        """Negative watts mean charge on every other Marstek payload."""
        assert manual_set_for_slot(power=-2000, enable=True) == MANUAL_SET_CHARGE

    def test_discharge_from_positive_power(self) -> None:
        """Positive watts mean discharge."""
        assert manual_set_for_slot(power=800, enable=True) == MANUAL_SET_DISCHARGE

    def test_zero_power_sends_charge_not_auto(self) -> None:
        """An enabled 0 W slot sends charge until hardware confirms auto."""
        assert manual_set_for_slot(power=0, enable=True) == MANUAL_SET_CHARGE

    def test_payload_includes_manual_set_only_when_requested(self) -> None:
        """Venus A/C/D/E omit the field; the mini includes unsigned power."""
        without = build_manual_mode_config(power=-500, enable=True)
        assert "manual_set" not in without["manual_cfg"]
        assert without["manual_cfg"]["power"] == -500
        with_field = build_manual_mode_config(power=-500, enable=True, include_manual_set=True)
        assert with_field["manual_cfg"]["manual_set"] == MANUAL_SET_CHARGE
        assert with_field["manual_cfg"]["power"] == 500

    def test_mini_discharge_keeps_positive_magnitude(self) -> None:
        """Positive user watts stay a positive wire magnitude with discharge."""
        payload = build_manual_mode_config(power=800, enable=True, include_manual_set=True)
        assert payload["manual_cfg"]["manual_set"] == MANUAL_SET_DISCHARGE
        assert payload["manual_cfg"]["power"] == 800

    def test_mini_disabled_slot_sends_manual_set_zero(self) -> None:
        """A disabled mini slot still sends enable 0 and manual_set 0."""
        payload = build_manual_mode_config(power=-500, enable=False, include_manual_set=True)
        assert payload["manual_cfg"]["enable"] == 0
        assert payload["manual_cfg"]["manual_set"] == MANUAL_SET_DISABLE
        assert payload["manual_cfg"]["power"] == 500
