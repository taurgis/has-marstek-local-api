"""Singleton, setup, scheduling and scan task lifecycle."""

from __future__ import annotations

import time
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.helpers.domain_data import domain_data
from custom_components.marstek.scanner import MarstekScanner


async def test_scanner_singleton(hass: HomeAssistant):
    """Test that async_get returns singleton instance."""
    scanner1 = MarstekScanner.async_get(hass)
    scanner2 = MarstekScanner.async_get(hass)

    assert scanner1 is scanner2


async def test_scanner_init(hass: HomeAssistant):
    """Test scanner initialization."""
    scanner = MarstekScanner(hass)

    assert scanner._hass is hass
    assert scanner._track_interval is None


async def test_scanner_async_setup(hass: HomeAssistant):
    """Test scanner setup starts interval tracking."""
    scanner = MarstekScanner(hass)

    with (
        patch("custom_components.marstek.scanner.async_track_time_interval") as mock_track,
        patch.object(scanner, "async_scan") as mock_scan,
    ):
        mock_track.return_value = MagicMock()

        await scanner.async_setup()

        mock_track.assert_called_once()
        mock_scan.assert_called_once()
        assert scanner._track_interval is not None


async def test_scanner_async_setup_noop_when_initialized(hass: HomeAssistant):
    """Test scanner setup returns early when already initialized."""
    scanner = MarstekScanner(hass)

    with (
        patch("custom_components.marstek.scanner.async_track_time_interval") as mock_track,
        patch.object(scanner, "async_scan") as mock_scan,
    ):
        mock_track.return_value = MagicMock()
        await scanner.async_setup()

        # Second call should no-op
        await scanner.async_setup()

        mock_track.assert_called_once()
        mock_scan.assert_called_once()


async def test_scanner_async_scan_creates_background_task(hass: HomeAssistant):
    """A sweep runs as a background task, not one Home Assistant waits for.

    A sweep sits out the discovery timeout waiting for replies. Created with
    async_create_task it would hold up async_block_till_done, and with it
    Home Assistant's shutdown, for that whole timeout.
    """
    scanner = MarstekScanner(hass)
    captured_coro = None

    def capture_task(coro, **kwargs):
        nonlocal captured_coro
        captured_coro = coro

    with (
        patch.object(hass, "async_create_background_task", side_effect=capture_task),
        patch.object(hass, "async_create_task", side_effect=AssertionError),
    ):
        scanner.async_scan()

    # Verify task was created and clean up the coroutine
    assert captured_coro is not None
    # Close the coroutine to prevent warning (we don't need to run it)
    captured_coro.close()


async def test_scanner_async_request_scan_debounced(hass: HomeAssistant) -> None:
    """Test async_request_scan debounces rapid scans."""
    scanner = MarstekScanner(hass)
    scanner._last_scan_monotonic = time.monotonic()

    assert scanner.async_request_scan() is False


async def test_scanner_async_request_scan_triggers(hass: HomeAssistant) -> None:
    """Test async_request_scan triggers a scan when not debounced."""
    scanner = MarstekScanner(hass)

    with patch.object(scanner, "async_scan") as mock_scan:
        assert scanner.async_request_scan() is True
        mock_scan.assert_called_once()


async def test_scanner_build_scan_ports_includes_30030(hass: HomeAssistant) -> None:
    """Test scanner probes port 30030 by default for custom-port devices."""
    scanner = MarstekScanner(hass)

    assert 30030 in scanner._build_scan_ports()


async def test_scanner_build_scan_ports_includes_30004(hass: HomeAssistant) -> None:
    """Test scanner probes port 30004 used by the Venus A 150 Docker mock."""
    scanner = MarstekScanner(hass)

    assert 30004 in scanner._build_scan_ports()


async def test_scanner_scan_impl_no_devices(hass: HomeAssistant):
    """Test _async_scan_impl when no devices are discovered."""
    scanner = MarstekScanner(hass)

    with patch(
        "custom_components.marstek.scanner.discover_devices",
        AsyncMock(return_value=[]),
    ) as mock_discover:
        await scanner._async_scan_impl()

        mock_discover.assert_called_once()
        assert "ports" in mock_discover.call_args.kwargs


async def test_scanner_pauses_shared_receiver_during_scan(hass: HomeAssistant) -> None:
    """Test scanner pauses the shared UDP listener while it binds the Open API port."""
    client = MagicMock()
    client.async_pause_receiver = AsyncMock()
    client.async_resume_receiver = AsyncMock()
    domain_data(hass).udp_clients[30000] = client
    scanner = MarstekScanner(hass)

    with patch(
        "custom_components.marstek.scanner.discover_devices",
        AsyncMock(return_value=[]),
    ):
        await scanner._async_scan_impl()

    client.async_pause_receiver.assert_awaited_once()
    client.async_resume_receiver.assert_awaited_once()


