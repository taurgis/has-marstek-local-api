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

# Rev 3.1 ``manual_cfg.manual_set``, Venus E mini only. The PDF also lists
# ``3`` (auto). This integration does not send 3 until a real device
# confirms it: an enabled 0 W slot sends charge (1) with power 0.
MANUAL_SET_DISABLE = 0
MANUAL_SET_CHARGE = 1
MANUAL_SET_DISCHARGE = 2


def manual_set_for_slot(*, power: int, enable: bool) -> int:
    """Derive Rev 3.1 Venus E mini ``manual_set`` from signed user power.

    Home Assistant services and device actions stay signed (negative charge,
    positive discharge). The Open API field is a separate flag; the official
    Manual example pairs ``power`` 100 with ``manual_set`` 1. Disabled slots
    send 0. An enabled zero-watt slot sends charge (1), not auto (3).
    """
    if not enable:
        return MANUAL_SET_DISABLE
    if power > 0:
        return MANUAL_SET_DISCHARGE
    return MANUAL_SET_CHARGE


def build_manual_mode_config(
    power: int,
    enable: bool,
    *,
    time_num: int = 0,
    start_time: str = "00:00",
    end_time: str = "23:59",
    week_set: int = WEEKDAYS_ALL,
    include_manual_set: bool = False,
) -> dict[str, Any]:
    """Build manual mode configuration payload.

    When ``include_manual_set`` is true (Venus E mini), ``power`` on the
    wire is ``abs(power)`` and ``manual_set`` carries the direction.
    Other families keep the signed ``power`` they already accept.
    """
    manual_cfg: dict[str, Any] = {
        "time_num": time_num,
        "start_time": start_time,
        "end_time": end_time,
        "week_set": week_set,
        "power": abs(power) if include_manual_set else power,
        "enable": 1 if enable else 0,
    }
    if include_manual_set:
        manual_cfg["manual_set"] = manual_set_for_slot(power=power, enable=enable)
    return {
        "mode": MODE_TO_API.get(MODE_MANUAL, MODE_MANUAL),
        "manual_cfg": manual_cfg,
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
