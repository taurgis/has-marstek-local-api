"""Open API UDP port bookkeeping.

Marstek firmware answers on the port it listens on, and that port is
user-configurable per device. Deciding which port to bind, which port an
entry is configured for, and which ports a scan must probe all follow from
that one fact, so they live together here.
"""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT

from ..const import DEFAULT_UDP_PORT
from ..pymarstek.network import is_loopback_host

# Ports the community configures often enough that a scan should try them
# even when no entry uses one yet.
COMMON_CUSTOM_PORTS: tuple[int, ...] = (30001, 30002, 30003, 30004, 30030)


def bind_port_for_host(host: str, port: int) -> int:
    """Return the local UDP bind port for a device endpoint.

    Loopback devices already occupy their Open API port, so bind ephemeral.
    """
    if is_loopback_host(host):
        return 0
    return port


def configured_device_port(entry: ConfigEntry) -> int:
    """Return the Open API port stored on a config entry."""
    raw = entry.data.get(CONF_PORT, DEFAULT_UDP_PORT)
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_UDP_PORT
    if not 1 <= port <= 65535:
        return DEFAULT_UDP_PORT
    return port


def entry_bind_port(entry: ConfigEntry) -> int:
    """Return the pool key for a config entry's UDP client."""
    host = entry.data.get(CONF_HOST)
    port = configured_device_port(entry)
    if not isinstance(host, str) or not host:
        return port
    return bind_port_for_host(host, port)


def discovery_scan_ports(entries: Iterable[ConfigEntry]) -> list[int]:
    """Return the UDP ports a broadcast scan should probe.

    The default port, the common custom ones, and every port already
    configured on an entry: a probe sent from 30000 never reaches a device
    listening on 30003, so a port left out is a device left undiscovered.
    """
    ports = {DEFAULT_UDP_PORT, *COMMON_CUSTOM_PORTS}
    ports.update(configured_device_port(entry) for entry in entries)
    return sorted(ports)
