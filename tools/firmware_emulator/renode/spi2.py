# SPI2 at 0x40003800: always ready; logs MOSI bytes, answers 0x00.
if request.IsInit:
    regs = {}
    import os
    log = open(os.environ.get("VNSE3_SPI_LOG", "/tmp/vnse3_spi2.log"), "a")
elif request.IsWrite:
    regs[request.Offset] = request.Value
    if request.Offset == 0x0C:
        log.write("%02x " % (request.Value & 0xFF)); log.flush()
elif request.IsRead:
    off = request.Offset
    if off == 0x08:
        request.Value = 0x03  # RBNE | TBE, never busy
    elif off == 0x0C:
        request.Value = 0x00
    else:
        request.Value = regs.get(off, 0)
