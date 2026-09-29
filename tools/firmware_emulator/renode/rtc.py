# ruff: noqa: F821  (Renode injects `request` into PythonPeripheral scripts)
# RTC at 0x40002800. The Control firmware drives it with the STM32F4-style
# calendar API (TR 0x00, DR 0x04, CR 0x08, ISR 0x0C, BCD fields), not the
# GD32F30x counter RTC. A stub that reads 0 leaves the date invalid and the clock
# stopped at 00:00, so manual schedules never start.
#
# TR/DR here always read the host clock (container local time, UTC unless TZ
# is set), as on a unit the cloud keeps synchronised. The firmware's own writes
# are ignored: at boot it restores the last time it saved to EEPROM (or a 2019
# default), which a real unit then corrects from the cloud this emulator lacks.
# ISR reports INITS (date set), RSF (shadow registers synced) and INITF while
# INIT is set, so the firmware's init-mode waits succeed.
# IronPython 2.7: no f-strings, no imports beyond `time`.
import time


def _bcd(n):
    return ((n // 10) << 4) | (n % 10)


def _days(y, m, d):
    # Days since 1970-01-01 (proleptic Gregorian, H. Hinnant's days_from_civil).
    y -= m <= 2
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _local_now():
    t = time.localtime()
    days = _days(t.tm_year, t.tm_mon, t.tm_mday)
    return days * 86400 + t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec


def _registers(now):
    days, secs = divmod(now, 86400)
    t = time.gmtime(days * 86400 + secs)
    tr = (_bcd(t.tm_hour) << 16) | (_bcd(t.tm_min) << 8) | _bcd(t.tm_sec)
    # WDU: 1 = Monday ... 7 = Sunday (tm_wday 0 = Monday).
    dr = (_bcd(t.tm_year % 100) << 16) | ((t.tm_wday + 1) << 13)
    dr |= (_bcd(t.tm_mon) << 8) | _bcd(t.tm_mday)
    return tr, dr


if request.IsInit:
    regs = {}
elif request.IsWrite:
    regs[request.Offset] = request.Value
elif request.IsRead:
    off = request.Offset
    v = regs.get(off, 0)
    if off in (0x00, 0x04):
        v = _registers(_local_now())[off // 4]
    elif off == 0x0C:  # ISR: INITS | RSF, INITF while INIT (bit 7) is set
        v |= (1 << 4) | (1 << 5)
        if v & (1 << 7):
            v |= 1 << 6
    request.Value = v
