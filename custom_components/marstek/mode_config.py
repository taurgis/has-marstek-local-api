"""Utilities for building mode configuration payloads."""

from __future__ import annotations

from typing import Any

from .const import (
    MODE_AI,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_PASSIVE,
    MODE_TO_API,
    MODE_UPS,
    WEEKDAYS_ALL,
)


def build_manual_mode_config(
    power: int,
    enable: bool,
    *,
    time_num: int = 0,
    start_time: str = "00:00",
    end_time: str = "23:59",
    week_set: int = WEEKDAYS_ALL,
) -> dict[str, Any]:
    """Build manual mode configuration payload."""
    return {
        "mode": MODE_TO_API.get(MODE_MANUAL, MODE_MANUAL),
        "manual_cfg": {
            "time_num": time_num,
            "start_time": start_time,
            "end_time": end_time,
            "week_set": week_set,
            "power": power,
            "enable": 1 if enable else 0,
        },
    }


def build_mode_config(mode: str) -> dict[str, Any]:
    """Build the configuration payload for a mode.

    This is used for default select-based mode changes. Service calls can
    override these defaults with explicit parameters.
    """
    api_mode = MODE_TO_API.get(mode, mode)

    if mode == MODE_AUTO:
        return {
            "mode": api_mode,
            "auto_cfg": {"enable": 1},
        }

    if mode == MODE_AI:
        return {
            "mode": api_mode,
            "ai_cfg": {"enable": 1},
        }

    if mode == MODE_MANUAL:
        return build_manual_mode_config(power=0, enable=False)

    if mode == MODE_PASSIVE:
        return {
            "mode": api_mode,
            "passive_cfg": {
                "power": 0,
                "cd_time": 3600,
            },
        }

    if mode == MODE_UPS:
        return {
            "mode": api_mode,
            "ups_cfg": {"enable": 1},
        }

    raise ValueError(f"Unknown mode: {mode}")
