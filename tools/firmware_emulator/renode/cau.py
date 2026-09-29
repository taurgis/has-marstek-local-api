# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# Block engine at 0xA0001000 (config + FIFO at 0x60, status 0x28, count 0x24).
# Unknown algorithm: it is modelled as a loopback so waits complete.
if request.IsInit:
    regs = {}
    fifo = []
elif request.IsWrite:
    off = request.Offset
    if off == 0x60:
        fifo.append(request.Value)
    else:
        regs[off] = request.Value
elif request.IsRead:
    off = request.Offset
    if off == 0x60:
        request.Value = fifo.pop(0) if fifo else 0
    elif off == 0x28:
        request.Value = 0x1 | (0x8 if fifo else 0)
    elif off == 0x24:
        request.Value = len(fifo)
    else:
        request.Value = regs.get(off, 0)
