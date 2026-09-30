# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# BCD calendar RTC at 0x40002800, STM32F4 register layout, which is what the
# Control firmware drives here (build 150: time write 0x802ad40, date write
# 0x802b08c, read 0x802afb0 / 0x802af64): TR 0x00, DR 0x04, CR 0x08, ISR 0x0C,
# PRER 0x10, WPR 0x24 (unlocked with 0xCA, 0x53), SSR 0x28.
#
# The calendar follows the host's wall clock, in the Renode process's local
# time (set TZ for the container), so it keeps real time even when Renode's
# virtual time runs slower. A real device sets its clock from the cloud
# (HTTP getDateInfo / MQTT "Set local time"), which the emulator has no way to
# do, so starting from host time stands in for a clock that is already synced.
# Manual schedules and the Open API's time-based behaviour then run on the
# same clock Home Assistant uses.
#
# A write while ISR.INIT is set (TR and/or DR) still moves the calendar: it
# becomes an offset from host time, applied when INIT is cleared, the way the
# hardware latches the new time on leaving init mode. RSF (shadow registers
# synchronised) and INITS (calendar initialised) always read as set, INITF
# follows INIT, so the firmware's polling loops end at once.
if request.IsInit:
    from System import DateTime

    regs = {}
    offset = 0.0  # seconds added to host local time
    in_init = False
    pending = {}

    def _bcd(n):
        return ((n // 10) << 4) | (n % 10)

    def _bin(b):
        return ((b >> 4) & 0xF) * 10 + (b & 0xF)

    def _now():
        return DateTime.Now.AddSeconds(offset)

    def _tr(t):
        return (_bcd(t.Hour) << 16) | (_bcd(t.Minute) << 8) | _bcd(t.Second)

    def _dr(t):
        # .NET: Sunday = 0; RTC and firmware: Monday = 1 .. Sunday = 7
        weekday = int(t.DayOfWeek) or 7
        return (_bcd(t.Year % 100) << 16) | (weekday << 13) | (_bcd(t.Month) << 8) | _bcd(t.Day)

    def _latch():
        """Leave init mode: turn the TR/DR written during it into a new offset."""
        global offset
        now = _now()
        tr = pending.get(0x00, _tr(now))
        dr = pending.get(0x04, _dr(now))
        try:
            target = DateTime(
                2000 + _bin((dr >> 16) & 0xFF),
                _bin((dr >> 8) & 0x1F),
                _bin(dr & 0x3F),
                _bin((tr >> 16) & 0x3F),
                _bin((tr >> 8) & 0x7F),
                _bin(tr & 0x7F),
            )
        except Exception:  # invalid calendar (e.g. month 0): the hardware would refuse it too
            return
        offset = (target - DateTime.Now).TotalSeconds


if request.IsWrite:
    off = request.Offset
    value = request.Value
    if off == 0x0C:  # ISR: only INIT (bit 7) matters; flags are rc_w0 and read as set
        init = bool(value & 0x80)
        if in_init and not init and pending:
            _latch()
            pending = {}
        in_init = init
    elif off in (0x00, 0x04):
        if in_init:
            pending[off] = value
    else:
        regs[off] = value
elif request.IsRead:
    off = request.Offset
    if off == 0x00:
        v = pending.get(0x00, _tr(_now())) if in_init else _tr(_now())
    elif off == 0x04:
        v = pending.get(0x04, _dr(_now())) if in_init else _dr(_now())
    elif off == 0x0C:
        # INITS | RSF | write flags (WUTWF, ALRBWF, ALRAWF), INITF/INIT follow init mode
        v = 0x10 | 0x20 | 0x07 | ((0x40 | 0x80) if in_init else 0)
    elif off == 0x10:
        v = regs.get(off, 0x007F00FF)  # PRER reset value
    elif off == 0x28:  # SSR counts down from PREDIV_S within the second
        prediv_s = regs.get(0x10, 0x007F00FF) & 0x7FFF
        v = int(prediv_s * (1000 - DateTime.Now.Millisecond) / 1000)
    else:
        v = regs.get(off, 0)
    request.Value = v
