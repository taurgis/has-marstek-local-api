#!/usr/bin/env python3
"""Extensive live Home Assistant campaign against Docker mock devices.

Extends ``ha_cdp.py``: add / edit / remove / modes / automations / repairs
for every compose mock. Drive HA through the logged-in page (REST/WS via CDP).
Never click screenshot pixels.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ha_cdp  # noqa: E402

HA_URL = os.environ.get("HA_URL_BASE", "http://127.0.0.1:8123")
HA_USER = os.environ.get("HA_USERNAME", "admin")
HA_PASSWORD = os.environ.get("HA_PASSWORD", "marstek-dev")
CLIENT_ID = f"{HA_URL.rstrip('/')}/"
COMPOSE_FILE = "docker-compose.yml"
MANUAL_DEVICE_OPTION = "__manual__"
DEFAULT_UDP_PORT = 30000
# argparse default in tools/mock_device/__main__.py when compose omits --ble-mac.
DEFAULT_MOCK_BLE_MAC = "009b08a5aa39"
HA_CONTAINER = "marstek-ha-dev"
BAT_ENTITY_KEYS = frozenset(
    {
        "bat_temp",
        "bat_capacity",
        "bat_rated_capacity",
        "bat_charg_flag",
        "bat_dischrg_flag",
    }
)


def repo_root() -> Path:
    """Return the repository root that contains ``.devcontainer/docker-compose.yml``."""
    for parent in [SCRIPTS_DIR, *SCRIPTS_DIR.parents]:
        if (parent / ".devcontainer" / COMPOSE_FILE).is_file():
            return parent
    raise RuntimeError("cannot find repo root with .devcontainer/docker-compose.yml")


def _ensure_custom_components_import() -> None:
    root = str(repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


_ensure_custom_components_import()

from custom_components.marstek.firmware_profile import (  # noqa: E402
    DeviceFamily,
    is_unsupported_venus_e2,
    resolve_firmware_profile,
)


def _log(message: str) -> None:
    stamp = datetime.now(UTC).strftime("%H:%M:%S")
    print(f"[campaign {stamp}] {message}", file=sys.stderr, flush=True)


@dataclass(frozen=True)
class ComposeMock:
    """One ``mock-marstek*`` service from docker-compose."""

    service: str
    container: str
    host: str
    port: int
    device: str
    ver: int
    ble_mac: str | None
    unique_port: bool
    expectation: str
    family: str
    supports_pv: bool
    supports_sys: bool
    supports_ups: bool
    supports_em_status: bool
    max_manual_schedule_slot: int


@dataclass
class Check:
    """One campaign assertion."""

    name: str
    ok: bool
    detail: Any = None


def setup_expectation(device: str, ver: int) -> str:
    """Return how config flow should treat this mock."""
    if is_unsupported_venus_e2(device):
        return "reject_unsupported"
    profile = resolve_firmware_profile(device, ver)
    if profile.family is DeviceFamily.UNKNOWN:
        return "add_unknown"
    return "add_supported"


def parse_compose_mocks(compose_text: str) -> list[ComposeMock]:
    """Parse mock services from ``.devcontainer/docker-compose.yml``."""
    pattern = re.compile(
        r"^  (mock-marstek(?:-\d+)?):\n(?P<body>(?:    .*\n|\n)+)",
        re.MULTILINE,
    )
    mocks: list[ComposeMock] = []
    for match in pattern.finditer(compose_text):
        service = match.group(1)
        body = match.group("body")
        container_match = re.search(r"container_name:\s+(\S+)", body)
        ip_match = re.search(r"ipv4_address:\s+(\S+)", body)
        command_match = re.search(r"command:\s+(\[.*?\])", body)
        if not container_match or not ip_match or not command_match:
            continue
        args = ast.literal_eval(command_match.group(1))
        device = "VenusE 3.0"
        ver = 145
        port = DEFAULT_UDP_PORT
        ble_mac: str | None = None
        pending: str | None = None
        for token in args:
            if pending == "device":
                device = str(token)
                pending = None
            elif pending == "ver":
                ver = int(token)
                pending = None
            elif pending == "port":
                port = int(token)
                pending = None
            elif pending == "ble-mac":
                ble_mac = str(token)
                pending = None
            elif token == "--device":
                pending = "device"
            elif token == "--ver":
                pending = "ver"
            elif token == "--port":
                pending = "port"
            elif token == "--ble-mac":
                pending = "ble-mac"
        if ble_mac is None:
            ble_mac = DEFAULT_MOCK_BLE_MAC
        profile = resolve_firmware_profile(device, ver)
        mocks.append(
            ComposeMock(
                service=service,
                container=container_match.group(1),
                host=ip_match.group(1),
                port=port,
                device=device,
                ver=ver,
                ble_mac=ble_mac,
                unique_port=port != DEFAULT_UDP_PORT,
                expectation=setup_expectation(device, ver),
                family=profile.family.value,
                supports_pv=profile.supports_pv,
                supports_sys=bool(
                    profile.supports_sys_dod
                    or profile.supports_sys_ble_advertising
                    or profile.supports_sys_led
                ),
                supports_ups=profile.supports_ups,
                supports_em_status=profile.supports_em_status,
                max_manual_schedule_slot=profile.max_manual_schedule_slot,
            )
        )
    return mocks


def load_compose_mocks() -> list[ComposeMock]:
    """Load mock inventory from the repo compose file."""
    path = repo_root() / ".devcontainer" / COMPOSE_FILE
    return parse_compose_mocks(path.read_text(encoding="utf-8"))


def _json_http(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
    form: dict[str, str] | None = None,
) -> Any:
    headers = {}
    data: bytes | None = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except HTTPError as err:
        payload = err.read().decode()
        try:
            parsed = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            parsed = {"message": payload}
        return {"ok": False, "status_code": err.code, "body": parsed}


def rest_login_tokens() -> dict[str, Any]:
    """Obtain HA tokens via ``/auth/login_flow`` (official authorize API)."""
    started = _json_http(
        "POST",
        f"{HA_URL}/auth/login_flow",
        {
            "client_id": CLIENT_ID,
            "handler": ["homeassistant", None],
            "redirect_uri": f"{HA_URL}/?auth_callback=1",
        },
    )
    if not isinstance(started, dict) or not started.get("flow_id"):
        return {"ok": False, "error": "login_flow_start", "detail": started}
    finished = _json_http(
        "POST",
        f"{HA_URL}/auth/login_flow/{started['flow_id']}",
        {
            "client_id": CLIENT_ID,
            "username": HA_USER,
            "password": HA_PASSWORD,
        },
    )
    if not isinstance(finished, dict) or finished.get("type") != "create_entry":
        return {"ok": False, "error": "login_flow_finish", "detail": finished}
    code = finished.get("result")
    tokens = _json_http(
        "POST",
        f"{HA_URL}/auth/token",
        form={
            "grant_type": "authorization_code",
            "code": str(code),
            "client_id": CLIENT_ID,
        },
    )
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        return {"ok": False, "error": "login_token", "detail": tokens}
    tokens["hassUrl"] = HA_URL.rstrip("/")
    tokens["clientId"] = CLIENT_ID
    tokens["expires"] = int(time.time() * 1000) + int(tokens.get("expires_in") or 1800) * 1000
    return {"ok": True, "tokens": tokens}


def onboard_home_assistant() -> dict[str, Any]:
    """Finish HA onboarding over REST when the config volume is fresh."""
    status = _json_http("GET", f"{HA_URL}/api/onboarding")
    if not isinstance(status, list):
        return {"ok": False, "error": "onboarding_status", "detail": status}
    done = {row["step"]: bool(row.get("done")) for row in status if isinstance(row, dict)}
    if all(done.get(step) for step in ("user", "core_config", "analytics", "integration")):
        return {"ok": True, "already": True}

    token: str | None = None
    if not done.get("user"):
        created = _json_http(
            "POST",
            f"{HA_URL}/api/onboarding/users",
            {
                "client_id": CLIENT_ID,
                "name": "Admin",
                "username": HA_USER,
                "password": HA_PASSWORD,
                "language": "en",
            },
        )
        if not isinstance(created, dict) or not created.get("auth_code"):
            return {"ok": False, "error": "onboarding_users", "detail": created}
        token_resp = _json_http(
            "POST",
            f"{HA_URL}/auth/token",
            form={
                "grant_type": "authorization_code",
                "code": str(created["auth_code"]),
                "client_id": CLIENT_ID,
            },
        )
        if not isinstance(token_resp, dict) or not token_resp.get("access_token"):
            return {"ok": False, "error": "onboarding_token", "detail": token_resp}
        token = str(token_resp["access_token"])

    if token is None:
        return {"ok": False, "error": "onboarding_partial_without_token", "done": done}

    if not done.get("core_config"):
        core = _json_http("POST", f"{HA_URL}/api/onboarding/core_config", {}, token=token)
        if isinstance(core, dict) and core.get("ok") is False:
            return {"ok": False, "error": "onboarding_core_config", "detail": core}

    if not done.get("analytics"):
        analytics = _json_http("POST", f"{HA_URL}/api/onboarding/analytics", {}, token=token)
        if isinstance(analytics, dict) and analytics.get("ok") is False:
            return {"ok": False, "error": "onboarding_analytics", "detail": analytics}

    if not done.get("integration"):
        integration = _json_http(
            "POST",
            f"{HA_URL}/api/onboarding/integration",
            {"client_id": CLIENT_ID, "redirect_uri": f"{HA_URL}/?auth_callback=1"},
            token=token,
        )
        if isinstance(integration, dict) and integration.get("ok") is False:
            return {
                "ok": False,
                "error": "onboarding_integration",
                "detail": integration,
            }

    return {"ok": True, "already": False}


def bring_up_compose() -> dict[str, Any]:
    """Build and start Home Assistant plus every mock from compose."""
    compose = repo_root() / ".devcontainer" / COMPOSE_FILE
    cmd = [
        "sudo",
        "docker",
        "compose",
        "-f",
        str(compose),
        "up",
        "-d",
        "--build",
    ]
    _log("docker compose up -d --build")
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-4000:],
        "stderr": (proc.stderr or "")[-4000:],
    }


def _iptables_forward_accept() -> None:
    for args in (
        ["sudo", "iptables-legacy", "-P", "FORWARD", "ACCEPT"],
        ["sudo", "iptables-legacy", "-I", "FORWARD", "-i", "br-+", "-j", "ACCEPT"],
        ["sudo", "iptables-legacy", "-I", "FORWARD", "-o", "br-+", "-j", "ACCEPT"],
    ):
        subprocess.run(args, check=False, capture_output=True, text=True)


def docker_container(action: str, name: str) -> dict[str, Any]:
    """Start or stop one mock container by name."""
    proc = subprocess.run(
        ["sudo", "docker", action, name],
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "ok": proc.returncode == 0,
        "action": action,
        "container": name,
        "stderr": (proc.stderr or "").strip(),
    }


def ha_container_logs(since: str) -> str:
    """Return Home Assistant container logs since an RFC3339 or relative stamp."""
    proc = subprocess.run(
        ["sudo", "docker", "logs", "--since", since, HA_CONTAINER],
        check=False,
        capture_output=True,
        text=True,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return combined


def marstek_log_lines(log_text: str) -> list[str]:
    """Keep Marstek / Open API lines from a docker log dump."""
    kept: list[str] = []
    for raw in log_text.splitlines():
        if (
            "custom_components.marstek" in raw
            or "pymarstek" in raw
            or "UDP socket bound" in raw
            or "via pooled UDP client" in raw
            or "Querying device info" in raw
            or "Request timeout" in raw
            or "No valid response from device" in raw
            or "Invalid device response" in raw
            or "Start polling device" in raw
            or "Polling paused" in raw
        ):
            kept.append(raw)
    return kept


def analyze_ha_logs(log_text: str) -> dict[str, Any]:
    """Summarize Open API traffic and known wire issues from HA debug logs."""
    send_re = re.compile(r"Send: (\S+):(\d+) \|")
    recv_re = re.compile(r"Recv: (\S+):(\d+) \|")
    method_re = re.compile(r'"method"\s*:\s*"([^"]+)"')
    timeout_re = re.compile(r"Request timeout: (\S+):(\d+)")
    bound_re = re.compile(r"UDP socket bound to (\S+):(\S+)")
    pooled_re = re.compile(
        r"Querying device info from (\S+):(\d+) via pooled UDP client"
    )
    query_re = re.compile(r"Querying device info from (\S+):(\d+)\b")
    no_resp_re = re.compile(r"No valid response from device at (\S+):(\d+)")
    invalid_re = re.compile(r"Invalid device response from (\S+)")
    ts_re = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)")

    methods: dict[str, int] = {}
    send_hosts: dict[str, int] = {}
    recv_hosts: dict[str, int] = {}
    timeouts: list[str] = []
    binds: list[str] = []
    pooled: list[str] = []
    unpooled: list[str] = []
    no_response: list[str] = []
    invalid: list[str] = []
    send_times: dict[str, list[float]] = {}
    paused = 0
    errors = 0
    warnings = 0

    for line in log_text.splitlines():
        if " ERROR " in line and "marstek" in line.lower():
            errors += 1
        if " WARNING " in line and "marstek" in line.lower():
            warnings += 1
        if "Polling paused" in line:
            paused += 1
        send = send_re.search(line)
        if send:
            host = send.group(1)
            send_hosts[host] = send_hosts.get(host, 0) + 1
            method_match = method_re.search(line)
            method = method_match.group(1) if method_match else "unknown"
            methods[method] = methods.get(method, 0) + 1
            ts = ts_re.search(line)
            if ts:
                try:
                    stamp = datetime.fromisoformat(ts.group(1)).timestamp()
                except ValueError:
                    stamp = None
                if stamp is not None:
                    send_times.setdefault(host, []).append(stamp)
        recv = recv_re.search(line)
        if recv:
            recv_hosts[recv.group(1)] = recv_hosts.get(recv.group(1), 0) + 1
        timeout = timeout_re.search(line)
        if timeout:
            timeouts.append(f"{timeout.group(1)}:{timeout.group(2)}")
        bound = bound_re.search(line)
        if bound:
            binds.append(f"{bound.group(1)}:{bound.group(2)}")
        pooled_m = pooled_re.search(line)
        if pooled_m:
            pooled.append(f"{pooled_m.group(1)}:{pooled_m.group(2)}")
        elif "Querying device info from" in line:
            query = query_re.search(line)
            if query:
                unpooled.append(f"{query.group(1)}:{query.group(2)}")
        no_resp = no_resp_re.search(line)
        if no_resp:
            no_response.append(f"{no_resp.group(1)}:{no_resp.group(2)}")
        invalid_m = invalid_re.search(line)
        if invalid_m:
            invalid.append(invalid_m.group(1))

    min_interval: float | None = None
    for stamps in send_times.values():
        ordered = sorted(stamps)
        for prev, nxt in pairwise(ordered):
            gap = nxt - prev
            if min_interval is None or gap < min_interval:
                min_interval = gap

    return {
        "send_count": sum(send_hosts.values()),
        "recv_count": sum(recv_hosts.values()),
        "methods": dict(sorted(methods.items())),
        "send_hosts": dict(sorted(send_hosts.items())),
        "recv_hosts": dict(sorted(recv_hosts.items())),
        "timeouts": timeouts,
        "timeout_count": len(timeouts),
        "socket_binds": binds,
        "getdevice_pooled": pooled,
        "getdevice_unpooled": unpooled,
        "no_response": no_response,
        "invalid_response": invalid,
        "paused_polls": paused,
        "error_lines": errors,
        "warning_lines": warnings,
        "min_send_interval_s": min_interval,
    }


def _flow_errors(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    errors = result.get("errors")
    if isinstance(errors, dict):
        return errors
    body = result.get("body")
    if isinstance(body, dict) and isinstance(body.get("errors"), dict):
        return body["errors"]
    return {}


def _flow_type(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    if result.get("type"):
        return str(result["type"])
    body = result.get("body")
    if isinstance(body, dict) and body.get("type"):
        return str(body["type"])
    return ""


def _flow_id(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    if result.get("flow_id"):
        return str(result["flow_id"])
    body = result.get("body")
    if isinstance(body, dict) and body.get("flow_id"):
        return str(body["flow_id"])
    return None


def _flow_reason(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    if result.get("reason"):
        return str(result["reason"])
    body = result.get("body")
    if isinstance(body, dict) and body.get("reason"):
        return str(body["reason"])
    return None


def _flow_step(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    if result.get("step_id"):
        return str(result["step_id"])
    body = result.get("body")
    if isinstance(body, dict) and body.get("step_id"):
        return str(body["step_id"])
    return None


def entity_by_key(entities: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """Return the registry row whose unique_id ends with ``_{key}``."""
    suffix = f"_{key}"
    for row in entities:
        unique_id = str(row.get("unique_id") or "")
        if unique_id.endswith(suffix):
            return row
    return None


def campaign_mac(value: str | None) -> str | None:
    """Normalize a 6-octet MAC from compose, identifiers, or unique_id."""
    if not value:
        return None
    hex_only = re.sub(r"[^0-9A-Fa-f]", "", str(value))
    if len(hex_only) != 12:
        return None
    return ":".join(hex_only[i : i + 2].lower() for i in range(0, 12, 2))


def unwrap_config_entry(payload: Any) -> dict[str, Any]:
    """Return the config entry dict from HA 2026 ``get_single`` wrapping."""
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("config_entry")
    if isinstance(inner, dict):
        return inner
    if payload.get("entry_id"):
        return payload
    return {}


def mocks_by_mac(mocks: list[ComposeMock]) -> dict[str, ComposeMock]:
    """Index compose mocks by formatted BLE MAC."""
    mapping: dict[str, ComposeMock] = {}
    for mock in mocks:
        mac = campaign_mac(mock.ble_mac)
        if mac:
            mapping[mac] = mock
    return mapping


def resolve_entry_host(
    *,
    row: dict[str, Any],
    entry: dict[str, Any],
    mocks: dict[str, ComposeMock],
    remembered: dict[str, str],
) -> str | None:
    """Map a loaded config entry to a compose mock host.

    HA 2026 ``config_entries/get_single`` wraps ``{config_entry: {...}}`` and
    omits ``data.host``. Bind via remembered add, then BLE-MAC unique_id /
    device identifiers, then ``data.host`` when present.
    """
    entry_id = str(row.get("entry_id") or entry.get("entry_id") or "")
    if entry_id and entry_id in remembered:
        return remembered[entry_id]
    data = entry.get("data")
    if isinstance(data, dict) and data.get("host"):
        return str(data["host"])
    candidates = (
        row.get("mac"),
        row.get("unique_id"),
        entry.get("unique_id"),
        data.get("ble_mac") if isinstance(data, dict) else None,
        data.get("mac") if isinstance(data, dict) else None,
    )
    for raw in candidates:
        mac = campaign_mac(str(raw) if raw else None)
        if mac and mac in mocks:
            return mocks[mac].host
    return None


def diagnostics_has_profile(diag: Any) -> bool:
    """Return whether a diagnostics download includes firmware_profile."""
    if not isinstance(diag, dict) or diag.get("ok") is False:
        return False
    if "firmware_profile" in diag:
        return True
    data = diag.get("data")
    return isinstance(data, dict) and "firmware_profile" in data


def is_numeric_state(state: Any) -> bool:
    """Return whether an HA state string is a finite number."""
    if not isinstance(state, dict):
        return False
    raw = state.get("state")
    if raw in {None, "", "unknown", "unavailable", "none"}:
        return False
    try:
        float(str(raw))
    except (TypeError, ValueError):
        return False
    return True


class Campaign:
    """Run the extensive live matrix against one logged-in HA tab."""

    def __init__(
        self,
        cdp: ha_cdp.Cdp,
        page: dict[str, Any],
        mocks: list[ComposeMock],
        *,
        skip_remove: bool,
        skip_lifecycle: bool,
    ) -> None:
        self.cdp = cdp
        self.page = page
        self.mocks = mocks
        self.skip_remove = skip_remove
        self.skip_lifecycle = skip_lifecycle
        self.checks: list[Check] = []
        self.entry_by_host: dict[str, dict[str, Any]] = {}
        self.remembered_hosts: dict[str, str] = {}
        self.log_since = datetime.now(UTC).isoformat()
        self.log_analysis: dict[str, Any] = {}
        self.log_path: str | None = None

    def record(self, name: str, ok: bool, detail: Any = None) -> Check:
        check = Check(name=name, ok=ok, detail=detail)
        self.checks.append(check)
        status = "ok" if ok else "FAIL"
        _log(f"{status} {name}")
        return check

    async def api(self, method: str, path: str, body: Any | None = None) -> Any:
        return await ha_cdp.cmd_api(self.cdp, self.page, method, path, body)

    async def entries(self) -> list[dict[str, Any]]:
        result = await ha_cdp.cmd_entries(self.cdp, self.page, "marstek")
        return result if isinstance(result, list) else []

    async def get_entry(self, entry_id: str) -> Any:
        return await ha_cdp.cmd_get_entry(self.cdp, self.page, entry_id)

    async def entities_for(self, device_id: str) -> list[dict[str, Any]]:
        return await ha_cdp.cmd_entities(self.cdp, self.page, device_id, None)

    async def state(self, entity_id: str) -> Any:
        return await ha_cdp.cmd_states(self.cdp, self.page, None, entity_id)

    async def service(self, domain: str, service: str, data: dict[str, Any]) -> Any:
        return await ha_cdp.cmd_service(self.cdp, self.page, domain, service, data)

    async def abort_open_flows(self) -> None:
        flows = await ha_cdp.cmd_flows(self.cdp, self.page, "marstek")
        for flow in flows or []:
            flow_id = flow.get("flow_id")
            if flow_id:
                await ha_cdp.cmd_abort_flow(self.cdp, self.page, str(flow_id))

    async def start_user_flow(self) -> Any:
        return await ha_cdp.cmd_api(
            self.cdp,
            self.page,
            "POST",
            "config/config_entries/flow",
            {"handler": "marstek", "show_advanced_options": False},
        )

    async def add_manual(self, host: str, port: int) -> Any:
        started = await self.start_user_flow()
        flow_id = _flow_id(started)
        step = _flow_step(started)
        current = started
        if step == "user" and flow_id:
            current = await ha_cdp.cmd_flow_next(
                self.cdp, self.page, flow_id, {"device": MANUAL_DEVICE_OPTION}
            )
            flow_id = _flow_id(current) or flow_id
            step = _flow_step(current)
        if step == "manual" and flow_id:
            current = await ha_cdp.cmd_flow_next(
                self.cdp,
                self.page,
                flow_id,
                {"host": host, "port": port},
            )
        return current

    async def refresh_entry_map(self) -> None:
        mapping: dict[str, dict[str, Any]] = {}
        by_mac = mocks_by_mac(self.mocks)
        for row in await self.entries():
            entry_id = str(row.get("entry_id") or "")
            if not entry_id or row.get("source") == "ignore":
                continue
            full = await self.get_entry(entry_id)
            entry = unwrap_config_entry(full)
            host = resolve_entry_host(
                row=row,
                entry=entry,
                mocks=by_mac,
                remembered=self.remembered_hosts,
            )
            if not host:
                continue
            unique = campaign_mac(str(row.get("mac") or "")) or campaign_mac(
                str(entry.get("unique_id") or "")
            )
            mapping[host] = {
                **row,
                "host": host,
                "data": entry.get("data") if isinstance(entry.get("data"), dict) else None,
                "unique_id": unique or entry.get("unique_id") or row.get("mac"),
            }
            self.remembered_hosts[entry_id] = host
        self.entry_by_host = mapping

    def remember_entry(self, host: str, entry_id: str) -> None:
        """Record host → entry_id from a successful add before HA list refresh."""
        self.remembered_hosts[entry_id] = host

    async def wait_numeric(self, entity_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: Any = None
        while time.time() < deadline:
            last = await self.state(entity_id)
            if is_numeric_state(last):
                return {"ok": True, "state": last}
            await asyncio.sleep(2)
        return {"ok": False, "last": last, "entity_id": entity_id}

    async def wait_equals(
        self, entity_id: str, expected: str, timeout: float
    ) -> dict[str, Any]:
        return await ha_cdp.cmd_wait_state(
            self.cdp, self.page, entity_id, timeout, expected, False
        )

    async def _safe_navigate(self, url: str) -> None:
        try:
            await ha_cdp.cmd_navigate(self.cdp, self.page, url)
        except RuntimeError:
            await asyncio.sleep(1.5)
            await ha_cdp.cmd_navigate(self.cdp, self.page, url)

    async def login_ui(self) -> dict[str, Any]:
        tokens = rest_login_tokens()
        if not tokens.get("ok"):
            return {"ok": False, "error": "rest_login", "detail": tokens.get("error")}
        payload = json.dumps(tokens["tokens"])
        stored = False
        for _ in range(6):
            try:
                await self.cdp.evaluate(
                    "window.localStorage.setItem('hassTokens', "
                    + json.dumps(payload)
                    + ")"
                )
                stored = True
                break
            except RuntimeError:
                await asyncio.sleep(1)
        if not stored:
            return {"ok": False, "error": "token_store"}
        await self._safe_navigate(f"{HA_URL}/config/integrations/dashboard")
        deadline = time.time() + 45
        last: dict[str, Any] = {"ok": False}
        while time.time() < deadline:
            try:
                last = await ha_cdp.cmd_token(self.cdp, self.page)
            except Exception as err:
                last = {"ok": False, "error": str(err)}
            if last.get("ok"):
                return last
            await asyncio.sleep(2)
        return last

    async def phase_reject(self) -> None:
        for mock in self.mocks:
            if mock.expectation != "reject_unsupported":
                continue
            _log(f"reject {mock.host} {mock.device} {mock.ver}")
            await self.abort_open_flows()
            result = await self.add_manual(mock.host, mock.port)
            errors = _flow_errors(result)
            reason = _flow_reason(result)
            ok = errors.get("base") == "unsupported_device" or reason == "unsupported_device"
            self.record(
                f"reject:{mock.host}",
                ok,
                {"errors": errors, "reason": reason, "type": _flow_type(result)},
            )
            flow_id = _flow_id(result)
            if flow_id:
                await ha_cdp.cmd_abort_flow(self.cdp, self.page, flow_id)

    async def phase_add(self) -> None:
        await self.refresh_entry_map()
        for mock in self.mocks:
            if mock.expectation == "reject_unsupported":
                continue
            if mock.host in self.entry_by_host:
                self.record(f"add:{mock.host}", True, "already_configured")
                continue
            _log(f"add {mock.host}:{mock.port} {mock.device} {mock.ver}")
            await self.abort_open_flows()
            before = {row["entry_id"] for row in await self.entries()}
            result = await self.add_manual(mock.host, mock.port)
            created = _flow_type(result) == "create_entry"
            if mock.expectation == "add_unknown":
                self.record(
                    f"add:{mock.host}",
                    created,
                    {"type": _flow_type(result), "family": mock.family},
                )
            else:
                self.record(
                    f"add:{mock.host}",
                    created,
                    {
                        "type": _flow_type(result),
                        "errors": _flow_errors(result),
                        "reason": _flow_reason(result),
                    },
                )
            if not created:
                flow_id = _flow_id(result)
                if flow_id:
                    await ha_cdp.cmd_abort_flow(self.cdp, self.page, flow_id)
                continue
            deadline = time.time() + 90
            entry_id = None
            while time.time() < deadline and entry_id is None:
                for row in await self.entries():
                    if row.get("entry_id") not in before and row.get("source") != "ignore":
                        entry_id = str(row["entry_id"])
                        break
                if entry_id is None:
                    await asyncio.sleep(2)
            if entry_id:
                self.remember_entry(mock.host, entry_id)
                loaded = await ha_cdp.cmd_wait_entry(
                    self.cdp, self.page, entry_id, "loaded", 90
                )
                self.record(f"loaded:{mock.host}", bool(loaded.get("ok")), loaded)
        await self.refresh_entry_map()

    async def _bound(self, mock: ComposeMock) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        row = self.entry_by_host.get(mock.host)
        if not row or not row.get("device_id"):
            self.record(f"bind:{mock.host}", False, "missing_entry")
            return None
        entities = await self.entities_for(str(row["device_id"]))
        return row, entities

    async def phase_smoke(self) -> None:
        for mock in self.mocks:
            if mock.expectation == "reject_unsupported":
                continue
            bound = await self._bound(mock)
            if bound is None:
                continue
            row, entities = bound
            device_id = str(row["device_id"])
            soc = entity_by_key(entities, "battery_soc")
            mode = entity_by_key(entities, "operating_mode")
            power = entity_by_key(entities, "battery_power")
            if soc is None or mode is None:
                self.record(f"entities:{mock.host}", False, [e.get("unique_id") for e in entities])
                continue
            self.record(f"entities:{mock.host}", True, len(entities))
            numeric = await self.wait_numeric(str(soc["entity_id"]), 90)
            self.record(
                f"soc:{mock.host}",
                bool(numeric.get("ok")),
                numeric.get("last") or numeric.get("state"),
            )
            sync = await self.service(
                "marstek", "request_data_sync", {"device_id": device_id}
            )
            sync_ok = not (isinstance(sync, dict) and sync.get("ok") is False)
            self.record(f"sync:{mock.host}", sync_ok, sync if not sync_ok else None)
            if power:
                changed = await ha_cdp.cmd_wait_state(
                    self.cdp, self.page, str(power["entity_id"]), 90, None, True
                )
                self.record(
                    f"power-updated:{mock.host}",
                    bool(changed.get("ok")),
                    changed.get("error"),
                )
            actions = await ha_cdp.cmd_device_actions(self.cdp, self.page, device_id)
            types = {
                str(item.get("type"))
                for item in (actions or [])
                if isinstance(item, dict)
            }
            self.record(
                f"device-actions:{mock.host}",
                {"charge", "discharge", "stop"}.issubset(types),
                sorted(types),
            )
            diag = await ha_cdp.cmd_diagnostics(self.cdp, self.page, str(row["entry_id"]))
            profile_ok = diagnostics_has_profile(diag)
            self.record(
                f"diagnostics:{mock.host}",
                profile_ok,
                None
                if profile_ok
                else (list(diag)[:8] if isinstance(diag, dict) else diag),
            )
            sys_ent = entity_by_key(entities, "depth_of_discharge")
            self.record(
                f"sys-present:{mock.host}",
                bool(sys_ent) == mock.supports_sys,
                bool(sys_ent),
            )
            em_ent = entity_by_key(entities, "em_total_power")
            if mock.supports_em_status:
                self.record(f"em-present:{mock.host}", em_ent is not None, None)
            else:
                self.record(f"em-absent:{mock.host}", em_ent is None, em_ent)
            pv1 = entity_by_key(entities, "pv1_power")
            self.record(
                f"pv-present:{mock.host}",
                bool(pv1) == mock.supports_pv,
                bool(pv1),
            )
            for key in BAT_ENTITY_KEYS:
                bat = entity_by_key(entities, key)
                if bat and not bat.get("disabled_by"):
                    self.record(f"bat-disabled:{mock.host}:{key}", False, bat)
            select_id = str(mode["entity_id"])
            ai = await self.service(
                "select",
                "select_option",
                {"entity_id": select_id, "option": "ai"},
            )
            ai_ok = not (isinstance(ai, dict) and ai.get("ok") is False)
            waited = await self.wait_equals(select_id, "ai", 90) if ai_ok else {"ok": False}
            self.record(f"mode-ai:{mock.host}", bool(waited.get("ok")), waited.get("error") or ai)
            auto = await self.service(
                "select",
                "select_option",
                {"entity_id": select_id, "option": "auto"},
            )
            auto_ok = not (isinstance(auto, dict) and auto.get("ok") is False)
            waited_auto = (
                await self.wait_equals(select_id, "auto", 90) if auto_ok else {"ok": False}
            )
            self.record(
                f"mode-auto:{mock.host}",
                bool(waited_auto.get("ok")),
                waited_auto.get("error") or auto,
            )
            if mock.supports_ups:
                ups = await self.service(
                    "select",
                    "select_option",
                    {"entity_id": select_id, "option": "ups"},
                )
                ups_ok = not (isinstance(ups, dict) and ups.get("ok") is False)
                waited_ups = (
                    await self.wait_equals(select_id, "ups", 90) if ups_ok else {"ok": False}
                )
                self.record(
                    f"mode-ups:{mock.host}",
                    bool(waited_ups.get("ok")),
                    waited_ups.get("error") or ups,
                )
                await self.service(
                    "select",
                    "select_option",
                    {"entity_id": select_id, "option": "auto"},
                )
            if mock.supports_sys and sys_ent:
                set_dod = await self.service(
                    "number",
                    "set_value",
                    {"entity_id": sys_ent["entity_id"], "value": 80},
                )
                dod_wait = await self.wait_equals(str(sys_ent["entity_id"]), "80.0", 60)
                if not dod_wait.get("ok"):
                    dod_wait = await self.wait_equals(str(sys_ent["entity_id"]), "80", 30)
                self.record(
                    f"sys-dod:{mock.host}",
                    bool(dod_wait.get("ok")),
                    dod_wait.get("error") or set_dod,
                )
                led = entity_by_key(entities, "panel_led")
                ble = entity_by_key(entities, "bluetooth_advertising")
                if led:
                    await self.service(
                        "switch", "turn_off", {"entity_id": led["entity_id"]}
                    )
                    off = await self.wait_equals(str(led["entity_id"]), "off", 60)
                    await self.service(
                        "switch", "turn_on", {"entity_id": led["entity_id"]}
                    )
                    self.record(f"sys-led:{mock.host}", bool(off.get("ok")), off.get("error"))
                if ble:
                    await self.service(
                        "switch", "turn_on", {"entity_id": ble["entity_id"]}
                    )
                    on = await self.wait_equals(str(ble["entity_id"]), "on", 60)
                    self.record(f"sys-ble:{mock.host}", bool(on.get("ok")), on.get("error"))
            if mock.supports_pv and pv1:
                pv_changed = await ha_cdp.cmd_wait_state(
                    self.cdp, self.page, str(pv1["entity_id"]), 90, None, True
                )
                self.record(
                    f"pv-updated:{mock.host}",
                    bool(pv_changed.get("ok")),
                    pv_changed.get("error"),
                )

    async def phase_automations(self) -> None:
        target = next(
            (m for m in self.mocks if m.supports_sys and m.host in self.entry_by_host),
            None,
        )
        if target is None:
            target = next((m for m in self.mocks if m.host in self.entry_by_host), None)
        if target is None:
            self.record("automations", False, "no_device")
            return
        row, entities = await self._bound(target) or (None, None)  # type: ignore[misc]
        if not row or not entities:
            return
        mode = entity_by_key(entities, "operating_mode")
        soc = entity_by_key(entities, "battery_soc")
        dod = entity_by_key(entities, "depth_of_discharge")
        if mode is None or soc is None:
            self.record("automations", False, "missing_entities")
            return
        event_id = "marstek_campaign_mode"
        auto_cfg = {
            "alias": "Marstek campaign AI mode",
            "mode": "single",
            "triggers": [{"trigger": "event", "event_type": event_id}],
            "conditions": [
                {
                    "condition": "state",
                    "entity_id": mode["entity_id"],
                    "state": "auto",
                }
            ],
            "actions": [
                {
                    "action": "select.select_option",
                    "data": {"entity_id": mode["entity_id"], "option": "ai"},
                }
            ],
        }
        upsert = await ha_cdp.cmd_upsert_automation(
            self.cdp, self.page, "marstek_campaign_ai", auto_cfg
        )
        self.record(
            "automation-upsert",
            not (isinstance(upsert, dict) and upsert.get("ok") is False),
            upsert if isinstance(upsert, dict) and upsert.get("ok") is False else None,
        )
        await self.service(
            "select",
            "select_option",
            {"entity_id": mode["entity_id"], "option": "auto"},
        )
        await self.wait_equals(str(mode["entity_id"]), "auto", 60)
        fired = await ha_cdp.cmd_fire_event(self.cdp, self.page, event_id, {})
        self.record(
            "automation-fire",
            not (isinstance(fired, dict) and fired.get("ok") is False),
            fired if isinstance(fired, dict) and fired.get("ok") is False else None,
        )
        waited = await self.wait_equals(str(mode["entity_id"]), "ai", 90)
        self.record("automation-mode-ai", bool(waited.get("ok")), waited.get("error"))
        script = await ha_cdp.cmd_upsert_script(
            self.cdp,
            self.page,
            "marstek_campaign_sync",
            {
                "alias": "Marstek campaign sync",
                "sequence": [
                    {
                        "action": "marstek.request_data_sync",
                        "data": {"device_id": row["device_id"]},
                    }
                ],
            },
        )
        self.record(
            "script-upsert",
            not (isinstance(script, dict) and script.get("ok") is False),
            script if isinstance(script, dict) and script.get("ok") is False else None,
        )
        turned = await self.service(
            "script",
            "turn_on",
            {"entity_id": "script.marstek_campaign_sync"},
        )
        self.record(
            "script-run",
            not (isinstance(turned, dict) and turned.get("ok") is False),
            turned if isinstance(turned, dict) and turned.get("ok") is False else None,
        )
        if dod:
            await self.service(
                "select", "select_option", {"entity_id": mode["entity_id"], "option": "ai"}
            )
            await self.wait_equals(str(mode["entity_id"]), "ai", 60)
            await self.service(
                "number", "set_value", {"entity_id": dod["entity_id"], "value": 90}
            )
            await self.wait_equals(str(dod["entity_id"]), "90", 60)
            num_auto = {
                "alias": "Marstek campaign DOD",
                "mode": "single",
                "triggers": [
                    {
                        "trigger": "numeric_state",
                        "entity_id": dod["entity_id"],
                        "below": 85,
                    }
                ],
                "actions": [
                    {
                        "action": "select.select_option",
                        "data": {"entity_id": mode["entity_id"], "option": "auto"},
                    }
                ],
            }
            await ha_cdp.cmd_upsert_automation(
                self.cdp, self.page, "marstek_campaign_dod", num_auto
            )
            await self.service(
                "number", "set_value", {"entity_id": dod["entity_id"], "value": 70}
            )
            crossed = await self.wait_equals(str(mode["entity_id"]), "auto", 90)
            self.record("automation-numeric-dod", bool(crossed.get("ok")), crossed.get("error"))
        passive = await self.service(
            "marstek",
            "set_passive_mode",
            {"device_id": row["device_id"], "power": -400, "duration": 30},
        )
        self.record(
            "passive-mode",
            not (isinstance(passive, dict) and passive.get("ok") is False),
            passive if isinstance(passive, dict) and passive.get("ok") is False else None,
        )
        schedule = await self.service(
            "marstek",
            "set_manual_schedule",
            {
                "device_id": row["device_id"],
                "schedule_slot": 0,
                "start_time": "07:00",
                "end_time": "08:00",
                "power": 200,
                "days": ["mon"],
                "enable": True,
            },
        )
        self.record(
            "manual-schedule",
            not (isinstance(schedule, dict) and schedule.get("ok") is False),
            schedule if isinstance(schedule, dict) and schedule.get("ok") is False else None,
        )
        cleared = await self.service(
            "marstek",
            "clear_manual_schedules",
            {"device_id": row["device_id"]},
        )
        self.record(
            "clear-schedules",
            not (isinstance(cleared, dict) and cleared.get("ok") is False),
            cleared if isinstance(cleared, dict) and cleared.get("ok") is False else None,
        )
        await self.service(
            "select", "select_option", {"entity_id": mode["entity_id"], "option": "auto"}
        )

    async def _options_payload(self, failure_threshold: int) -> dict[str, Any]:
        return {
            "polling_settings": {
                "poll_interval_fast": 30,
                "poll_interval_medium": 60,
                "poll_interval_slow": 300,
            },
            "network_settings": {
                "parallel_api_requests": False,
                "request_delay": 5.0,
                "request_timeout": 10.0,
                "failure_threshold": failure_threshold,
            },
            "power_settings": {
                "action_charge_power": -1300,
                "action_discharge_power": 800,
                "socket_limit": False,
            },
        }

    async def phase_edit_and_lifecycle(self) -> None:
        if self.skip_lifecycle:
            self.record("lifecycle", True, "skipped")
            return
        sys_mock = next(
            (m for m in self.mocks if m.supports_sys and m.host in self.entry_by_host),
            None,
        )
        same_port = next(
            (
                m
                for m in self.mocks
                if not m.unique_port
                and m.expectation == "add_supported"
                and m.host in self.entry_by_host
            ),
            None,
        )
        unique = next(
            (m for m in self.mocks if m.unique_port and m.host in self.entry_by_host),
            None,
        )
        unique_b = next(
            (
                m
                for m in self.mocks
                if m.unique_port
                and unique is not None
                and m.host != unique.host
                and m.host in self.entry_by_host
            ),
            None,
        )
        confirm = next(
            (
                m
                for m in self.mocks
                if m.unique_port
                and unique is not None
                and unique_b is not None
                and m.host not in {unique.host, unique_b.host}
                and m.host in self.entry_by_host
            ),
            unique_b,
        )
        if sys_mock:
            await self._lifecycle_sys(sys_mock)
        if same_port:
            await self._lifecycle_reconfigure(same_port)
        if unique:
            await self._lifecycle_disable(unique)
            await self._lifecycle_connection_loss(unique, unique_b)
        if confirm and (unique is None or confirm.host != unique.host):
            await self._lifecycle_discovery_confirm(confirm)
        mini = next(
            (
                m
                for m in self.mocks
                if m.device == "Venus E mini" and m.host in self.entry_by_host
            ),
            None,
        )
        if mini:
            await self._lifecycle_e_mini_slot(mini)

    async def _lifecycle_sys(self, mock: ComposeMock) -> None:
        bound = await self._bound(mock)
        if bound is None:
            return
        row, entities = bound
        dod = entity_by_key(entities, "depth_of_discharge")
        if dod:
            await self.service(
                "number", "set_value", {"entity_id": dod["entity_id"], "value": 73}
            )
            await self.wait_equals(str(dod["entity_id"]), "73", 60)
            reload = await ha_cdp.cmd_reload_entry(
                self.cdp, self.page, str(row["entry_id"])
            )
            await ha_cdp.cmd_wait_entry(
                self.cdp, self.page, str(row["entry_id"]), "loaded", 90
            )
            restored = await self.wait_equals(str(dod["entity_id"]), "73", 60)
            self.record(
                "restore-number",
                bool(restored.get("ok")),
                restored.get("error") or reload,
            )
        started = await ha_cdp.cmd_start_options(
            self.cdp, self.page, str(row["entry_id"])
        )
        flow_id = _flow_id(started)
        if flow_id:
            nxt = await ha_cdp.cmd_options_next(
                self.cdp, self.page, flow_id, await self._options_payload(3)
            )
            self.record(
                "options",
                _flow_type(nxt) in {"create_entry", "abort"} or nxt.get("type") == "create_entry",
                {"type": _flow_type(nxt)},
            )
            await ha_cdp.cmd_wait_entry(
                self.cdp, self.page, str(row["entry_id"]), "loaded", 90
            )
        renamed = await ha_cdp.cmd_rename_device(
            self.cdp, self.page, str(row["device_id"]), "Campaign SYS"
        )
        self.record(
            "rename-device",
            not (isinstance(renamed, dict) and renamed.get("error") == "ws_error"),
            renamed if isinstance(renamed, dict) and renamed.get("error") else None,
        )
        await ha_cdp.cmd_rename_device(self.cdp, self.page, str(row["device_id"]), None)
        soc = entity_by_key(entities, "battery_soc")
        if soc:
            hidden = await ha_cdp.cmd_hide_entity(
                self.cdp, self.page, str(soc["entity_id"]), True
            )
            self.record(
                "hide-entity",
                not (isinstance(hidden, dict) and hidden.get("error") == "ws_error"),
                None,
            )
            await ha_cdp.cmd_hide_entity(
                self.cdp, self.page, str(soc["entity_id"]), False
            )
            hist = await ha_cdp.cmd_history(self.cdp, self.page, str(soc["entity_id"]), 2)
            self.record("history", isinstance(hist, (list, dict)), None)
            await ha_cdp.cmd_expose_entity(
                self.cdp, self.page, [str(soc["entity_id"])], ["conversation"], True
            )
            exposed = await ha_cdp.cmd_exposed(self.cdp, self.page, "venus")
            self.record("assist-expose", exposed is not None, None)

    async def _lifecycle_reconfigure(self, mock: ComposeMock) -> None:
        row = self.entry_by_host[mock.host]
        started = await ha_cdp.cmd_start_reconfigure(
            self.cdp, self.page, str(row["entry_id"])
        )
        flow_id = _flow_id(started)
        if not flow_id:
            self.record("reconfigure-start", False, started)
            return
        bad = await ha_cdp.cmd_flow_next(
            self.cdp,
            self.page,
            flow_id,
            {"host": "172.28.0.99", "port": mock.port},
        )
        self.record(
            "reconfigure-cannot-connect",
            _flow_errors(bad).get("base") == "cannot_connect",
            _flow_errors(bad),
        )
        other = next(
            (
                m
                for m in self.mocks
                if m.host != mock.host
                and m.port == mock.port
                and m.expectation == "add_supported"
                and m.host in self.entry_by_host
            ),
            None,
        )
        if other:
            mismatch = await ha_cdp.cmd_flow_next(
                self.cdp,
                self.page,
                flow_id,
                {"host": other.host, "port": other.port},
            )
            self.record(
                "reconfigure-unique-id-mismatch",
                _flow_errors(mismatch).get("base") == "unique_id_mismatch",
                _flow_errors(mismatch),
            )
        ok = await ha_cdp.cmd_flow_next(
            self.cdp,
            self.page,
            flow_id,
            {"host": mock.host, "port": mock.port},
        )
        reason = _flow_reason(ok)
        self.record(
            "reconfigure-success",
            _flow_type(ok) in {"create_entry", "abort"}
            or reason == "reconfigure_successful",
            {"type": _flow_type(ok), "reason": reason},
        )

    async def _lifecycle_disable(self, mock: ComposeMock) -> None:
        bound = await self._bound(mock)
        if bound is None:
            return
        row, entities = bound
        soc = entity_by_key(entities, "battery_soc")
        disabled = await ha_cdp.cmd_set_entry_disabled(
            self.cdp, self.page, str(row["entry_id"]), True
        )
        self.record(
            "disable-entry",
            not (isinstance(disabled, dict) and disabled.get("error") == "ws_error"),
            disabled if isinstance(disabled, dict) and disabled.get("error") else None,
        )
        await asyncio.sleep(2)
        if soc:
            gone = await self.state(str(soc["entity_id"]))
            gone_state = gone.get("state") if isinstance(gone, dict) else None
            self.record(
                "disable-entry-entities-gone",
                gone is None or gone_state in {"unavailable", "unknown", None},
                gone,
            )
        enabled = await ha_cdp.cmd_set_entry_disabled(
            self.cdp, self.page, str(row["entry_id"]), False
        )
        loaded = await ha_cdp.cmd_wait_entry(
            self.cdp, self.page, str(row["entry_id"]), "loaded", 90
        )
        self.record(
            "enable-entry",
            bool(loaded.get("ok")),
            enabled if isinstance(enabled, dict) and enabled.get("error") else loaded.get("error"),
        )
        await self.refresh_entry_map()
        bound = await self._bound(mock)
        if bound is None:
            return
        row, entities = bound
        device_off = await ha_cdp.cmd_set_device_disabled(
            self.cdp, self.page, str(row["device_id"]), True
        )
        self.record(
            "disable-device",
            not (isinstance(device_off, dict) and device_off.get("error") == "ws_error"),
            device_off if isinstance(device_off, dict) and device_off.get("error") else None,
        )
        await ha_cdp.cmd_set_device_disabled(
            self.cdp, self.page, str(row["device_id"]), False
        )
        wifi = entity_by_key(entities, "wifi_rssi")
        if wifi and wifi.get("disabled_by"):
            enabled_ent = await ha_cdp.cmd_enable_entity(
                self.cdp, self.page, str(wifi["entity_id"])
            )
            delay = 30
            if isinstance(enabled_ent, dict):
                delay = int(enabled_ent.get("reload_delay") or 30)
            _log(f"waiting {delay}s after enabling wifi rssi")
            await asyncio.sleep(delay)
            await ha_cdp.cmd_wait_entry(
                self.cdp, self.page, str(row["entry_id"]), "loaded", 90
            )
            rssi = await self.wait_numeric(str(wifi["entity_id"]), 90)
            self.record("wifi-rssi", bool(rssi.get("ok")), rssi.get("last") or rssi.get("state"))
        ct = entity_by_key(entities, "ct_connection")
        if ct and ct.get("disabled_by"):
            await ha_cdp.cmd_enable_entity(self.cdp, self.page, str(ct["entity_id"]))
            await asyncio.sleep(30)
            ct_state = await self.state(str(ct["entity_id"]))
            self.record(
                "ct-connection",
                isinstance(ct_state, dict) and ct_state.get("state") in {"on", "off"},
                ct_state,
            )

    async def _lifecycle_connection_loss(
        self, mock: ComposeMock, other: ComposeMock | None
    ) -> None:
        row = self.entry_by_host.get(mock.host)
        if not row:
            return
        started = await ha_cdp.cmd_start_options(
            self.cdp, self.page, str(row["entry_id"])
        )
        flow_id = _flow_id(started)
        if flow_id:
            await ha_cdp.cmd_options_next(
                self.cdp, self.page, flow_id, await self._options_payload(1)
            )
            await ha_cdp.cmd_wait_entry(
                self.cdp, self.page, str(row["entry_id"]), "loaded", 90
            )
        stopped = docker_container("stop", mock.container)
        self.record("stop-unique-port-mock", bool(stopped.get("ok")), stopped.get("stderr"))
        await self.service("marstek", "request_data_sync", {"device_id": str(row["device_id"])})
        issue_id = f"cannot_connect_{row['entry_id']}"
        present = await ha_cdp.cmd_wait_issue(
            self.cdp, self.page, issue_id, "marstek", 180, False
        )
        self.record("repair-issue", bool(present.get("ok")), present.get("error"))
        repair = await ha_cdp.cmd_start_repair(
            self.cdp, self.page, issue_id, "marstek"
        )
        repair_id = _flow_id(repair)
        if repair_id:
            cannot = await ha_cdp.cmd_repair_next(
                self.cdp,
                self.page,
                repair_id,
                {"host": "172.28.0.99", "port": mock.port},
            )
            self.record(
                "repair-cannot-connect",
                _flow_errors(cannot).get("base") == "cannot_connect",
                _flow_errors(cannot),
            )
            if other:
                mismatch = await ha_cdp.cmd_repair_next(
                    self.cdp,
                    self.page,
                    repair_id,
                    {"host": other.host, "port": other.port},
                )
                self.record(
                    "repair-unique-id-mismatch",
                    _flow_errors(mismatch).get("base") == "unique_id_mismatch",
                    _flow_errors(mismatch),
                )
        docker_container("start", mock.container)
        await asyncio.sleep(2)
        if repair_id:
            success = await ha_cdp.cmd_repair_next(
                self.cdp,
                self.page,
                repair_id,
                {"host": mock.host, "port": mock.port},
            )
            self.record(
                "repair-success",
                _flow_type(success) in {"create_entry", "abort"}
                or _flow_reason(success) in {None, "reconfigure_successful"},
                {"type": _flow_type(success), "reason": _flow_reason(success)},
            )
        gone = await ha_cdp.cmd_wait_issue(
            self.cdp, self.page, issue_id, "marstek", 180, True
        )
        self.record("repair-cleared", bool(gone.get("ok")), gone.get("error"))
        docker_container("stop", mock.container)
        reload = await ha_cdp.cmd_reload_entry(
            self.cdp, self.page, str(row["entry_id"])
        )
        retry = await ha_cdp.cmd_wait_entry(
            self.cdp, self.page, str(row["entry_id"]), "setup_retry", 90
        )
        self.record(
            "setup-retry",
            bool(retry.get("ok")),
            retry.get("error") or reload,
        )
        docker_container("start", mock.container)
        loaded = await ha_cdp.cmd_wait_entry(
            self.cdp, self.page, str(row["entry_id"]), "loaded", 180
        )
        self.record("setup-retry-recovered", bool(loaded.get("ok")), loaded.get("error"))
        started = await ha_cdp.cmd_start_options(
            self.cdp, self.page, str(row["entry_id"])
        )
        flow_id = _flow_id(started)
        if flow_id:
            await ha_cdp.cmd_options_next(
                self.cdp, self.page, flow_id, await self._options_payload(3)
            )

    async def _lifecycle_discovery_confirm(self, mock: ComposeMock) -> None:
        row = self.entry_by_host.get(mock.host)
        if not row:
            return
        unique_id = str(row.get("unique_id") or row.get("mac") or "")
        deleted = await ha_cdp.cmd_api(
            self.cdp,
            self.page,
            "DELETE",
            f"config/config_entries/entry/{row['entry_id']}",
            None,
        )
        self.record(
            f"confirm-delete:{mock.host}",
            not (isinstance(deleted, dict) and deleted.get("ok") is False),
            None,
        )
        waited = await ha_cdp.cmd_wait_flow(
            self.cdp, self.page, unique_id, "marstek", 700
        )
        if waited.get("ok"):
            flows = waited.get("flows") or []
            flow_id = flows[0].get("flow_id") if flows else None
            if flow_id:
                nxt = await ha_cdp.cmd_flow_next(
                    self.cdp,
                    self.page,
                    str(flow_id),
                    {"host": mock.host, "port": mock.port},
                )
                self.record(
                    f"discovery-confirm:{mock.host}",
                    _flow_type(nxt) == "create_entry",
                    {"type": _flow_type(nxt), "errors": _flow_errors(nxt)},
                )
            else:
                self.record(f"discovery-confirm:{mock.host}", False, waited)
        else:
            self.record(f"discovery-confirm:{mock.host}", False, waited.get("error"))
            await self.abort_open_flows()
            result = await self.add_manual(mock.host, mock.port)
            self.record(
                f"discovery-confirm-fallback-manual:{mock.host}",
                _flow_type(result) == "create_entry",
                {"type": _flow_type(result)},
            )
        await self.refresh_entry_map()
        rebound = self.entry_by_host.get(mock.host)
        if rebound:
            loaded = await ha_cdp.cmd_wait_entry(
                self.cdp, self.page, str(rebound["entry_id"]), "loaded", 90
            )
            self.record(
                f"discovery-confirm-loaded:{mock.host}",
                bool(loaded.get("ok")),
                loaded.get("error"),
            )

    async def _lifecycle_e_mini_slot(self, mock: ComposeMock) -> None:
        row = self.entry_by_host[mock.host]
        too_high = await self.service(
            "marstek",
            "set_manual_schedule",
            {
                "device_id": row["device_id"],
                "schedule_slot": mock.max_manual_schedule_slot + 1,
                "start_time": "08:00",
                "end_time": "09:00",
                "power": 100,
                "days": ["mon"],
                "enable": True,
            },
        )
        failed = isinstance(too_high, dict) and too_high.get("ok") is False
        self.record("e-mini-slot-rejected", failed, too_high)

    async def phase_already_configured(self) -> None:
        mock = next(
            (
                m
                for m in self.mocks
                if m.expectation == "add_supported" and m.host in self.entry_by_host
            ),
            None,
        )
        if mock is None:
            return
        await self.abort_open_flows()
        result = await self.add_manual(mock.host, mock.port)
        reason = _flow_reason(result)
        errors = _flow_errors(result)
        self.record(
            "already-configured",
            reason == "already_configured" or errors.get("base") == "already_configured",
            {"reason": reason, "errors": errors},
        )
        flow_id = _flow_id(result)
        if flow_id:
            await ha_cdp.cmd_abort_flow(self.cdp, self.page, flow_id)

    async def phase_remove_readd(self) -> None:
        if self.skip_remove:
            self.record("remove", True, "skipped")
            return
        targets = [
            m
            for m in self.mocks
            if m.expectation != "reject_unsupported" and m.host in self.entry_by_host
        ]
        for mock in targets:
            bound = await self._bound(mock)
            if bound is None:
                continue
            row, entities = bound
            before_ids = sorted(
                str(ent.get("entity_id"))
                for ent in entities
                if ent.get("entity_id")
            )
            before_unique = sorted(
                str(ent.get("unique_id"))
                for ent in entities
                if ent.get("unique_id")
            )
            _log(f"delete {mock.host} {row['entry_id']}")
            deleted = await ha_cdp.cmd_api(
                self.cdp,
                self.page,
                "DELETE",
                f"config/config_entries/entry/{row['entry_id']}",
                None,
            )
            self.record(
                f"delete:{mock.host}",
                not (isinstance(deleted, dict) and deleted.get("ok") is False),
                deleted if isinstance(deleted, dict) and deleted.get("ok") is False else None,
            )
            await self.abort_open_flows()
            result = await self.add_manual(mock.host, mock.port)
            created = _flow_type(result) == "create_entry"
            self.record(f"readd:{mock.host}", created, {"type": _flow_type(result)})
            await asyncio.sleep(2)
            await self.refresh_entry_map()
            rebound = await self._bound(mock)
            if rebound is None:
                continue
            row, entities = rebound
            after_ids = sorted(
                str(ent.get("entity_id"))
                for ent in entities
                if ent.get("entity_id")
            )
            after_unique = sorted(
                str(ent.get("unique_id"))
                for ent in entities
                if ent.get("unique_id")
            )
            grew = any(eid.endswith("_2") or "_2_" in eid for eid in after_ids)
            self.record(
                f"unique-ids:{mock.host}",
                after_unique == before_unique and not grew,
                {"before": before_ids[:3], "after": after_ids[:3], "grew": grew},
            )
            soc = entity_by_key(entities, "battery_soc")
            if soc:
                numeric = await self.wait_numeric(str(soc["entity_id"]), 90)
                self.record(
                    f"readd-soc:{mock.host}",
                    bool(numeric.get("ok")),
                    numeric.get("last") or numeric.get("state"),
                )

    async def screenshot(self, name: str) -> None:
        dest = Path("/opt/cursor/artifacts")
        if not dest.is_dir():
            dest = Path("/tmp")
        path = str(dest / name)
        await ha_cdp.cmd_navigate(
            self.cdp,
            self.page,
            f"{HA_URL}/config/integrations/integration/marstek",
        )
        await asyncio.sleep(2)
        await ha_cdp.cmd_screenshot(self.cdp, self.page, path)

    async def enable_debug_logging(self) -> None:
        result = await ha_cdp.cmd_debug_logging(
            self.cdp, self.page, "marstek", "debug", "none"
        )
        ok = not (isinstance(result, dict) and result.get("error") == "ws_error")
        self.record("debug-logging", ok, result if not ok else None)

    async def collect_ha_logs(self) -> dict[str, Any]:
        raw = ha_container_logs(self.log_since)
        lines = marstek_log_lines(raw)
        analysis = analyze_ha_logs("\n".join(lines) if lines else raw)
        dest = Path("/opt/cursor/artifacts")
        if not dest.is_dir():
            dest = Path("/tmp")
        path = dest / "ha_live_campaign_ha.log"
        path.write_text("\n".join(lines), encoding="utf-8")
        self.log_path = str(path)
        self.log_analysis = analysis
        invalid = analysis.get("invalid_response") or []
        self.record(
            "log-invalid-response",
            not invalid,
            invalid[:8] if invalid else None,
        )
        classic = bool(invalid) and any(
            str(bind).endswith(":30000") for bind in analysis.get("socket_binds") or []
        )
        self.record("udp-reuseport-collision", not classic, analysis.get("socket_binds"))
        self.record(
            "log-api-traffic",
            int(analysis.get("send_count") or 0) > 0,
            {
                "send_count": analysis.get("send_count"),
                "recv_count": analysis.get("recv_count"),
                "methods": analysis.get("methods"),
                "timeout_count": analysis.get("timeout_count"),
                "min_send_interval_s": analysis.get("min_send_interval_s"),
                "getdevice_pooled": len(analysis.get("getdevice_pooled") or []),
                "getdevice_unpooled": analysis.get("getdevice_unpooled"),
            },
        )
        return analysis

    async def disable_debug_logging(self) -> None:
        await ha_cdp.cmd_debug_logging(
            self.cdp, self.page, "marstek", "warning", "none"
        )

    async def run(self) -> dict[str, Any]:
        token = await self.login_ui()
        self.record("login", bool(token.get("ok")), None if token.get("ok") else token)
        if not token.get("ok"):
            return self.summary()
        self.log_since = datetime.now(UTC).isoformat()
        await self.enable_debug_logging()
        await self.phase_reject()
        await self.phase_add()
        await self.phase_smoke()
        await self.phase_automations()
        await self.phase_edit_and_lifecycle()
        await self.phase_already_configured()
        await self.phase_remove_readd()
        try:
            await self.screenshot("ha_live_campaign_integrations.png")
        except Exception as err:
            self.record("screenshot", False, str(err))
        try:
            await self.collect_ha_logs()
        except Exception as err:
            self.record("ha-logs", False, str(err))
        try:
            await self.disable_debug_logging()
        except Exception as err:
            self.record("debug-logging-reset", False, str(err))
        return self.summary()

    def summary(self) -> dict[str, Any]:
        failed = [asdict(c) for c in self.checks if not c.ok]
        return {
            "ok": not failed,
            "passed": sum(1 for c in self.checks if c.ok),
            "failed": len(failed),
            "failures": failed,
            "checks": [asdict(c) for c in self.checks],
            "entries": list(self.entry_by_host),
            "ha_logs": self.log_analysis,
            "ha_log_path": self.log_path,
        }


def wait_ha_api(timeout: float = 120) -> bool:
    """Wait until HA onboarding/status HTTP answers."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(f"{HA_URL}/api/onboarding", timeout=3) as resp:
                if resp.status == 200:
                    return True
        except (URLError, TimeoutError, OSError):
            time.sleep(2)
    return False


