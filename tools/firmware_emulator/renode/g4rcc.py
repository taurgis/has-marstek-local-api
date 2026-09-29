# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# STM32G4 RCC at 0x40021000 (RM0440 section 7.4): stores writes and reports
# every oscillator and the PLL ready as soon as it is switched on, and the
# requested SYSCLK source as active.
if request.IsInit:
    regs = {0x00: 0x00000500, 0x08: 0x00000005, 0x94: 0x0C000000}
elif request.IsWrite:
    regs[request.Offset] = request.Value
elif request.IsRead:
    off = request.Offset
    v = regs.get(off, 0)
    if off == 0x00:  # CR: HSIRDY/HSERDY/PLLRDY follow HSION/HSEON/PLLON
        v |= ((v >> 8) & 1) << 10
        v |= ((v >> 16) & 1) << 17
        v |= ((v >> 24) & 1) << 25
    elif off == 0x08:  # CFGR: SWS mirrors SW
        v = (v & ~0xC) | ((v & 0x3) << 2)
    elif off in (0x90, 0x94, 0x98):  # BDCR LSERDY, CSR LSIRDY, CRRCR HSI48RDY
        v |= (v & 1) << 1
    request.Value = v
