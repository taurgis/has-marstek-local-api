"""A Battery health refresh must not scroll a reader back up the page.

Home Assistant calls ``set hass`` on every state change. Rebuilding the shadow
tree drops the focused tab or panel, and the browser then scrolls that control
back into view. On a phone that feels like the page yanking to the top every
few seconds.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

import pytest
import pytest_socket

_FRONTEND = Path(__file__).resolve().parents[1] / "custom_components" / "marstek" / "frontend"
_MODULE = _FRONTEND / "marstek-battery-health.js"
_OPENER = build_opener(ProxyHandler({}))

# Phone-sized viewport. The card plus the spacer is taller than this.
_VIEWPORT = {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True}


def test_refresh_patches_nodes_instead_of_replacing_the_shadow_tree() -> None:
    """The redraw path keeps the focused control instead of parsing over it."""
    source = _MODULE.read_text(encoding="utf-8")
    redraw = source[source.index("function redraw") : source.index("class MarstekCard")]
    assert "root.innerHTML" not in redraw
    assert "preventScroll: true" in redraw
    assert "function morphChildren" in source


def _chrome_bin() -> str | None:
    """Return a Chrome binary, or None when this machine has none."""
    candidates = [
        os.environ.get("HA_CHROME_BIN"),
        "/opt/google/chrome/chrome",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _FrontendHandler(BaseHTTPRequestHandler):
    """Serves the card module and one scroll fixture. Nothing else."""

    html = b""

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in {"/", "/index.html"}:
            body, content_type = self.html, "text/html; charset=utf-8"
        elif path.startswith("/frontend/"):
            file = (_FRONTEND / path.removeprefix("/frontend/")).resolve()
            if file.parent != _FRONTEND or not file.is_file():
                self.send_error(404)
                return
            body, content_type = file.read_bytes(), "text/javascript; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _page() -> str:
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>scroll</title>
<style>html,body{margin:0}#head{height:900px}#tail{height:2400px}</style></head><body>
<script>
customElements.define("home-assistant", class extends HTMLElement {});
customElements.define("ha-card", class extends HTMLElement {
  connectedCallback() { this.style.display = "block"; }
});
customElements.define("ha-icon", class extends HTMLElement {
  connectedCallback() {
    this.style.display = "inline-block";
    this.style.width = "24px";
    this.style.height = "24px";
  }
});
</script>
<div id="head"></div><div id="mount"></div><div id="tail"></div>
<script type="module">
await customElements.whenDefined("marstek-battery-health-card");
const deviceId = "dev1";
const entity = (id, key) => ({
  entity_id: id, device_id: deviceId, platform: "marstek", translation_key: key,
});
const entities = {
  "sensor.cap": entity("sensor.cap", "battery_usable_capacity"),
  "sensor.soc": entity("sensor.soc", "battery_level"),
  "sensor.power": entity("sensor.power", "battery_power"),
  "sensor.status": entity("sensor.status", "battery_status"),
  "sensor.soh": entity("sensor.soh", "battery_state_of_health"),
  "sensor.in": entity("sensor.in", "total_grid_input_energy"),
  "sensor.out": entity("sensor.out", "total_grid_output_energy"),
  "sensor.restarts": entity("sensor.restarts", "device_restarts"),
};
const windows = Array.from({ length: 12 }, (_, i) => ({
  direction: i % 2 ? "charge" : "discharge", start_soc: 40, end_soc: 70, soc_delta: 30,
  capacity_wh: 4700 + i, closed_at: 1760000000 + i * 3600, duration_s: 2400, average_power_w: 900,
}));
function hass(power, soc) {
  const stamp = "2026-10-28T06:10:" + String(power % 60).padStart(2, "0") + "+00:00";
  const state = (value, attributes = {}) => ({
    state: String(value), last_updated: stamp, attributes,
  });
  return {
    language: "en", locale: { language: "en" },
    devices: { [deviceId]: { name: "Venus E" } }, entities,
    states: {
      "sensor.cap": state(4800, {
        rated_capacity_wh: 5120, state_of_health: 93.6, confidence: "high",
        unit_of_measurement: "Wh",
        history: [
          { week_start: 1750000000, usable_wh: 5000 },
          { week_start: 1750000000 + 86400 * 40, usable_wh: 4800 },
        ],
        windows, spread_wh: 80, implied_round_trip_efficiency: 0.91,
      }),
      "sensor.soc": state(soc, { unit_of_measurement: "%" }),
      "sensor.power": state(power, { unit_of_measurement: "W" }),
      "sensor.status": state(power > 20 ? "discharging" : "idle"),
      "sensor.soh": state(93.6, { unit_of_measurement: "%" }),
      "sensor.in": state(1200000, { unit_of_measurement: "Wh" }),
      "sensor.out": state(1100000, { unit_of_measurement: "Wh" }),
      "sensor.restarts": state(1, { restarts_30d: 1, dropouts_30d: 0, outages: [] }),
    },
  };
}
const fleet = document.createElement("marstek-battery-fleet-card");
const card = document.createElement("marstek-battery-health-card");
document.getElementById("mount").append(fleet, card);
card.setConfig({ device_id: deviceId });
const first = hass(640, 62);
fleet.hass = first;
card.hass = first;
window.__card = card;
window.__apply = (power, soc) => {
  const before = card.shadowRoot.querySelector("ha-card");
  const next = hass(power, soc);
  fleet.hass = next;
  card.hass = next;
  return {
    y: window.scrollY,
    flow: card.shadowRoot.querySelector(".flow")?.textContent || "",
    sameCard: before === card.shadowRoot.querySelector("ha-card"),
    active: card.shadowRoot.activeElement?.getAttribute("data-tab")
      || card.shadowRoot.activeElement?.getAttribute("role") || null,
  };
};
window.__press = (selector) => {
  const el = window.__card.shadowRoot.querySelector(selector);
  if (!el) return { missing: selector };
  const docTop = window.scrollY + el.getBoundingClientRect().top;
  // Leave the control in the lower part of the phone, as when the reader
  // has scrolled down to it. Focusing it must not jump back up.
  const offset = Math.round(window.innerHeight * 0.72);
  window.scrollTo(0, Math.max(0, docTop - offset));
  const before = window.scrollY;
  el.click();
  return {
    before,
    docTop,
    offset,
    head: document.getElementById("head")?.offsetHeight || 0,
    y: window.scrollY,
    selected: el.getAttribute("aria-selected"),
    pressed: el.getAttribute("aria-pressed"),
  };
};
window.__ready = true;
</script>
<script type="module" src="/frontend/marstek-battery-health.js"></script>
</body></html>"""


