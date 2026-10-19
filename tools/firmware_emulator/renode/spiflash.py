# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# SPI1 NOR flash for VNSEMINI-0 PMU 301 (0x40013000, chip select PA4).
#
# The image keeps its settings in this flash, not in an I2C EEPROM. An
# 18-byte record at 0x14000 holds the Open API enable flag (byte 0x0A), the
# port twice (0x0B and 0x0D, little-endian) and a CRC-16/XMODEM of bytes 0-14
# at 0x10. The image uses opcodes 03 (read), 02 (page program), 20 (4 KiB
# erase), D8 (64 KiB erase), 06 (write enable) and 05 (status), nothing else.
#
# Renode cannot show this peripheral the chip select, so a GPIOA write hook
# (run_firmware.py) writes the PA4 level to offset 0x3F0: 0 starts a command,
# 1 ends it. Like the Control boards' EEPROM, LOCAL_API_PORT > 0 keeps the
# Open API on: the record is rewritten with that port at start-up and after
# every program, which is what the app's enable command stores.
#
# run_firmware.py renders this file into the work directory with the two
# placeholders below filled in. The script runs under Renode's IronPython 2.7.
if request.IsInit:
    import os

    PATH = "@SPI_FLASH@"
    LOCAL_API_PORT = int("@LOCAL_API_PORT@")
    SIZE = 0x800000  # 8 MiB; addresses wrap like a real part
    SECTOR = 0x1000
    RECORD = 0x14000
    if not os.path.exists(PATH):
        blank = open(PATH, "wb")  # noqa: SIM115 - closed below
        chunk = b"\xff" * 0x10000
        for _ in range(SIZE // len(chunk)):
            blank.write(chunk)
        blank.close()
    image = open(PATH, "r+b")  # noqa: SIM115 - lives as long as the peripheral
    sectors = {}
    regs = {}
    state = {"cmd": -1, "addr": 0, "count": 0, "rx": 0, "wel": 0, "data": []}

    def _sector(address):
        index = (address % SIZE) // SECTOR
        if index not in sectors:
            image.seek(index * SECTOR)
            sectors[index] = bytearray(image.read(SECTOR))
        return index, sectors[index]

    def _read(address):
        _, data = _sector(address)
        return data[address % SECTOR]

    def _save(index):
        image.seek(index * SECTOR)
        image.write(bytes(sectors[index]))
        image.flush()

    def _crc16(data):
        crc = 0
        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
                crc &= 0xFFFF
        return crc

    def _force_local_api():
        if LOCAL_API_PORT <= 0:
            return
        index, data = _sector(RECORD)
        offset = RECORD % SECTOR
        record = data[offset : offset + 18]
        stored = record[16] | (record[17] << 8)
        body = record[:15] if _crc16(record[:15]) == stored else bytearray(15)
        body[0x0A] = 1
        for at in (0x0B, 0x0D):
            body[at] = LOCAL_API_PORT & 0xFF
            body[at + 1] = LOCAL_API_PORT >> 8
        crc = _crc16(body)
        if record[:15] == body and stored == crc:
            return
        data[offset : offset + 15] = body
        data[offset + 16] = crc & 0xFF
        data[offset + 17] = crc >> 8
        _save(index)

    def _finish():
        cmd = state["cmd"]
        touched = []
        if state["wel"] and cmd == 0x02 and state["count"] >= 4:
            address = state["addr"]
            for n, byte in enumerate(state["data"]):
                # Page program wraps inside its 256-byte page and only clears bits.
                at = (address & ~0xFF) | ((address + n) & 0xFF)
                index, data = _sector(at)
                data[at % SECTOR] &= byte
                if index not in touched:
                    touched.append(index)
        elif state["wel"] and cmd in (0x20, 0xD8) and state["count"] >= 4:
            span = SECTOR if cmd == 0x20 else 0x10000
            start = state["addr"] - (state["addr"] % span)
            for at in range(start, start + span, SECTOR):
                index, data = _sector(at)
                data[:] = b"\xff" * SECTOR
                touched.append(index)
        for index in touched:
            _save(index)
        if cmd in (0x02, 0x20, 0xD8):
            state["wel"] = 0
        if cmd == 0x02 and (RECORD % SIZE) // SECTOR in touched:
            _force_local_api()
        state["cmd"] = -1

    def _byte(value):
        cmd = state["cmd"]
        if cmd < 0:
            state["cmd"] = value
            state["count"] = 1
            state["addr"] = 0
            state["data"] = []
            if value == 0x06:
                state["wel"] = 1
            elif value == 0x04:
                state["wel"] = 0
            return 0xFF
        count = state["count"]
        state["count"] = count + 1
        if cmd == 0x05:
            return 0x02 if state["wel"] else 0x00  # never busy
        if cmd in (0x02, 0x03, 0x20, 0xD8) and count < 4:
            state["addr"] = (state["addr"] << 8) | value
            return 0xFF
        if cmd == 0x03:
            address = state["addr"] + count - 4
            return _read(address)
        if cmd == 0x02:
            state["data"].append(value)
        return 0xFF

    _force_local_api()
elif request.IsWrite:
    off = request.Offset
    if off == 0x3F0:
        if request.Value & 1:
            if state["cmd"] >= 0:
                _finish()
        else:
            state["cmd"] = -1
    elif off == 0x0C:
        state["rx"] = _byte(request.Value & 0xFF)
    else:
        regs[off] = request.Value
elif request.IsRead:
    off = request.Offset
    if off == 0x08:
        request.Value = 0x03  # RBNE | TBE, never busy
    elif off == 0x0C:
        request.Value = state["rx"]
    else:
        request.Value = regs.get(off, 0)
