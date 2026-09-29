# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# STM32G4 FLASH interface at 0x40022000 (RM0440 section 5.7): never busy,
# unlocked by the KEYR sequence. Page erase is not modelled.
if request.IsInit:
    regs = {0x14: 0xC0000000, 0x20: 0xFFEFF8AA}
elif request.IsWrite:
    off = request.Offset
    if off == 0x08:  # KEYR: second key unlocks
        if request.Value == 0xCDEF89AB:
            regs[0x14] = regs.get(0x14, 0) & ~0x80000000
    elif off == 0x0C:  # OPTKEYR
        if request.Value == 0x4C5D6E7F:
            regs[0x14] = regs.get(0x14, 0) & ~0x40000000
    elif off == 0x10:  # SR: write 1 to clear
        regs[0x10] = regs.get(0x10, 0) & ~request.Value
    elif off == 0x14:  # CR: STRT/OPTSTRT complete at once
        regs[0x14] = request.Value & ~((1 << 16) | (1 << 17))
        if request.Value & ((1 << 16) | (1 << 17)):
            regs[0x10] = regs.get(0x10, 0) | 1  # EOP
    else:
        regs[off] = request.Value
elif request.IsRead:
    v = regs.get(request.Offset, 0)
    if request.Offset == 0x10:
        v &= ~(1 << 16)  # BSY
    request.Value = v
