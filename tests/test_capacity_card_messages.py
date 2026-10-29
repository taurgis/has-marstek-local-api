"""What the health and fleet cards claim matches what the estimator measured."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

_FRONTEND = Path(__file__).resolve().parents[1] / "custom_components" / "marstek" / "frontend"
_MODULE = _FRONTEND / "marstek-battery-health.js"
_WEEK_S = 7 * 86_400
_T0 = 1_780_000_000


def _runtime() -> str:
    """Return the card source through its CSV export, imports made absolute."""
    source = _MODULE.read_text(encoding="utf-8")
    source = source[: source.index("function openDevice")]
    return re.sub(
        r"`\./(marstek-battery-health-[a-z]+\.js)\$\{new URL\(import\.meta\.url\)\.search\}`",
        lambda match: json.dumps((_FRONTEND / match.group(1)).resolve().as_uri()),
        source,
    )


_RENDER = """
const [what, hass, language] = process.argv.slice(2).map((arg) => JSON.parse(arg));
hass.locale = { language };
const view = { width: 360, runs: { filter: "all" }, page: 0 };
const out = {
  card: () => healthCardHtml(hass, { device_id: "dev0" }, "capacity", "c", view),
  fleet: () => fleetCardHtml(hass),
  csv: () => csvOf(readModel(hass, listPacks(hass)[0]), "capacity"),
}[what]();
process.stdout.write(JSON.stringify(out));
"""


def _render(tmp_path: Path, what: str, packs: list[dict[str, Any]], language: str = "en") -> str:
    """Render one card for ``packs`` by running the card source in Node."""
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is required to render the battery health cards")
    script = tmp_path / "card-messages.mjs"
    script.write_text(
        "const document = { documentElement: { lang: 'en' } };\n" + _runtime() + _RENDER,
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(script), *(json.dumps(arg) for arg in (what, _hass(packs), language))],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    rendered: str = json.loads(result.stdout)
    # The style sheet names every class; only the markup says what is shown.
    return re.sub(r"<style>.*?</style>", "", rendered, flags=re.S)


def _hass(packs: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the registries and states the cards read, one device per pack."""
    states: dict[str, Any] = {}
    entities: dict[str, Any] = {}
    devices: dict[str, Any] = {}
    for index, pack in enumerate(packs):
        device = f"dev{index}"
        devices[device] = {"name": pack.get("name", f"Pack {index}"), "model": pack.get("model")}
        readings = {
            "battery_usable_capacity": (pack.get("usable"), pack.get("attrs", {})),
            "battery_level": (pack.get("soc", 50), {}),
            "battery_power": (pack.get("power", 0), {}),
            "depth_of_discharge": (pack.get("dod"), {}),
        }
        for key, (value, attributes) in readings.items():
            entity_id = f"sensor.p{index}_{key}"
            entities[entity_id] = {
                "entity_id": entity_id,
                "platform": "marstek",
                "device_id": device,
                "translation_key": key,
            }
            state = "unknown" if value is None else str(value)
            states[entity_id] = {"state": state, "attributes": attributes}
    return {"states": states, "entities": entities, "devices": devices}


def _windows(discharge: int, charge: int) -> list[dict[str, Any]]:
    runs = [("discharge", 4_600.0)] * discharge + [("charge", 5_000.0)] * charge
    return [
        {"direction": direction, "capacity_wh": wh, "closed_at": _T0 + index * 3_600}
        for index, (direction, wh) in enumerate(runs)
    ]


def _pack(usable: float | None, **attrs: Any) -> dict[str, Any]:
    """A Venus E 3.0 measured both ways, overridable field by field."""
    base: dict[str, Any] = {
        "rated_capacity_wh": 5_120,
        "efficiency_correction": "charge_discharge_geometric_mean",
        "confidence": "high",
        "confidence_limits": [],
        "run_scatter_pct": 0.8,
        "windows": _windows(6, 6),
    }
    if usable is not None:
        base["state_of_health"] = round(usable / 5_120 * 100, 1)
    return {"usable": usable, "model": "VenusE 3.0", "attrs": {**base, **attrs}}


