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
from datetime import datetime, timedelta, timezone
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
      try {
        if (body === undefined || body === null || body === "") {
          return await hass.callApi(method, path);
        }
        return await hass.callApi(method, path, body);
      } catch (e) {
        const out = { ok: false, error: "api_error" };
        if (e && typeof e === "object") {
          out.status_code = e.status_code;
          out.body = e.body;
          out.message =
            (e.body && e.body.message) || e.message || String(e);
        } else {
          out.message = String(e);
        }
        return out;
      }
    },
    async ws(message) {
      try {
        return await this.hass().callWS(message);
      } catch (e) {
        const out = { ok: false, error: "ws_error" };
        if (e && typeof e === "object") {
          out.code = e.code;
          out.message = e.message || String(e);
        } else {
          out.message = String(e);
        }
        return out;
      }
    },
    state(entityId) {
      const s = this.hass().states[entityId];
      if (!s) return null;
      const a = s.attributes || {};
      return {
        entity_id: s.entity_id,
        state: s.state,
        last_changed: s.last_changed,
        last_updated: s.last_updated,
        attributes: {
          friendly_name: a.friendly_name,
          device_class: a.device_class,
          state_class: a.state_class,
          unit_of_measurement: a.unit_of_measurement,
          entity_category: a.entity_category,
          options: a.options,
          icon: a.icon,
          last_triggered: a.last_triggered,
          message: a.message,
          restored: a.restored,
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
          last_triggered: s.attributes.last_triggered,
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
        result = await self._evaluate_with_retry(expression)
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            text = details.get("text") or ""
            exc = (details.get("exception") or {}).get("description") or text
            raise RuntimeError(exc)
        remote = result.get("result") or {}
        return remote.get("value")

    # A renderer that navigates, or is discarded while an awaited fetch is in
    # flight, answers Runtime.evaluate with a protocol error instead of a
    # result. The request itself already reached Home Assistant, so the lost
    # answer is a reporting failure, not a failed operation. Re-read rather
    # than abandon a campaign half way through.
    _TRANSIENT_EVAL_ERRORS = (
        "Promise was collected",
        "Execution context was destroyed",
        "Inspected target navigated or closed",
        "Cannot find context with specified id",
        "Target closed",
        "Session closed",
    )

    async def _evaluate_with_retry(self, expression: str, attempts: int = 4) -> dict[str, Any]:
        last: RuntimeError | None = None
        for attempt in range(attempts):
            try:
                return await self.call(
                    "Runtime.evaluate",
                    {
                        "expression": expression,
                        "returnByValue": True,
                        "awaitPromise": True,
                        "userGesture": True,
                    },
                )
            except RuntimeError as err:
                if not any(token in str(err) for token in self._TRANSIENT_EVAL_ERRORS):
                    raise
                last = err
                if attempt == attempts - 1:
                    break
                await asyncio.sleep(0.5 * (attempt + 1))
                # The helper lives in the destroyed context; put it back before
                # the retry re-runs an expression that calls into it.
                with contextlib.suppress(RuntimeError):
                    await self.call(
                        "Runtime.evaluate",
                        {
                            "expression": HELPER_JS,
                            "returnByValue": True,
                            "awaitPromise": True,
                        },
                    )
        raise RuntimeError(f"Runtime.evaluate failed after {attempts} attempts: {last}")

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
    await asyncio.sleep(0.4)
    with contextlib.suppress(TimeoutError, RuntimeError):
        await asyncio.wait_for(wait_load(cdp), timeout=15)
    for _ in range(8):
        try:
            await cdp.inject()
            break
        except RuntimeError:
            await asyncio.sleep(0.4)
    return {"ok": True, "url": url, "frameId": result.get("frameId")}


async def wait_load(cdp: Cdp) -> None:
    await cdp.call("Page.enable")
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            ready = await cdp.evaluate("document.readyState")
        except RuntimeError:
            await asyncio.sleep(0.3)
            continue
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
                "disabled_by": entry.get("disabled_by"),
                "reason": entry.get("reason"),
                "pref_disable_new_entities": entry.get("pref_disable_new_entities"),
                "pref_disable_polling": entry.get("pref_disable_polling"),
                "supports_reconfigure": entry.get("supports_reconfigure"),
                "supports_options": entry.get("supports_options"),
                "num_subentries": entry.get("num_subentries"),
                "device_id": dev.get("id"),
                "device_name": dev.get("name"),
                "model": dev.get("model"),
                "sw_version": dev.get("sw_version"),
                "mac": macs[0] if macs else None,
                "unique_id": macs[0] if macs else None,
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
                "name_by_user": dev.get("name_by_user"),
                "area_id": dev.get("area_id"),
                "labels": list(dev.get("labels") or []),
                "model": dev.get("model"),
                "sw_version": dev.get("sw_version"),
                "disabled_by": dev.get("disabled_by"),
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
                "hidden_by": ent.get("hidden_by"),
                "entity_category": ent.get("entity_category"),
                "original_name": ent.get("original_name"),
                "has_entity_name": ent.get("has_entity_name"),
            }
        )
    return out


async def cmd_device_triggers(cdp: Cdp, _page: dict[str, Any], device_id: str) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {"type": "device_automation/trigger/list", "device_id": device_id},
    )


