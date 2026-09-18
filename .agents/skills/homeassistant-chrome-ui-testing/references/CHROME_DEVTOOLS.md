# Chrome DevTools for HA UI automation

Official sources for why CDP disappears and how this skill brings it back. Do not use screenshot pixel coordinates.

## Chrome 136+: debug port ignored on the default profile

[Changes to remote debugging switches to improve security](https://developer.chrome.com/blog/remote-debugging-port) (Will Harris, 17 Mar 2025):

> From Chrome 136 we're making changes to the behavior of `--remote-debugging-port` and `--remote-debugging-pipe`. These switches will no longer be respected if attempting to debug the default Chrome data directory. These switches must now be accompanied by the `--user-data-dir` switch to point to a non-standard directory.

This Cloud VM runs Chrome 148. Launching with only `--remote-debugging-port=9222` against `~/.config/google-chrome` opens **no** socket on 9222. There is no useful error.

Workaround used here: `--user-data-dir=/tmp/chrome-ha-debug`. Copy the existing profile with `cp -a` if you need the HA login cookie, then delete `SingletonLock`, `SingletonCookie`, and `SingletonSocket` before start.

Chrome for Testing keeps the old flag behavior; we do not require it on this VM.

## HTTP discovery endpoints

[Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/): if Chrome is started with a remote-debugging-port, these HTTP endpoints are on that port.

| Endpoint | Use |
|----------|-----|
| `GET /json/version` | Browser metadata + browser-level `webSocketDebuggerUrl`. **This is the health check.** |
| `GET /json/list` (or `/json`) | Page targets, each with a page-level `webSocketDebuggerUrl` |
| `GET /json/protocol` | Full protocol schema |
| `GET /json/new` | Open a tab |

`--remote-debugging-port=0` picks a free port and writes `DevToolsActivePort` in the profile. This skill pins **9222**.

## WebSocket Origin (Chrome 111+)

Chromium commit [`0154caee`](https://chromium.googlesource.com/chromium/src.git/+/0154caeefc74530d5cb57ce71608beb1b77bca39): debugging WebSockets that send an `Origin` header are rejected unless `--remote-allow-origins=<origin>` or `*`.

`aiohttp` `ws_connect(..., origin=...)` sends Origin. Without the flag you get **403** even when `/json/version` works. `ha_cdp.py` launches with `--remote-allow-origins=*`.

Clients that omit Origin are unaffected.

## ProcessSingleton swallows a second launch

[`process_singleton.h`](https://chromium.googlesource.com/chromium/src/+/HEAD/chrome/browser/process_singleton.h): at most one Chrome per user-data-dir.

[`process_singleton_posix.cc`](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/chrome/browser/process_singleton_posix.cc): a second launch sends its command line to the first process and **exits**. That is treated as “open a tab”, not “restart with new flags”.

So: Chrome already running without 9222 → you “relaunch” with debug flags → command returns instantly → 9222 still closed.

Fix: send **SIGTERM/SIGKILL to those PIDs** (never `pkill -f`), then launch once with the debug `user-data-dir`. `ha_cdp.py ensure` does this.

## How to click (CDP)

Protocol reference: [chromedevtools.github.io/devtools-protocol](https://chromedevtools.github.io/devtools-protocol/).

This skill uses `Runtime.evaluate` with `userGesture: true` to `element.click()` after walking **open shadow roots** (Home Assistant Lit). That matches [ChromeDriver’s JavaScript path](https://chromium.googlesource.com/chromium/src/+/main/chrome/test/chromedriver/docs/run_javascript.md).

Do **not** store `x,y` from a screenshot and later `Input.dispatchMouseEvent`. CDP mouse events are viewport pixels; HA dialogs reflow; computerUse screenshots are a different scale than `xdotool`. [Puppeteer locators](https://pptr.dev/guides/page-interactions) are the high-level equivalent: resolve the element from the live DOM, then act.

`Input.dispatchKeyEvent` is used only for `Enter` / `Escape` / `Tab` / `Backspace`.

## `chrome://inspect` toggle (do not use here)

Chrome 136+ can enable debugging of the default profile via `chrome://inspect/#remote-debugging`. That path is not what `/json/version` on 9222 is. Automation in this repo always uses an explicit non-default `--user-data-dir` plus HTTP 9222.
