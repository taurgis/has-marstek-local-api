#!/usr/bin/env python3
"""Drive Home Assistant in Chrome via the Chrome DevTools Protocol.

Never click screenshot pixels. Chrome 136+ silently ignores
``--remote-debugging-port`` on the default profile; this helper can
relaunch Chrome with a non-default ``--user-data-dir`` so ``/json/version``
works again.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import aiohttp

CDP_HOST = os.environ.get("HA_CDP_HOST", "127.0.0.1")
CDP_PORT = int(os.environ.get("HA_CDP_PORT", "9222"))
USER_DATA_DIR = os.environ.get("HA_CHROME_USER_DATA_DIR", "/tmp/chrome-ha-debug")
DEFAULT_URL = os.environ.get(
    "HA_URL", "http://127.0.0.1:8123/config/integrations/dashboard"
)
CHROME_BIN = os.environ.get("HA_CHROME_BIN", "/opt/google/chrome/chrome")
DISPLAY = os.environ.get("DISPLAY", ":1")

# Injected into the page. Walks open shadow roots (HA Lit).
HELPER_JS = r"""
(() => {
  const SKIP = new Set(["SCRIPT", "STYLE", "LINK", "META", "NOSCRIPT"]);

  const isVisible = (el) => {
    try {
      const s = getComputedStyle(el);
      if (s.display === "none" || s.visibility === "hidden" || s.opacity === "0") {
        return false;
      }
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    } catch (e) {
      return true;
    }
  };

  const walk = (root, fn) => {
    const nodes = root.querySelectorAll("*");
    for (const el of nodes) {
      fn(el);
      if (el.shadowRoot) walk(el.shadowRoot, fn);
    }
  };

  const ownText = (el) => {
    try {
      return Array.from(el.childNodes)
        .filter((n) => n.nodeType === 3)
        .map((n) => n.textContent || "")
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();
    } catch (e) {
      return "";
    }
  };

  const deepText = (el) => {
    const parts = [];
    const rec = (n) => {
      if (!n) return;
      if (n.nodeType === 3) {
        parts.push(n.textContent || "");
        return;
      }
      if (n.nodeType === 1) {
        if (SKIP.has(n.tagName)) return;
        if (n.shadowRoot) rec(n.shadowRoot);
        for (const c of n.childNodes) rec(c);
      }
    };
    rec(el);
    return parts.join(" ").replace(/\s+/g, " ").trim();
  };

  const attr = (el, name) => {
    try {
      return el.getAttribute(name) || "";
    } catch (e) {
      return "";
    }
  };

  const labelOf = (el) => {
    const bits = [
      attr(el, "aria-label"),
      attr(el, "label"),
      attr(el, "title"),
      attr(el, "name"),
      attr(el, "placeholder"),
      el.label || "",
      ownText(el),
    ];
    try {
      const lab = el.shadowRoot && el.shadowRoot.querySelector("label, .label, .mdc-floating-label");
      if (lab) bits.push((lab.textContent || "").trim());
    } catch (e) {}
    return bits.filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
  };

  const contextOf = (el) => {
    let n = el;
    const chunks = [];
    for (let i = 0; i < 12 && n; i++) {
      const tag = (n.tagName || "").toLowerCase();
      if (tag.includes("card") || tag.includes("dialog") || tag.includes("flow")) {
        chunks.push(deepText(n).slice(0, 240));
        try {
          const flow = n.flow;
          if (flow) {
            const uid = (flow.context && flow.context.unique_id) || "";
            chunks.push(String(uid));
            chunks.push(String(flow.flow_id || ""));
            chunks.push(String(flow.step_id || ""));
            chunks.push(String(flow.localized_title || ""));
          }
        } catch (e) {}
      }
      n = n.parentNode || (n.getRootNode && n.getRootNode().host) || null;
    }
    return chunks.join(" ").replace(/\s+/g, " ").trim();
  };

  const isClickable = (el) => {
    const tag = el.tagName.toLowerCase();
    const role = attr(el, "role");
    if (tag === "button" || tag === "a" || role === "button" || role === "listitem") return true;
    if (tag.includes("button") || tag.includes("list-item") || tag.includes("list-item-button")) {
      return true;
    }
    if (tag === "ha-md-list-item" || tag === "mwc-list-item" || tag === "md-list-item") {
      return true;
    }
    return attr(el, "type") === "submit";
  };

  const describe = (el) => {
    const tag = el.tagName.toLowerCase();
    return {
      tag,
      label: labelOf(el),
      text: (ownText(el) || labelOf(el) || deepText(el)).slice(0, 120),
      context: contextOf(el).slice(0, 240),
    };
  };

  const collectClickables = () => {
    const out = [];
    walk(document, (el) => {
      if (!isClickable(el) || !isVisible(el)) return;
      const d = describe(el);
      if (!d.text && !d.label) return;
      out.push(d);
      d._el = el;
    });
    return out;
  };

  const collectFields = () => {
    const out = [];
    walk(document, (el) => {
      const tag = el.tagName.toLowerCase();
      const interesting =
        tag === "input" ||
        tag === "textarea" ||
        tag === "ha-textfield" ||
        tag === "ha-selector-text" ||
        tag === "ha-selector-number" ||
        tag === "ha-combo-box" ||
        tag === "ha-form" ||
        tag.includes("textfield") ||
        tag.includes("selector");
      if (!interesting) return;
      if (tag === "ha-form") return;
      let value = "";
      try {
        value = el.value != null ? String(el.value) : "";
      } catch (e) {}
      if (!value) {
        const input = deepestInput(el);
        if (input) value = input.value || "";
      }
      out.push({
        tag,
        label: labelOf(el),
        value,
        type: attr(el, "type") || el.type || "",
        _el: el,
      });
    });
    return out;
  };

  const deepestInput = (el) => {
    if (!el) return null;
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") return el;
    let found = null;
    const rec = (root) => {
      if (found) return;
      const hit = root.querySelector && root.querySelector("input, textarea");
      if (hit) {
        found = hit;
        return;
      }
      walk(root, (n) => {
        if (found) return;
        if (n.tagName === "INPUT" || n.tagName === "TEXTAREA") found = n;
      });
    };
    rec(el.shadowRoot || el);
    return found;
  };

  const collectCards = () => {
    const out = [];
    walk(document, (el) => {
      const tag = el.tagName.toLowerCase();
      if (tag !== "ha-config-flow-card" && tag !== "ha-integration-card") return;
      const item = { tag, text: deepText(el).slice(0, 200), unique_id: "", step_id: "" };
      try {
        const flow = el.flow;
        if (flow) {
          item.unique_id = (flow.context && flow.context.unique_id) || "";
          item.step_id = flow.step_id || "";
          item.flow_id = flow.flow_id || "";
          item.title = flow.localized_title || "";
        }
      } catch (e) {}
      out.push(item);
    });
    return out;
  };

  const collectDialogs = () => {
    const out = [];
    walk(document, (el) => {
      const tag = el.tagName.toLowerCase();
      if (tag !== "ha-dialog" && tag !== "mwc-dialog" && !tag.endsWith("-dialog")) return;
      if (!isVisible(el)) return;
      const heading =
        attr(el, "heading") ||
        el.heading ||
        (el.shadowRoot &&
          (el.shadowRoot.querySelector("[slot=heading], .heading, h2, h1") || {}).textContent) ||
        "";
      out.push({
        tag,
        heading: String(heading || deepText(el).slice(0, 80)).replace(/\s+/g, " ").trim(),
        text: deepText(el).slice(0, 500),
      });
    });
    return out;
  };

  const matches = (item, needle, near) => {
    const n = needle.toLowerCase();
    const hay = `${item.text || ""} ${item.label || ""}`.toLowerCase();
    const exact = (item.text || "").toLowerCase() === n || (item.label || "").toLowerCase() === n;
    const contains = hay.includes(n);
    if (!contains && !exact) return 0;
    if (near) {
      const ctx = `${item.context || ""} ${item.text || ""} ${item.label || ""}`.toLowerCase();
      if (!ctx.includes(near.toLowerCase())) return 0;
    }
    return exact ? 2 : 1;
  };

  const pick = (items, needle, near, nth) => {
    const scored = [];
    items.forEach((it, idx) => {
      const score = matches(it, needle, near);
      if (score) scored.push({ score, idx, it });
    });
    scored.sort((a, b) => b.score - a.score);
    const exact = scored.filter((s) => s.score === 2);
    const pool = exact.length ? exact : scored;
    if (!pool.length) return { ok: false, error: "not found", needle, near };
    if (nth == null && pool.length > 1) {
      return {
        ok: false,
        error: "ambiguous",
        needle,
        near,
        matches: pool.map((p) => {
          const { _el, ...rest } = p.it;
          return rest;
        }),
      };
    }
    const chosen = pool[nth || 0];
    if (!chosen) return { ok: false, error: "nth out of range", count: pool.length };
    return { ok: true, item: chosen.it };
  };

  window.__haCdp = {
    dump() {
      const clickables = collectClickables().map(({ _el, ...rest }) => rest);
      const fields = collectFields().map(({ _el, ...rest }) => rest);
      return {
        url: location.href,
        title: document.title,
        dialogs: collectDialogs(),
        cards: collectCards(),
        fields,
        clickables,
      };
    },
    click(needle, near, nth) {
      const items = collectClickables();
      const picked = pick(items, needle, near, nth);
      if (!picked.ok) return picked;
      const el = picked.item._el;
      el.scrollIntoView({ block: "center", inline: "center" });
      const inner = (el.shadowRoot && el.shadowRoot.querySelector("button, a")) || el;
      inner.click();
      const { _el, ...rest } = picked.item;
      return { ok: true, clicked: rest };
    },
    fill(needle, value, near, nth) {
      const items = collectFields();
      const picked = pick(items, needle, near, nth);
      if (!picked.ok) return picked;
      const el = picked.item._el;
      el.scrollIntoView({ block: "center" });
      const input = deepestInput(el) || el;
      try { input.focus(); } catch (e) {}
      try { el.focus && el.focus(); } catch (e) {}
      try {
        if (input.tagName === "INPUT" || input.tagName === "TEXTAREA") {
          const proto = input.tagName === "TEXTAREA"
            ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
          const desc = Object.getOwnPropertyDescriptor(proto, "value");
          if (desc && desc.set) desc.set.call(input, value);
          else input.value = value;
          input.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
          input.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
        }
      } catch (e) {}
      try { el.value = value; } catch (e) {}
      try {
        el.dispatchEvent(
          new CustomEvent("value-changed", {
            detail: { value },
            bubbles: true,
            composed: true,
          })
        );
      } catch (e) {}
      const { _el, ...rest } = picked.item;
      return { ok: true, filled: { ...rest, value } };
    },
    visibleText() {
      return deepText(document.body || document.documentElement).slice(0, 8000);
    },
    accessToken() {
      try {
        const ha = document.querySelector("home-assistant");
        return ha.hass.auth.data.access_token || "";
      } catch (e) {
        return "";
      }
    },
  };
  return true;
})()
"""


def _http_json(path: str) -> Any:
    url = f"http://{CDP_HOST}:{CDP_PORT}{path}"
    with urlopen(url, timeout=3) as resp:
        return json.loads(resp.read().decode())


def devtools_status() -> dict[str, Any]:
    try:
        version = _http_json("/json/version")
        pages = _http_json("/json/list")
        return {"ok": True, "version": version, "pages": pages}
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as err:
        return {"ok": False, "error": str(err)}


def chrome_main_pids() -> list[int]:
    pids: list[int] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            raw = (proc / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        parts = [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]
        if not parts:
            continue
        exe = parts[0]
        if "chrome" not in exe.lower():
            continue
        if any(arg.startswith("--type=") for arg in parts):
            continue
        pids.append(int(proc.name))
    return pids


def _wait_pid_exit(pid: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not Path(f"/proc/{pid}").exists():
            return True
        time.sleep(0.2)
    return not Path(f"/proc/{pid}").exists()


def quit_chrome() -> list[int]:
    """SIGTERM then SIGKILL specific Chrome PIDs. Never pkill -f."""
    pids = chrome_main_pids()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    for pid in pids:
        if not _wait_pid_exit(pid, 8):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            _wait_pid_exit(pid, 3)
    return pids


def prepare_profile() -> None:
    dest = Path(USER_DATA_DIR)
    src = Path.home() / ".config" / "google-chrome"
    if not dest.exists() and src.exists():
        subprocess.run(["cp", "-a", str(src), str(dest)], check=True)
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (dest / name).unlink()
        except FileNotFoundError:
            pass


def launch_chrome(url: str) -> subprocess.Popen[bytes]:
    prepare_profile()
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    cmd = [
        CHROME_BIN,
        "--no-sandbox",
        "--test-type",
        "--disable-dev-shm-usage",
        "--use-gl=angle",
        "--use-angle=swiftshader-webgl",
        "--password-store=basic",
        "--no-first-run",
        "--no-default-browser-check",
        f"--remote-debugging-port={CDP_PORT}",
        "--remote-allow-origins=*",
        f"--user-data-dir={USER_DATA_DIR}",
        "--class=google-chrome",
        "--window-size=1820,1100",
        "--window-position=50,50",
        url,
    ]
    return subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def wait_devtools(timeout: float = 20) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {"ok": False, "error": "timeout"}
    while time.time() < deadline:
        last = devtools_status()
        if last.get("ok"):
            return last
        time.sleep(0.4)
    return last


def ensure_chrome(url: str, restart: bool = False) -> dict[str, Any]:
    status = devtools_status()
    if status.get("ok") and not restart:
        return {"action": "already_running", **status}
    killed = quit_chrome() if (restart or chrome_main_pids()) else []
    time.sleep(0.5)
    proc = launch_chrome(url)
    status = wait_devtools()
    if not status.get("ok"):
        return {
            "action": "launch_failed",
            "pid": proc.pid,
            "killed": killed,
            "hint": (
                "Chrome 136+ ignores --remote-debugging-port on the default "
                "profile. This helper launches with "
                f"--user-data-dir={USER_DATA_DIR}. If a previous Chrome was "
                "still running, ProcessSingleton swallowed the new flags — "
                "quit those PIDs and retry."
            ),
            **status,
        }
    return {"action": "launched", "pid": proc.pid, "killed": killed, **status}


def pick_page(pages: list[dict[str, Any]], url_substr: str | None) -> dict[str, Any]:
    candidates = [p for p in pages if p.get("type") == "page"]
    if url_substr:
        matched = [p for p in candidates if url_substr in (p.get("url") or "")]
        if matched:
            return matched[0]
    ha = [p for p in candidates if "8123" in (p.get("url") or "")]
    if ha:
        return ha[0]
    if not candidates:
        raise RuntimeError("No CDP page targets. Is Chrome open on the HA tab?")
    return candidates[0]


class Cdp:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self.ws = ws
        self._id = 0

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        msg_id = self._id
        payload: dict[str, Any] = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        await self.ws.send_str(json.dumps(payload))
        while True:
            raw = await self.ws.receive()
            if raw.type != aiohttp.WSMsgType.TEXT:
                if raw.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                    raise RuntimeError("CDP WebSocket closed")
                continue
            data = json.loads(raw.data)
            if data.get("id") != msg_id:
                continue
            if "error" in data:
                raise RuntimeError(f"{method}: {data['error']}")
            return data.get("result") or {}

    async def evaluate(self, expression: str) -> Any:
        result = await self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
            },
        )
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            text = details.get("text") or ""
            exc = (details.get("exception") or {}).get("description") or text
            raise RuntimeError(exc)
        remote = result.get("result") or {}
        return remote.get("value")

    async def inject(self) -> None:
        ok = await self.evaluate(HELPER_JS)
        if not ok:
            raise RuntimeError("Failed to inject HA CDP helper")


async def with_page(url_substr: str | None, fn: Any) -> Any:
    status = devtools_status()
    if not status.get("ok"):
        raise RuntimeError(
            "DevTools is down (GET /json/version failed). "
            "Run: python3 scripts/ha_cdp.py ensure"
        )
    page = pick_page(status["pages"], url_substr)
    ws_url = page["webSocketDebuggerUrl"]
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            ws_url, origin=f"http://{CDP_HOST}:{CDP_PORT}"
        ) as ws:
            cdp = Cdp(ws)
            await cdp.call("Page.enable")
            await cdp.call("Runtime.enable")
            await cdp.inject()
            return await fn(cdp, page)


def _print(data: Any, as_json: bool) -> None:
    if as_json or not isinstance(data, dict):
        json.dump(data, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return
    if "error" in data and not data.get("ok", True):
        print(json.dumps(data, indent=2, default=str))
        return
    json.dump(data, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _fail_if_needed(data: Any) -> int:
    if isinstance(data, dict) and data.get("ok") is False:
        return 2
    return 0


async def cmd_dump(cdp: Cdp, _page: dict[str, Any]) -> dict[str, Any]:
    await cdp.inject()
    return await cdp.evaluate("window.__haCdp.dump()")


async def cmd_click(
    cdp: Cdp, _page: dict[str, Any], text: str, near: str | None, nth: int | None
) -> dict[str, Any]:
    await cdp.inject()
    near_js = json.dumps(near)
    nth_js = "null" if nth is None else str(nth)
    return await cdp.evaluate(
        f"window.__haCdp.click({json.dumps(text)}, {near_js}, {nth_js})"
    )


async def cmd_fill(
    cdp: Cdp,
    _page: dict[str, Any],
    field: str,
    value: str,
    near: str | None,
    nth: int | None,
) -> dict[str, Any]:
    await cdp.inject()
    near_js = json.dumps(near)
    nth_js = "null" if nth is None else str(nth)
    return await cdp.evaluate(
        f"window.__haCdp.fill({json.dumps(field)}, {json.dumps(value)}, {near_js}, {nth_js})"
    )


async def cmd_eval(cdp: Cdp, _page: dict[str, Any], expression: str) -> Any:
    return await cdp.evaluate(expression)


async def cmd_navigate(cdp: Cdp, _page: dict[str, Any], url: str) -> dict[str, Any]:
    result = await cdp.call("Page.navigate", {"url": url})
    try:
        await asyncio.wait_for(wait_load(cdp), timeout=15)
    except TimeoutError:
        pass
    await cdp.inject()
    return {"ok": True, "url": url, "frameId": result.get("frameId")}


async def wait_load(cdp: Cdp) -> None:
    await cdp.call("Page.enable")
    deadline = time.time() + 15
    while time.time() < deadline:
        ready = await cdp.evaluate("document.readyState")
        if ready == "complete":
            return
        await asyncio.sleep(0.2)


async def cmd_press(cdp: Cdp, _page: dict[str, Any], key: str) -> dict[str, Any]:
    mapping = {
        "Enter": ("Enter", "Enter", 13),
        "Escape": ("Escape", "Escape", 27),
        "Tab": ("Tab", "Tab", 9),
        "Backspace": ("Backspace", "Backspace", 8),
    }
    name, code, vk = mapping.get(key, (key, key, 0))
    for etype in ("keyDown", "keyUp"):
        params: dict[str, Any] = {"type": etype, "key": name, "code": code}
        if vk:
            params["windowsVirtualKeyCode"] = vk
            params["nativeVirtualKeyCode"] = vk
        await cdp.call("Input.dispatchKeyEvent", params)
    return {"ok": True, "key": key}


async def cmd_screenshot(cdp: Cdp, _page: dict[str, Any], path: str) -> dict[str, Any]:
    result = await cdp.call("Page.captureScreenshot", {"format": "png"})
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(base64.b64decode(result["data"]))
    return {"ok": True, "path": str(dest)}


async def cmd_wait(cdp: Cdp, _page: dict[str, Any], text: str, timeout: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        await cdp.inject()
        last = await cdp.evaluate("window.__haCdp.visibleText()")
        if text.lower() in last.lower():
            return {"ok": True, "found": text}
        dump = await cdp.evaluate("window.__haCdp.dump()")
        blob = json.dumps(dump).lower()
        if text.lower() in blob:
            return {"ok": True, "found": text}
        await asyncio.sleep(0.4)
    return {"ok": False, "error": "timeout", "needle": text, "snippet": last[:500]}


async def cmd_token(cdp: Cdp, _page: dict[str, Any]) -> dict[str, Any]:
    await cdp.inject()
    token = await cdp.evaluate("window.__haCdp.accessToken()")
    if token:
        Path("/tmp/ha_access_token.txt").write_text(token)
    return {"ok": bool(token), "token": token, "path": "/tmp/ha_access_token.txt"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control Home Assistant Chrome via CDP (no pixel clicks)."
    )
    parser.add_argument("--json", action="store_true", help="Always print JSON")
    parser.add_argument("--page", default=None, help="Substring of tab URL")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="GET /json/version and /json/list")
    ensure = sub.add_parser("ensure", help="Restore DevTools if port 9222 is down")
    ensure.add_argument("--restart", action="store_true")
    ensure.add_argument("--url", default=DEFAULT_URL)
    nav = sub.add_parser("navigate")
    nav.add_argument("url")
    sub.add_parser("dump", help="Shadow-DOM dump of dialogs/cards/buttons/fields")
    click = sub.add_parser("click")
    click.add_argument("text")
    click.add_argument("--near", default=None)
    click.add_argument("--nth", type=int, default=None)
    fill = sub.add_parser("fill")
    fill.add_argument("field")
    fill.add_argument("value")
    fill.add_argument("--near", default=None)
    fill.add_argument("--nth", type=int, default=None)
    press = sub.add_parser("press")
    press.add_argument("key")
    ev = sub.add_parser("eval")
    ev.add_argument("expression")
    shot = sub.add_parser("screenshot")
    shot.add_argument("path")
    wait = sub.add_parser("wait")
    wait.add_argument("text")
    wait.add_argument("--timeout", type=float, default=15)
    sub.add_parser("token", help="Read HA access token from the logged-in page")
    return parser


async def async_main(args: argparse.Namespace) -> int:
    as_json = args.json
    if args.cmd == "status":
        data = devtools_status()
        _print(data, as_json)
        return 0 if data.get("ok") else 1
    if args.cmd == "ensure":
        data = ensure_chrome(args.url, restart=args.restart)
        _print(data, True)
        return 0 if data.get("ok") else 1

    async def run(cdp: Cdp, page: dict[str, Any]) -> Any:
        if args.cmd == "dump":
            return await cmd_dump(cdp, page)
        if args.cmd == "click":
            return await cmd_click(cdp, page, args.text, args.near, args.nth)
        if args.cmd == "fill":
            return await cmd_fill(cdp, page, args.field, args.value, args.near, args.nth)
        if args.cmd == "eval":
            return await cmd_eval(cdp, page, args.expression)
        if args.cmd == "navigate":
            return await cmd_navigate(cdp, page, args.url)
        if args.cmd == "press":
            return await cmd_press(cdp, page, args.key)
        if args.cmd == "screenshot":
            return await cmd_screenshot(cdp, page, args.path)
        if args.cmd == "wait":
            return await cmd_wait(cdp, page, args.text, args.timeout)
        if args.cmd == "token":
            return await cmd_token(cdp, page)
        raise RuntimeError(args.cmd)

    data = await with_page(args.page, run)
    _print(data, as_json or args.cmd in {"dump", "eval", "token"})
    return _fail_if_needed(data)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        code = asyncio.run(async_main(args))
    except Exception as err:  # noqa: BLE001 — CLI boundary
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)
    sys.exit(code)


if __name__ == "__main__":
    main()
