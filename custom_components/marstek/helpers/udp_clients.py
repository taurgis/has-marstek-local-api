"""Per-port UDP client pool for Marstek Open API traffic.

Marstek firmware replies to the device listen port, not an ephemeral source
port. Devices that share a listen port share one bound socket. Distinct
user-configured ports each get their own socket so mixed 30000/30001/...
installs keep working.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

from ..const import (
    DATA_DISCOVERY_LOCK,
    DATA_ENTRY_BIND_PORTS,
    DATA_UDP_CLIENT_OWNERS,
    DATA_UDP_CLIENTS,
    DATA_UDP_CLIENTS_LOCK,
    DEFAULT_UDP_PORT,
    DOMAIN,
)
from ..pymarstek import MarstekUDPClient
from ..pymarstek.network import is_loopback_host

_LOGGER = logging.getLogger(__name__)

_ACTIVE_RESOURCE_STATES = frozenset(
    {
        ConfigEntryState.LOADED,
        ConfigEntryState.SETUP_RETRY,
        ConfigEntryState.SETUP_IN_PROGRESS,
    }
)


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


def discovery_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Return the lock that serializes broadcast discovery vs pool changes."""
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    lock = domain_data.get(DATA_DISCOVERY_LOCK)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        domain_data[DATA_DISCOVERY_LOCK] = lock
    return lock


def _udp_client_owners(hass: HomeAssistant) -> dict[int, set[str]]:
    """Return bind-port → config-entry owner ids."""
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    owners = domain_data.get(DATA_UDP_CLIENT_OWNERS)
    if not isinstance(owners, dict):
        typed_owners: dict[int, set[str]] = {}
        domain_data[DATA_UDP_CLIENT_OWNERS] = typed_owners
        return typed_owners
    return cast(dict[int, set[str]], owners)


def _entry_bind_ports(hass: HomeAssistant) -> dict[str, int]:
    """Return config-entry id → leased bind port."""
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    leased = domain_data.get(DATA_ENTRY_BIND_PORTS)
    if not isinstance(leased, dict):
        typed_leased: dict[str, int] = {}
        domain_data[DATA_ENTRY_BIND_PORTS] = typed_leased
        return typed_leased
    return cast(dict[str, int], leased)


def domain_has_udp_leases(hass: HomeAssistant) -> bool:
    """Return True when any config entry still owns a pooled UDP client."""
    return bool(_entry_bind_ports(hass))


def acquire_udp_client_lease(
    hass: HomeAssistant, entry_id: str, bind_port: int
) -> None:
    """Record that *entry_id* owns the pooled client bound to *bind_port*."""
    leased = _entry_bind_ports(hass)
    owners = _udp_client_owners(hass)
    previous = leased.get(entry_id)
    if previous is not None and previous != bind_port:
        previous_owners = owners.get(previous)
        if previous_owners is not None:
            previous_owners.discard(entry_id)
            if not previous_owners:
                owners.pop(previous, None)
    leased[entry_id] = bind_port
    owners.setdefault(bind_port, set()).add(entry_id)


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
    leased_port = _entry_bind_ports(hass).get(entry.entry_id)
    if isinstance(leased_port, int):
        leased_client = get_udp_client(hass, leased_port)
        if leased_client is not None:
            return leased_client
    return get_udp_client(hass, entry_bind_port(entry))


def transfer_reset_prone_mark_for_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    old_host: str,
    new_host: str,
    *,
    new_port: int | None = None,
) -> None:
    """Move a config entry's reset-prone UDP mark when its endpoint changes.

    The mark lives on a pooled client. An IP-only change on the same bind
    port can transfer in place. A port change (or loopback/LAN bind change)
    must clear the old client and, when the new client already exists, mark
    the new IP there. Setup re-applies the mark if the new client is created
    later.
    """
    old_client = get_udp_client_for_entry(hass, entry)
    if old_client is None:
        return

    target_port = new_port if new_port is not None else configured_device_port(entry)
    new_bind_port = bind_port_for_host(new_host, target_port)
    new_client = get_udp_client(hass, new_bind_port)
    owner = entry.entry_id
    is_marked = getattr(old_client, "is_openapi_reset_prone", None)
    marked = (
        bool(is_marked(old_host, owner=owner)) if callable(is_marked) else True
    )

    if new_client is old_client:
        if old_host != new_host:
            old_client.transfer_openapi_reset_prone(
                old_host, new_host, owner=owner
            )
        return

    old_client.clear_openapi_reset_prone(old_host, owner=owner)
    if marked and new_client is not None:
        new_client.set_openapi_reset_prone(new_host, True, owner=owner)


