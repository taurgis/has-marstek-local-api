# Sandbox environments

Two sandboxes run this skill. Everything after bring-up (CDP, `ha_cdp.py`,
mock matrix, config-flow paths) is identical; only the four rows in
[SKILL.md](../SKILL.md#pick-your-sandbox) differ. Detect with:

```bash
docker info >/dev/null 2>&1 && echo "daemon up" || echo "daemon down — start it"
command -v google-chrome || echo "no google-chrome — Playwright Chromium"
[ -n "$DISPLAY" ] && echo "windowed" || echo "headless"
```

## Cursor cloud VM

Docker runs as a service and the user is not root, so every Docker command
takes `sudo`. Google Chrome is installed at `/opt/google/chrome/chrome` with
an X display, so Chrome is windowed and screen recording works.

That VM's `iptables-legacy` FORWARD policy can drop Docker inter-container
traffic. If HA cannot reach `172.28.0.26`:

```bash
sudo iptables-legacy -P FORWARD ACCEPT
sudo iptables-legacy -I FORWARD -i br-+ -j ACCEPT
sudo iptables-legacy -I FORWARD -o br-+ -j ACCEPT
```

Do not run this on the Claude Code sandbox — see below.

## Claude Code remote sandbox

### Start the daemon yourself

The Docker CLI is installed but no daemon runs at boot: there is no
`/var/run/docker.sock` until you launch one. You are already root, so `sudo`
is unnecessary (and may be absent).

```bash
nohup dockerd > /tmp/dockerd.log 2>&1 &
until docker info >/dev/null 2>&1; do sleep 1; done
```

The daemon comes up in about a second with the default `overlayfs` driver and
nftables. `dockerd` logs one warning about deprecated cgroup v1 and two about
missing nftables tables on a cold start; both are harmless.
See [Start the daemon manually](https://docs.docker.com/engine/daemon/start/)
and the [`dockerd` reference](https://docs.docker.com/reference/cli/dockerd/)
for `--iptables=false` / `-s vfs` fallbacks if a future image cannot bring
overlayfs or nftables up.

### Do not touch FORWARD here

Docker 29 programs its own nftables chains and container-to-container UDP
works with the default `FORWARD` policy of `DROP`. Verified by probing all
three mocks from `marstek-ha-dev` with the policy left at `DROP`. Copying the
Cursor VM's `iptables-legacy` rules here changes nothing and only hides a
real failure.

### No Google Chrome — use Playwright's Chromium

There is no `google-chrome` binary. Playwright's bundled Chromium lives under
`PLAYWRIGHT_BROWSERS_PATH` (`/opt/pw-browsers/chromium-<rev>/chrome-linux/chrome`,
Chrome 141 at the time of writing) and speaks the same CDP. `ha_cdp.py`
resolves it automatically: `/opt/google/chrome/chrome`, then `google-chrome` /
`chromium` / `chromium-browser` on `PATH`, then the newest
`chromium-*/chrome-linux/chrome` under `PLAYWRIGHT_BROWSERS_PATH`. Override
with `HA_CHROME_BIN`. `status` and `ensure` echo the resolved `chrome_bin`.

Do not run `playwright install` — the browsers are already there and
`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` is set.

### Headless

There is no X server (`DISPLAY` is unset, no `/tmp/.X11-unix`), so `ha_cdp.py`
adds `--headless=new --disable-gpu`. `screenshot` still works because CDP
captures the page, not the screen. Video recording of a visible window does
not — skip the Recording section, or start `Xvfb :1` yourself and run with
`DISPLAY=:1 HA_CHROME_HEADLESS=0`.

Chrome 136's rule still applies headless: `--remote-debugging-port` is only
honoured alongside a non-default `--user-data-dir`
([Chrome for Developers](https://developer.chrome.com/blog/remote-debugging-port)).
`ha_cdp.py` always passes both, plus `--no-sandbox` (Chrome's sandbox refuses
to start as root) and `--disable-dev-shm-usage`.

### Run the helpers with the repo venv

The system `python3` has no `aiohttp`. Use `.venv/bin/python`.

### Bypass the HTTPS proxy for localhost

Outbound HTTPS goes through an agent proxy, and it swallows requests to
`127.0.0.1:8123` and `127.0.0.1:9222`. Export `NO_PROXY='*' no_proxy='*'` for
every `ha_cdp.py` / `curl` call against HA or CDP. Never unset `HTTPS_PROXY`
globally.

### Log the browser in after REST onboarding

REST onboarding authenticates your shell, not the tab: `ha_cdp.py` commands
then fail with `Home Assistant is not ready on this tab`. Seed the frontend's
`hassTokens` from the tokens the onboarding exchange returned, then reload.

```python
tokens = {
    "access_token": access_token, "token_type": "Bearer",
    "refresh_token": refresh_token, "expires_in": 1800,
    "hassUrl": "http://127.0.0.1:8123", "clientId": "http://127.0.0.1:8123/",
    "expires": int(time.time() * 1000) + 1800 * 1000,
}
# CDP Runtime.evaluate:
#   localStorage.setItem("hassTokens", <json>)  then Page.navigate
```

`ha_cdp.py token` works only after that, because it reads the live page.
