# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# Plain register file for STM32G4 blocks the firmware only configures
# (PWR, HRTIM, COMP, OPAMP, DAC, CORDIC...): reads return what was written.
if request.IsInit:
    regs = {}
elif request.IsWrite:
    regs[request.Offset] = request.Value
elif request.IsRead:
    request.Value = regs.get(request.Offset, 0)
