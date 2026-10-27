"""The battery health card lists device restarts and network dropouts on their own tab."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_FRONTEND = Path(__file__).resolve().parents[1] / "custom_components" / "marstek" / "frontend"
_CARD = _FRONTEND / "marstek-battery-health.js"
_RESTARTS = _FRONTEND / "marstek-battery-health-restarts.js"
_COPY = _FRONTEND / "marstek-battery-health-copy.js"

# Local noon on 2026-10-27, so Today and Yesterday do not depend on the clock.
_NOW = "2026-10-27T12:00:00"


def _node(tmp_path: Path, body: str, *args: Any) -> Any:
    """Run a module script in Node and return what it writes as JSON."""
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is required to render the battery health Restarts tab")
    script = tmp_path / "restarts-tab.mjs"
    script.write_text(
        f"import {{ COPY }} from {json.dumps(_COPY.resolve().as_uri())};\n"
        "import { restartsHtml, outagesOf, OUTAGES_STEP } from "
        f"{json.dumps(_RESTARTS.resolve().as_uri())};\n"
        "const [attrs, language, extra] = process.argv.slice(2).map((arg) => JSON.parse(arg));\n"
        "const text = COPY[language];\n"
        "const day = (date) => `day(${date.getDate()})`;\n"
        "const pad = (value) => String(value).padStart(2, '0');\n"
        "const clock = (date) => `${date.getHours()}:${pad(date.getMinutes())}`;\n"
        f"const now = new Date({json.dumps(_NOW)});\n" + body,
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(script), *(json.dumps(arg) for arg in args)],
        check=False,
        capture_output=True,
        text=True,
        env={"TZ": "Europe/Brussels", "PATH": str(Path(node).parent)},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _tab(tmp_path: Path, attrs: dict[str, Any] | None, language: str = "en", **extra: Any) -> str:
    html: str = _node(
        tmp_path,
        "const html = restartsHtml(attrs, text, { day, clock, now, ...extra });\n"
        "process.stdout.write(JSON.stringify(html));\n",
        attrs,
        language,
        extra,
    )
    return html


def _outage(at: str, kind: str = "restart", **fields: Any) -> dict[str, Any]:
    return {
        "at": at,
        "kind": kind,
        "offline_s": 66.0,
        "failed_polls": 1,
        "evidence": ["energy_rollback", "ct_lost"] if kind == "restart" else [],
        **fields,
    }


def test_outages_are_grouped_by_day_newest_first(tmp_path: Path) -> None:
    """Today, Yesterday and older days each get a heading over their own list."""
    attrs = {
        "restarts_30d": 2,
        "dropouts_30d": 1,
        "outages": [
            _outage("2026-10-20T03:12:00+00:00", "dropout", offline_s=600.0, failed_polls=12),
            _outage("2026-10-26T21:05:00+00:00"),
            _outage("2026-10-27T09:30:00+00:00", evidence=["boot_pulse"], failed_polls=0),
        ],
    }
    html = _tab(tmp_path, attrs)

    assert html.startswith(
        '<p class="muted num">2 restarts and 1 network dropout in the last 30 days.</p>'
    )
    headings = [part.split("</h3>", 1)[0].split(">", 1)[1] for part in html.split("<h3")[1:]]
    assert headings == ["Today · 1 restart", "Yesterday · 1 restart", "day(20) · 1 dropout"]
    # Two lines a row: kind and time, then how long it was gone and why.
    assert "Restarted between two checks · start-up test seen at the plug" in html
    assert (
        '<time datetime="PT66S">Offline 66\u00a0s</time> · energy totals restored, '
        "CT meter disconnected"
    ) in html
    assert "Offline 10\u00a0min</time> · no sign of a restart" in html
    assert "failed poll" not in html
    # The kind is written out next to its icon; colour is never the only cue.
    assert html.count("<strong>Restart</strong>") == 2
    assert html.count("<strong>Network dropout</strong>") == 1
    assert '<time class="num" datetime="2026-10-27T09:30:00.000Z">10:30</time>' in html
    assert 'aria-labelledby="outage-day-0"' in html
    assert "data-action" not in html


def test_long_logs_show_ten_rows_and_a_show_more_button(tmp_path: Path) -> None:
    """The first revealed row can take focus; the button counts what is left."""
    outages = [_outage(f"2026-10-{day:02d}T08:00:00+00:00") for day in range(1, 26)]
    attrs = {"restarts_30d": 25, "dropouts_30d": 0, "outages": outages}

    first = _tab(tmp_path, attrs)
    assert first.count("<li") == 10
    assert "data-outage" not in first
    assert 'data-action="more-outages">Show 10 more</button>' in first

    second = _tab(tmp_path, attrs, "nl", shown=20)
    assert second.count("<li") == 20
    assert 'tabindex="-1" data-outage="10"' in second
    assert ">Toon nog 5</button>" in second
    assert "25 herstarts en 0 netwerkonderbrekingen in de laatste 30 dagen." in second

    assert ">Show" not in _tab(tmp_path, attrs, shown=30)


@pytest.mark.parametrize(
    ("attrs", "extra", "expected"),
    [
        (
            {"restarts_30d": 0, "dropouts_30d": 0, "outages": []},
            {},
            "No restarts or network dropouts in the last 30 days",
        ),
        (
            {"outages": [{"kind": "reboot", "at": "x"}, {"kind": "restart", "at": "nonsense"}]},
            {},
            "No restarts",
        ),
        (None, {}, "Restarts appear here after the first poll."),
        (None, {"tracked": False}, "Enable the Restarts sensor of this device"),
    ],
)
def test_an_empty_tab_says_why(
    attrs: dict[str, Any] | None, extra: dict[str, Any], expected: str, tmp_path: Path
) -> None:
    """No outages, a sensor still waiting and a disabled sensor read differently."""
    html = _tab(tmp_path, attrs, **extra)
    assert expected in html
    assert "<li" not in html


def test_a_young_log_says_since_when(tmp_path: Path) -> None:
    """Under 30 days of tracking, "in the last 30 days" would claim too much."""
    young = {"restarts_30d": 0, "dropouts_30d": 0, "outages": []}
    young["tracked_since"] = "2026-10-21T08:00:00+00:00"
    assert "No restarts or network dropouts since day(21)" in _tab(tmp_path, young)
    young.update(restarts_30d=1, outages=[_outage("2026-10-27T09:30:00+00:00")])
    assert "1 restart and 0 network dropouts since day(21)." in _tab(tmp_path, young)
    young["tracked_since"] = "2026-09-01T08:00:00+00:00"
    assert "in the last 30 days." in _tab(tmp_path, young)


def test_a_dropout_between_polls_claims_no_restart(tmp_path: Path) -> None:
    """Only a restart can fall between two polls; a dropout row never says so."""
    odd = _outage("2026-10-27T09:30:00+00:00", "dropout", failed_polls=0)
    html = _tab(tmp_path, {"dropouts_30d": 1, "outages": [odd]})
    assert '<span class="why num">no sign of a restart</span>' in html


def test_dutch_rows(tmp_path: Path) -> None:
    """Every row's words come from the copy, so the Dutch card has no English left."""
    attrs = {
        "restarts_30d": 1,
        "dropouts_30d": 1,
        "outages": [
            _outage("2026-10-27T09:30:00+00:00"),
            _outage("2026-10-27T10:30:00+00:00", "dropout"),
        ],
    }
    html = _tab(tmp_path, attrs, "nl")
    assert '<h3 id="outage-day-0">Vandaag · 1 herstart, 1 onderbreking</h3>' in html
    assert "66\u00a0s offline</time> · energietellers teruggezet, verbinding met CT-meter" in html
    assert "geen teken van een herstart" in html
    assert "<strong>Netwerkonderbreking</strong>" in html


def test_restarts_csv_holds_fixed_words_only(tmp_path: Path) -> None:
    """The download keeps attribute names, oldest first, and no cell can start a formula."""
    source = _CARD.read_text(encoding="utf-8")
    start = source.index("// Columns keep the attribute names.")
    end = source.index("function exportHtml")
    body = (
        "const finite = (value) =>\n"
        "  value != null && value !== '' && Number.isFinite(+value) ? +value : null;\n"
        "const toDate = (value) => new Date(value);\n"
        + source[start:end]
        + "process.stdout.write(JSON.stringify(csvOf({ restarts: attrs }, 'restarts')));\n"
    )
    attrs = {
        "outages": [
            _outage("2026-10-27T09:30:00+00:00", evidence=["ct_lost", "=cmd()", "boot_pulse"]),
            _outage("2026-10-20T03:12:00+00:00", "dropout"),
        ]
    }
    assert _node(tmp_path, body, attrs, "en", {}) == (
        "at,kind,offline_s,failed_polls,evidence\r\n"
        "2026-10-20T03:12:00.000Z,dropout,66,1,\r\n"
        "2026-10-27T09:30:00.000Z,restart,66,1,ct_lost;boot_pulse\r\n"
    )