async def run_campaign(
    cdp: ha_cdp.Cdp,
    page: dict[str, Any],
    *,
    skip_compose: bool,
    skip_remove: bool,
    skip_lifecycle: bool,
    only_hosts: list[str] | None,
) -> dict[str, Any]:
    """Execute the extensive live campaign on an open HA tab."""
    mocks = load_compose_mocks()
    if only_hosts:
        wanted = {host.lower() for host in only_hosts}
        mocks = [mock for mock in mocks if mock.host.lower() in wanted]
    if not skip_compose:
        _iptables_forward_accept()
        compose = bring_up_compose()
        if not compose.get("ok"):
            return {"ok": False, "error": "compose", "detail": compose}
    if not wait_ha_api():
        return {"ok": False, "error": "home_assistant_unreachable"}
    onboard = onboard_home_assistant()
    if not onboard.get("ok"):
        return {"ok": False, "error": "onboarding", "detail": onboard}
    campaign = Campaign(
        cdp,
        page,
        mocks,
        skip_remove=skip_remove,
        skip_lifecycle=skip_lifecycle,
    )
    result = await campaign.run()
    result["mocks"] = [asdict(mock) for mock in mocks]
    result["onboarding"] = onboard
    return result


async def cmd_add_device(cdp: ha_cdp.Cdp, page: dict[str, Any], host: str, port: int) -> Any:
    """Start a user flow and submit manual host/port."""
    campaign = Campaign(
        cdp, page, [], skip_remove=True, skip_lifecycle=True
    )
    await campaign.abort_open_flows()
    return await campaign.add_manual(host, port)


