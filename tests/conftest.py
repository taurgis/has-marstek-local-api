"""Fixtures for Marstek integration tests."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_socket
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from custom_components.marstek.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom components in HA test harness."""
    yield


_REAL_UDP_PATH_MARKERS = (
    "/tests/test_udp_client.py",
    "/tests/test_discovery.py",
    "/tests/test_scanner.py",
    "/tests/test_mock_device/",
)


@pytest.fixture(autouse=True)
def _guard_ha_test_sockets(request: pytest.FixtureRequest) -> Iterator[None]:
    """Keep HA 2026.9 pytest-socket happy without disabling UDP unit tests.

    Core's harness blocks INET sockets. Config-entry setup would otherwise
    bind a real UDP client. Mock-device tests and UDP/discovery/scanner
    tests still exercise sockets (or their own mocks).
    """
    path = Path(str(getattr(request, "path", request.fspath))).as_posix()
    if "/tests/test_mock_device/" in path:
        pytest_socket.enable_socket()
        yield
        return
    if any(marker in path for marker in _REAL_UDP_PATH_MARKERS):
        yield
        return
    with patch_marstek_integration():
        yield


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Snapshot assertion with HA extension."""
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Basic config entry for Marstek with unique_id for entity registry.

    Note: unique_id must be lowercase to match format_mac() output.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            "host": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus",
            "version": 3,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
    )


def create_mock_client(
    status: dict[str, Any] | Exception | None = None,
    setup_error: Exception | None = None,
    send_request_error: Exception | None = None,
) -> MagicMock:
    """Create a mock MarstekUDPClient.

    Args:
        status: Device status to return, or Exception to raise.
        setup_error: Exception to raise during async_setup.
        send_request_error: Exception to raise during send_request (triggers SETUP_RETRY).

    Returns:
        Configured MagicMock simulating MarstekUDPClient.

    Note:
        Using spec=MarstekUDPClient would be ideal but the async methods
        need AsyncMock which conflicts with create_autospec. MagicMock with
        explicit method setup provides the same protection via explicit attrs.
    """
    client = MagicMock(name="MarstekUDPClient")
    client.async_setup = AsyncMock(side_effect=setup_error)
    client.async_cleanup = AsyncMock(return_value=None)
    client.is_polling_paused = MagicMock(return_value=False)
    client.pause_polling = AsyncMock(return_value=None)
    client.resume_polling = AsyncMock(return_value=None)
    client.async_pause_receiver = AsyncMock(return_value=None)
    client.async_resume_receiver = AsyncMock(return_value=None)

    if send_request_error:
        client.send_request = AsyncMock(side_effect=send_request_error)
        client.fetch_es_mode = AsyncMock(return_value=None)
    else:
        client.send_request = AsyncMock(return_value={"result": {}})
        client.fetch_es_mode = AsyncMock(return_value={"device_mode": "auto"})

    if isinstance(status, Exception):
        client.get_device_status = AsyncMock(side_effect=status)
    else:
        default_status = {
            "battery_soc": 55,
            "pv1_power": 100,
            "device_mode": "auto",
            "battery_power": -250,
            "battery_status": "discharging",
            "ongrid_power": -150,
            # WiFi status
            "wifi_rssi": -58,
            "wifi_ssid": "TestNetwork",
            # CT / Energy Meter status
            "ct_state": 1,
            "ct_connected": True,
            "em_a_power": 120,
            "em_b_power": 115,
            "em_c_power": 125,
            "em_total_power": 360,
            # Battery details
            "bat_temp": 27.5,
            "bat_charg_flag": 1,
            "bat_dischrg_flag": 1,
        }
        client.get_device_status = AsyncMock(
            return_value=status if status is not None else default_status
        )
    return client


def create_mock_scanner() -> MagicMock:
    """Create a mock MarstekScanner."""
    scanner = MagicMock()
    scanner.async_setup = AsyncMock(return_value=None)
    scanner.async_unload = AsyncMock(return_value=None)
    return scanner


@contextmanager
def patch_marstek_integration(
    client: MagicMock | None = None,
    scanner: MagicMock | None = None,
) -> Generator[tuple[MagicMock, MagicMock], None, None]:
    """Patch MarstekUDPClient and MarstekScanner for integration tests.

    Args:
        client: Mock UDP client, created if not provided.
        scanner: Mock scanner, created if not provided.

    Yields:
        Tuple of (client, scanner) mocks.
    """
    client = client or create_mock_client()
    scanner = scanner or create_mock_scanner()
    with (
        patch("custom_components.marstek.scanner.MarstekScanner._scanner", None),
        patch("custom_components.marstek.MarstekUDPClient", return_value=client),
        patch(
            "custom_components.marstek.pymarstek.MarstekUDPClient", return_value=client
        ),
        patch(
            "custom_components.marstek.scanner.MarstekScanner.async_get",
            return_value=scanner,
        ),
    ):
        yield client, scanner


@pytest.fixture
def mock_udp_client() -> MagicMock:
    """Provide a mock UDP client for direct coordinator/scanner tests."""
    return create_mock_client()


# ---------------------------------------------------------------------------
# Reusable patch context managers for config flow tests
# ---------------------------------------------------------------------------


@contextmanager
def patch_discovery(
    devices: list[dict[str, Any]], error: Exception | None = None
) -> Iterator[None]:
    """Patch discover_devices for config flow tests.

    Args:
        devices: List of device dicts to return from discovery.
        error: Optional exception to raise instead of returning devices.

    Yields:
        None - context manager for use in tests.
    """
    mock_fn = AsyncMock(side_effect=error) if error else AsyncMock(return_value=devices)
    with patch("custom_components.marstek.config_flow.discover_devices", mock_fn):
        yield


@contextmanager
def patch_manual_connection(
    device_info: dict[str, Any] | None = None, error: Exception | None = None
) -> Iterator[None]:
    """Patch get_device_info for manual connection validation tests.

    Args:
        device_info: Device info dict to return, or None for no device.
        error: Optional exception to raise instead of returning device_info.

    Yields:
        None - context manager for use in tests.
    """
    mock_fn = (
        AsyncMock(side_effect=error) if error else AsyncMock(return_value=device_info)
    )
    with patch("custom_components.marstek.config_flow.get_device_info", mock_fn):
        yield
