"""Unicast exchanges: validation, retransmission and rate limiting."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.marstek.pymarstek.udp import (
    MIN_REQUEST_INTERVAL,
    MarstekUDPClient,
)
from custom_components.marstek.pymarstek.validators import MAX_JSON_RPC_ID, ValidationError

from ._helpers import (
    _complete_first_pending,
    _patch_sock_sendto,
    _unicast_test_client,
)


class TestSendRequest:
    """Tests for send_request method."""

    async def test_validation_failure(self) -> None:
        """Test that validation errors are raised."""
        client = MarstekUDPClient()
        client._socket = MagicMock()

        # Invalid method name should fail validation
        invalid_message = json.dumps({"id": 1, "method": "Invalid.Method", "params": {}})

        with pytest.raises(ValidationError):
            await client.send_request(invalid_message, "192.168.1.100", 30000, timeout=0.1)

    async def test_skip_validation(self) -> None:
        """Test that validation can be skipped."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        # Use mocked loop to avoid socket blocking mode checks
        mock_loop = MagicMock()
        mock_loop.time.return_value = 1000.0
        client._loop = mock_loop
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False

        # Invalid method but validation skipped - should get ValueError for no id,
        # not ValidationError (since validation is skipped)
        message = json.dumps({"id": 1, "method": "Invalid.Method", "params": {}})

        # Mock UDP send to do nothing, test will timeout
        with patch.object(client, "_send_udp_message", AsyncMock()):
            # Should not raise ValidationError because validate=False
            # Just timeout since no response arrives
            with pytest.raises(TimeoutError):
                await client.send_request(
                    message, "192.168.1.100", 30000, timeout=0.01, validate=False
                )

    async def test_missing_id_raises_value_error(self) -> None:
        """Test that message without id raises ValueError."""
        client = MarstekUDPClient()
        client._socket = MagicMock()

        message = json.dumps({"method": "ES.GetStatus", "params": {}})

        with pytest.raises((ValueError, ValidationError)):
            await client.send_request(message, "192.168.1.100", 30000, timeout=0.1, validate=False)

    async def test_bypass_rate_limit_forwarded_to_udp_send(self) -> None:
        """Test send_request forwards bypass_rate_limit to UDP send path."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        mock_loop = MagicMock()
        mock_loop.time.return_value = 1000.0
        client._loop = mock_loop
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False

        message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})

        async def send_and_complete(*_args: Any, **_kwargs: Any) -> None:
            _complete_first_pending(client)

        with patch.object(
            client, "_send_udp_message", AsyncMock(side_effect=send_and_complete)
        ) as mock_send_udp:
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.1,
                validate=False,
                bypass_rate_limit=True,
            )

        mock_send_udp.assert_called_once_with(
            message,
            "192.168.1.100",
            30000,
            bypass_rate_limit=True,
        )

    async def test_stable_firmware_retransmits_after_silent_wait(self) -> None:
        """RFC 1122 UDP retry: second send only after the first wait is silent."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})
        sends = 0

        async def send_and_complete_on_retransmit(*_args: Any, **kwargs: Any) -> None:
            nonlocal sends
            sends += 1
            if sends >= 2:
                assert kwargs.get("bypass_rate_limit") is True
                _complete_first_pending(client)

        with (
            patch("custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01),
            patch.object(
                client,
                "_send_udp_message",
                AsyncMock(side_effect=send_and_complete_on_retransmit),
            ),
        ):
            result = await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=1.0,
                validate=False,
            )

        assert sends == 2
        assert result["result"] == {}

    async def test_fast_reply_skips_retransmit(self) -> None:
        """An on-time first reply must not send a second datagram."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})
        sends = 0

        async def send_and_complete(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1
            _complete_first_pending(client)

        with (
            patch("custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.2),
            patch.object(
                client,
                "_send_udp_message",
                AsyncMock(side_effect=send_and_complete),
            ),
        ):
            result = await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=1.0,
                validate=False,
            )

        assert sends == 1
        assert result["result"] == {}

    async def test_reset_prone_skips_wifi_retransmit(self) -> None:
        """Reset-prone Control must stay one-shot; do not add extra datagrams."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        client.set_openapi_reset_prone("192.168.1.100", True)
        message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})
        sends = 0

        async def send_and_complete(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1
            _complete_first_pending(client)

        with (
            patch("custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01),
            patch.object(
                client,
                "_send_udp_message",
                AsyncMock(side_effect=send_and_complete),
            ),
        ):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=1.0,
                validate=False,
            )

        assert sends == 1

    async def test_reset_prone_does_not_retry_after_timeout(self) -> None:
        """Reset-prone Control must not get a second send/wait cycle."""
        client = _unicast_test_client()
        client.set_openapi_reset_prone("192.168.1.100", True)
        message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})
        sends = 0

        async def count_sends(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1

        with (
            patch.object(client, "_send_udp_message", AsyncMock(side_effect=count_sends)),
            pytest.raises(TimeoutError),
        ):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.02,
                validate=False,
            )

        assert sends == 1

    async def test_unknown_firmware_stays_one_shot(self) -> None:
        """Unmarked IPs must not get Wi-Fi copies before the profile opts in."""
        client = _unicast_test_client()
        message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})
        sends = 0

        async def count_sends(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1

        with (
            patch("custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01),
            patch.object(client, "_send_udp_message", AsyncMock(side_effect=count_sends)),
            pytest.raises(TimeoutError),
        ):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.05,
                validate=False,
            )

        assert sends == 1

    @pytest.mark.parametrize(
        "method",
        ["ES.SetMode", "DOD.SET", "Ble.Adv", "Led.Ctrl"],
    )
    async def test_writes_stay_one_shot_on_safe_firmware(self, method: str) -> None:
        """Control writes must not be duplicated even on known-safe firmware."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps({"id": 1, "method": method, "params": {"id": 0}})
        sends = 0

        async def count_sends(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1

        with (
            patch("custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01),
            patch.object(client, "_send_udp_message", AsyncMock(side_effect=count_sends)),
            pytest.raises(TimeoutError),
        ):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.05,
                validate=False,
            )

        assert sends == 1

    async def test_timeout_retry_succeeds_on_second_attempt(self) -> None:
        """A silent-wait copy recovers when the first unicast is lost."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})
        sends = 0

        async def send_and_complete_on_retry(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1
            if sends >= 2:
                _complete_first_pending(client)

        with (
            patch("custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01),
            patch.object(
                client,
                "_send_udp_message",
                AsyncMock(side_effect=send_and_complete_on_retry),
            ),
        ):
            result = await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=1.0,
                validate=False,
            )

        assert sends == 2
        assert result["id"] == 1
        stats = client.get_command_stats_for_ip("192.168.1.100")
        assert stats["ES.GetStatus"]["total_retransmits"] == 1

    async def test_wifi_retry_stays_within_configured_timeout(self) -> None:
        """Silent-wait copy plus remaining wait, never a second full timeout."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})
        sends = 0

        async def count_sends(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1

        started = time.monotonic()
        with (
            patch("custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01),
            patch.object(client, "_send_udp_message", AsyncMock(side_effect=count_sends)),
            pytest.raises(TimeoutError),
        ):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.05,
                validate=False,
            )

        elapsed = time.monotonic() - started
        assert sends == 2
        assert elapsed < 0.12

    async def test_late_reply_survives_first_timeout_wait(self) -> None:
        """Do not cancel the pending future when the first wait times out."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})

        async def delayed_complete() -> None:
            await asyncio.sleep(0.03)
            _complete_first_pending(client)

        with (
            patch("custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01),
            patch.object(client, "_send_udp_message", AsyncMock()),
        ):
            completer = asyncio.create_task(delayed_complete())
            try:
                result = await client.send_request(
                    message,
                    "192.168.1.100",
                    30000,
                    timeout=0.05,
                    validate=False,
                )
            finally:
                await completer

        assert result["id"] == 1

    async def test_retransmit_and_recovery_are_debug_not_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Silent-wait copies must not raise the default HA log level."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})
        sends = 0

        async def send_and_complete_on_retransmit(*_args: Any, **_kwargs: Any) -> None:
            nonlocal sends
            sends += 1
            if sends >= 2:
                _complete_first_pending(client)

        caplog.set_level(logging.DEBUG)
        with (
            patch("custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01),
            patch.object(
                client,
                "_send_udp_message",
                AsyncMock(side_effect=send_and_complete_on_retransmit),
            ),
        ):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=1.0,
                validate=False,
            )

        assert "retransmitting" in caplog.text
        assert "after retransmit" in caplog.text
        assert "Request timeout" not in caplog.text
        assert "FC41D" not in caplog.text
        assert not any(record.levelno >= logging.WARNING for record in caplog.records)

    async def test_fast_reply_does_not_log_retransmit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A sub-500 ms reply (typical Ethernet) must stay silent at warning."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps({"id": 1, "method": "ES.GetStatus", "params": {"id": 0}})

        async def send_and_complete(*_args: Any, **_kwargs: Any) -> None:
            _complete_first_pending(client)

        caplog.set_level(logging.DEBUG)
        with patch.object(client, "_send_udp_message", AsyncMock(side_effect=send_and_complete)):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=1.0,
                validate=False,
            )

        assert "retransmitting" not in caplog.text
        assert "after retransmit" not in caplog.text
        assert "Request timeout" not in caplog.text

    async def test_write_timeout_does_not_log_retransmit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Mode writes stay one-shot and must not emit recovery logs."""
        client = _unicast_test_client()
        client.set_openapi_retransmit_safe("192.168.1.100", True)
        message = json.dumps({"id": 1, "method": "ES.SetMode", "params": {"id": 0}})

        caplog.set_level(logging.DEBUG)
        with (
            patch("custom_components.marstek.pymarstek.udp.UNICAST_RETRANSMIT_WAIT", 0.01),
            patch.object(client, "_send_udp_message", AsyncMock()),
            pytest.raises(TimeoutError),
        ):
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.05,
                validate=False,
            )

        assert "retransmitting" not in caplog.text
        assert "Request timeout" in caplog.text
        assert "ES.SetMode" in caplog.text


class TestRateLimiting:
    """Tests for rate limiting functionality."""

    async def test_enforces_minimum_interval(self) -> None:
        """Test that minimum interval is enforced between requests."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()

        time_value = 0.0

        def get_time() -> float:
            return time_value

        client._loop.time.side_effect = get_time

        # First call - no wait
        await client._enforce_rate_limit("192.168.1.100")
        assert client._throttle.last_request_time.get("192.168.1.100") == 0.0

        # Second call - should wait (mocked)
        time_value = 0.1  # Only 100ms elapsed
        with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            await client._enforce_rate_limit("192.168.1.100")

            # Should have called sleep for the remaining time
            mock_sleep.assert_called_once()
            wait_time = mock_sleep.call_args[0][0]
            assert wait_time > 0
            assert wait_time <= MIN_REQUEST_INTERVAL

    async def test_creates_per_ip_lock(self) -> None:
        """Test that per-IP locks are created."""
        client = MarstekUDPClient()

        lock1 = await client._throttle.rate_limit_lock("192.168.1.100")
        lock2 = await client._throttle.rate_limit_lock("192.168.1.100")
        lock3 = await client._throttle.rate_limit_lock("192.168.1.101")

        # Same IP should get same lock
        assert lock1 is lock2
        # Different IP should get different lock
        assert lock1 is not lock3


class TestValidationErrorLogging:
    """Tests for validation error context extraction."""

    async def test_validation_error_extracts_method_from_json(self) -> None:
        """Test that method name is extracted from invalid message."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        # Send an invalid command with a recognizable method
        invalid_message = '{"id": 1, "method": "Invalid.Method", "params": {}}'

        with pytest.raises(ValidationError):
            await client.send_request(invalid_message, "192.168.1.100", 30000)

    async def test_validation_error_handles_non_json_message(self) -> None:
        """Test that method extraction handles non-JSON gracefully."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        client._loop = MagicMock()
        client._loop.time.return_value = 1000.0

        # Send completely invalid message
        with pytest.raises(ValidationError):
            await client.send_request("not json at all", "192.168.1.100", 30000)


class TestSendRequestSkipValidation:
    """Tests for send_request with validation disabled."""

    async def test_send_request_skip_validation_success(self) -> None:
        """Test send_request works with validation disabled."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        loop = asyncio.get_event_loop()
        client._loop = loop

        async def mock_recvfrom(sock: Any, bufsize: int) -> tuple[bytes, tuple[str, int]]:
            # Wait briefly, then return response
            await asyncio.sleep(0.01)
            return (
                json.dumps({"id": 999, "result": {"test": "data"}}).encode(),
                ("192.168.1.100", 30000),
            )

        with (
            patch.object(loop, "sock_recvfrom", mock_recvfrom),
            _patch_sock_sendto(),
        ):
            # Pre-validated message (skip_validation=True)
            message = '{"id": 999, "method": "ES.GetStatus", "params": {"id": 0}}'
            result = await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=1.0,
                validate=False,
            )

        assert result["id"] == 999

        # Clean up listen task
        if client._listen_task:
            client._listen_task.cancel()
            with suppress(asyncio.CancelledError):
                await client._listen_task

    async def test_send_request_rewrites_overflow_id_when_validation_skipped(
        self,
    ) -> None:
        """validate=False still rewrites ids that would wrap to 0 on the MCU."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        mock_loop = MagicMock()
        mock_loop.time.return_value = 1000.0
        client._loop = mock_loop
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False

        message = json.dumps(
            {"id": MAX_JSON_RPC_ID + 1, "method": "ES.GetStatus", "params": {"id": 0}}
        )
        with patch.object(client, "_send_udp_message", AsyncMock()) as mock_send:

            async def send_and_complete(*_args: Any, **_kwargs: Any) -> None:
                _complete_first_pending(client, {"id": 1, "result": {}})

            mock_send.side_effect = send_and_complete
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.1,
                validate=False,
            )

        sent = json.loads(mock_send.call_args.args[0])
        assert sent["id"] == 1
        assert sent["method"] == "ES.GetStatus"

    async def test_send_request_keeps_discovery_id_zero(self) -> None:
        """Discovery may still send JSON-RPC id 0."""
        client = MarstekUDPClient()
        client._socket = MagicMock()
        mock_loop = MagicMock()
        mock_loop.time.return_value = 1000.0
        client._loop = mock_loop
        client._listen_task = MagicMock()
        client._listen_task.done.return_value = False

        message = json.dumps({"id": 0, "method": "Marstek.GetDevice", "params": {"ble_mac": "0"}})
        with patch.object(client, "_send_udp_message", AsyncMock()) as mock_send:

            async def send_and_complete(*_args: Any, **_kwargs: Any) -> None:
                _complete_first_pending(client, {"id": 0, "result": {}})

            mock_send.side_effect = send_and_complete
            await client.send_request(
                message,
                "192.168.1.100",
                30000,
                timeout=0.1,
                validate=False,
            )

        assert json.loads(mock_send.call_args.args[0])["id"] == 0
