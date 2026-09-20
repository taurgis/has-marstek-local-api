"""Tests for the Home Assistant side of discovery broadcast addressing."""

from __future__ import annotations

import threading
from ipaddress import IPv4Address
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.marstek.helpers.broadcast import async_broadcast_addresses

_MODULE = "custom_components.marstek.helpers.broadcast"


async def test_unions_home_assistant_and_interface_addresses(
    hass: HomeAssistant,
) -> None:
    """Neither source may narrow the sweep, so both are merged.

    Home Assistant collapses its adapter list to the default interface on a
    common setup; the interface table sees the rest.
    """
    with (
        patch(
            f"{_MODULE}.network.async_get_ipv4_broadcast_addresses",
            AsyncMock(return_value={IPv4Address("192.168.1.255")}),
        ),
        patch(
            f"{_MODULE}.get_broadcast_addresses",
            return_value=["10.0.0.255", "255.255.255.255"],
        ),
    ):
        assert await async_broadcast_addresses(hass) == [
            "10.0.0.255",
            "192.168.1.255",
            "255.255.255.255",
        ]


async def test_network_integration_failure_still_sweeps(hass: HomeAssistant) -> None:
    """A sweep reaching fewer interfaces still beats no sweep at all."""
    with (
        patch(
            f"{_MODULE}.network.async_get_ipv4_broadcast_addresses",
            AsyncMock(side_effect=RuntimeError("network integration unavailable")),
        ),
        patch(f"{_MODULE}.get_broadcast_addresses", return_value=["10.0.0.255"]),
    ):
        assert await async_broadcast_addresses(hass) == [
            "10.0.0.255",
            "255.255.255.255",
        ]


async def test_interface_table_is_read_off_the_event_loop(
    hass: HomeAssistant,
) -> None:
    """Reading the interface table imports psutil, which blocks the loop.

    https://developers.home-assistant.io/docs/asyncio_blocking_operations/
    """
    reading_thread: list[int] = []

    def _record_thread(**_kwargs: object) -> list[str]:
        reading_thread.append(threading.get_ident())
        return ["255.255.255.255"]

    with (
        patch(
            f"{_MODULE}.network.async_get_ipv4_broadcast_addresses",
            AsyncMock(return_value=set()),
        ),
        patch(f"{_MODULE}.get_broadcast_addresses", side_effect=_record_thread),
    ):
        await async_broadcast_addresses(hass)

    assert reading_thread and reading_thread[0] != threading.get_ident()