def _lead(html: str) -> str:
    return html[html.index('<p class="lead">') :].split("</p>", 1)[0]


def _header(html: str) -> str:
    return html[html.index('<div class="top">') :].split("</div>\n", 1)[0]


def test_still_measuring_counts_runs_per_direction(tmp_path: Path) -> None:
    """Three runs in two directions are not three runs in one."""
    attrs = {"confidence": "collecting", "windows": _windows(1, 2), "efficiency_correction": None}
    attrs |= {"run_scatter_pct": None}
    html = _render(tmp_path, "card", [_pack(None, **attrs)])
    assert "three runs in the same direction" in html
    assert "So far: 1 of 3 discharge runs, 2 of 3 charge runs." in html
    assert "Based on" not in html
    assert "collecting confidence" in html
    nl = _render(tmp_path, "card", [_pack(None, **attrs)], "nl")
    assert "Tot nu toe: 1 van 3 ontlaadritten, 2 van 3 laadritten." in nl


def test_a_one_way_estimate_says_so_and_draws_no_fade(tmp_path: Path) -> None:
    """A single-direction median leans on the assumed efficiency."""
    attrs = {"efficiency_correction": "discharge_median", "confidence": "low"}
    attrs |= {"windows": _windows(3, 2)}
    html = _render(tmp_path, "card", [_pack(4_560, **attrs)])
    assert "First estimate, from discharge runs only: holds" in _lead(html)
    assert "about 89% of its 5.12 kWh rating" in _lead(html)
    assert "Measuring" in _header(html)
    assert "faded</li>" not in html
    assert "Based on 3 runs since" in html
    nl = _render(tmp_path, "card", [_pack(4_560, **attrs)], "nl")
    assert "Eerste schatting, alleen uit ontlaadritten" in _lead(nl)


@pytest.mark.parametrize(
    ("usable", "lead", "faded"),
    [
        (5_100, "99.6% of its 5.12 kWh rating", None),
        (5_130, "at least its full 5.12 kWh rating", None),
        (4_500, "88% of its 5.12 kWh rating", "620 Wh faded"),
    ],
)
def test_fade_is_drawn_only_beyond_the_scatter(
    tmp_path: Path, usable: float, lead: str, faded: str | None
) -> None:
    """Just under the rating keeps a decimal and draws no fade."""
    html = _render(tmp_path, "card", [_pack(usable)])
    assert lead in _lead(html)
    if faded is None:
        assert "faded</li>" not in html
    else:
        assert faded in html
        assert "Fading" in _header(html)


def test_a_wide_scatter_absorbs_a_small_gap(tmp_path: Path) -> None:
    html = _render(tmp_path, "card", [_pack(4_950, run_scatter_pct=4.0)])
    assert "faded</li>" not in html
    assert "Half of them lie within ±4.0% of the median." in html


def test_large_counting_drift_withholds_the_band(tmp_path: Path) -> None:
    """A count referenced to the nameplate can hide fade; the card says it cannot tell."""
    pack = _pack(5_050, soc_drift_bound_pct=18.3, confidence="low")
    pack["attrs"]["confidence_limits"] = ["soc_drift"]
    html = _render(tmp_path, "card", [pack])
    assert "Uncertain" in _header(html)
    assert "Healthy" not in html
    assert "faded</li>" not in html
    assert "low confidence: charge-level drift" in html


def test_runs_that_disagree_widely_withhold_the_band(tmp_path: Path) -> None:
    """A non-linear charge level put a full-health pack at 89% with 15% scatter."""
    pack = _pack(4_560, run_scatter_pct=14.7, confidence="low")
    pack["attrs"]["confidence_limits"] = ["run_scatter"]
    html = _render(tmp_path, "card", [pack])
    assert "Uncertain" in _header(html)
    assert "Fading" not in html
    assert "faded</li>" not in html
    assert "low confidence: runs disagree" in html


