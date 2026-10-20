"""INV, DCDC and Modbus peers for the VNSEMINI-0 PMU 301 image.

The PMU does not ask its modem for an address (``AT+QGETIP``) until the pack
reads above a threshold, and the Open API replies stay on the limited
broadcast until it has one. ``mini_peers.py`` supplies that reading on the
UARTs the image actually uses; the Open API replies still come from the
firmware.

* USART4 (address ``0x81``) is the inverter link. Command ``0x02`` stores a
  big-endian pack voltage at struct ``0x2000b780+0x56`` and a second reading
  at ``+0x58``. The control task wants those above 4000 and 800 before the
  QGETIP sequencer runs.
* USART5 (address ``0x82``) is the DCDC link. Answering it sets the peer-alive
  flag; the QGETIP gate does not read those bytes.
* UART7 is the PMU's Modbus master. It is kept connected and drained so its
  DMA keeps moving; nothing is written back. A provisioned module joins on
  its own (``fc41d.py --autojoin``), so the factory-join Modbus write is not
  needed, and leaving it out keeps the image off its built-in factory AP
  credentials.

Frames use the image's own AA layout and Modbus CRC-16. Nothing here pretends
to be an Open API datagram.
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
import time

_LOGGER = logging.getLogger("mini_peers")

# Command 0x02 of the USART4 table: halfword +0x56 must exceed 0xFA0 and
# halfword +0x58 must exceed 0x320 before ES.GetStatus's UDP opener runs.
_PACK_VOLTAGE = 0x1000
_PACK_SECOND = 0x0400
_STATUS_BYTES = 96


def crc16(data: bytes) -> int:
    """Modbus CRC-16 (init 0xFFFF, poly 0xA001), matching the PMU's tables."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def aa_frame(address: int, command: int, payload: bytes) -> bytes:
    """One AA reply: ``AA <addr> 80 <len BE> <cmd> <payload> <crc LE>``.

    The length halfword is big-endian, matching the polls the PMU transmits
    (``aa 80 81 00 08 ...`` is an 8-byte frame). The CRC stays little-endian.
    """
    total = 6 + len(payload) + 2
    body = bytes([0xAA, address, 0x80, (total >> 8) & 0xFF, total & 0xFF, command])
    body += payload
    crc = crc16(body)
    return body + bytes((crc & 0xFF, crc >> 8))


def status_payload() -> bytes:
    """96-byte command 0x02 body. The first two halfwords pass the UDP gate."""
    payload = bytearray(_STATUS_BYTES)
    payload[0:2] = _PACK_VOLTAGE.to_bytes(2, "big")
    payload[2:4] = _PACK_SECOND.to_bytes(2, "big")
    return bytes(payload)


def take_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Pull complete AA frames out of ``buffer``. Return frames and the tail."""
    frames: list[bytes] = []
    while True:
        start = buffer.find(b"\xaa")
        if start < 0:
            return frames, b""
        buffer = buffer[start:]
        if len(buffer) < 8:
            return frames, buffer
        total = (buffer[3] << 8) | buffer[4]
        if total < 8 or total > 600:
            buffer = buffer[1:]
            continue
        if len(buffer) < total:
            return frames, buffer
        frame, buffer = buffer[:total], buffer[total:]
        body, crc_bytes = frame[:-2], frame[-2:]
        expected = crc_bytes[0] | (crc_bytes[1] << 8)
        if crc16(body) == expected:
            frames.append(frame)


def _connect(port: int, stop: threading.Event) -> socket.socket | None:
    while not stop.is_set():
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        except OSError:
            stop.wait(0.5)
            continue
        sock.settimeout(2.0)
        _LOGGER.info("connected to port %s", port)
        return sock
    return None


def _serve_aa(port: int, address: int, stop: threading.Event, command: int) -> None:
    payload = status_payload() if command == 0x02 else bytes(32)
    while not stop.is_set():
        sock = _connect(port, stop)
        if sock is None:
            return
        buffer = b""
        try:
            while not stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except TimeoutError:
                    continue
                if not chunk:
                    break
                buffer += chunk
                frames, buffer = take_frames(buffer)
                for frame in frames:
                    asked = frame[5] if len(frame) > 5 else 0
                    reply = aa_frame(address, command, payload)
                    sock.sendall(reply)
                    _LOGGER.info(
                        "port %s poll cmd 0x%02x -> reply cmd 0x%02x (%s bytes)",
                        port,
                        asked,
                        command,
                        len(reply),
                    )
        except OSError as exc:
            _LOGGER.info("port %s closed: %s", port, exc)
        finally:
            sock.close()


def _drain(sock: socket.socket) -> None:
    """Read the PMU's bytes so a full TCP buffer cannot stall its UART."""
    try:
        while sock.recv(4096):
            pass
    except TimeoutError:
        return


def _serve_modbus(port: int, stop: threading.Event) -> None:
    """Keep the Modbus UART connected and drained. Nothing is written to it."""
    while not stop.is_set():
        sock = _connect(port, stop)
        if sock is None:
            return
        sock.settimeout(0.5)
        try:
            while not stop.is_set():
                _drain(sock)
        except OSError as exc:
            _LOGGER.info("port %s closed: %s", port, exc)
        finally:
            sock.close()


def main() -> None:
    """Answer the three mini-only UART sockets until killed."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--usart4", type=int, required=True)
    parser.add_argument("--usart5", type=int, required=True)
    parser.add_argument("--uart7", type=int, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    stop = threading.Event()
    threads = [
        threading.Thread(
            target=_serve_aa, args=(args.usart4, 0x81, stop, 0x02), name="inv", daemon=True
        ),
        threading.Thread(
            target=_serve_aa, args=(args.usart5, 0x82, stop, 0x80), name="dcdc", daemon=True
        ),
        threading.Thread(target=_serve_modbus, args=(args.uart7, stop), name="modbus", daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
