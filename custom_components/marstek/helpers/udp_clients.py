"""Per-port UDP client pool for Marstek Open API traffic.

Marstek firmware replies to the device listen port, not an ephemeral source
port. Devices that share a listen port share one bound socket. Distinct
user-configured ports each get their own socket so mixed 30000/30001/...
installs keep working.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

from ..const import (
    DATA_UDP_CLIENTS,
    DATA_UDP_CLIENTS_LOCK,
    DEFAULT_UDP_PORT,
    DOMAIN,
)
from ..pymarstek import MarstekUDPClient
from ..pymarstek.network import is_loopback_host

_LOGGER = logging.getLogger(__name__)


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


def udp_client_pool(hass: HomeAssistant) -> dict[int, MarstekUDPClient]:
    """Return the per-bind-port UDP client pool, creating it if needed."""
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    pool = domain_data.get(DATA_UDP_CLIENTS)
    if not isinstance(pool, dict):
        typed_pool: dict[int, MarstekUDPClient] = {}
        domain_data[DATA_UDP_CLIENTS] = typed_pool
        return typed_pool
    return cast(dict[int, MarstekUDPClient], pool)


def udp_client_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Return the lock that serializes pool mutations."""
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    lock = domain_data.get(DATA_UDP_CLIENTS_LOCK)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        domain_data[DATA_UDP_CLIENTS_LOCK] = lock
    return lock


def get_udp_client(hass: HomeAssistant, bind_port: int) -> MarstekUDPClient | None:
    """Return the pooled client for *bind_port*, if any."""
    return udp_client_pool(hass).get(bind_port)


def iter_udp_clients(hass: HomeAssistant) -> tuple[MarstekUDPClient, ...]:
    """Return a snapshot of pooled UDP clients."""
    return tuple(udp_client_pool(hass).values())


def get_udp_client_for_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> MarstekUDPClient | None:
    """Return the UDP client used by a config entry.

    Prefers the coordinator client created at setup so control paths follow
    the same socket as polling. Falls back to the bind-port pool.
    """
    runtime_data = getattr(entry, "runtime_data", None)
    coordinator = getattr(runtime_data, "coordinator", None)
    udp_client = getattr(coordinator, "udp_client", None)
    if udp_client is not None:
        return cast(MarstekUDPClient, udp_client)
    return get_udp_client(hass, entry_bind_port(entry))


def store_udp_client(
    hass: HomeAssistant, bind_port: int, client: MarstekUDPClient
) -> None:
    """Store *client* in the pool under *bind_port*."""
    udp_client_pool(hass)[bind_port] = client


def _bind_port_in_use(
    hass: HomeAssistant, bind_port: int, *, excluding_entry_id: str
) -> bool:
    """Return True when another loaded entry still needs *bind_port*."""
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.entry_id == excluding_entry_id:
            continue
        if other.state != ConfigEntryState.LOADED:
            continue
        if entry_bind_port(other) == bind_port:
            return True
    return False


async def async_release_udp_client_for_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Close the pooled client when no remaining loaded entry needs its port."""
    bind_port = entry_bind_port(entry)
    async with udp_client_lock(hass):
        if _bind_port_in_use(hass, bind_port, excluding_entry_id=entry.entry_id):
            return
        client = udp_client_pool(hass).pop(bind_port, None)
    if client is not None:
        _LOGGER.debug("Closing UDP client bound to port %s", bind_port)
        await client.async_cleanup()


async def async_cleanup_all_udp_clients(hass: HomeAssistant) -> None:
    """Close every pooled UDP client."""
    async with udp_client_lock(hass):
        pool = udp_client_pool(hass)
        clients = list(pool.values())
        pool.clear()
    for client in clients:
        await client.async_cleanup()


async def async_pause_udp_receivers(hass: HomeAssistant) -> tuple[MarstekUDPClient, ...]:
    """Pause pooled listeners so discovery can bind the same Open API ports."""
    clients = iter_udp_clients(hass)
    paused: list[MarstekUDPClient] = []
    try:
        for client in clients:
            pause = getattr(client, "async_pause_receiver", None)
            if callable(pause):
                result = pause()
                if asyncio.iscoroutine(result):
                    await result
            paused.append(client)
    except Exception:
        await async_resume_udp_receivers(tuple(paused))
        raise
    return tuple(paused)


async def async_resume_udp_receivers(clients: tuple[MarstekUDPClient, ...]) -> None:
    """Restart pooled listeners after a discovery scan."""
    for client in clients:
        resume = getattr(client, "async_resume_receiver", None)
        if callable(resume):
            result = resume()
            if asyncio.iscoroutine(result):
                await result
