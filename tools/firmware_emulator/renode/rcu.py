# GD32F30x RCU (STM32F1-compatible RCC) at 0x40021000: stores writes and
# reports every oscillator/PLL as stable and the requested SYSCLK as active.
if request.IsInit:
    regs = {0x00: 0x00000083, 0x04: 0, 0x24: 0x0C000000}
elif request.IsWrite:
    regs[request.Offset] = request.Value
elif request.IsRead:
    off = request.Offset
    v = regs.get(off, 0)
    if off == 0x00:  # CTL: IRC8MSTB, HXTALSTB, PLLSTB, PLL1STB, PLL2STB
        v |= (1 << 1) | (1 << 17) | (1 << 25) | (1 << 27) | (1 << 29)
    elif off == 0x04:  # CFG0: SCSS mirrors SCS
        v = (v & ~0xC) | ((v & 0x3) << 2)
    elif off == 0x20:  # BDCTL: LXTALSTB
        v |= 1 << 1
    elif off == 0x24:  # RSTSCK: IRC40KSTB
        v |= 1 << 1
    elif off == 0x08:  # INT: stable flags
        v |= 0x1F
    request.Value = v
