# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# STM32G4 HRTIM at 0x40016800 (RM0440 section 27.5): a register file whose
# common ISR (0x388) reports the DLL calibration done (DLLRDY), so the HAL
# init sequence completes. No PWM is generated.
if request.IsInit:
    regs = {}
elif request.IsWrite:
    regs[request.Offset] = request.Value
elif request.IsRead:
    v = regs.get(request.Offset, 0)
    if request.Offset == 0x388:
        v |= 1 << 16
    request.Value = v
