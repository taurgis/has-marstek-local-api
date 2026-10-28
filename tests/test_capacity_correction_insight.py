"""The battery health card names BMS charge-level corrections and stale full charges."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

_FRONTEND = Path(__file__).resolve().parents[1] / "custom_components" / "marstek" / "frontend"
_MODULE = _FRONTEND / "marstek-battery-health.js"
_COPY = _FRONTEND / "marstek-battery-health-copy.js"


def _iso(days_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - days_ago * 86400, UTC).isoformat()


def _runtime() -> str:
    """Return the card source from its helpers through correctionInsight."""
    source = _MODULE.read_text(encoding="utf-8")
    start = source.index("function languageOf")
    end = source.index("function insightsHtml")
    return source[start:end]


def _insight(attrs: dict[str, Any], language: str, tmp_path: Path) -> Any:
    """Run correctionInsight for one set of sensor attributes in Node."""
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is required to render the battery health insights")
    script = tmp_path / "correction-insight.mjs"
    script.write_text(
        "import { COPY } from "
        + json.dumps(_COPY.resolve().as_uri())
        + ";\n"
        + "const document = { documentElement: { lang: 'en' } };\n"
        + _runtime()
        + "\nconst model = { attrs: JSON.parse(process.argv[2]) };\n"
        + "const hass = { locale: { language: process.argv[3] } };\n"
        + "process.stdout.write(JSON.stringify(correctionInsight(hass, model, copyFor(hass))));\n",
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


_JUMP = {"at": _iso(2), "from_soc": 94, "to_soc": 100, "points": 6.0, "style": "jump"}


def test_a_recent_correction_names_the_jump_and_the_drift_bound(tmp_path: Path) -> None:
    """The newest correction and the pooled bound are both on one line."""
    attrs = {"soc_corrections": [_JUMP], "soc_corrections_30d": 1, "soc_drift_bound_pct": 3.2}
    insight = _insight(attrs, "en", tmp_path)
    assert insight["icon"] == "mdi:battery-sync-outline"
    assert "corrected its charge level 1 time in 30 days" in insight["text"]
    assert "on reaching 100% (6.0 points)" in insight["text"]
    assert "94%" not in insight["text"]
    assert "±3.2%" in insight["text"]


def test_a_correction_without_a_bound_skips_the_bound(tmp_path: Path) -> None:
    """Too little throughput for a bound leaves only the correction."""
    attrs = {"soc_corrections": [_JUMP, _JUMP], "soc_corrections_30d": 2}
    insight = _insight(attrs, "nl", tmp_path)
    assert "2 keer in 30 dagen" in insight["text"]
    assert "telafwijking" not in insight["text"]


def test_an_old_correction_gives_way_to_the_full_charge_tip(tmp_path: Path) -> None:
    """Corrections outside 30 days are history; a stale full charge is the tip."""
    attrs = {
        "soc_corrections": [_JUMP],
        "soc_corrections_30d": 0,
        "last_full_charge_at": _iso(20),
    }
    insight = _insight(attrs, "en", tmp_path)
    assert insight["icon"] == "mdi:battery-clock-outline"
    assert insight["text"].startswith("Not fully charged since")


@pytest.mark.parametrize(
    "attrs",
    [
        {},
        {"soc_corrections": [], "soc_corrections_30d": 0, "last_full_charge_at": _iso(3)},
        {"last_full_charge_at": "not a date"},
    ],
)
def test_nothing_to_say_shows_nothing(attrs: dict[str, Any], tmp_path: Path) -> None:
    """A recent full charge, no history or junk adds no insight."""
    assert _insight(attrs, "en", tmp_path) is None


def test_a_step_mid_range_names_both_ends(tmp_path: Path) -> None:
    """Only a step that did not end at 100% has a meaningful starting level."""
    drop = {**_JUMP, "from_soc": 60, "to_soc": 52, "points": -6.0}
    attrs = {"soc_corrections": [drop], "soc_corrections_30d": 1}
    insight = _insight(attrs, "en", tmp_path)
    assert "from 60% to 52% (6.0 points)" in insight["text"]
