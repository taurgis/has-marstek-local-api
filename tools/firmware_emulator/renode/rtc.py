# GD32F30x RTC at 0x40002800: registers synchronised, last write done.
if request.IsInit:
    regs = {}
elif request.IsWrite:
    regs[request.Offset] = request.Value
elif request.IsRead:
    off = request.Offset
    v = regs.get(off, 0)
    if off == 0x04:  # CTL: RSYNF | LWOFF
        v |= (1 << 3) | (1 << 5)
    request.Value = v