def test_a_pack_without_a_rating_has_no_band(tmp_path: Path) -> None:
    """A Venus E mini publishes no nameplate; that is not 'measuring'."""
    pack = _pack(2_150, rated_capacity_wh=None)
    pack["attrs"].pop("state_of_health")
    html = _render(tmp_path, "card", [pack])
    assert 'class="band' not in _header(html)
    assert "from 0 to 100%" in _lead(html)
    fleet = _render(tmp_path, "fleet", [pack, _pack(5_000)])
    assert "No rating" in fleet
    assert "Measuring" not in fleet


def test_the_footer_names_what_holds_confidence_down(tmp_path: Path) -> None:
    attrs = {"confidence": "medium", "confidence_limits": ["run_scatter", "solar_charging"]}
    html = _render(tmp_path, "card", [_pack(5_000, **attrs)])
    assert "medium confidence: runs disagree, mostly solar-charged runs" in html
    nl = _render(tmp_path, "card", [_pack(5_000, **attrs)], "nl")
    assert "betrouwbaarheid gemiddeld: ritten lopen uiteen, vooral zonneladingen" in nl
    plain = _render(tmp_path, "card", [_pack(5_000)])
    assert "high confidence</p>" in plain


def _weeks(*points: tuple[float, str | None, float | None]) -> list[dict[str, Any]]:
    history = []
    for index, (wh, method, rated) in enumerate(points):
        point: dict[str, Any] = {"week_start": _T0 + index * _WEEK_S, "usable_wh": wh}
        if method is not None:
            point["method"] = method
        if rated is not None:
            point["rated_wh"] = rated
        history.append(point)
    return history


def test_the_trend_ignores_weeks_of_another_method(tmp_path: Path) -> None:
    """A switch from a one-way median to the geometric mean is not a gain."""
    geo = "charge_discharge_geometric_mean"
    history = _weeks(
        (4_870, "discharge_median", 5_120), *[(5_110 + week, geo, 5_120) for week in range(6)]
    )
    html = _render(tmp_path, "card", [_pack(5_115, history=history)])
    assert "Steady over the last 35 days" in html
    assert "since the first weekly estimate" not in html


def test_the_trend_ignores_weeks_of_another_pack(tmp_path: Path) -> None:
    geo = "charge_discharge_geometric_mean"
    history = _weeks(
        *[(2_085, geo, 2_080)] * 3, *[(4_150 - 30 * week, geo, 4_160) for week in range(6)]
    )
    pack = _pack(4_000, history=history, rated_capacity_wh=4_160, run_scatter_pct=0.5)
    pack["attrs"]["state_of_health"] = 96.2
    html = _render(tmp_path, "card", [pack])
    assert "\u2212150 Wh (\u22123.6 points) since the first weekly estimate" in html


def test_a_real_drop_beyond_the_scatter_is_not_called_steady(tmp_path: Path) -> None:
    """The old threshold was half the charge/discharge gap, which hid this drop."""
    geo = "charge_discharge_geometric_mean"
    history = _weeks(*[(5_110 - 35 * week, geo, 5_120) for week in range(7)])
    html = _render(tmp_path, "card", [_pack(4_900, history=history, spread_wh=900)])
    assert "\u2212210 Wh" in html
    assert "Steady" not in html


@pytest.mark.parametrize(
    ("power", "expected"),
    [
        (300, "At 300 W, it reaches its 12% floor in about 6\u00a0h\u00a02\u00a0min."),
        (-1_000, "At 1,000 W, it reaches 100% in about 2\u00a0h\u00a045\u00a0min."),
    ],
)
def test_runtime_converts_between_the_ac_port_and_the_cells(
    tmp_path: Path, power: float, expected: str
) -> None:
    """Usable capacity is DC energy; the power sensor reads the AC port."""
    pack = _pack(5_120) | {"power": power, "soc": 50}
    assert expected in _render(tmp_path, "card", [pack])


