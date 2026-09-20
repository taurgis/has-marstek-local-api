"""Firmware-specific wire behaviour and the datagram storm guards."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mock_device import MockMarstekDevice
from mock_device.__main__ import main


class TestFirmwareCli:
    """Tests for the versioned mock command line."""

    def test_cli_passes_selected_firmware_to_discovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The CLI-selected integer is used by the running mock."""
        selected: list[int | None] = []

        def capture_start(device: MockMarstekDevice) -> None:
            response = device.build_response(1, "Marstek.GetDevice", {})
            assert response is not None
            selected.append(response["result"]["ver"])

        monkeypatch.setattr(
            "sys.argv",
            ["mock_device", "--ver", "150", "--state-dir", ""],
        )
        with patch.object(MockMarstekDevice, "start", capture_start):
            main()

        assert selected == [150]

    @pytest.mark.parametrize("version", ["not-a-version", "-1"])
    def test_cli_rejects_invalid_firmware(
        self,
        version: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Invalid firmware input exits with a clear command-line error."""
        monkeypatch.setattr("sys.argv", ["mock_device", "--ver", version])

        with pytest.raises(SystemExit) as err:
            main()

        assert err.value.code == 2
        assert "--ver" in capsys.readouterr().err


class TestVenusEFirmware150Capture:
    """Mock Venus E 3.0 / ver 150 matches the 2026-09-15 LAN GET capture."""

    def test_legacy_getmode_omits_rev31_meter_keys(self) -> None:
        """Firmware 145 Venus E does not invent GetMode CT/energy fields."""
        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 145},
        )

        response = device.build_response(1, "ES.GetMode", {"id": 0})

        assert response is not None
        result = response["result"]
        assert result["mode"] == "Auto"
        assert result["id"] == 0
        assert "ct_state" not in result
        assert "input_energy" not in result

    def test_getmode_ct_keys_are_zeros_while_em_is_live(self) -> None:
        """Firmware 150 Venus E GetMode CT template stays zeros; EM has the live CT."""
        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 150},
        )
        device._static_power = 800

        mode = device.build_response(1, "ES.GetMode", {"id": 0})
        em = device.build_response(2, "EM.GetStatus", {"id": 0})
        status = device.build_response(3, "ES.GetStatus", {"id": 0})

        assert mode is not None
        assert em is not None
        assert status is not None
        assert mode["result"]["mode"] == "Auto"
        assert mode["result"]["id"] == 0
        assert mode["result"]["ct_state"] == 0
        assert mode["result"]["a_power"] == 0
        assert mode["result"]["total_power"] == 0
        assert mode["result"]["input_energy"] == 0
        assert em["result"]["ct_state"] == 1
        assert "input_energy" in em["result"]
        assert "bat_power" not in status["result"]
        assert status["result"]["pv_power"] == 0

    def test_getmode_echoes_instance_id(self) -> None:
        """Both GetMode instance ids answer; result.id echoes the request."""
        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 150},
        )

        id0 = device.build_response(1, "ES.GetMode", {"id": 0})
        id1 = device.build_response(2, "ES.GetMode", {"id": 1})

        assert id0 is not None
        assert id1 is not None
        assert id0["result"]["id"] == 0
        assert id1["result"]["id"] == 1
        assert id0["result"]["mode"] == "Auto"

    def test_pv_method_not_found_includes_observed_data_field(self) -> None:
        """Firmware 150 Venus E PV.GetStatus matches the captured -32601 payload."""
        device = MockMarstekDevice(
            simulate=False,
            device_config={"device": "VenusE 3.0", "ver": 150},
        )

        response = device.build_response(1, "PV.GetStatus", {"id": 0})

        assert response is not None
        assert response["error"] == {
            "code": -32601,
            "message": "Method not found",
            "data": 424,
        }
        assert "result" not in response


class TestFirmwareUdpQuirks:
    """Reproduce Control firmware Open API quirks found in VNSE3-0 binaries."""

    def _device_with_socket(
        self, *, ver: int, device: str = "VenusE 3.0"
    ) -> MockMarstekDevice:
        mock = MockMarstekDevice(
            simulate=False,
            device_config={"device": device, "ver": ver},
        )
        mock.sock = MagicMock()
        return mock

    def test_empty_datagram_freezes_openapi(self) -> None:
        """A 0-byte UDP packet stops later Local API replies."""
        device = self._device_with_socket(ver=145)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (b"", ("127.0.0.1", 12345))

        device._handle_request()

        assert device._openapi_frozen is True
        device.sock.sendto.assert_not_called()

        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 12345),
        )
        device._handle_request()
        device.sock.sendto.assert_not_called()

    def test_invalid_json_returns_parse_error(self) -> None:
        """Malformed JSON yields JSON-RPC parse error id 0 / -32700."""
        device = self._device_with_socket(ver=150)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (b"{not json", ("127.0.0.1", 1))

        device._handle_request()

        payload = json.loads(device.sock.sendto.call_args[0][0])
        assert payload["id"] == 0
        assert payload["error"]["code"] == -32700
        assert device.sock.sendto.call_count == 1

    def test_legacy_firmware_duplicates_udp_reply(self) -> None:
        """Pre-150 Control sends Local API replies twice (WiFi + Ethernet)."""
        device = self._device_with_socket(ver=145)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 1),
        )

        device._handle_request()

        assert device.profile.openapi_reset_prone is True
        assert device.sock.sendto.call_count == 2
        first = device.sock.sendto.call_args_list[0][0][0]
        second = device.sock.sendto.call_args_list[1][0][0]
        assert first == second

    def test_firmware_150_sends_single_reply(self) -> None:
        """Control 150's Local API ethernet send anomaly fix is a single reply."""
        device = self._device_with_socket(ver=150)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 1),
        )

        device._handle_request()

        assert device.profile.openapi_reset_prone is False
        assert device.sock.sendto.call_count == 1

    def test_venus_c_153_duplicates_udp_reply(self) -> None:
        """HMG-50 Control 153 is reset-prone; mock duplicates Local API replies."""
        device = self._device_with_socket(ver=153, device="VenusC")
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 1),
        )

        device._handle_request()

        assert device.profile.hmg50_control is True
        assert device.profile.openapi_reset_prone is True
        assert device.sock.sendto.call_count == 2

    def test_venus_c_156_sends_single_reply(self) -> None:
        """HMG-50 Control 156 Open API stability fix is a single UDP reply."""
        device = self._device_with_socket(ver=156, device="VenusC")
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 1),
        )

        device._handle_request()

        assert device.profile.openapi_reset_prone is False
        assert device.sock.sendto.call_count == 1

    def test_uint16_id_truncation(self) -> None:
        """JSON-RPC ids wrap to uint16 the way json_data.c stores them."""
        device = self._device_with_socket(ver=150)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":65536,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 1),
        )

        device._handle_request()

        payload = json.loads(device.sock.sendto.call_args[0][0])
        assert payload["id"] == 0

    def test_lan_replies_go_to_listen_port_not_ephemeral_source(
        self,
    ) -> None:
        """Container/LAN firmware replies to the Open API listen port."""
        device = self._device_with_socket(ver=150)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("172.28.0.2", 54321),
        )

        device._handle_request()

        dest = device.sock.sendto.call_args[0][1]
        assert dest == ("172.28.0.2", device.port)

    def test_loopback_replies_keep_ephemeral_source_port(self) -> None:
        """Unit tests bind ephemeral on loopback and must still receive replies."""
        device = self._device_with_socket(ver=150)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 54321),
        )

        device._handle_request()

        dest = device.sock.sendto.call_args[0][1]
        assert dest == ("127.0.0.1", 54321)


