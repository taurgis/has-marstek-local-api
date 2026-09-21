"""Typed ``hass.data`` container for the Marstek integration.

Everything shared between config entries lives on one object behind a
``HassKey``: the per-bind-port UDP client pool, the locks that serialize it,
the lease bookkeeping, and the set of entries whose next options update must
not trigger a reload. A config entry's own state stays in ``runtime_data`` —
only what several entries own at once belongs here.

https://developers.home-assistant.io/blog/2024/05/01/improved-hass-data-typing/
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.util.hass_dict import HassKey

from ..const import DOMAIN
from ..pymarstek import MarstekUDPClient

__all__ = ["MARSTEK_DATA", "MarstekDomainData", "domain_data", "peek_domain_data"]


@dataclass(slots=True)
class MarstekDomainData:
    """Shared state that outlives any single config entry."""

    #: Bind port -> the one client bound to it. Firmware replies to the
    #: device listen port, so entries on the same port share a socket.
    udp_clients: dict[int, MarstekUDPClient] = field(default_factory=dict)
    #: Serializes pool mutations.
    udp_clients_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    #: Serializes broadcast discovery against pool changes.
    discovery_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    #: Bind port -> the config entry ids that still need it.
    udp_client_owners: dict[int, set[str]] = field(default_factory=dict)
    #: Config entry id -> the bind port it leased.
    entry_bind_ports: dict[str, int] = field(default_factory=dict)
    #: Entry ids whose next options update is metadata-only, so the update
    #: listener skips the reload it would otherwise do.
    suppress_reloads: set[str] = field(default_factory=set)


MARSTEK_DATA: HassKey[MarstekDomainData] = HassKey(DOMAIN)


def domain_data(hass: HomeAssistant) -> MarstekDomainData:
    """Return the shared Marstek state, creating it on first use.

    A value stored at the wrong type — left by an older version, or by a
    test writing a bare dict — is replaced rather than used.
    """
    existing = hass.data.get(MARSTEK_DATA)
    if isinstance(existing, MarstekDomainData):
        return existing
    created = MarstekDomainData()
    hass.data[MARSTEK_DATA] = created
    return created


def peek_domain_data(hass: HomeAssistant) -> MarstekDomainData | None:
    """Return the shared state only if it already exists.

    For readers that must not resurrect the container after the last entry
    unloaded and popped it.
    """
    existing = hass.data.get(MARSTEK_DATA)
    return existing if isinstance(existing, MarstekDomainData) else None