def _iso(days_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - days_ago * 86_400, UTC).isoformat()


def test_a_glitch_sized_drift_bound_is_not_printed(tmp_path: Path) -> None:
    jump = {"at": _iso(2), "from_soc": 94, "to_soc": 100, "points": 6.0, "style": "jump"}
    attrs = {"soc_corrections": [jump], "soc_corrections_30d": 1, "soc_drift_bound_pct": 237.7}
    html = _render(tmp_path, "card", [_pack(5_000, **attrs)])
    assert "more than ±30% counting drift" in html
    assert "237" not in html


@pytest.mark.parametrize(("days", "shown"), [(20, True), (3, False)])
def test_never_full_since_tracking_began(tmp_path: Path, days: float, shown: bool) -> None:
    attrs = {"soc_corrections": [], "soc_corrections_30d": 0, "soc_tracking_since": _iso(days)}
    html = _render(tmp_path, "card", [_pack(5_000, **attrs)])
    assert ("Not fully charged since tracking began on" in html) is shown


def test_fleet_totals_say_how_many_packs_they_cover(tmp_path: Path) -> None:
    packs = [_pack(5_000) | {"name": "Garage"}, _pack(4_700), _pack(None, confidence="collecting")]
    html = _render(tmp_path, "fleet", packs)
    assert "usable · 2 of 3 batteries" in html
    assert "stored now (50%) · 2 of 3 batteries" in html
    assert "Garage holds 52% of your usable capacity (2 of 3 batteries measured)." in html
    whole = _render(tmp_path, "fleet", packs[:2])
    assert "of 2 batteries" not in whole


@pytest.mark.parametrize(
    ("models", "rtes", "line"),
    [
        (("VenusE 3.0", "VenusE 3.0"), (0.85, 0.78), "Pack 1 returns 7.0 points less"),
        (("VenusE 3.0", "VenusE 3.0"), (0.85, 0.82), None),
        (("VenusE 3.0", "VenusA"), (0.87, 0.78), None),
    ],
)
def test_round_trips_compare_within_one_model(
    tmp_path: Path, models: tuple[str, str], rtes: tuple[float, float], line: str | None
) -> None:
    packs = []
    for model, rte in zip(models, rtes, strict=True):
        pack = _pack(5_000, implied_round_trip_efficiency=rte)
        packs.append(pack | {"model": model})
    html = _render(tmp_path, "fleet", packs)
    if line is None:
        assert "less of each charge" not in html
    else:
        assert f"{line} of each charge than the best battery of the same model." in html


def test_an_implausible_implied_round_trip_is_hidden(tmp_path: Path) -> None:
    html = _render(tmp_path, "card", [_pack(5_000, implied_round_trip_efficiency=1.04)])
    assert "Round trip" not in html
    shown = _render(tmp_path, "card", [_pack(5_000, implied_round_trip_efficiency=0.86)])
    assert "Round trip 86.0%" in shown


def test_the_weekly_csv_keeps_each_weeks_rating_and_method(tmp_path: Path) -> None:
    history = _weeks((2_085, "discharge_median", 2_080), (4_150, None, None))
    csv = _render(tmp_path, "csv", [_pack(4_150, history=history, rated_capacity_wh=4_160)])
    rows = csv.strip().split("\r\n")
    assert rows[0] == "week_start,usable_wh,rated_wh,method"
    assert rows[1].endswith(",2085,2080,discharge_median")
    assert rows[2].endswith(",4150,4160,")


def test_the_capacity_tab_says_what_the_figure_measures(tmp_path: Path) -> None:
    history = _weeks((5_000, "charge_discharge_geometric_mean", 5_120))
    html = _render(tmp_path, "card", [_pack(5_000, history=history)])
    assert "Measured per 100% of the charge level the battery reports." in html
