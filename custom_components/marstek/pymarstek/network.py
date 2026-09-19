"""Network helpers for pymarstek."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from collections.abc import Mapping
from contextlib import suppress
from typing import Any, Protocol

_LOGGER = logging.getLogger(__name__)

# Open API `src` is typically "{model}-{ble_mac}", e.g. "VenusC-AABBCCDDEEFF".
_SRC_MAC_SEPARATED = re.compile(
    r"(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}"
)
_SRC_MAC_COMPACT = re.compile(r"[0-9A-Fa-f]{12}")


def mac_from_openapi_src(src: Any) -> str:
    """Extract a MAC address from a GetDevice ``src`` field.

    Some firmware builds (observed on Venus C ``ver`` 153) omit ``ble_mac`` /
    ``wifi_mac`` from ``result`` while still embedding the BLE MAC in ``src``.
    """
    if not isinstance(src, str) or not src:
        return ""
    separated = _SRC_MAC_SEPARATED.search(src)
    raw = separated.group(0) if separated else ""
    if not raw:
        compact = _SRC_MAC_COMPACT.search(src)
        raw = compact.group(0) if compact else ""
    if not raw:
        return ""
    hex_only = re.sub(r"[:\-]", "", raw)
    if len(hex_only) != 12:
        return ""
    return ":".join(hex_only[index : index + 2] for index in range(0, 12, 2))


def udp_source_matches_host(source_ip: str, host: str) -> bool:
    """Return True when a UDP sender is the host we queried.

    Unicast GetDevice must not accept another device's reply. Numeric IPs
    compare directly; hostnames resolve to IPv4 addresses.
    """
    if source_ip == host:
        return True
    try:
        return ipaddress.ip_address(source_ip) == ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(
            host,
            None,
            family=socket.AF_INET,
            type=socket.SOCK_DGRAM,
        )
    except OSError:
        return False
    return any(info[4][0] == source_ip for info in infos)


def is_loopback_host(host: str) -> bool:
    """Return True when *host* is a loopback address or localhost name."""
    if host in {"localhost", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def create_udp_socket(
    *,
    bind_port: int,
    broadcast: bool = False,
    fallback_ephemeral: bool = False,
    logger: logging.Logger | None = None,
) -> socket.socket:
    """Create a non-blocking IPv4 UDP socket bound to ``bind_port``.

    Marstek Open API firmware replies to the device listen port (default
    30000), not to an ephemeral client source port. Bind to that port so
    unicast and broadcast replies are receivable.

    See Python's ``loop.create_datagram_endpoint`` notes on ``reuse_port``:
    https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.create_datagram_endpoint

    Args:
        bind_port: Local UDP port to bind. ``0`` asks the OS for ephemeral.
        broadcast: Enable ``SO_BROADCAST`` for discovery probes.
        fallback_ephemeral: If the requested port cannot be bound, bind to
            an ephemeral port instead of raising.
        logger: Optional logger for bind diagnostics.

    Returns:
        A non-blocking datagram socket bound to a local address.

    Raises:
        OSError: If the socket cannot be bound.
    """
    log = logger or _LOGGER
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if broadcast:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        with suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.setblocking(False)

    try:
        sock.bind(("0.0.0.0", bind_port))
    except OSError as err:
        if not fallback_ephemeral or bind_port == 0:
            sock.close()
            raise
        log.warning(
            "Could not bind UDP socket to port %s (%s); falling back to an ephemeral port",
            bind_port,
            err,
        )
        try:
            sock.bind(("0.0.0.0", 0))
        except OSError:
            sock.close()
            raise

    bound = sock.getsockname()
    if isinstance(bound, tuple) and len(bound) >= 2:
        log.debug("UDP socket bound to %s:%s", bound[0], bound[1])
    return sock


class PsutilAddress(Protocol):
    """Protocol for psutil address info objects."""

    family: int
    address: str
    broadcast: str | None
    netmask: str | None


class PsutilModule(Protocol):
    """Protocol for psutil module functions used here."""

    def net_if_addrs(self) -> Mapping[str, list[PsutilAddress]]: ...


def get_broadcast_addresses(
    *,
    psutil_module: PsutilModule | None = None,
    logger: logging.Logger | None = None,
    allow_import: bool = True,
) -> list[str]:
    """Get broadcast addresses for all network interfaces."""
    addresses: set[str] = {"255.255.255.255"}

    if logger is None:
        logger = _LOGGER

    if psutil_module is None:
        if not allow_import:
            logger.debug("psutil not available, using only global broadcast")
            return list(addresses)
        try:
            import importlib

            psutil_module = importlib.import_module("psutil")
        except Exception:
            logger.debug("psutil not available, using only global broadcast")
            return list(addresses)

    if psutil_module is None:
        logger.debug("psutil not available, using only global broadcast")
        return list(addresses)

    try:
        for addrs in psutil_module.net_if_addrs().values():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    broadcast = getattr(addr, "broadcast", None)
                    if isinstance(broadcast, str):
                        addresses.add(broadcast)
                        continue
                    netmask = getattr(addr, "netmask", None)
                    if isinstance(netmask, str):
                        try:
                            network = ipaddress.IPv4Network(
                                f"{addr.address}/{netmask}", strict=False
                            )
                            addresses.add(str(network.broadcast_address))
                        except (ValueError, OSError):
                            continue
    except OSError as err:
        logger.warning("Failed to get network interfaces: %s", err)

    try:
        local_ips = {
            addr.address
            for addrs in psutil_module.net_if_addrs().values()
            for addr in addrs
            if addr.family == socket.AF_INET
        }
        addresses -= local_ips
    except OSError:
        pass

    return list(addresses)
