"""Utility functions for mock Marstek device."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

DEFAULT_STATE_DIR = Path.home() / ".marstek_mock_device"


def get_local_ip() -> str:
    """Get the local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def resolve_state_dir(state_dir: str | Path | None) -> Path:
    """Resolve the directory used to store persistent mock device state."""
    if state_dir is None:
        return DEFAULT_STATE_DIR
    return Path(state_dir).expanduser()


def _state_file_path(ble_mac: str, state_dir: str | Path | None) -> Path:
    normalized_ble = ble_mac.replace(":", "").lower()
    return resolve_state_dir(state_dir) / f"{normalized_ble}.json"


def load_persistent_state(
    ble_mac: str, state_dir: str | Path | None
) -> dict[str, Any] | None:
    """Load persisted state for a mock device, if available."""
    path = _state_file_path(ble_mac, state_dir)
    if not path.exists():
        return None
    try:
        data = path.read_text(encoding="utf-8")
        payload = json.loads(data)
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        return payload
    return None


def save_persistent_state(
    ble_mac: str, state_dir: str | Path | None, state: dict[str, Any]
) -> None:
    """Persist mock device state for later reuse."""
    path = _state_file_path(ble_mac, state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True)
    path.write_text(payload, encoding="utf-8")


def reset_persistent_state(ble_mac: str, state_dir: str | Path | None) -> None:
    """Remove any persisted state for the mock device."""
    path = _state_file_path(ble_mac, state_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        return