async def cmd_start_user_flow(cdp: ha_cdp.Cdp, page: dict[str, Any]) -> Any:
    """POST a new Marstek user config flow."""
    return await ha_cdp.cmd_api(
        cdp,
        page,
        "POST",
        "config/config_entries/flow",
        {"handler": "marstek", "show_advanced_options": False},
    )


def _write_report(result: dict[str, Any], path: str | None) -> str | None:
    if not path:
        artifacts = Path("/opt/cursor/artifacts")
        dest = artifacts if artifacts.is_dir() else Path("/tmp")
        path = str(dest / "ha_live_campaign.json")
    Path(path).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return path


async def async_standalone(args: argparse.Namespace) -> dict[str, Any]:
    if not args.skip_ensure:
        ensured = ha_cdp.ensure_chrome(f"{HA_URL}/config/integrations/dashboard")
        if not ensured.get("ok"):
            return {"ok": False, "error": "chrome", "detail": ensured}

    async def _run(cdp: ha_cdp.Cdp, page: dict[str, Any]) -> dict[str, Any]:
        return await run_campaign(
            cdp,
            page,
            skip_compose=args.skip_compose,
            skip_remove=args.skip_remove,
            skip_lifecycle=args.skip_lifecycle,
            only_hosts=args.only,
        )

    return await ha_cdp.with_page(None, _run)


def build_standalone_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-compose", action="store_true")
    parser.add_argument("--skip-remove", action="store_true")
    parser.add_argument("--skip-lifecycle", action="store_true")
    parser.add_argument("--skip-ensure", action="store_true")
    parser.add_argument("--only", action="append", default=None)
    parser.add_argument("--output", default=None)
    return parser


def main() -> None:
    parser = build_standalone_parser()
    args = parser.parse_args()
    try:
        result = asyncio.run(async_standalone(args))
    except Exception as err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)
    report = _write_report(result, args.output)
    if report:
        result = {**result, "report": report}
        Path(report).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    sys.exit(0 if result.get("ok") else 2)


if __name__ == "__main__":
    main()
