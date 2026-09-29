# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# STM32G4 DBGMCU at 0xE0042000 (RM0440 section 47.6.1): IDCODE reads as a
# category 3 device (STM32G47x/G48x, DEV_ID 0x469, rev Z = 0x2001).
if request.IsInit:
    regs = {0x00: 0x20016469}
elif request.IsWrite:
    if request.Offset != 0x00:
        regs[request.Offset] = request.Value
elif request.IsRead:
    request.Value = regs.get(request.Offset, 0)
