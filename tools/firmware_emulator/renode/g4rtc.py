# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# STM32G4 RTC at 0x40002800 (RM0440 section 37.6): calendar registers keep
# what was written; ICSR reports init mode, shadow sync and wakeup-timer
# write access at once.
if request.IsInit:
    regs = {0x0C: 0x00000007}
elif request.IsWrite:
    regs[request.Offset] = request.Value
elif request.IsRead:
    off = request.Offset
    v = regs.get(off, 0)
    if off == 0x0C:  # ICSR: INITF follows INIT; RSF, INITS, WUTWF set
        v |= (1 << 5) | (1 << 4) | (1 << 2) | 0x3
        if v & (1 << 7):
            v |= 1 << 6
    request.Value = v
