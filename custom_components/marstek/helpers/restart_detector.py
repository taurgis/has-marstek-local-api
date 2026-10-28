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

Wi-Fi that answers late is not an outage. On firmware marked retransmit-safe
(Control 150+, HMG-50 156+) the UDP client sends a read again after 0.5, 2.5
and 5 s, so a reply lost to the modem waking up is recovered inside the same
poll and only shows in the command statistics. Older firmware gets no
retransmit, so one lost reply there is already a failed poll. A poll that
still fails starts an outage. When the device answers again, the outage is a
restart if a boot trace is present and a dropout otherwise.

A rollback is a restart even without a failed poll, since a quick reboot can
fall between two polls. The mode and CT traces count only across an outage:
while the device is online, a user or a flaky CT changes them on its own.

The plug's self-test alone is not enough. Control 150 resets its Ethernet chip
(Wi-Fi: its FC41D module) every 30 minutes (15 on Wi-Fi) while its cloud
upload queue is stuck (``tools/firmware/ANALYSIS.md``); the unit leaves the LAN
for 5-10 s, asks DHCP for an address again, and the plug shows the same
self-test, but nothing in the polls changes. A self-test with no other trace
and at most ``BRIEF_RESET_MAX_FAILED`` failed poll is logged as a brief network
reset. A full restart keeps the unit away for 30-60 s, so it misses more polls
or restores its energy totals, and still counts as a restart.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
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
NETWORK_RESET = "network_reset"
_KINDS = frozenset({RESTART, DROPOUT, NETWORK_RESET})
# How the coordinator's log line says what happened.
LOG_VERBS = {
    RESTART: "restarted",
    DROPOUT: "dropped out",
    NETWORK_RESET: "reset its network connection",
}

ENERGY_ROLLBACK = "energy_rollback"
CT_LOST = "ct_lost"
MODE_CHANGED = "mode_changed"
BOOT_PULSE = "boot_pulse"
_EVIDENCE = frozenset({ENERGY_ROLLBACK, CT_LOST, MODE_CHANGED, BOOT_PULSE})

# A unit that restarts every few hours logs 150 a month; the card lists 30 days.
# Dropouts go first when the log is full, so a flaky network (HMG-50, #82)
# cannot push the restarts out.
MAX_EVENTS = 200
_RECENT_SECONDS = 30 * 86_400
# Stored times past this are corrupt, and datetime cannot render them.
_LATEST_AT = 4_102_444_800.0  # 2100-01-01
# A self-test closes up to BRACKET_S after its burst, so it can reach the
# detector a poll after the recovery that already counted its restart.
_PULSE_JOIN_S = 120.0
# A brief network reset is gone 5-10 s, which the retransmits usually bridge;
# one failed poll is the most it costs. Two failed polls is a minute or more.
BRIEF_RESET_MAX_FAILED = 1


def classify(evidence: tuple[str, ...], failed_polls: int) -> str:
    """Return what an outage, or a trace between two polls, was."""
    if not evidence:
        return DROPOUT
    if evidence == (BOOT_PULSE,) and failed_polls <= BRIEF_RESET_MAX_FAILED:
        return NETWORK_RESET
    return RESTART


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
        if not 0 < at < _LATEST_AT:
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
        # When this log started, so the card can say "since" while it is
        # younger than the 30 days it reports on.
        self._since: float | None = None
        self._dirty = False

    @classmethod
    def from_dict(cls, raw: object) -> RestartDetector:
        """Restore stored outages. The baseline poll starts again."""
        detector = cls()
        detector.load(raw)
        return detector

    def load(self, raw: object) -> None:
        """Take the stored log, keeping failures or a self-test already seen."""
        if not isinstance(raw, dict):
            return
        events = raw.get("events")
        parsed: list[Outage] = []
        if isinstance(events, list):
            parsed = [event for item in events if (event := Outage.from_dict(item))]
        # Logs stored before brief network resets had their own kind list them
        # as restarts; give them their kind and take them off the count.
        relabelled = 0
        for index, event in enumerate(parsed):
            kind = classify(event.evidence, event.failed_polls)
            if event.kind == RESTART and kind == NETWORK_RESET:
                parsed[index] = replace(event, kind=kind)
                relabelled += 1
        self._events = sorted(parsed, key=lambda event: event.at)
        self._trim()
        restarts = finite(raw.get("restarts"))
        if restarts is not None and restarts >= 0:
            self._restarts = max(0, int(restarts) - relabelled)
        if relabelled:
            self._dirty = True
        since = finite(raw.get("since"))
        if since is not None and 0 < since < _LATEST_AT:
            self._since = since

    def to_dict(self) -> dict[str, Any]:
        """Return the stored outages and the restart count."""
        return {
            "events": [event.to_dict() for event in self._events],
            "restarts": self._restarts,
            "since": self._since,
        }

    @property
    def since(self) -> float | None:
        """Return when the first good poll started this log."""
        return self._since

    def _trim(self) -> None:
        """Drop the oldest dropouts and network resets first, then the oldest restarts."""
        while len(self._events) > MAX_EVENTS:
            index = next((i for i, event in enumerate(self._events) if event.kind != RESTART), 0)
            del self._events[index]

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
        if self._since is None:
            self._since = now
            self._dirty = True
        if last_ok is None or previous is None:
            return None
        if pulse is not None and pulse <= last_ok and self._join_pulse(pulse):
            pulse = None
        evidence = boot_traces(previous, current, outage=failed > 0)
        if pulse is not None:
            evidence = (*evidence, BOOT_PULSE)
        if not failed and not evidence:
            return None
        kind = classify(evidence, failed)
        event = Outage(now, kind, round(max(0.0, now - last_ok), 1), failed, evidence)
        self._events.append(event)
        self._trim()
        if kind == RESTART:
            self._restarts += 1
        self._dirty = True
        return event

    def _join_pulse(self, pulse: float) -> bool:
        """Add a late self-test to the outage the recovery poll already logged.

        A dropout it joins becomes the restart or network reset it was.
        """
        last = self._events[-1] if self._events else None
        if last is None or abs(last.at - pulse) > _PULSE_JOIN_S:
            return False
        if BOOT_PULSE not in last.evidence:
            evidence = (*last.evidence, BOOT_PULSE)
            kind = classify(evidence, last.failed_polls)
            if kind == RESTART and last.kind != RESTART:
                self._restarts += 1
            self._events[-1] = replace(last, kind=kind, evidence=evidence)
            self._dirty = True
        return True


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
        "network_resets_30d": sum(1 for event in recent if event.kind == NETWORK_RESET),
    }
    restarts = [event for event in events if event.kind == RESTART]
    if restarts:
        attrs["last_restart_at"] = _iso(restarts[-1].at)
    if detector.since is not None:
        attrs["tracked_since"] = _iso(detector.since)
    return attrs