async def cmd_device_conditions(cdp: Cdp, _page: dict[str, Any], device_id: str) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {"type": "device_automation/condition/list", "device_id": device_id},
    )


async def cmd_diagnostics(cdp: Cdp, _page: dict[str, Any], entry_id: str) -> Any:
    return await cmd_api(
        cdp, _page, "GET", f"diagnostics/config_entry/{entry_id}", None
    )


async def cmd_start_user_flow(cdp: Cdp, _page: dict[str, Any]) -> Any:
    return await cmd_api(
        cdp,
        _page,
        "POST",
        "config/config_entries/flow",
        {"handler": "marstek", "show_advanced_options": False},
    )


async def cmd_start_reconfigure(
    cdp: Cdp, _page: dict[str, Any], entry_id: str
) -> Any:
    return await cmd_api(
        cdp,
        _page,
        "POST",
        "config/config_entries/flow",
        {"handler": "marstek", "show_advanced_options": False, "entry_id": entry_id},
    )


async def cmd_start_options(cdp: Cdp, _page: dict[str, Any], entry_id: str) -> Any:
    return await cmd_api(
        cdp,
        _page,
        "POST",
        "config/config_entries/options/flow",
        {"handler": entry_id},
    )


async def cmd_flow_next(
    cdp: Cdp, _page: dict[str, Any], flow_id: str, data: dict[str, Any]
) -> Any:
    return await cmd_api(
        cdp, _page, "POST", f"config/config_entries/flow/{flow_id}", data
    )


async def cmd_options_next(
    cdp: Cdp, _page: dict[str, Any], flow_id: str, data: dict[str, Any]
) -> Any:
    return await cmd_api(
        cdp, _page, "POST", f"config/config_entries/options/flow/{flow_id}", data
    )


async def cmd_upsert_automation(
    cdp: Cdp, _page: dict[str, Any], automation_id: str, config: dict[str, Any]
) -> Any:
    payload = {"id": automation_id, **config}
    payload["id"] = automation_id
    return await cmd_api(
        cdp,
        _page,
        "POST",
        f"config/automation/config/{automation_id}",
        payload,
    )


async def cmd_upsert_script(
    cdp: Cdp, _page: dict[str, Any], script_id: str, config: dict[str, Any]
) -> Any:
    # HA rejects ``id`` inside the script body ("not a valid option at 'id'").
    # The script id lives in the URL only. Automations still accept ``id``.
    payload = dict(config)
    payload.pop("id", None)
    payload["alias"] = config.get("alias") or script_id
    return await cmd_api(
        cdp, _page, "POST", f"config/script/config/{script_id}", payload
    )


async def cmd_notifications(cdp: Cdp, _page: dict[str, Any]) -> Any:
    """HA 2026 persistent notifications are not entity states."""
    return await cmd_ws(cdp, _page, {"type": "persistent_notification/get"})


async def cmd_enable_entity(cdp: Cdp, _page: dict[str, Any], entity_id: str) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {
            "type": "config/entity_registry/update",
            "entity_id": entity_id,
            "disabled_by": None,
        },
    )