async def test_scanner_pauses_all_port_clients_during_scan(hass: HomeAssistant) -> None:
    """Test scanner pauses every pooled UDP listener, not only the first port."""
    client_a = MagicMock()
    client_a.async_pause_receiver = AsyncMock()
    client_a.async_resume_receiver = AsyncMock()
    client_b = MagicMock()
    client_b.async_pause_receiver = AsyncMock()
    client_b.async_resume_receiver = AsyncMock()
    domain_data(hass).udp_clients.update({30000: client_a, 30003: client_b})
    scanner = MarstekScanner(hass)

    with patch(
        "custom_components.marstek.scanner.discover_devices",
        AsyncMock(return_value=[]),
    ):
        await scanner._async_scan_impl()

    client_a.async_pause_receiver.assert_awaited_once()
    client_b.async_pause_receiver.assert_awaited_once()
    client_a.async_resume_receiver.assert_awaited_once()
    client_b.async_resume_receiver.assert_awaited_once()


async def test_scanner_resumes_shared_receiver_after_scan_error(
    hass: HomeAssistant,
) -> None:
    """Test scanner resumes the shared UDP listener even when discovery fails."""
    client = MagicMock()
    client.async_pause_receiver = AsyncMock()
    client.async_resume_receiver = AsyncMock()
    domain_data(hass).udp_clients[30000] = client
    scanner = MarstekScanner(hass)

    with patch(
        "custom_components.marstek.scanner.discover_devices",
        AsyncMock(side_effect=OSError("bind failed")),
    ):
        await scanner._async_scan_impl()

    client.async_pause_receiver.assert_awaited_once()
    client.async_resume_receiver.assert_awaited_once()


async def test_scanner_scan_impl_exception_handling(hass: HomeAssistant):
    """Test _async_scan_impl handles exceptions gracefully."""
    scanner = MarstekScanner(hass)

    with patch(
        "custom_components.marstek.scanner.discover_devices",
        AsyncMock(side_effect=Exception("Network error")),
    ):
        # Should not raise - exceptions are caught
        await scanner._async_scan_impl()


async def test_scanner_scan_impl_none_devices(hass: HomeAssistant):
    """Test _async_scan_impl when discover_devices returns None."""
    scanner = MarstekScanner(hass)

    with patch(
        "custom_components.marstek.scanner.discover_devices",
        AsyncMock(return_value=None),
    ):
        # Should not raise
        await scanner._async_scan_impl()


async def test_scanner_async_unload_cancels_task(hass: HomeAssistant):
    """Test async_unload cancels running scan task."""
    import asyncio

    scanner = MarstekScanner(hass)

    # Setup the scanner first
    with (
        patch("custom_components.marstek.scanner.async_track_time_interval") as mock_track,
        patch.object(scanner, "async_scan"),
    ):
        mock_cancel = MagicMock()
        mock_track.return_value = mock_cancel

        await scanner.async_setup()

        # Simulate a long-running scan task
        async def slow_scan():
            await asyncio.sleep(10)

        scanner._scan_task = asyncio.create_task(slow_scan())

        # Unload should cancel the task
        await scanner.async_unload()

        assert scanner._track_interval is None
        assert scanner._scan_task is None
        mock_cancel.assert_called_once()


async def test_scanner_async_scan_skips_if_previous_running(hass: HomeAssistant):
    """Test async_scan skips if previous scan task is still running."""
    import asyncio

    scanner = MarstekScanner(hass)

    # Create a task that hasn't completed
    async def slow_scan():
        await asyncio.sleep(10)

    scanner._scan_task = asyncio.create_task(slow_scan())

    # Try to start a new scan - should skip
    scanner.async_scan()

    # Should still be the original task (not replaced)
    assert not scanner._scan_task.done()

    # Cleanup
    scanner._scan_task.cancel()
    with suppress(asyncio.CancelledError):
        await scanner._scan_task


async def test_scanner_scan_impl_continues_after_entry_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """One malformed entry must not skip IP recovery for the remaining devices."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.mock_state(hass, ConfigEntryState.LOADED)
    broken = MockConfigEntry(
        domain=DOMAIN,
        unique_id="02:de:ad:be:ef:00",
        data={"host": "9.9.9.9", "ble_mac": "02:DE:AD:BE:EF:00"},
    )
    broken.add_to_hass(hass)
    broken.mock_state(hass, ConfigEntryState.LOADED)

    scanner = MarstekScanner(hass)
    original = scanner._process_discovered_entry
    calls = {"count": 0}

    def _side_effect(entry: MockConfigEntry, devices: list[dict[str, object]]) -> None:
        calls["count"] += 1
        if entry is broken:
            raise RuntimeError("bad entry")
        original(entry, devices)

    with (
        patch(
            "custom_components.marstek.scanner.discover_devices",
            AsyncMock(
                return_value=[
                    {
                        "ip": "5.6.7.8",
                        "ble_mac": "AA:BB:CC:DD:EE:FF",
                    }
                ]
            ),
        ),
        patch.object(scanner, "_process_discovered_entry", side_effect=_side_effect),
        patch(
            "custom_components.marstek.scanner.discovery_flow.async_create_flow"
        ) as mock_create_flow,
    ):
        await scanner._async_scan_impl()

    assert calls["count"] == 2
    mock_create_flow.assert_called_once()
