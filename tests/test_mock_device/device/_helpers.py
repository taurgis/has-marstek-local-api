"""Helpers shared by the device tests."""

from __future__ import annotations


def _ups_set_mode_params() -> dict[str, object]:
    return {
        "id": 0,
        "config": {"mode": "UPS", "ups_cfg": {"enable": 1}},
    }