async def cmd_disable_entity(cdp: Cdp, _page: dict[str, Any], entity_id: str) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {
            "type": "config/entity_registry/update",
            "entity_id": entity_id,
            "disabled_by": "user",
        },
    )


async def cmd_set_entry_disabled(
    cdp: Cdp, _page: dict[str, Any], entry_id: str, disabled: bool
) -> Any:
    """WS config_entries/disable. disabled_by is only ``user`` or null."""
    return await cmd_ws(
        cdp,
        _page,
        {
            "type": "config_entries/disable",
            "entry_id": entry_id,
            "disabled_by": "user" if disabled else None,
        },
    )


async def cmd_set_device_disabled(
    cdp: Cdp, _page: dict[str, Any], device_id: str, disabled: bool
) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {
            "type": "config/device_registry/update",
            "device_id": device_id,
            "disabled_by": "user" if disabled else None,
        },
    )


def _issues_from_ws(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        issues = result.get("issues")
        if isinstance(issues, list):
            return [i for i in issues if isinstance(i, dict)]
    if isinstance(result, list):
        return [i for i in result if isinstance(i, dict)]
    return []


async def cmd_issues(
    cdp: Cdp, _page: dict[str, Any], domain: str | None
) -> list[dict[str, Any]]:
    result = await cmd_ws(cdp, _page, {"type": "repairs/list_issues"})
    issues = _issues_from_ws(result)
    if domain:
        needle = domain.lower()
        issues = [
            issue
            for issue in issues
            if needle in str(issue.get("domain") or "").lower()
            or needle in str(issue.get("issue_domain") or "").lower()
        ]
    return issues


async def cmd_wait_issue(
    cdp: Cdp,
    _page: dict[str, Any],
    issue_id: str | None,
    domain: str | None,
    timeout: float,
    gone: bool,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: list[dict[str, Any]] = []
    needle = (issue_id or "").lower()
    while time.time() < deadline:
        last = await cmd_issues(cdp, _page, domain)
        matches = last
        if needle:
            matches = [
                issue
                for issue in last
                if needle in str(issue.get("issue_id") or "").lower()
            ]
        present = bool(matches)
        if gone and not present:
            return {"ok": True, "gone": True, "issues": last}
        if not gone and present:
            return {"ok": True, "issues": matches}
        await asyncio.sleep(2)
    return {
        "ok": False,
        "error": "timeout",
        "gone": gone,
        "issue_id": issue_id,
        "last": last,
    }


async def cmd_start_repair(
    cdp: Cdp, _page: dict[str, Any], issue_id: str, handler: str
) -> Any:
    return await cmd_api(
        cdp,
        _page,
        "POST",
        "repairs/issues/fix",
        {"handler": handler, "issue_id": issue_id},
    )


async def cmd_repair_next(
    cdp: Cdp, _page: dict[str, Any], flow_id: str, data: dict[str, Any]
) -> Any:
    return await cmd_api(
        cdp, _page, "POST", f"repairs/issues/fix/{flow_id}", data
    )


async def cmd_abort_repair(cdp: Cdp, _page: dict[str, Any], flow_id: str) -> Any:
    return await cmd_api(
        cdp, _page, "DELETE", f"repairs/issues/fix/{flow_id}", None
    )


def _parse_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off", ""}:
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {value!r}")


def _ws_error(result: Any) -> dict[str, Any] | None:
    if isinstance(result, dict) and result.get("error") == "ws_error":
        return result
    return None


async def cmd_get_entry(cdp: Cdp, _page: dict[str, Any], entry_id: str) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {"type": "config_entries/get_single", "entry_id": entry_id},
    )


async def cmd_update_entry(
    cdp: Cdp,
    _page: dict[str, Any],
    entry_id: str,
    *,
    title: str | None,
    disable_new_entities: bool | None,
    disable_polling: bool | None,
) -> Any:
    message: dict[str, Any] = {
        "type": "config_entries/update",
        "entry_id": entry_id,
    }
    if title is not None:
        message["title"] = title
    if disable_new_entities is not None:
        message["pref_disable_new_entities"] = disable_new_entities
    if disable_polling is not None:
        message["pref_disable_polling"] = disable_polling
    return await cmd_ws(cdp, _page, message)


async def cmd_wait_entry(
    cdp: Cdp,
    _page: dict[str, Any],
    entry_id: str,
    state: str | None,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: Any = None
    while time.time() < deadline:
        last = await cmd_get_entry(cdp, _page, entry_id)
        err = _ws_error(last)
        if err:
            last = err
        else:
            entry = last.get("config_entry") if isinstance(last, dict) else None
            current = (entry or {}).get("state") if isinstance(entry, dict) else None
            if state is None or current == state:
                return {"ok": True, "entry": entry, "raw": last}
        await asyncio.sleep(2)
    return {
        "ok": False,
        "error": "timeout",
        "entry_id": entry_id,
        "want": state,
        "last": last,
    }


async def cmd_ignore_flow(
    cdp: Cdp, _page: dict[str, Any], flow_id: str, title: str
) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {
            "type": "config_entries/ignore_flow",
            "flow_id": flow_id,
            "title": title,
        },
    )


async def cmd_ignore_issue(
    cdp: Cdp,
    _page: dict[str, Any],
    issue_id: str,
    domain: str,
    ignore: bool,
) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {
            "type": "repairs/ignore_issue",
            "domain": domain,
            "issue_id": issue_id,
            "ignore": ignore,
        },
    )


