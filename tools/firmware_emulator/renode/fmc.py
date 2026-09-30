# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# GD32F30x FMC at 0x40022000: never busy, never locked.
if request.IsInit:
    regs = {}
elif request.IsWrite:
    regs[request.Offset] = request.Value
elif request.IsRead:
    off = request.Offset
    v = regs.get(off, 0)
    if off in (0x0C, 0x4C):  # STAT0/STAT1: clear BUSY, set ENDF
        v = (v & ~1) | (1 << 5)
    request.Value = v
