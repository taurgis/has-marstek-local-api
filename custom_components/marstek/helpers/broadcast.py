"""Broadcast addresses for Marstek UDP discovery sweeps."""

from __future__ import annotations

import logging

from homeassistant.components import network
from homeassistant.components.network.const import IPV4_BROADCAST_ADDR
from homeassistant.core import HomeAssistant

from ..pymarstek.network import get_broadcast_addresses

_LOGGER = logging.getLogger(__name__)


async def async_broadcast_addresses(hass: HomeAssistant) -> list[str]:
    """Return every IPv4 broadcast address a discovery sweep should target.

    Two sources, unioned so neither can narrow the sweep:

    * ``homeassistant.components.network`` knows the adapters the user
      enabled in Home Assistant's network settings, including ones psutil
      is not installed to see.
    * The interface table read through psutil covers adapters Home Assistant
      collapses away when only the default one is enabled.

    The psutil pass imports a module and reads the interface table, both
    blocking, so it runs in the executor: Home Assistant reports an import
    made on the event loop as a blocking call.
    https://developers.home-assistant.io/docs/asyncio_blocking_operations/
    """
    addresses: set[str] = {IPV4_BROADCAST_ADDR}
    try:
        addresses.update(
            str(address)
            for address in await network.async_get_ipv4_broadcast_addresses(hass)
        )
    except Exception:
        # A sweep that reaches fewer interfaces still beats no sweep at all.
        _LOGGER.debug(
            "Could not read broadcast addresses from the network integration",
            exc_info=True,
        )
    addresses.update(
        await hass.async_add_executor_job(_interface_broadcast_addresses)
    )
    return sorted(addresses)


def _interface_broadcast_addresses() -> list[str]:
    """Read the interface table. Blocking; call it from the executor."""
    return get_broadcast_addresses(logger=_LOGGER)
