"""Tell a device restart apart from a Wi-Fi or network dropout.

The Open API has no uptime field or boot counter (see
``docs/marstek_device_openapi.MD``), so a restart is inferred from what a boot
leaves behind in the polls this integration already makes:

* Lifetime energy totals go backwards. At boot the Control firmware writes its
  stored energy counters back to the inverter (``tools/firmware_emulator/README.md``,
  CAN cmd 0x16/0x17), so whatever was counted after the last save is lost.
  Nothing else makes these totals fall.
* The CT link drops and the operating mode changes. Issue #14 reports both
  after a reset: "loses CT connection, falls back to manual".
* With an energy meter linked, the plug shows the self-test the unit runs as
  it boots (see ``boot_pulse.py``). That arrives between polls and is held
  until the next good poll closes the outage it belongs to.

Wi-Fi that answers late is not an outage. The UDP client retransmits a read
after 0.5, 2.5 and 5 s, so a reply lost to the modem waking up is recovered
inside the same poll and only shows in the command statistics. A poll that
still fails starts an outage. When the device answers again, the outage is a
restart if a boot trace is present and a dropout otherwise.

A rollback is a restart even without a failed poll, since a quick reboot can
fall between two polls. The mode and CT traces count only across an outage:
while the device is online, a user or a flaky CT changes them on its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..pymarstek.energy_guard import TOTALS_FELL_KEY
from .capacity_fields import finite

# Coordinator data keys, and the key inside the usable-capacity store.
RESTARTS_DATA_KEY = "device_restarts"
RESTARTS_ATTR_KEY = "device_restarts_attributes"
RESTARTS_STORE_KEY = "restarts"
# The card reads the live state, so the recorder does not need the log.
RESTART_CARD_ATTRIBUTES = frozenset({"outages"})

RESTART = "restart"
DROPOUT = "dropout"
_KINDS = frozenset({RESTART, DROPOUT})

ENERGY_ROLLBACK = "energy_rollback"
CT_LOST = "ct_lost"
MODE_CHANGED = "mode_changed"
BOOT_PULSE = "boot_pulse"
_EVIDENCE = frozenset({ENERGY_ROLLBACK, CT_LOST, MODE_CHANGED, BOOT_PULSE})

# A unit that restarts every few hours logs 150 a month; the card lists 30 days.
MAX_EVENTS = 200
_RECENT_SECONDS = 30 * 86_400


@dataclass(frozen=True, slots=True)
class Outage:
    """One stretch the device did not answer, or a restart between two polls.

    ``at`` is when the device answered again. ``offline_s`` runs from the last
    good poll, so it includes the poll interval the outage started in.
    """

    at: float
    kind: str
    offline_s: float
    failed_polls: int
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form stored across restarts and shown on the card."""
        return {
            "at": self.at,
            "kind": self.kind,
            "offline_s": self.offline_s,
            "failed_polls": self.failed_polls,
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_dict(cls, raw: object) -> Outage | None:
        """Parse a stored entry, or return None when it is not usable."""
        if not isinstance(raw, dict) or raw.get("kind") not in _KINDS:
            return None
        at, offline, failed = (finite(raw.get(key)) for key in ("at", "offline_s", "failed_polls"))
        if at is None or offline is None or failed is None or offline < 0 or failed < 0:
            return None
        evidence = raw.get("evidence")
        traces = (
            tuple(item for item in evidence if item in _EVIDENCE)
            if isinstance(evidence, list)
            else ()
        )
        return cls(at, str(raw["kind"]), offline, int(failed), traces)


def boot_traces(
    previous: Mapping[str, Any], current: Mapping[str, Any], *, outage: bool
) -> tuple[str, ...]:
    """Return what a boot changed between two good polls.

    Without an outage only a rollback counts; see the module docstring.
    """
    traces: list[str] = []
    # The parser compares raw ES.GetStatus totals; see track_restored_totals.
    if current.get(TOTALS_FELL_KEY) is True:
        traces.append(ENERGY_ROLLBACK)
    if not outage:
        return tuple(traces)
    if previous.get("ct_connected") is True and current.get("ct_connected") is False:
        traces.append(CT_LOST)
    before_mode, after_mode = previous.get("device_mode"), current.get("device_mode")
    if isinstance(before_mode, str) and isinstance(after_mode, str) and before_mode != after_mode:
        traces.append(MODE_CHANGED)
    return tuple(traces)


class RestartDetector:
    """Follow poll outcomes and keep the outages they showed."""

    def __init__(self) -> None:
        """Start with no history and no baseline poll."""
        self._events: list[Outage] = []
        self._restarts = 0
        self._last_ok: float | None = None
        self._failed = 0
        self._pulse_at: float | None = None
        self._dirty = False

    @classmethod
    def from_dict(cls, raw: object) -> RestartDetector:
        """Restore stored outages. The baseline poll starts again."""
        detector = cls()
        if not isinstance(raw, dict):
            return detector
        events = raw.get("events")
        if isinstance(events, list):
            parsed = [event for item in events if (event := Outage.from_dict(item))]
            detector._events = sorted(parsed, key=lambda event: event.at)[-MAX_EVENTS:]
        restarts = finite(raw.get("restarts"))
        if restarts is not None and restarts >= 0:
            detector._restarts = int(restarts)
        return detector

    def to_dict(self) -> dict[str, Any]:
        """Return the stored outages and the restart count."""
        return {
            "events": [event.to_dict() for event in self._events],
            "restarts": self._restarts,
        }

    @property
    def events(self) -> list[Outage]:
        """Return stored entries, oldest first."""
        return list(self._events)

    @property
    def restarts(self) -> int:
        """Return every restart detected, including ones the log dropped."""
        return self._restarts

    def consume_dirty(self) -> bool:
        """Return whether anything changed since the last call."""
        dirty, self._dirty = self._dirty, False
        return dirty

    def record_failure(self) -> None:
        """Count a poll the device did not answer.

        Failures before the first good poll have no baseline to compare
        against, so they are not counted.
        """
        if self._last_ok is not None:
            self._failed += 1

    def record_boot_pulse(self, at: float) -> None:
        """Hold a self-test seen at the plug for the next good poll.

        Several before that poll are one restart as far as the polls can tell.
        """
        if self._pulse_at is None:
            self._pulse_at = at

    def record_success(
        self,
        now: float,
        previous: Mapping[str, Any] | None,
        current: Mapping[str, Any],
    ) -> Outage | None:
        """Close any outage at a good poll and return it.

        ``previous`` is the last good status, which the coordinator keeps
        through failed polls.
        """
        last_ok, failed, pulse = self._last_ok, self._failed, self._pulse_at
        self._last_ok, self._failed, self._pulse_at = now, 0, None
        if last_ok is None or previous is None:
            return None
        evidence = boot_traces(previous, current, outage=failed > 0)
        if pulse is not None:
            evidence = (*evidence, BOOT_PULSE)
        if not failed and not evidence:
            return None
        kind = RESTART if evidence else DROPOUT
        event = Outage(now, kind, round(max(0.0, now - last_ok), 1), failed, evidence)
        self._events.append(event)
        del self._events[:-MAX_EVENTS]
        if kind == RESTART:
            self._restarts += 1
        self._dirty = True
        return event


def _iso(moment: float) -> str:
    """Return a UTC ISO-8601 timestamp for a stored unix time."""
    return datetime.fromtimestamp(moment, UTC).isoformat()


def restart_attributes(detector: RestartDetector, now: float) -> dict[str, Any]:
    """Return the sensor attributes the battery health card reads."""
    events = detector.events
    recent = [event for event in events if now - event.at <= _RECENT_SECONDS]
    attrs: dict[str, Any] = {
        "outages": [{**event.to_dict(), "at": _iso(event.at)} for event in recent],
        "restarts_30d": sum(1 for event in recent if event.kind == RESTART),
        "dropouts_30d": sum(1 for event in recent if event.kind == DROPOUT),
    }
    restarts = [event for event in events if event.kind == RESTART]
    if restarts:
        attrs["last_restart_at"] = _iso(restarts[-1].at)
    return attrs