async def cmd_rename_device(
    cdp: Cdp, _page: dict[str, Any], device_id: str, name: str | None
) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {
            "type": "config/device_registry/update",
            "device_id": device_id,
            "name_by_user": name,
        },
    )


async def cmd_set_device_area(
    cdp: Cdp, _page: dict[str, Any], device_id: str, area_id: str | None
) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {
            "type": "config/device_registry/update",
            "device_id": device_id,
            "area_id": area_id,
        },
    )


async def cmd_set_device_labels(
    cdp: Cdp, _page: dict[str, Any], device_id: str, labels: list[str]
) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {
            "type": "config/device_registry/update",
            "device_id": device_id,
            "labels": labels,
        },
    )


async def cmd_areas(cdp: Cdp, _page: dict[str, Any]) -> Any:
    return await cmd_ws(cdp, _page, {"type": "config/area_registry/list"})


async def cmd_create_area(cdp: Cdp, _page: dict[str, Any], name: str) -> Any:
    return await cmd_ws(
        cdp, _page, {"type": "config/area_registry/create", "name": name}
    )


async def cmd_labels(cdp: Cdp, _page: dict[str, Any]) -> Any:
    return await cmd_ws(cdp, _page, {"type": "config/label_registry/list"})


async def cmd_create_label(
    cdp: Cdp, _page: dict[str, Any], name: str, color: str | None
) -> Any:
    message: dict[str, Any] = {"type": "config/label_registry/create", "name": name}
    if color:
        message["color"] = color
    return await cmd_ws(cdp, _page, message)


async def cmd_hide_entity(
    cdp: Cdp, _page: dict[str, Any], entity_id: str, hidden: bool
) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {
            "type": "config/entity_registry/update",
            "entity_id": entity_id,
            "hidden_by": "user" if hidden else None,
        },
    )


async def cmd_expose_entity(
    cdp: Cdp,
    _page: dict[str, Any],
    entity_ids: list[str],
    assistants: list[str],
    should_expose: bool,
) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {
            "type": "homeassistant/expose_entity",
            "assistants": assistants,
            "entity_ids": entity_ids,
            "should_expose": should_expose,
        },
    )


async def cmd_exposed(cdp: Cdp, _page: dict[str, Any], prefix: str | None) -> Any:
    result = await cmd_ws(cdp, _page, {"type": "homeassistant/expose_entity/list"})
    err = _ws_error(result)
    if err:
        return err
    entities = result.get("exposed_entities") if isinstance(result, dict) else result
    if prefix and isinstance(entities, dict):
        needle = prefix.lower()
        entities = {
            key: value
            for key, value in entities.items()
            if needle in key.lower()
        }
        return {"exposed_entities": entities}
    return result


async def cmd_history(
    cdp: Cdp, _page: dict[str, Any], entity_id: str, hours: float
) -> Any:
    start = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat(timespec="seconds")
    path = f"history/period/{start}?filter_entity_id={entity_id}&minimal_response"
    return await cmd_api(cdp, _page, "GET", path, None)


