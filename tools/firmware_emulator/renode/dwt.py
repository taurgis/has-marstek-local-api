# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# DWT_CYCCNT at 0xE0001004. PMU 301 times AT-command replies off this counter.
# An unmapped read stays 0, so the wait never elapses. Each read steps the
# counter so those timeouts finish; this is not a cycle-accurate clock.
if request.IsInit:
    cyc = 0
elif request.IsRead:
    if request.Offset == 0x4:
        cyc = (cyc + 0x10000) & 0xFFFFFFFF
        request.Value = cyc
    elif request.Offset == 0x0:
        request.Value = 1  # CYCCNTENA
    else:
        request.Value = 0