class TestDatagramStormGuards:
    """A shared UDP port must not turn the mock into a packet amplifier."""

    def _device_with_socket(self, verbose: bool = True) -> MockMarstekDevice:
        device = MockMarstekDevice(
            simulate=False, status_interval=0, verbose=verbose
        )
        device.sock = MagicMock()
        return device

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("own success reply", {"id": 1, "result": {"set_result": True}}),
            ("error reply", {"id": 2, "error": {"code": -32601, "message": "x"}}),
        ],
    )
    def test_reply_is_never_answered(
        self, label: str, payload: dict[str, object]
    ) -> None:
        """Answering a reply is what spins two sockets into a storm."""
        device = self._device_with_socket()
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            json.dumps(payload).encode(),
            ("127.0.0.1", 30000),
        )

        device._handle_request()

        device.sock.sendto.assert_not_called()

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("unknown method", {"id": 9, "method": "Wifi.SetConfig", "params": {}}),
            ("empty method", {"id": 3, "method": "", "params": {}}),
            ("missing method", {"id": 4, "params": {}}),
            ("non-string method", {"id": 5, "method": 7, "params": {}}),
        ],
    )
    def test_unanswerable_method_still_answers_method_not_found(
        self, label: str, payload: dict[str, object]
    ) -> None:
        """Real firmware answers -32601 for a method it cannot serve.

        docs/marstek_device_openapi.MD section 2.1 defines -32601 as "Method
        missing or not available on this firmware", so a request the mock
        cannot serve must still get a reply. Only a *reply* is dropped.
        """
        device = self._device_with_socket()
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            json.dumps(payload).encode(),
            ("127.0.0.1", 30000),
        )

        device._handle_request()

        sent = json.loads(device.sock.sendto.call_args[0][0].decode())
        assert sent["error"]["code"] == -32601

    def test_dropped_datagrams_are_rate_limited(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A flood of unanswerable datagrams must not print a line each."""
        device = self._device_with_socket()
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"result":{"set_result":true}}',
            ("127.0.0.1", 30000),
        )

        capsys.readouterr()
        for _ in range(500):
            device._handle_request()

        printed = capsys.readouterr().out.strip().splitlines()
        assert len(printed) == 1
        assert "not a request" in printed[0]

    def test_handled_request_logs_one_line(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Steady-state logging stays at a single line per request."""
        device = self._device_with_socket()
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 30000),
        )

        capsys.readouterr()
        device._handle_request()

        assert len(capsys.readouterr().out.strip().splitlines()) == 1

    def test_quiet_mode_logs_nothing_per_request(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--quiet silences per-request lines but keeps the device working."""
        device = self._device_with_socket(verbose=False)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"method":"ES.GetStatus","params":{}}',
            ("127.0.0.1", 30000),
        )

        capsys.readouterr()
        device._handle_request()

        assert capsys.readouterr().out == ""
        device.sock.sendto.assert_called()

    def test_non_object_params_are_coerced_not_dropped(self) -> None:
        """Malformed params must not crash the handler or silence the reply."""
        device = self._device_with_socket()
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":7,"method":"ES.GetStatus","params":7}',
            ("127.0.0.1", 30000),
        )

        device._handle_request()

        sent = json.loads(device.sock.sendto.call_args[0][0].decode())
        assert "result" in sent

    def test_drop_counts_are_kept_per_reason(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A burst of one kind must not be reported under another."""
        device = self._device_with_socket()
        assert device.sock is not None
        capsys.readouterr()

        device._log_dropped("a reply, not a request", "1.2.3.4:30000")
        capsys.readouterr()
        device._log_dropped("invalid JSON", "1.2.3.4:30000")
        device._log_dropped("invalid JSON", "1.2.3.4:30000")
        device._log_dropped("a reply, not a request", "5.6.7.8:30000")
        device._flush_dropped()

        printed = capsys.readouterr().out.strip()
        assert "Dropped 3 datagram(s)" in printed
        assert "invalid JSON x2" in printed
        assert "a reply, not a request x1" in printed

    def test_pending_drops_survive_until_flushed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Counts suppressed by the rate limit are printed, never discarded."""
        device = self._device_with_socket()
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            b'{"id":1,"result":{"set_result":true}}',
            ("127.0.0.1", 30000),
        )

        capsys.readouterr()
        for _ in range(4):
            device._handle_request()
        capsys.readouterr()

        device._flush_dropped()

        assert "Dropped 3 datagram(s)" in capsys.readouterr().out

    def test_flush_prints_nothing_when_no_drops_pending(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A clean shutdown must not emit an empty drop summary."""
        device = self._device_with_socket()
        capsys.readouterr()

        device._flush_dropped()

        assert capsys.readouterr().out == ""

    def test_shutdown_flushes_pending_drops(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """start() must print the tail of a burst on the way out."""
        device = MockMarstekDevice(
            simulate=False, status_interval=0, state_dir=str(tmp_path)
        )
        replies = [
            (b'{"id":1,"result":{"set_result":true}}', ("127.0.0.1", 30000)),
            (b'{"id":2,"result":{"set_result":true}}', ("127.0.0.1", 30000)),
        ]

        def _recvfrom(_size: int) -> tuple[bytes, tuple[str, int]]:
            if replies:
                return replies.pop(0)
            raise KeyboardInterrupt

        with patch("socket.socket") as sock_cls:
            sock_cls.return_value.recvfrom.side_effect = _recvfrom
            capsys.readouterr()
            device.start()

        # The first drop prints immediately; the second falls inside the rate
        # limit window and is only visible because shutdown flushes it.
        assert capsys.readouterr().out.count("Dropped 1 datagram(s)") == 2
