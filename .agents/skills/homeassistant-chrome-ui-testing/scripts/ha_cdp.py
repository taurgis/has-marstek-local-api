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
import contextlib
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
      if (s.display === "none" || s.visibility === "hidden") {
        return false;
      }
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) return true;
      const tag = (el.tagName || "").toLowerCase();
      return tag.includes("dialog");
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
      // 1 = element, 11 = DocumentFragment (open shadow roots)
      if (n.nodeType === 1 || n.nodeType === 11) {
        if (n.tagName && SKIP.has(n.tagName)) return;
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
      const lab = el.shadowRoot && el.shadowRoot.querySelector(
        "label, .label, .mdc-floating-label"
      );
      if (lab) bits.push((lab.textContent || "").trim());
    } catch (e) {}
    return bits.filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
  };

  const contextOf = (el) => {
    let n = el;
    const chunks = [];
    for (let i = 0; i < 12 && n; i++) {
      const tag = (n.tagName || "").toLowerCase();
      if (
        tag.includes("card") ||
        tag.includes("dialog") ||
        tag.includes("flow") ||
        tag.includes("list-item") ||
        tag.includes("config-entry") ||
        tag.includes("integration") ||
        tag.includes("data-table") ||
        tag === "tr" ||
        tag.includes("data-row")
      ) {
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
    if (role === "radio" || role === "option" || role === "menuitem") return true;
    if (tag.includes("button") || tag.includes("list-item") || tag.includes("list-item-button")) {
      return true;
    }
    if (
      tag === "ha-md-list-item" ||
      tag === "mwc-list-item" ||
      tag === "md-list-item" ||
      tag === "ha-md-menu-item" ||
      tag === "md-menu-item" ||
      tag === "ha-dropdown-item" ||
      tag.includes("radio-option") ||
      tag.includes("option") ||
      tag.includes("menu-item") ||
      tag.includes("dropdown-item")
    ) {
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
        tag === "ha-form-string" ||
        tag === "ha-form-integer" ||
        tag === "ha-input" ||
        tag === "wa-input" ||
        tag === "ha-selector-text" ||
        tag === "ha-selector-number" ||
        tag === "ha-combo-box" ||
        tag.includes("textfield") ||
        tag.includes("selector");
      if (!interesting) return;
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
      const isDialog =
        tag === "ha-dialog" ||
        tag === "mwc-dialog" ||
        tag === "wa-dialog" ||
        tag === "dialog-data-entry-flow" ||
        tag.endsWith("-dialog");
      if (!isDialog) return;
      if (!isVisible(el)) return;
      let heading = attr(el, "heading") || el.heading || "";
      try {
        heading = heading || (el.innerText || "").split("\n")[0];
      } catch (e) {}
      out.push({
        tag,
        heading: String(heading || deepText(el).slice(0, 80)).replace(/\s+/g, " ").trim(),
        text: (el.innerText || deepText(el)).slice(0, 500),
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
    const tagRank = (tag) => {
      if (!tag) return 0;
      if (tag.startsWith("ha-form")) return 5;
      if (tag === "ha-dropdown-item" || tag.includes("dropdown-item")) return 5;
      if (tag.includes("radio-option")) return 4;
      if (tag === "ha-button" || tag === "ha-textfield" || tag === "ha-input") return 4;
      if (tag.includes("list-item")) return 3;
      if (tag === "wa-input") return 2;
      if (tag === "button" || tag === "input" || tag === "a") return 1;
      return 2;
    };
    scored.sort((a, b) => b.score - a.score || tagRank(b.it.tag) - tagRank(a.it.tag));
    const exact = scored.filter((s) => s.score === 2);
    const pool = exact.length ? exact : scored;
    if (!pool.length) return { ok: false, error: "not found", needle, near };
    if (nth == null && pool.length > 1) {
      if (tagRank(pool[0].it.tag) > tagRank(pool[1].it.tag)) {
        return { ok: true, item: pool[0].it };
      }
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
      const rootTag = (el.tagName || "").toLowerCase();
      const isHaForm = rootTag.startsWith("ha-form");
      try { input.focus(); } catch (e) {}
      try { if (!isHaForm && el.focus) el.focus(); } catch (e) {}
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
      // Never assign ha-form.value — it replaces the whole form data with a scalar.
      if (!isHaForm) {
        try { el.value = value; } catch (e) {}
      }
      let n = el;
      for (let i = 0; i < 8 && n; i++) {
        const tag = (n.tagName || "").toLowerCase();
        if (tag.startsWith("ha-form")) {
          n = n.parentNode || (n.getRootNode && n.getRootNode().host) || null;
          continue;
        }
        if (tag === "ha-input" || tag === "wa-input" || tag === "ha-textfield") {
          try { n.value = value; } catch (e) {}
          try {
            n.dispatchEvent(
              new CustomEvent("value-changed", {
                detail: { value },
                bubbles: true,
                composed: true,
              })
            );
          } catch (e) {}
        }
        n = n.parentNode || (n.getRootNode && n.getRootNode().host) || null;
      }
      const { _el, ...rest } = picked.item;
      return { ok: true, filled: { ...rest, value } };
    },
    visibleText() {
      return deepText(document.body || document.documentElement).slice(0, 8000);
    },
    hass() {
      const ha = document.querySelector("home-assistant");
      if (!ha || !ha.hass) throw new Error("Home Assistant is not ready on this tab");
      return ha.hass;
    },
    accessToken() {
      try {
        return this.hass().auth.data.access_token || "";
      } catch (e) {
        return "";
      }
    },
    async api(method, path, body) {
      const hass = this.hass();
      if (body === undefined || body === null || body === "") {
        return await hass.callApi(method, path);
      }
      return await hass.callApi(method, path, body);
    },
    async ws(message) {
      return await this.hass().callWS(message);
    },
    state(entityId) {
      const s = this.hass().states[entityId];
      if (!s) return null;
      return {
        entity_id: s.entity_id,
        state: s.state,
        last_changed: s.last_changed,
        last_updated: s.last_updated,
        attributes: {
          friendly_name: s.attributes.friendly_name,
          device_class: s.attributes.device_class,
          options: s.attributes.options,
          icon: s.attributes.icon,
        },
      };
    },
    states(prefix) {
      const p = (prefix || "").toLowerCase();
      return Object.values(this.hass().states)
        .filter((s) => {
          const eid = s.entity_id || "";
          const name = (s.attributes && s.attributes.friendly_name) || "";
          if (!p) {
            return eid.includes("venus") || eid.includes("marstek");
          }
          return eid.toLowerCase().includes(p) || name.toLowerCase().includes(p);
        })
        .map((s) => ({
          entity_id: s.entity_id,
          state: s.state,
          last_updated: s.last_updated,
          friendly_name: s.attributes.friendly_name,
          options: s.attributes.options,
        }));
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
        if "crashpad" in exe.lower():
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
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
            _wait_pid_exit(pid, 3)
    return pids


def prepare_profile() -> None:
    dest = Path(USER_DATA_DIR)
    src = Path.home() / ".config" / "google-chrome"
    if not dest.exists() and src.exists():
        subprocess.run(["cp", "-a", str(src), str(dest)], check=True)
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        (dest / name).unlink(missing_ok=True)


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
    async with (
        aiohttp.ClientSession() as session,
        session.ws_connect(ws_url, origin=f"http://{CDP_HOST}:{CDP_PORT}") as ws,
    ):
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
    result = await cdp.evaluate(
        f"window.__haCdp.fill({json.dumps(field)}, {json.dumps(value)}, {near_js}, {nth_js})"
    )
    if not result.get("ok"):
        return result
    # wa-input ignores some .value writes; type into the focused native input.
    await cdp.call(
        "Input.dispatchKeyEvent",
        {
            "type": "keyDown",
            "key": "a",
            "code": "KeyA",
            "modifiers": 2,
            "windowsVirtualKeyCode": 65,
        },
    )
    await cdp.call(
        "Input.dispatchKeyEvent",
        {
            "type": "keyUp",
            "key": "a",
            "code": "KeyA",
            "modifiers": 2,
            "windowsVirtualKeyCode": 65,
        },
    )
    await cdp.call("Input.insertText", {"text": value})
    return result


async def cmd_eval(cdp: Cdp, _page: dict[str, Any], expression: str) -> Any:
    return await cdp.evaluate(expression)


async def cmd_navigate(cdp: Cdp, _page: dict[str, Any], url: str) -> dict[str, Any]:
    result = await cdp.call("Page.navigate", {"url": url})
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(wait_load(cdp), timeout=15)
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


async def cmd_api(
    cdp: Cdp,
    _page: dict[str, Any],
    method: str,
    path: str,
    body: Any | None,
) -> Any:
    await cdp.inject()
    method_js = json.dumps(method.upper())
    path_js = json.dumps(path.lstrip("/"))
    body_js = "undefined" if body is None else json.dumps(body)
    return await cdp.evaluate(
        f"window.__haCdp.api({method_js}, {path_js}, {body_js})"
    )


async def cmd_ws(
    cdp: Cdp, _page: dict[str, Any], message: dict[str, Any]
) -> Any:
    await cdp.inject()
    return await cdp.evaluate(f"window.__haCdp.ws({json.dumps(message)})")


async def cmd_states(
    cdp: Cdp, _page: dict[str, Any], prefix: str | None, entity: str | None
) -> Any:
    await cdp.inject()
    if entity:
        return await cdp.evaluate(f"window.__haCdp.state({json.dumps(entity)})")
    return await cdp.evaluate(f"window.__haCdp.states({json.dumps(prefix or '')})")


async def cmd_wait_state(
    cdp: Cdp,
    _page: dict[str, Any],
    entity: str,
    timeout: float,
    equals: str | None,
    changed: bool,
) -> dict[str, Any]:
    await cdp.inject()
    first = await cdp.evaluate(f"window.__haCdp.state({json.dumps(entity)})")
    if first is None:
        return {"ok": False, "error": "unknown entity", "entity_id": entity}
    deadline = time.time() + timeout
    last = first
    while time.time() < deadline:
        last = await cdp.evaluate(f"window.__haCdp.state({json.dumps(entity)})")
        if last is None:
            return {"ok": False, "error": "entity disappeared", "entity_id": entity}
        if equals is not None and str(last.get("state")) == equals:
            return {"ok": True, "entity_id": entity, "state": last, "from": first}
        if changed and (
            last.get("state") != first.get("state")
            or last.get("last_updated") != first.get("last_updated")
        ):
            return {"ok": True, "entity_id": entity, "from": first, "to": last}
        await asyncio.sleep(1)
        await cdp.inject()
    return {
        "ok": False,
        "error": "timeout",
        "entity_id": entity,
        "from": first,
        "last": last,
    }


async def cmd_service(
    cdp: Cdp,
    _page: dict[str, Any],
    domain: str,
    service: str,
    data: dict[str, Any] | None,
) -> Any:
    path = f"services/{domain}/{service}"
    return await cmd_api(cdp, _page, "POST", path, data or {})


async def cmd_entries(cdp: Cdp, _page: dict[str, Any], domain: str) -> Any:
    entries = await cmd_api(cdp, _page, "GET", "config/config_entries/entry", None)
    if domain:
        entries = [e for e in entries if e.get("domain") == domain]
    devices = await cmd_devices(cdp, _page, domain or "marstek")
    by_entry: dict[str, dict[str, Any]] = {}
    for dev in devices:
        for eid in dev.get("config_entries") or []:
            by_entry[str(eid)] = dev
    slim = []
    for entry in entries:
        entry_id = str(entry.get("entry_id") or "")
        dev = by_entry.get(entry_id) or {}
        macs = [
            ident[1]
            for ident in (dev.get("identifiers") or [])
            if ident and len(ident) > 1
        ]
        slim.append(
            {
                "entry_id": entry_id,
                "domain": entry.get("domain"),
                "title": entry.get("title"),
                "state": entry.get("state"),
                "source": entry.get("source"),
                "device_id": dev.get("id"),
                "device_name": dev.get("name"),
                "model": dev.get("model"),
                "sw_version": dev.get("sw_version"),
                "mac": macs[0] if macs else None,
            }
        )
    return slim


async def cmd_devices(cdp: Cdp, _page: dict[str, Any], integration: str) -> Any:
    devices = await cmd_ws(cdp, _page, {"type": "config/device_registry/list"})
    out = []
    for dev in devices:
        if dev.get("parent_device_id"):
            continue
        idents = dev.get("identifiers") or []
        if integration and not any(
            ident and ident[0] == integration for ident in idents
        ):
            continue
        entries = list(dev.get("config_entries") or [])
        entry_id = dev.get("config_entry_id")
        if entry_id and str(entry_id) not in {str(e) for e in entries}:
            entries.append(entry_id)
        out.append(
            {
                "id": dev.get("id"),
                "name": dev.get("name_by_user") or dev.get("name"),
                "identifiers": idents,
                "config_entries": entries,
                "config_entry_id": entry_id,
                "model": dev.get("model"),
                "sw_version": dev.get("sw_version"),
            }
        )
    return out


async def cmd_flows(
    cdp: Cdp, _page: dict[str, Any], handler: str | None
) -> list[dict[str, Any]]:
    flows = await cmd_ws(cdp, _page, {"type": "config_entries/flow/progress"})
    out: list[dict[str, Any]] = []
    for flow in flows or []:
        if handler and flow.get("handler") != handler:
            continue
        ctx = flow.get("context") or {}
        out.append(
            {
                "flow_id": flow.get("flow_id"),
                "handler": flow.get("handler"),
                "step_id": flow.get("step_id"),
                "source": ctx.get("source"),
                "unique_id": ctx.get("unique_id"),
            }
        )
    return out


async def cmd_wait_flow(
    cdp: Cdp,
    _page: dict[str, Any],
    unique_id: str,
    handler: str | None,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    needle = unique_id.lower()
    last: list[dict[str, Any]] = []
    while time.time() < deadline:
        last = await cmd_flows(cdp, _page, handler)
        matches = [
            flow
            for flow in last
            if needle in str(flow.get("unique_id") or "").lower()
        ]
        if matches:
            return {"ok": True, "flows": matches}
        await asyncio.sleep(2)
    return {"ok": False, "error": "timeout", "unique_id": unique_id, "last": last}


async def cmd_abort_flow(cdp: Cdp, _page: dict[str, Any], flow_id: str) -> Any:
    return await cmd_api(
        cdp, _page, "DELETE", f"config/config_entries/flow/{flow_id}", None
    )


async def cmd_reload_entry(cdp: Cdp, _page: dict[str, Any], entry_id: str) -> Any:
    return await cmd_api(
        cdp,
        _page,
        "POST",
        f"config/config_entries/entry/{entry_id}/reload",
        None,
    )


async def cmd_device_actions(cdp: Cdp, _page: dict[str, Any], device_id: str) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {"type": "device_automation/action/list", "device_id": device_id},
    )


async def cmd_run_script(cdp: Cdp, _page: dict[str, Any], sequence: Any) -> Any:
    if isinstance(sequence, dict):
        sequence = [sequence]
    if not isinstance(sequence, list):
        return {"ok": False, "error": "sequence must be a list or action dict"}
    return await cmd_ws(cdp, _page, {"type": "execute_script", "sequence": sequence})


async def cmd_fire_event(
    cdp: Cdp, _page: dict[str, Any], event_type: str, data: dict[str, Any] | None
) -> Any:
    return await cmd_api(cdp, _page, "POST", f"events/{event_type}", data or {})


async def cmd_entities(
    cdp: Cdp,
    _page: dict[str, Any],
    device_id: str | None,
    prefix: str | None,
) -> list[dict[str, Any]]:
    ents = await cmd_ws(cdp, _page, {"type": "config/entity_registry/list"})
    prefix_l = (prefix or "").lower()
    out: list[dict[str, Any]] = []
    for ent in ents or []:
        if device_id and ent.get("device_id") != device_id:
            continue
        entity_id = str(ent.get("entity_id") or "")
        unique_id = str(ent.get("unique_id") or "")
        platform = str(ent.get("platform") or "")
        if prefix_l and prefix_l not in entity_id.lower() and prefix_l not in unique_id.lower():
            continue
        if (
            not device_id
            and not prefix_l
            and platform != "marstek"
            and "venus" not in entity_id.lower()
        ):
            continue
        out.append(
            {
                "entity_id": entity_id,
                "unique_id": unique_id,
                "device_id": ent.get("device_id"),
                "platform": platform,
                "disabled_by": ent.get("disabled_by"),
            }
        )
    return out


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
    api = sub.add_parser("api", help="hass.callApi METHOD path [json-body]")
    api.add_argument("method")
    api.add_argument("path")
    api.add_argument("body", nargs="?", default=None)
    wsc = sub.add_parser("ws", help="hass.callWS JSON message")
    wsc.add_argument("message")
    entries = sub.add_parser("entries", help="Config entries (default domain=marstek)")
    entries.add_argument("--domain", default="marstek")
    devices = sub.add_parser("devices", help="Device registry (default integration=marstek)")
    devices.add_argument("--integration", default="marstek")
    states = sub.add_parser("states", help="Entity states")
    states.add_argument("--prefix", default="")
    states.add_argument("--entity", default=None)
    wstate = sub.add_parser("wait-state", help="Wait for an entity state change")
    wstate.add_argument("entity")
    wstate.add_argument("--timeout", type=float, default=90)
    wstate.add_argument("--equals", default=None)
    wstate.add_argument("--changed", action="store_true")
    svc = sub.add_parser("service", help="POST /api/services/<domain>/<service>")
    svc.add_argument("domain")
    svc.add_argument("service")
    svc.add_argument("--data", default="{}")
    delete = sub.add_parser("delete-entry", help="DELETE a config entry by entry_id")
    delete.add_argument("entry_id")
    reload_e = sub.add_parser("reload-entry", help="POST reload a config entry")
    reload_e.add_argument("entry_id")
    flows = sub.add_parser("flows", help="WS config_entries/flow/progress")
    flows.add_argument("--handler", default="marstek")
    wait_flow = sub.add_parser("wait-flow", help="Wait for a discovery confirm flow")
    wait_flow.add_argument("--unique-id", required=True)
    wait_flow.add_argument("--handler", default="marstek")
    wait_flow.add_argument("--timeout", type=float, default=700)
    abort_flow = sub.add_parser("abort-flow", help="DELETE an in-progress config flow")
    abort_flow.add_argument("flow_id")
    dact = sub.add_parser("device-actions", help="WS device_automation/action/list")
    dact.add_argument("device_id")
    run_script = sub.add_parser("run-script", help="WS execute_script")
    run_script.add_argument("sequence", help="JSON list of actions, or one action dict")
    fire = sub.add_parser("fire-event", help="POST /api/events/<event_type>")
    fire.add_argument("event_type")
    fire.add_argument("--data", default="{}")
    ents = sub.add_parser("entities", help="Entity registry rows")
    ents.add_argument("--device-id", default=None)
    ents.add_argument("--prefix", default="")
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
        if args.cmd == "api":
            body = None if args.body is None else json.loads(args.body)
            return await cmd_api(cdp, page, args.method, args.path, body)
        if args.cmd == "ws":
            return await cmd_ws(cdp, page, json.loads(args.message))
        if args.cmd == "entries":
            return await cmd_entries(cdp, page, args.domain)
        if args.cmd == "devices":
            return await cmd_devices(cdp, page, args.integration)
        if args.cmd == "states":
            return await cmd_states(cdp, page, args.prefix, args.entity)
        if args.cmd == "wait-state":
            if not args.changed and args.equals is None:
                args.changed = True
            return await cmd_wait_state(
                cdp, page, args.entity, args.timeout, args.equals, args.changed
            )
        if args.cmd == "service":
            return await cmd_service(cdp, page, args.domain, args.service, json.loads(args.data))
        if args.cmd == "delete-entry":
            return await cmd_api(
                cdp,
                page,
                "DELETE",
                f"config/config_entries/entry/{args.entry_id}",
                None,
            )
        if args.cmd == "reload-entry":
            return await cmd_reload_entry(cdp, page, args.entry_id)
        if args.cmd == "flows":
            return await cmd_flows(cdp, page, args.handler)
        if args.cmd == "wait-flow":
            return await cmd_wait_flow(
                cdp, page, args.unique_id, args.handler, args.timeout
            )
        if args.cmd == "abort-flow":
            return await cmd_abort_flow(cdp, page, args.flow_id)
        if args.cmd == "device-actions":
            return await cmd_device_actions(cdp, page, args.device_id)
        if args.cmd == "run-script":
            return await cmd_run_script(cdp, page, json.loads(args.sequence))
        if args.cmd == "fire-event":
            return await cmd_fire_event(cdp, page, args.event_type, json.loads(args.data))
        if args.cmd == "entities":
            return await cmd_entities(cdp, page, args.device_id, args.prefix or None)
        raise RuntimeError(args.cmd)

    data = await with_page(args.page, run)
    _print(
        data,
        as_json
        or args.cmd
        in {
            "dump",
            "eval",
            "token",
            "api",
            "ws",
            "entries",
            "devices",
            "states",
            "wait-state",
            "service",
            "delete-entry",
            "reload-entry",
            "flows",
            "wait-flow",
            "abort-flow",
            "device-actions",
            "run-script",
            "fire-event",
            "entities",
        },
    )
    return _fail_if_needed(data)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        code = asyncio.run(async_main(args))
    except Exception as err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)
    sys.exit(code)


if __name__ == "__main__":
    main()
