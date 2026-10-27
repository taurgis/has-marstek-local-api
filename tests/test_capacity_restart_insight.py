"""The battery health card reports device restarts apart from network dropouts."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_FRONTEND = Path(__file__).resolve().parents[1] / "custom_components" / "marstek" / "frontend"
_RESTARTS = _FRONTEND / "marstek-battery-health-restarts.js"
_COPY = _FRONTEND / "marstek-battery-health-copy.js"


def _insight(attrs: dict[str, Any] | None, language: str, tmp_path: Path) -> Any:
    """Run restartInsight for one set of Restarts sensor attributes in Node."""
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is required to render the battery health insights")
    script = tmp_path / "restart-insight.mjs"
    script.write_text(
        f"import {{ COPY }} from {json.dumps(_COPY.resolve().as_uri())};\n"
        f"import {{ restartInsight }} from {json.dumps(_RESTARTS.resolve().as_uri())};\n"
        "const attrs = JSON.parse(process.argv[2]);\n"
        "const day = (at) => `day(${at})`;\n"
        "const text = COPY[process.argv[3]];\n"
        "process.stdout.write(JSON.stringify(restartInsight(attrs, text, day)));\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(script), json.dumps(attrs), language],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


_RESTART = {
    "at": "2026-10-20T03:12:00+00:00",
    "kind": "restart",
    "offline_s": 48.0,
    "failed_polls": 1,
    "evidence": ["energy_rollback", "ct_lost"],
}
_DROPOUT = {**_RESTART, "kind": "dropout", "offline_s": 600.0, "evidence": []}


def test_a_restart_names_when_how_long_and_the_traces(tmp_path: Path) -> None:
    """The newest restart's downtime and boot traces are on one line."""
    attrs = {
        "restarts_30d": 2,
        "dropouts_30d": 1,
        "last_restart_at": _RESTART["at"],
        "outages": [_RESTART, _DROPOUT],
    }
    insight = _insight(attrs, "en", tmp_path)
    assert insight["icon"] == "mdi:restart-alert"
    assert insight["text"] == (
        f"Restarted 2 times in 30 days, last on day({_RESTART['at']}), unreachable for "
        "48\u00a0s (energy totals restored, CT meter lost). "
        "The network also dropped it 1 time without a restart."
    )


def test_dutch_restart_without_a_downtime(tmp_path: Path) -> None:
    """A restart between two polls has no failed poll, only the rollback."""
    quick = {**_RESTART, "offline_s": 0, "evidence": ["energy_rollback", "made_up"]}
    attrs = {"restarts_30d": 1, "last_restart_at": quick["at"], "outages": [quick]}
    insight = _insight(attrs, "nl", tmp_path)
    assert insight["text"] == (
        f"1 keer herstart in 30 dagen, het laatst op day({quick['at']}) "
        "(energietellers teruggezet)."
    )


def test_dropouts_alone_point_at_the_network(tmp_path: Path) -> None:
    """Silence without a boot trace is a network problem, not the battery."""
    insight = _insight(
        {"restarts_30d": 0, "dropouts_30d": 3, "outages": [_DROPOUT]}, "en", tmp_path
    )
    assert insight["icon"] == "mdi:wifi-alert"
    assert "no sign of a restart" in insight["text"]


@pytest.mark.parametrize(
    "attrs",
    [None, {}, {"restarts_30d": 0, "dropouts_30d": 0, "outages": []}, {"restarts_30d": "x"}],
)
def test_a_quiet_month_shows_nothing(attrs: dict[str, Any] | None, tmp_path: Path) -> None:
    """No outage in 30 days, a missing sensor or junk adds no insight."""
    assert _insight(attrs, "en", tmp_path) is None


def test_a_plug_self_test_is_named(tmp_path: Path) -> None:
    """A restart seen only at the linked plug says so."""
    seen = {**_RESTART, "failed_polls": 0, "offline_s": 30.0, "evidence": ["boot_pulse"]}
    attrs = {"restarts_30d": 1, "last_restart_at": seen["at"], "outages": [seen]}
    assert "(boot self-test at the plug)" in _insight(attrs, "en", tmp_path)["text"]
    assert "(opstarttest aan de stekker)" in _insight(attrs, "nl", tmp_path)["text"]