def clear_reset_prone_owner_from_pool(hass: HomeAssistant, owner: str) -> None:
    """Drop *owner*'s reset-prone marks from every pooled UDP client."""
    for client in iter_udp_clients(hass):
        clear_owner = getattr(client, "clear_openapi_reset_prone_owner", None)
        if callable(clear_owner):
            clear_owner(owner)


def store_udp_client(
    hass: HomeAssistant, bind_port: int, client: MarstekUDPClient
) -> None:
    """Store *client* in the pool under *bind_port*."""
    udp_client_pool(hass)[bind_port] = client


def _bind_port_in_use(
    hass: HomeAssistant, bind_port: int, *, excluding_entry_id: str
) -> bool:
    """Return True when another entry still needs *bind_port*."""
    owners = _udp_client_owners(hass).get(bind_port, set())
    if any(owner != excluding_entry_id for owner in owners):
        return True
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.entry_id == excluding_entry_id:
            continue
        if other.state not in _ACTIVE_RESOURCE_STATES:
            continue
        if entry_bind_port(other) == bind_port:
            return True
    return False


async def async_release_udp_client_for_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Close the runtime client when no remaining entry needs its port."""
    async with discovery_lock(hass), udp_client_lock(hass):
        client = get_udp_client_for_entry(hass, entry)
        leased = _entry_bind_ports(hass)
        owners = _udp_client_owners(hass)
        bind_port = leased.pop(entry.entry_id, None)
        if isinstance(bind_port, int):
            port_owners = owners.get(bind_port)
            if port_owners is not None:
                port_owners.discard(entry.entry_id)
                if not port_owners:
                    owners.pop(bind_port, None)
        elif client is not None:
            runtime_port = getattr(client, "bind_port", None)
            bind_port = (
                runtime_port
                if isinstance(runtime_port, int)
                else entry_bind_port(entry)
            )
        else:
            bind_port = entry_bind_port(entry)

        if _bind_port_in_use(hass, bind_port, excluding_entry_id=entry.entry_id):
            return
        pool = udp_client_pool(hass)
        to_close: MarstekUDPClient | None = None
        if client is not None:
            for port, candidate in list(pool.items()):
                if candidate is client:
                    pool.pop(port, None)
            to_close = client
        else:
            to_close = pool.pop(bind_port, None)
    if to_close is not None:
        _LOGGER.debug("Closing UDP client bound to port %s", bind_port)
        await to_close.async_cleanup()


async def async_cleanup_all_udp_clients(hass: HomeAssistant) -> None:
    """Close every pooled UDP client."""
    async with discovery_lock(hass), udp_client_lock(hass):
        pool = udp_client_pool(hass)
        clients = list(pool.values())
        pool.clear()
        _udp_client_owners(hass).clear()
        _entry_bind_ports(hass).clear()
    for client in clients:
        await client.async_cleanup()


async def async_pause_udp_receivers(hass: HomeAssistant) -> tuple[MarstekUDPClient, ...]:
    """Pause pooled listeners so broadcast discovery can bind the same ports."""
    async with udp_client_lock(hass):
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
    except BaseException:
        await async_resume_udp_receivers(tuple(paused))
        raise
    return tuple(paused)


async def async_resume_udp_receivers(clients: tuple[MarstekUDPClient, ...]) -> None:
    """Restart pooled listeners after a discovery scan."""
    resume_error: BaseException | None = None
    for client in clients:
        resume = getattr(client, "async_resume_receiver", None)
        if not callable(resume):
            continue
        try:
            result = resume()
            if asyncio.iscoroutine(result):
                await result
        except BaseException as err:
            _LOGGER.exception("Failed to resume Open API UDP listener")
            if resume_error is None:
                resume_error = err
    if resume_error is not None:
        raise resume_error


@asynccontextmanager
async def async_paused_udp_receivers(
    hass: HomeAssistant,
) -> AsyncIterator[tuple[MarstekUDPClient, ...]]:
    """Pause pooled Open API listeners while broadcast discovery binds ports.

    Linux ``SO_REUSEPORT`` load-balances datagrams across sockets bound to
    the same port. Broadcast discovery must bind its own sockets, so pause
    the coordinator listeners first. Unicast GetDevice (manual add, Confirm
    device, repairs) must reuse the pooled client instead of binding again:
    pause does not unbind, and the kernel still delivers the reply to the
    existing socket.

    The discovery lock serializes scanner and config-flow broadcasts so two
    ``SO_REUSEPORT`` discovery sockets cannot steal each other's replies.
    UDP client create/close waits on the same lock.
    """
    async with discovery_lock(hass):
        paused = await async_pause_udp_receivers(hass)
        try:
            yield paused
        finally:
            await async_resume_udp_receivers(paused)