async def cmd_logbook(
    cdp: Cdp, _page: dict[str, Any], entity_id: str, hours: float
) -> Any:
    start = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat(timespec="seconds")
    path = f"logbook/{start}?entity={entity_id}"
    return await cmd_api(cdp, _page, "GET", path, None)


async def cmd_debug_logging(
    cdp: Cdp, _page: dict[str, Any], integration: str, level: str, persistence: str
) -> Any:
    return await cmd_ws(
        cdp,
        _page,
        {
            "type": "logger/integration_log_level",
            "integration": integration,
            "level": level.upper(),
            "persistence": persistence,
        },
    )


async def cmd_log_info(cdp: Cdp, _page: dict[str, Any], domain: str | None) -> Any:
    result = await cmd_ws(cdp, _page, {"type": "logger/log_info"})
    err = _ws_error(result)
    if err:
        return err
    if domain and isinstance(result, list):
        needle = domain.lower()
        return [
            row
            for row in result
            if needle in str(row.get("domain") or "").lower()
        ]
    return result


async def cmd_energy_prefs(cdp: Cdp, _page: dict[str, Any]) -> Any:
    return await cmd_ws(cdp, _page, {"type": "energy/get_prefs"})


async def cmd_energy_validate(cdp: Cdp, _page: dict[str, Any]) -> Any:
    return await cmd_ws(cdp, _page, {"type": "energy/validate"})


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
    dtrig = sub.add_parser("device-triggers", help="WS device_automation/trigger/list")
    dtrig.add_argument("device_id")
    dcond = sub.add_parser(
        "device-conditions", help="WS device_automation/condition/list"
    )
    dcond.add_argument("device_id")
    diag = sub.add_parser("diagnostics", help="GET diagnostics for a config entry")
    diag.add_argument("entry_id")
    sub.add_parser("start-user-flow", help="POST a new Marstek user config flow")
    add_dev = sub.add_parser(
        "add-device", help="User flow → Enter IP/port manually → host/port"
    )
    add_dev.add_argument("host")
    add_dev.add_argument("--port", type=int, default=30000)
    camp = sub.add_parser(
        "campaign",
        help="Extensive live HA campaign against every Docker mock",
    )
    camp.add_argument("--skip-compose", action="store_true")
    camp.add_argument("--skip-remove", action="store_true")
    camp.add_argument("--skip-lifecycle", action="store_true")
    camp.add_argument(
        "--only",
        action="append",
        default=None,
        help="Limit to this mock IP (repeatable)",
    )
    camp.add_argument("--output", default=None, help="Write JSON report path")
    recfg = sub.add_parser("start-reconfigure", help="Start reconfigure flow")
    recfg.add_argument("entry_id")
    opts = sub.add_parser("start-options", help="Start options flow")
    opts.add_argument("entry_id")
    fnext = sub.add_parser("flow-next", help="POST the next config-flow step")
    fnext.add_argument("flow_id")
    fnext.add_argument("data", nargs="?", default="{}")
    onext = sub.add_parser("options-next", help="POST the next options-flow step")
    onext.add_argument("flow_id")
    onext.add_argument("data", nargs="?", default="{}")
    auto = sub.add_parser("upsert-automation", help="Create/update automations.yaml")
    auto.add_argument("automation_id")
    auto.add_argument("config", help="JSON automation body")
    scriptp = sub.add_parser("upsert-script", help="Create/update scripts.yaml")
    scriptp.add_argument("script_id")
    scriptp.add_argument("config", help="JSON script body")
    een = sub.add_parser("enable-entity", help="Clear entity_registry disabled_by")
    een.add_argument("entity_id")
    den = sub.add_parser("disable-entity", help="Set entity_registry disabled_by=user")
    den.add_argument("entity_id")
    dent = sub.add_parser(
        "disable-entry", help="WS config_entries/disable disabled_by=user"
    )
    dent.add_argument("entry_id")
    eent = sub.add_parser(
        "enable-entry", help="WS config_entries/disable disabled_by=null"
    )
    eent.add_argument("entry_id")
    ddev = sub.add_parser(
        "disable-device", help="WS device_registry/update disabled_by=user"
    )
    ddev.add_argument("device_id")
    edev = sub.add_parser(
        "enable-device", help="WS device_registry/update disabled_by=null"
    )
    edev.add_argument("device_id")
    issues = sub.add_parser("issues", help="WS repairs/list_issues")
    issues.add_argument("--domain", default="marstek")
    wait_issue = sub.add_parser("wait-issue", help="Poll repairs until present or gone")
    wait_issue.add_argument("--issue-id", default=None)
    wait_issue.add_argument("--domain", default="marstek")
    wait_issue.add_argument("--timeout", type=float, default=180)
    wait_issue.add_argument(
        "--gone",
        action="store_true",
        help="Wait until matching issues disappear",
    )
    srep = sub.add_parser("start-repair", help="POST /api/repairs/issues/fix")
    srep.add_argument("issue_id")
    srep.add_argument("--handler", default="marstek")
    rnext = sub.add_parser("repair-next", help="POST the next repairs flow step")
    rnext.add_argument("flow_id")
    rnext.add_argument("data", nargs="?", default="{}")
    arep = sub.add_parser("abort-repair", help="DELETE an in-progress repairs flow")
    arep.add_argument("flow_id")
    sub.add_parser(
        "notifications",
        help="WS persistent_notification/get (HA 2026: not entity states)",
    )
    get_e = sub.add_parser(
        "get-entry", help="WS config_entries/get_single (prefs, state, subentries)"
    )
    get_e.add_argument("entry_id")
    upd = sub.add_parser(
        "update-entry",
        help="WS config_entries/update title / pref_disable_*",
    )
    upd.add_argument("entry_id")
    upd.add_argument("--title", default=None)
    upd.add_argument(
        "--disable-new-entities",
        type=_parse_bool,
        default=None,
        metavar="BOOL",
    )
    upd.add_argument(
        "--disable-polling",
        type=_parse_bool,
        default=None,
        metavar="BOOL",
    )
    wait_e = sub.add_parser("wait-entry", help="Poll get_single until state matches")
    wait_e.add_argument("entry_id")
    wait_e.add_argument("--state", default=None)
    wait_e.add_argument("--timeout", type=float, default=180)
    ign_f = sub.add_parser(
        "ignore-flow", help="WS config_entries/ignore_flow (SOURCE_IGNORE)"
    )
    ign_f.add_argument("flow_id")
    ign_f.add_argument("--title", default="Marstek")
    ign_i = sub.add_parser("ignore-issue", help="WS repairs/ignore_issue")
    ign_i.add_argument("issue_id")
    ign_i.add_argument("--domain", default="marstek")
    ign_i.add_argument(
        "--unignore",
        action="store_true",
        help="Set ignore=false (show the issue again)",
    )
    ren = sub.add_parser("rename-device", help="WS device_registry/update name_by_user")
    ren.add_argument("device_id")
    ren.add_argument("name", nargs="?", default=None)
    ren.add_argument(
        "--clear",
        action="store_true",
        help="Clear name_by_user (restore integration name)",
    )
    sarea = sub.add_parser("set-device-area", help="Assign or clear a device area")
    sarea.add_argument("device_id")
    sarea.add_argument("area_id", nargs="?", default="-")
    slbl = sub.add_parser("set-device-labels", help="Replace device labels")
    slbl.add_argument("device_id")
    slbl.add_argument("labels", nargs="*")
    sub.add_parser("areas", help="WS config/area_registry/list")
    carea = sub.add_parser("create-area", help="WS config/area_registry/create")
    carea.add_argument("name")
    sub.add_parser("labels", help="WS config/label_registry/list")
    clbl = sub.add_parser("create-label", help="WS config/label_registry/create")
    clbl.add_argument("name")
    clbl.add_argument("--color", default=None)
    hide = sub.add_parser("hide-entity", help="WS entity_registry/update hidden_by=user")
    hide.add_argument("entity_id")
    unhide = sub.add_parser("unhide-entity", help="Clear entity hidden_by")
    unhide.add_argument("entity_id")
    exp = sub.add_parser("expose-entity", help="WS homeassistant/expose_entity")
    exp.add_argument("entity_id")
    exp.add_argument("--assistant", default="conversation")
    exp.add_argument("--unexpose", action="store_true")
    exposed = sub.add_parser("exposed", help="WS homeassistant/expose_entity/list")
    exposed.add_argument("--prefix", default="venus")
    hist = sub.add_parser("history", help="GET /api/history/period for one entity")
    hist.add_argument("entity_id")
    hist.add_argument("--hours", type=float, default=2)
    logb = sub.add_parser("logbook", help="GET /api/logbook for one entity")
    logb.add_argument("entity_id")
    logb.add_argument("--hours", type=float, default=2)
    dbg = sub.add_parser(
        "debug-logging",
        help="WS logger/integration_log_level (Enable debug logging)",
    )
    dbg.add_argument("--integration", default="marstek")
    dbg.add_argument("--level", default="debug")
    dbg.add_argument("--persistence", default="none", choices=["none", "once", "permanent"])
    linfo = sub.add_parser("log-info", help="WS logger/log_info")
    linfo.add_argument("--domain", default="marstek")
    sub.add_parser("energy-prefs", help="WS energy/get_prefs")
    sub.add_parser("energy-validate", help="WS energy/validate")
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
    if args.cmd == "campaign":
        chrome = ensure_chrome(DEFAULT_URL)
        if not chrome.get("ok"):
            _print({"ok": False, "error": "chrome", "detail": chrome}, True)
            return 1

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
        if args.cmd == "device-triggers":
            return await cmd_device_triggers(cdp, page, args.device_id)
        if args.cmd == "device-conditions":
            return await cmd_device_conditions(cdp, page, args.device_id)
        if args.cmd == "diagnostics":
            return await cmd_diagnostics(cdp, page, args.entry_id)
        if args.cmd == "start-user-flow":
            return await cmd_start_user_flow(cdp, page)
        if args.cmd == "add-device":
            from ha_live_campaign import cmd_add_device

            return await cmd_add_device(cdp, page, args.host, args.port)
        if args.cmd == "campaign":
            from ha_live_campaign import _write_report, run_campaign

            result = await run_campaign(
                cdp,
                page,
                skip_compose=args.skip_compose,
                skip_remove=args.skip_remove,
                skip_lifecycle=args.skip_lifecycle,
                only_hosts=args.only,
            )
            report = _write_report(result, args.output)
            if report:
                result = {**result, "report": report}
                Path(report).write_text(
                    json.dumps(result, indent=2, default=str), encoding="utf-8"
                )
            return result
        if args.cmd == "start-reconfigure":
            return await cmd_start_reconfigure(cdp, page, args.entry_id)
        if args.cmd == "start-options":
            return await cmd_start_options(cdp, page, args.entry_id)
        if args.cmd == "flow-next":
            return await cmd_flow_next(cdp, page, args.flow_id, json.loads(args.data))
        if args.cmd == "options-next":
            return await cmd_options_next(cdp, page, args.flow_id, json.loads(args.data))
        if args.cmd == "upsert-automation":
            return await cmd_upsert_automation(
                cdp, page, args.automation_id, json.loads(args.config)
            )
        if args.cmd == "upsert-script":
            return await cmd_upsert_script(
                cdp, page, args.script_id, json.loads(args.config)
            )
        if args.cmd == "enable-entity":
            return await cmd_enable_entity(cdp, page, args.entity_id)
        if args.cmd == "disable-entity":
            return await cmd_disable_entity(cdp, page, args.entity_id)
        if args.cmd == "disable-entry":
            return await cmd_set_entry_disabled(cdp, page, args.entry_id, True)
        if args.cmd == "enable-entry":
            return await cmd_set_entry_disabled(cdp, page, args.entry_id, False)
        if args.cmd == "disable-device":
            return await cmd_set_device_disabled(cdp, page, args.device_id, True)
        if args.cmd == "enable-device":
            return await cmd_set_device_disabled(cdp, page, args.device_id, False)
        if args.cmd == "issues":
            return await cmd_issues(cdp, page, args.domain or None)
        if args.cmd == "wait-issue":
            return await cmd_wait_issue(
                cdp,
                page,
                args.issue_id,
                args.domain or None,
                args.timeout,
                args.gone,
            )
        if args.cmd == "start-repair":
            return await cmd_start_repair(cdp, page, args.issue_id, args.handler)
        if args.cmd == "repair-next":
            return await cmd_repair_next(
                cdp, page, args.flow_id, json.loads(args.data)
            )
        if args.cmd == "abort-repair":
            return await cmd_abort_repair(cdp, page, args.flow_id)
        if args.cmd == "notifications":
            return await cmd_notifications(cdp, page)
        if args.cmd == "get-entry":
            return await cmd_get_entry(cdp, page, args.entry_id)
        if args.cmd == "update-entry":
            return await cmd_update_entry(
                cdp,
                page,
                args.entry_id,
                title=args.title,
                disable_new_entities=args.disable_new_entities,
                disable_polling=args.disable_polling,
            )
        if args.cmd == "wait-entry":
            return await cmd_wait_entry(
                cdp, page, args.entry_id, args.state, args.timeout
            )
        if args.cmd == "ignore-flow":
            return await cmd_ignore_flow(cdp, page, args.flow_id, args.title)
        if args.cmd == "ignore-issue":
            return await cmd_ignore_issue(
                cdp, page, args.issue_id, args.domain, not args.unignore
            )
        if args.cmd == "rename-device":
            name = None if args.clear else args.name
            if name is None and not args.clear:
                return {"ok": False, "error": "name required unless --clear"}
            return await cmd_rename_device(cdp, page, args.device_id, name)
        if args.cmd == "set-device-area":
            area_id = args.area_id
            if area_id in {"", "-", "null", "none"}:
                area_id = None
            return await cmd_set_device_area(cdp, page, args.device_id, area_id)
        if args.cmd == "set-device-labels":
            return await cmd_set_device_labels(
                cdp, page, args.device_id, list(args.labels)
            )
        if args.cmd == "areas":
            return await cmd_areas(cdp, page)
        if args.cmd == "create-area":
            return await cmd_create_area(cdp, page, args.name)
        if args.cmd == "labels":
            return await cmd_labels(cdp, page)
        if args.cmd == "create-label":
            return await cmd_create_label(cdp, page, args.name, args.color)
        if args.cmd == "hide-entity":
            return await cmd_hide_entity(cdp, page, args.entity_id, True)
        if args.cmd == "unhide-entity":
            return await cmd_hide_entity(cdp, page, args.entity_id, False)
        if args.cmd == "expose-entity":
            return await cmd_expose_entity(
                cdp,
                page,
                [args.entity_id],
                [args.assistant],
                not args.unexpose,
            )
        if args.cmd == "exposed":
            return await cmd_exposed(cdp, page, args.prefix or None)
        if args.cmd == "history":
            return await cmd_history(cdp, page, args.entity_id, args.hours)
        if args.cmd == "logbook":
            return await cmd_logbook(cdp, page, args.entity_id, args.hours)
        if args.cmd == "debug-logging":
            return await cmd_debug_logging(
                cdp, page, args.integration, args.level, args.persistence
            )
        if args.cmd == "log-info":
            return await cmd_log_info(cdp, page, args.domain or None)
        if args.cmd == "energy-prefs":
            return await cmd_energy_prefs(cdp, page)
        if args.cmd == "energy-validate":
            return await cmd_energy_validate(cdp, page)
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
            "device-triggers",
            "device-conditions",
            "diagnostics",
            "start-user-flow",
            "add-device",
            "campaign",
            "start-reconfigure",
            "start-options",
            "flow-next",
            "options-next",
            "upsert-automation",
            "upsert-script",
            "enable-entity",
            "disable-entity",
            "disable-entry",
            "enable-entry",
            "disable-device",
            "enable-device",
            "issues",
            "wait-issue",
            "start-repair",
            "repair-next",
            "abort-repair",
            "notifications",
            "get-entry",
            "update-entry",
            "wait-entry",
            "ignore-flow",
            "ignore-issue",
            "rename-device",
            "set-device-area",
            "set-device-labels",
            "areas",
            "create-area",
            "labels",
            "create-label",
            "hide-entity",
            "unhide-entity",
            "expose-entity",
            "exposed",
            "history",
            "logbook",
            "debug-logging",
            "log-info",
            "energy-prefs",
            "energy-validate",
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
