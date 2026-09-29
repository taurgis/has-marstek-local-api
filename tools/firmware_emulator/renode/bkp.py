# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# Backup-domain data registers at 0x40006C00 (GD32F30x BKP layout: DATA0 at
# +0x04, 16-bit). At boot the Control firmware (build 150: 0x801cf80) reads
# DATA0 and, unless it holds 0xA5A5, re-initialises the RTC and loads the
# clock from the time it last saved in the EEPROM, or else from a built-in
# default date (2019-11-20); then it writes 0xA5A5.
#
# A real device keeps this domain powered from its own battery, so after the
# first cloud time sync every later boot finds 0xA5A5 and keeps a running
# clock. DATA0 starts as 0xA5A5 here for the same reason: the firmware leaves
# the host-seeded calendar in rtc.py alone instead of rewinding it to a stale
# EEPROM time or 2019.
if request.IsInit:
    regs = {0x04: 0xA5A5}
elif request.IsWrite:
    regs[request.Offset] = request.Value & 0xFFFF
elif request.IsRead:
    request.Value = regs.get(request.Offset, 0)
