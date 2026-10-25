"""The Energy tab omits solar and an unused backup socket."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_FRONTEND = Path(__file__).resolve().parents[1] / "custom_components" / "marstek" / "frontend"
_MODULE = _FRONTEND / "marstek-battery-health.js"
_COPY = _FRONTEND / "marstek-battery-health-copy.js"

# A Venus E 3.0 with nothing on the backup socket: AC totals in Wh, no PV.
_VENUS_E = {
    "gridIn": 1_326_000,
    "gridOut": 1_105_000,
    "solar": None,
    "load": 0,
    "pack": {"entities": {}},
}


def _closing_brace(source: str, open_index: int) -> int:
    """Return the index just after the `}` that closes source[open_index]."""
    depth = 0
    for index in range(open_index, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    raise AssertionError("unbalanced braces")


def _energy_runtime() -> str:
    """Return the card functions energyHtml needs, through energyHtml itself."""
    source = _MODULE.read_text(encoding="utf-8")
    start = source.index("function languageOf")
    signature = source.index("function energyHtml")
    end = _closing_brace(source, source.index("{", signature))
    return source[start:end]


def _energy_html(model: dict[str, Any], language: str, tmp_path: Path) -> str:
    """Render energyHtml for one model by running the card source in Node."""
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is required to render the battery health Energy tab")
    script = tmp_path / "energy-tab.mjs"
    script.write_text(
        "import { COPY } from "
        + json.dumps(_COPY.resolve().as_uri())
        + ";\n"
        + "const document = { documentElement: { lang: 'en' } };\n"
        + _energy_runtime()
        + "\nconst model = JSON.parse(process.argv[2]);\n"
        + "const hass = { locale: { language: process.argv[3] } };\n"
        + "process.stdout.write(energyHtml(hass, model, copyFor(hass)));\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(script), json.dumps(model), language],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _labels(html: str) -> list[str]:
    """Return the meter labels, in order."""
    return [part.split("</span>", 1)[0] for part in html.split('<div class="meter"><span>')[1:]]


def test_venus_e_without_pv_or_backup_use_shows_only_ac(tmp_path: Path) -> None:
    """No solar entity and a 0 Wh backup socket leave Charged and Delivered."""
    html = _energy_html(_VENUS_E, "en", tmp_path)
    assert _labels(html) == ["Charged from AC", "Delivered to AC"]
    assert "1,326 kWh" in html
    assert "1,105 kWh" in html
    assert "Solar" not in html
    assert "Backup socket" not in html
    assert "Home load" not in html
    assert "Not reported" not in html


def test_solar_row_stays_when_the_pack_has_a_solar_entity(tmp_path: Path) -> None:
    """Venus A/D keep Solar, including a missing reading, and hide a 0 Wh socket."""
    model = {
        "gridIn": 10_000,
        "gridOut": 8_000,
        "solar": None,
        "load": 0,
        "pack": {"entities": {"solar": "sensor.venus_a_total_solar_energy"}},
    }
    html = _energy_html(model, "en", tmp_path)
    assert _labels(html) == ["Charged from AC", "Delivered to AC", "Solar"]
    assert "Not reported" in html
    assert "Backup socket" not in html


def test_solar_row_stays_when_a_solar_total_is_known(tmp_path: Path) -> None:
    """A reported solar total shows even if the entity id is not on the pack yet."""
    model = {
        "gridIn": 10_000,
        "gridOut": 8_000,
        "solar": 0,
        "load": None,
        "pack": {"entities": {}},
    }
    html = _energy_html(model, "en", tmp_path)
    assert _labels(html) == ["Charged from AC", "Delivered to AC", "Solar"]
    assert ">0 Wh<" in html or "0 Wh" in html


@pytest.mark.parametrize("load", [None, 0, -1])
def test_backup_socket_row_is_omitted_unless_it_delivered_energy(
    load: float | None, tmp_path: Path
) -> None:
    """Null, zero, and negative load are an unused socket, not home consumption."""
    model = {**_VENUS_E, "load": load}
    html = _energy_html(model, "en", tmp_path)
    assert "Backup socket" not in html
    assert "Huisverbruik" not in html


def test_backup_socket_row_uses_the_socket_name(tmp_path: Path) -> None:
    """A socket that has delivered energy is named in English and Dutch."""
    model = {
        "gridIn": 10_000,
        "gridOut": 8_000,
        "solar": 2_500,
        "load": 12_500,
        "pack": {"entities": {"solar": "sensor.venus_a_total_solar_energy"}},
    }
    english = _energy_html(model, "en", tmp_path)
    assert _labels(english) == [
        "Charged from AC",
        "Delivered to AC",
        "Solar",
        "Backup socket",
    ]
    dutch = _energy_html(model, "nl", tmp_path)
    assert _labels(dutch) == [
        "Geladen via AC",
        "Geleverd aan AC",
        "Zon",
        "Back-upstekker",
    ]
    assert "Home load" not in english
    assert "Huisverbruik" not in dutch
