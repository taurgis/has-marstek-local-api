"""The battery health card lists BMS charge-level corrections on their own tab."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_FRONTEND = Path(__file__).resolve().parents[1] / "custom_components" / "marstek" / "frontend"
_CARD = _FRONTEND / "marstek-battery-health.js"
_CORRECTIONS = _FRONTEND / "marstek-battery-health-corrections.js"
_COPY = _FRONTEND / "marstek-battery-health-copy.js"

_NOW = "2026-10-28T12:00:00+00:00"


def _node(tmp_path: Path, body: str, *args: Any) -> Any:
    """Run a module script in Node and return what it writes as JSON."""
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is required to render the battery health Corrections tab")
    script = tmp_path / "corrections-tab.mjs"
    script.write_text(
        f"import {{ COPY }} from {json.dumps(_COPY.resolve().as_uri())};\n"
        "import { correctionsHtml, correctionsOf } from "
        f"{json.dumps(_CORRECTIONS.resolve().as_uri())};\n"
        "const [attrs, language] = process.argv.slice(2).map((arg) => JSON.parse(arg));\n"
        "const text = COPY[language];\n"
        "const fmt = (value, digits) => value.toFixed(digits);\n"
        "const day = (date) => `day(${date.getUTCDate()})`;\n"
        "const when = (date) => `when(${date.getUTCDate()})`;\n"
        f"const now = new Date({json.dumps(_NOW)});\n" + body,
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(script), *(json.dumps(arg) for arg in args)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _tab(tmp_path: Path, attrs: dict[str, Any] | None, language: str = "en") -> str:
    html: str = _node(
        tmp_path,
        "const html = correctionsHtml(attrs, text, { fmt, day, when, now });\n"
        "process.stdout.write(JSON.stringify(html));\n",
        attrs,
        language,
    )
    return html


def _fix(at: str, style: str = "jump", **fields: Any) -> dict[str, Any]:
    return {
        "at": at,
        "from_soc": 85.0,
        "to_soc": 100.0,
        "points": 4.3,
        "style": style,
        "throughput": 160.0,
        **fields,
    }


def test_corrections_are_listed_newest_first_with_their_kind(tmp_path: Path) -> None:
    """Each row names its kind, the move, the size and the share of the movement."""
    attrs = {
        "soc_corrections": [
            _fix("2026-10-20T08:00:00+00:00", "hold", from_soc=84.0, points=-5.0),
            _fix("2026-10-28T09:30:00+00:00"),
        ],
        "soc_corrections_30d": 2,
        "full_charges_30d": 6,
        "last_full_charge_at": "2026-10-28T09:30:00+00:00",
        "soc_drift_bound_pct": 3.3,
    }
    html = _tab(tmp_path, attrs)
    rows = html[html.index('<ul class="outages fixes"') :]
    assert rows.index("Jumped up") < rows.index("Held near full")
    assert "85% → 100%" not in html
    assert "+4.3 points" in html
    assert "2.7% of 160 points moved" in html
    assert "\u22125.0 points" in html
    assert "2 corrections in 6 full charges, last 30 days · last full day(28)" in html
    assert "±3.3%, so confidence is at most medium" in html
    assert (
        'role="img" aria-label="2 corrections from day(20) to day(28), between 4.3 and 5.0' in html
    )
    assert 'datetime="2026-10-28T09:30:00.000Z"' in html
    assert "mdi:pause" in html


def test_a_downward_jump_reads_as_a_drop(tmp_path: Path) -> None:
    """Only a jump can lower the reading; it gets its own kind."""
    attrs = {
        "soc_corrections": [_fix("2026-10-27T09:00:00+00:00", from_soc=60, to_soc=52, points=-6)]
    }
    html = _tab(tmp_path, attrs, "nl")
    assert "Gedaald" in html
    assert "60% → 52%" in html
    assert "mdi:arrow-down-bold" in html


def test_unusable_entries_are_dropped(tmp_path: Path) -> None:
    """A bad time, an unknown style or a missing level never reaches a row."""
    attrs = {
        "soc_corrections": [
            _fix("not a date"),
            _fix("2026-10-27T09:00:00+00:00", "full"),
            _fix("2026-10-27T09:00:00+00:00", from_soc=None),
            "junk",
        ]
    }
    rows = _node(
        tmp_path, "process.stdout.write(JSON.stringify(correctionsOf(attrs)));\n", attrs, "en"
    )
    assert rows == []


def test_no_corrections_says_so_and_explains_why_they_happen(tmp_path: Path) -> None:
    """The empty tab still carries the 30-day counts and the background."""
    attrs = {"soc_corrections": [], "soc_corrections_30d": 0, "full_charges_30d": 3}
    html = _tab(tmp_path, attrs)
    assert "No charge-level corrections yet" in html
    assert "0 corrections in 3 full charges" in html
    assert "<details" in html
    assert "LFP cells" in html
    assert 'class="strip"' not in html


def test_a_stale_full_charge_is_named(tmp_path: Path) -> None:
    """Two weeks without 100% means the drift has had no point to reset at."""
    attrs = {"soc_corrections": [], "last_full_charge_at": "2026-10-01T10:00:00+00:00"}
    html = _tab(tmp_path, attrs)
    assert "Not fully charged since day(1)." in html
    assert "last full" not in html


def test_a_single_correction_has_no_strip(tmp_path: Path) -> None:
    """One bar says nothing a row does not; the drift line needs a bound."""
    html = _tab(tmp_path, {"soc_corrections": [_fix("2026-10-27T09:00:00+00:00")]})
    assert 'class="strip"' not in html
    assert "Counting drift" not in html


def test_without_the_attribute_the_tab_waits(tmp_path: Path) -> None:
    """A sensor that has not published yet leaves a one-line note."""
    assert "Corrections appear here after the first poll." in _tab(tmp_path, {})


def test_the_card_offers_the_tab_and_its_csv() -> None:
    """The tab is in the tab list, has a panel and a download."""
    source = _CARD.read_text(encoding="utf-8")
    assert '"restarts", "corrections"]' in source
    assert "corrections: (hass, model, text) => correctionsTab(hass, model, text)" in source
    assert 'corrections: ["at", "style", "from_soc", "to_soc", "points", "throughput"]' in source


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", "1 correction in 6 full charges (4 checked), last 30 days"),
        ("nl", "1 correctie bij 6 volle ladingen (4 gecontroleerd), laatste 30 dagen"),
    ],
)
def test_full_charges_that_could_not_be_judged_are_counted_apart(
    tmp_path: Path, language: str, expected: str
) -> None:
    """A full charge without a covered approach counts, but is not checked."""
    attrs = {"soc_corrections": [], "soc_corrections_30d": 1, "full_charges_30d": 6}
    assert expected in _tab(tmp_path, {**attrs, "full_charges_checked_30d": 4}, language)
    every = _tab(tmp_path, {**attrs, "full_charges_checked_30d": 6}, language)
    assert "checked" not in every
    assert "gecontroleerd" not in every


def test_a_drift_bound_past_thirty_percent_is_not_printed(tmp_path: Path) -> None:
    """A bound that large comes from a glitch; its digits would mislead."""
    html = _tab(tmp_path, {"soc_corrections": [], "soc_drift_bound_pct": 237.7})
    assert "more than ±30%, so confidence is at most low" in html
    assert "237" not in html


@pytest.mark.parametrize(("since", "shown"), [("2026-10-10T08:00:00+00:00", True), (_NOW, False)])
def test_a_pack_never_full_since_tracking_began_is_named(
    tmp_path: Path, since: str, shown: bool
) -> None:
    """Two weeks of tracking without one full charge earns the same tip."""
    html = _tab(tmp_path, {"soc_corrections": [], "soc_tracking_since": since})
    assert ("Not fully charged since tracking began on day(10)." in html) is shown
    with_full = {"soc_corrections": [], "soc_tracking_since": since, "last_full_charge_at": _NOW}
    assert "tracking began" not in _tab(tmp_path, with_full)