class _Cdp:
    """A tiny Chrome DevTools client for one page."""

    def __init__(self, port: int) -> None:
        self.port = port
        self._socket: Any = None
        self._n = 0

    async def open(self, url: str) -> None:
        import aiohttp

        request = Request(f"http://127.0.0.1:{self.port}/json/new?{url}", method="PUT")
        with _OPENER.open(request) as response:
            target = json.load(response)
        session = aiohttp.ClientSession()
        self._session = session
        self._socket = await session.ws_connect(target["webSocketDebuggerUrl"], max_msg_size=0)
        await self.call("Emulation.setDeviceMetricsOverride", _VIEWPORT)
        await self.call("Page.enable")
        await self.call("Runtime.enable")
        await self.call("Network.setCacheDisabled", {"cacheDisabled": True})

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._n += 1
        msg_id = self._n
        await self._socket.send_json({"id": msg_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(self._socket.receive(), timeout=8)
            data = json.loads(raw.data)
            if data.get("id") != msg_id:
                continue
            if "error" in data:
                raise RuntimeError(data["error"])
            result = data.get("result", {})
            assert isinstance(result, dict)
            return result
        raise TimeoutError(method)

    async def evaluate(self, expression: str) -> Any:
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(json.dumps(result["exceptionDetails"])[:800])
        payload = result.get("result", {})
        assert isinstance(payload, dict)
        return payload.get("value")

    async def close(self) -> None:
        await self._session.close()


def _start_server() -> tuple[ThreadingHTTPServer, int]:
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _FrontendHandler)
    _FrontendHandler.html = _page().encode()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def _launch_chrome(profile: Path) -> subprocess.Popen[bytes]:
    """Start Chrome on a debugging port it picks itself.

    Port 0 lets Chrome bind a free port and write it to ``DevToolsActivePort``
    in the profile, so no other process can take it between choosing and
    binding (the race a pre-picked free port has under pytest-xdist).
    """
    binary = _chrome_bin()
    assert binary is not None
    return subprocess.Popen(
        [
            binary,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-debugging-port=0",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            "--window-size=390,844",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# A cold Chrome on a shared CI runner can take well over 8 s to start.
_CHROME_START_TIMEOUT = 60.0


def _wait_for_chrome(chrome: subprocess.Popen[bytes], profile: Path) -> int:
    """Return the debugging port once Chrome answers on it."""
    active = profile / "DevToolsActivePort"
    deadline = time.monotonic() + _CHROME_START_TIMEOUT
    while time.monotonic() < deadline:
        if chrome.poll() is not None:
            raise RuntimeError(f"Chrome exited with {chrome.returncode} before it was ready")
        try:
            port = int(active.read_text(encoding="utf-8").split("\n", 1)[0])
            with _OPENER.open(f"http://127.0.0.1:{port}/json/version", timeout=5) as response:
                json.load(response)
                return port
        except (ValueError, URLError, TimeoutError, OSError):
            time.sleep(0.1)
    raise RuntimeError("Chrome did not open a debugging port")


async def _scroll_stays(http_port: int, cdp_port: int) -> None:
    cdp = _Cdp(cdp_port)
    await cdp.open(f"http://127.0.0.1:{http_port}/")
    frames = "new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    try:
        for _ in range(50):
            if await cdp.evaluate("Boolean(window.__ready)"):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("the card module did not start")

        # Select Runs while it is on screen, then scroll down to read it.
        await cdp.evaluate("window.scrollTo(0, 0)")
        clicked = await cdp.evaluate(
            """(() => {
              const tab = window.__card.shadowRoot.querySelector('[data-tab="energy"]');
              tab.click();
              return window.__card.shadowRoot.textContent.includes("Charged from AC")
                && tab.getAttribute("aria-selected") === "true";
            })()"""
        )
        assert clicked is True

        for label, focus in (
            ("blur", "window.__card.shadowRoot.activeElement?.blur()"),
            ("tab", "window.__card.shadowRoot.querySelector('[data-tab=\"runs\"]').click()"),
            ("panel", "window.__card.shadowRoot.querySelector('[role=\"tabpanel\"]').focus()"),
        ):
            await cdp.evaluate("window.scrollTo(0, 0)")
            await cdp.evaluate(focus)
            await cdp.evaluate(frames)
            placed = await cdp.evaluate(
                "window.scrollTo(0, document.documentElement.scrollHeight);"
                "({y: window.scrollY, height: document.documentElement.scrollHeight,"
                " view: window.innerHeight})"
            )
            await cdp.evaluate(frames)
            start = await cdp.evaluate("window.scrollY")
            assert isinstance(placed, dict)
            assert start > 600, (label, placed, start)
            for power in (701, 802, 543, 964):
                sample = await cdp.evaluate(f"window.__apply({power}, {40 + power % 20})")
                await cdp.evaluate(frames)
                after = await cdp.evaluate("window.scrollY")
                assert sample["sameCard"] is True, label
                assert str(power) in sample["flow"], label
                assert after == start, (label, power, start, after)
    finally:
        await cdp.close()


async def _clicks_keep_scroll(http_port: int, cdp_port: int) -> None:
    """Tabs, run filters and page buttons must not jump back to the top."""
    cdp = _Cdp(cdp_port)
    await cdp.open(f"http://127.0.0.1:{http_port}/")
    frames = "new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    try:
        for _ in range(50):
            if await cdp.evaluate("Boolean(window.__ready)"):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("the card module did not start")
        size = await cdp.evaluate("({h: innerHeight, w: innerWidth, dpr: devicePixelRatio})")
        assert size == {"h": 844, "w": 390, "dpr": 2}, size

        async def press(selector: str) -> tuple[float, float]:
            pressed = await cdp.evaluate(f"window.__press({json.dumps(selector)})")
            await cdp.evaluate(frames)
            after = await cdp.evaluate("window.scrollY")
            assert isinstance(pressed, dict), selector
            assert "missing" not in pressed, selector
            before = float(pressed["before"])
            assert before > 200, (selector, pressed)
            assert float(after) > 200, (selector, before, after)
            assert abs(float(after) - before) < 48, (selector, before, after)
            return before, float(after)

        for name in ("runs", "energy", "restarts", "capacity"):
            await press(f'[data-tab="{name}"]')
            selected = await cdp.evaluate(
                f"window.__card.shadowRoot.querySelector('[data-tab=\"{name}\"]')"
                ".getAttribute('aria-selected')"
            )
            assert selected == "true", name

        await press('[data-tab="runs"]')
        await press('[data-action="page-next"]')
        await press('[data-action="page-prev"]')
        for name in ("discharge", "charge", "all"):
            await press(f'[data-action="runs-{name}"]')
            state = await cdp.evaluate(
                "window.__card.shadowRoot.querySelector"
                f"('[data-action=\"runs-{name}\"]').getAttribute('aria-pressed')"
            )
            assert state == "true", name
    finally:
        await cdp.close()


def _run_in_chrome(tmp_path: Path, scenario: Any) -> None:
    """Launch a phone-sized Chrome and run one scroll scenario."""
    pytest_socket.enable_socket()
    if _chrome_bin() is None:
        pytest.skip("Chrome is not installed")
    server, http_port = _start_server()
    profile = tmp_path / "chrome-profile"
    profile.mkdir()
    chrome = _launch_chrome(profile)
    try:
        cdp_port = _wait_for_chrome(chrome, profile)
        asyncio.run(scenario(http_port, cdp_port))
    finally:
        server.shutdown()
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except subprocess.TimeoutExpired:
            chrome.kill()


@pytest.mark.enable_socket
def test_mobile_scroll_stays_through_hass_updates(tmp_path: Path, socket_enabled: None) -> None:
    """Live power changes keep window.scrollY put on a 390x844 viewport.

    Covers a blurred page, a focused tab, and a focused tab panel. Each one
    used to jump because the refresh rebuilt the focused control.
    """
    del socket_enabled
    _run_in_chrome(tmp_path, _scroll_stays)


@pytest.mark.enable_socket
def test_mobile_tab_and_filter_clicks_keep_scroll(tmp_path: Path, socket_enabled: None) -> None:
    """Switching tabs, run filters and pages keeps a phone scrolled down.

    The control is placed at the top of a 390x844 viewport, with a long page
    above it, then activated. The scroll offset must stay there.
    """
    del socket_enabled
    _run_in_chrome(tmp_path, _clicks_keep_scroll)
