"""Utility functions for mock Marstek device."""

from __future__ import annotations

import json
import socket
import struct
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


_SIOCGIFADDR = 0x8915
_SIOCGIFBRDADDR = 0x8919
LIMITED_BROADCAST = "255.255.255.255"


def get_broadcast_address(ip: str) -> str:
    """Return the subnet broadcast address of the interface holding ``ip``.

    Uses the Linux ``SIOCGIFADDR`` / ``SIOCGIFBRDADDR`` ioctls
    (netdevice(7)). Anywhere they are missing, or no interface holds
    ``ip``, fall back to the limited broadcast address.
    """
    try:
        import fcntl  # Linux only
    except ImportError:
        return LIMITED_BROADCAST
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        for _index, name in socket.if_nameindex():
            request = struct.pack("256s", name.encode()[:15])
            try:
                address = socket.inet_ntoa(
                    fcntl.ioctl(probe.fileno(), _SIOCGIFADDR, request)[20:24]
                )
                if address != ip:
                    continue
                return socket.inet_ntoa(
                    fcntl.ioctl(probe.fileno(), _SIOCGIFBRDADDR, request)[20:24]
                )
            except OSError:
                continue
    return LIMITED_BROADCAST


def resolve_state_dir(state_dir: str | Path | None) -> Path:
    """Resolve the directory used to store persistent mock device state."""
    if state_dir is None:
        return DEFAULT_STATE_DIR
    return Path(state_dir).expanduser()


def _state_file_path(ble_mac: str, state_dir: str | Path | None) -> Path:
    normalized_ble = ble_mac.replace(":", "").lower()
    return resolve_state_dir(state_dir) / f"{normalized_ble}.json"


def load_persistent_state(ble_mac: str, state_dir: str | Path | None) -> dict[str, Any] | None:
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
