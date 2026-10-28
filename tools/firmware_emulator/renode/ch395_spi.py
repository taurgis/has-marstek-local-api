# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# SPI2 at 0x40003800 with a CH395 behind it (run_firmware.py --ethernet).
# Each DR write is one SPI clock: the MOSI byte goes to ch395.py over TCP and
# the MISO byte it answers is what DR reads back. Two bus hooks in the .resc
# use the spare offsets: 0x3F0 takes the PB12 chip-select level, and 0x3F4
# reads INT# (PD8) for the GPIOD IDR. Messages are two bytes; see ch395.py.
if request.IsInit:
    import socket
    import time

    PORT = int("@CH395_PORT@")
    link = {"sock": None, "buf": bytearray(), "miso": 0, "int": 1, "dead": False}
    regs = {}

    def _connect():
        if link["sock"] is not None or link["dead"]:
            return link["sock"]
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                s = socket.create_connection(("127.0.0.1", PORT))
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                link["sock"] = s
                return s
            except socket.error:  # noqa: UP024  (IronPython 2.7: not an OSError)
                time.sleep(0.2)
        link["dead"] = True  # no ch395.py: behave like the idle stub
        return None

    def _pump(want_reply):
        """Read link messages; return the MISO byte when want_reply is set."""
        s = _connect()
        if s is None:
            return 0
        buf = link["buf"]
        while True:
            while len(buf) >= 2:
                kind, value = buf[0], buf[1]
                del buf[:2]
                if kind == 0x49:  # "I": INT# level
                    link["int"] = value
                elif kind == 0x44 and want_reply:  # "D": MISO byte
                    return value
            s.settimeout(None if want_reply else 0.0)
            try:
                data = s.recv(4096)
            except socket.error:  # noqa: UP024  (IronPython 2.7: not an OSError)
                return 0
            if not data:
                link["dead"] = True
                link["sock"] = None
                return 0
            buf.extend(bytearray(data))

    def _send(kind, value):
        s = _connect()
        if s is not None:
            s.sendall(bytes(bytearray([kind, value & 0xFF])))
        return s

elif request.IsWrite:
    off = request.Offset
    if off == 0x0C:
        if _send(0x58, request.Value) is not None:  # "X"
            link["miso"] = _pump(True)
    elif off == 0x3F0:
        _send(0x43, request.Value)  # "C": chip select level
    else:
        regs[off] = request.Value
elif request.IsRead:
    off = request.Offset
    if off == 0x08:
        request.Value = 0x03  # RBNE | TBE, never busy
    elif off == 0x0C:
        request.Value = link["miso"]
    elif off == 0x3F4:
        if not link["dead"]:
            _pump(False)
        request.Value = link["int"]
    else:
        request.Value = regs.get(off, 0)
