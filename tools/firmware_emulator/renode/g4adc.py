# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# STM32G4 ADC1-5 at 0x50000000 (RM0440 section 21.7): every instance is
# always ready, calibration and conversions finish at once, data reads 0.
if request.IsInit:
    regs = {}
elif request.IsWrite:
    off = request.Offset
    if off & 0xFF == 0x00 and off & 0x300 != 0x300:  # ISR: write 1 to clear
        regs[off] = regs.get(off, 0) & ~request.Value
    elif off & 0xFF == 0x08 and off & 0x300 != 0x300:  # CR
        # ADCAL (31), ADSTART (2), ADSTP (4), ADDIS (1) self-clear; ADEN -> ADRDY
        regs[off] = request.Value & ~((1 << 31) | (1 << 4) | (1 << 2) | (1 << 1))
    else:
        regs[off] = request.Value
elif request.IsRead:
    off = request.Offset
    v = regs.get(off, 0)
    if off & 0xFF == 0x00 and off & 0x300 != 0x300:
        v |= 0x0F  # ADRDY, EOSMP, EOC, EOS
    request.Value = v
