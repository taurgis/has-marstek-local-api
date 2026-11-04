#!/usr/bin/env python3
"""Boot the minimum Home Assistant and require the integration to set up.

The version is the ``homeassistant`` field in ``hacs.json``. The script pulls
the official Core image at that tag, starts it with this integration and one
Venus A mock, and exits non-zero unless the config entry reaches ``loaded``
and the battery sensor reports the mock's state of charge.

Home Assistant Container:
https://www.home-assistant.io/installation/linux/#install-home-assistant-container

``hacs.json`` minimum version:
https://www.hacs.xyz/docs/publish/start/
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/home-assistant/home-assistant"
NETWORK = "ha-min-check"
CORE_NAME = "ha-min-check-core"
MOCK_NAME = "ha-min-check-mock"
MOCK_IMAGE = "ha-min-check-mock:local"
MOCK_IP = "172.30.28.20"
SUBNET = "172.30.28.0/24"
HOST_PORT = 18123
MOCK_PORT = 30000
MOCK_SOC = 61
USERNAME = "admin"
PASSWORD = "marstek-dev"
SETUP_TIMEOUT_SECONDS = 120


class CheckError(Exception):
    """The minimum-version boot did not produce a working config entry."""


@dataclass
class Running:
    """URLs and credentials for one booted Core instance."""

    base: str
    version: str
    token: str = ""


def minimum_version(root: Path) -> str:
    """Return the Home Assistant version declared in ``hacs.json``."""
    payload = json.loads((root / "hacs.json").read_text(encoding="utf-8"))
    version = payload.get("homeassistant")
    if not isinstance(version, str) or not version:
        raise CheckError("hacs.json is missing a homeassistant version")
    return version


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command and return its captured output."""
    return subprocess.run(args, check=check, text=True, capture_output=True)


def _log(message: str) -> None:
    """Print a progress line immediately."""
    print(message, flush=True)


def cleanup() -> None:
    """Remove containers and the network left by this script."""
    for name in (CORE_NAME, MOCK_NAME):
        _run(["docker", "rm", "-f", name], check=False)
    _run(["docker", "network", "rm", NETWORK], check=False)


def _dump_logs() -> None:
    """Print the tails of both containers after a failed check."""
    for name in (CORE_NAME, MOCK_NAME):
        result = _run(["docker", "logs", "--tail", "80", name], check=False)
        print(f"--- docker logs {name} ---", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)


def _ignore_bytecode(_directory: str, names: list[str]) -> set[str]:
    """Skip bytecode so the copied integration stays small."""
    return {name for name in names if name == "__pycache__" or name.endswith(".pyc")}


def _config_dir() -> Path:
    """Copy the integration into a fresh Home Assistant config directory."""
    config = Path(tempfile.mkdtemp(prefix="ha-min-check-"))
    destination = config / "custom_components" / "marstek"
    shutil.copytree(ROOT / "custom_components" / "marstek", destination, ignore=_ignore_bytecode)
    return config


def _build_mock() -> None:
    """Build the mock from the files its Dockerfile copies, not the whole repo."""
    with tempfile.TemporaryDirectory(prefix="ha-min-mock-") as raw:
        context = Path(raw)
        shutil.copytree(
            ROOT / "tools" / "mock_device",
            context / "tools" / "mock_device",
            ignore=_ignore_bytecode,
        )
        for relative in (
            "custom_components/marstek/firmware_profile.py",
            "custom_components/marstek/pymarstek/const.py",
            "custom_components/marstek/pymarstek/network.py",
            "custom_components/marstek/pymarstek/validators.py",
        ):
            target = context / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        shutil.copy2(ROOT / "tools" / "mock_device" / "Dockerfile", context / "Dockerfile")
        _log("Building the Venus A mock")
        _run(["docker", "build", "-t", MOCK_IMAGE, str(context)])


def _start_containers(version: str, config: Path) -> None:
    """Start the mock and Core on a private network."""
    _log(f"Pulling {IMAGE}:{version}")
    _run(["docker", "pull", f"{IMAGE}:{version}"])
    _run(["docker", "network", "create", "--subnet", SUBNET, NETWORK])
    _log("Starting the mock")
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            MOCK_NAME,
            "--network",
            NETWORK,
            "--ip",
            MOCK_IP,
            MOCK_IMAGE,
            "python",
            "-m",
            "mock_device",
            "--ip",
            MOCK_IP,
            "--device",
            "VenusA",
            "--ver",
            "151",
            "--soc",
            str(MOCK_SOC),
            "--ble-mac",
            "02deadbeef1e",
            "--no-simulate",
            "--quiet",
        ]
    )
    _log(f"Starting Home Assistant {version}")
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            CORE_NAME,
            "--network",
            NETWORK,
            "-p",
            f"{HOST_PORT}:8123",
            "-v",
            f"{config}:/config",
            f"{IMAGE}:{version}",
        ]
    )


def _http(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    *,
    token: str | None = None,
    form: dict[str, str] | None = None,
    timeout: float = 90,
) -> tuple[int, Any]:
    """Perform one JSON or form request and return status plus a parsed body."""
    headers: dict[str, str] = {}
    data: bytes | None = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        payload = err.read().decode()
        try:
            parsed: Any = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            parsed = payload
        return err.code, parsed


def _wait_for_onboarding(base: str) -> Any:
    """Wait until Core answers onboarding and the API is actually up."""
    deadline = time.monotonic() + SETUP_TIMEOUT_SECONDS
    last: Any = None
    while time.monotonic() < deadline:
        try:
            status, body = _http("GET", f"{base}/api/onboarding", timeout=10)
            api_status, _api = _http("GET", f"{base}/api/", timeout=10)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            last = str(err)
            time.sleep(2)
            continue
        if api_status in {200, 401} and status == 200 and isinstance(body, list):
            return body
        last = {"onboarding": status, "api": api_status}
        time.sleep(2)
    raise CheckError(f"Home Assistant did not finish booting: {last}")


def _exchange(base: str, code: str) -> str:
    """Turn an authorization code into an access token."""
    status, tokens = _http(
        "POST",
        f"{base}/auth/token",
        form={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": f"{base}/",
        },
    )
    if status != 200 or not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise CheckError(f"token exchange failed: {status} {tokens}")
    return str(tokens["access_token"])


def _onboard(running: Running, steps: list[dict[str, Any]]) -> None:
    """Create the owner account and finish the first-boot wizard."""
    done = {row.get("step"): bool(row.get("done")) for row in steps}
    client_id = f"{running.base}/"
    status, created = _http(
        "POST",
        f"{running.base}/api/onboarding/users",
        {
            "client_id": client_id,
            "name": "Admin",
            "username": USERNAME,
            "password": PASSWORD,
            "language": "en",
        },
    )
    if status != 200 or not isinstance(created, dict) or not created.get("auth_code"):
        raise CheckError(f"onboarding user failed: {status} {created}")
    running.token = _exchange(running.base, str(created["auth_code"]))
    follow_ups = (
        ("core_config", "/api/onboarding/core_config", {}),
        ("analytics", "/api/onboarding/analytics", {}),
        (
            "integration",
            "/api/onboarding/integration",
            {"client_id": client_id, "redirect_uri": f"{running.base}/?auth_callback=1"},
        ),
    )
    for step, path, payload in follow_ups:
        if done.get(step):
            continue
        step_status, step_body = _http(
            "POST", f"{running.base}{path}", payload, token=running.token
        )
        if step_status not in {200, 204}:
            raise CheckError(f"onboarding {step} failed: {step_status} {step_body}")


def _advance(running: Running, flow_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Submit one config-flow step."""
    status, body = _http(
        "POST",
        f"{running.base}/api/config/config_entries/flow/{flow_id}",
        payload,
        token=running.token,
        timeout=90,
    )
    if status != 200 or not isinstance(body, dict):
        raise CheckError(f"config flow step failed: {status} {body}")
    return body


def _add_device(running: Running) -> str:
    """Create a config entry for the mock, using manual IP when discovery asks."""
    status, started = _http(
        "POST",
        f"{running.base}/api/config/config_entries/flow",
        {"handler": "marstek", "show_advanced_options": False},
        token=running.token,
        timeout=90,
    )
    if status != 200 or not isinstance(started, dict):
        raise CheckError(f"config flow did not start: {status} {started}")
    current = started
    step = str(current.get("step_id") or current.get("type") or "")
    flow_id = str(current.get("flow_id") or "")
    if step in {"user", "user_venus_e2"}:
        current = _advance(running, flow_id, {"device": "__manual__"})
        step = str(current.get("step_id") or current.get("type") or "")
        flow_id = str(current.get("flow_id") or flow_id)
    if step == "manual":
        current = _advance(running, flow_id, {"host": MOCK_IP, "port": MOCK_PORT})
    if current.get("type") != "create_entry":
        raise CheckError(f"config flow did not create an entry: {json.dumps(current)[:1500]}")
    result = current.get("result")
    if isinstance(result, dict) and result.get("entry_id"):
        return str(result["entry_id"])
    if isinstance(result, str):
        return result
    raise CheckError(f"create_entry had no entry id: {json.dumps(current)[:1500]}")


def _entry(running: Running, entry_id: str) -> dict[str, Any] | None:
    """Return the Marstek config entry once Core will list it."""
    status, body = _http(
        "GET",
        f"{running.base}/api/config/config_entries/entry?domain=marstek",
        token=running.token,
    )
    if status != 200 or not isinstance(body, list):
        return None
    for entry in body:
        if isinstance(entry, dict) and entry.get("entry_id") == entry_id:
            return entry
    return None


def _wait_until_loaded(running: Running, entry_id: str) -> None:
    """Wait until the entry is loaded, or fail on setup_error."""
    deadline = time.monotonic() + SETUP_TIMEOUT_SECONDS
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = _entry(running, entry_id)
        state = None if last is None else last.get("state")
        if state == "loaded":
            return
        if state == "setup_error":
            raise CheckError(f"config entry setup_error: {last}")
        time.sleep(2)
    raise CheckError(f"config entry did not load: {last}")


def _battery_state(running: Running) -> float:
    """Return the battery sensor state."""
    status, states = _http("GET", f"{running.base}/api/states", token=running.token)
    if status != 200 or not isinstance(states, list):
        raise CheckError(f"could not read states: {status}")
    for state in states:
        if not isinstance(state, dict):
            continue
        attributes = state.get("attributes")
        if not isinstance(attributes, dict) or attributes.get("device_class") != "battery":
            continue
        raw = state.get("state")
        if raw in {None, "unknown", "unavailable"}:
            raise CheckError(f"battery sensor {state.get('entity_id')} is {raw}")
        return float(str(raw))
    raise CheckError("no battery sensor was created")


def _reject_import_errors() -> None:
    """Fail when Core logged the private-import crash this check exists to catch."""
    result = _run(["docker", "logs", CORE_NAME], check=False)
    logs = f"{result.stdout}\n{result.stderr}"
    if "No module named" in logs:
        raise CheckError("Home Assistant logged 'No module named' while loading Marstek")


def run_check() -> None:
    """Boot Core at the hacs.json minimum and require a live battery sensor."""
    version = minimum_version(ROOT)
    config = _config_dir()
    try:
        _build_mock()
        _start_containers(version, config)
        running = Running(base=f"http://127.0.0.1:{HOST_PORT}", version=version)
        _log("Waiting for Home Assistant")
        steps = _wait_for_onboarding(running.base)
        if not isinstance(steps, list):
            raise CheckError("onboarding status was not a list")
        _onboard(running, steps)
        _log("Adding the Venus A mock")
        entry_id = _add_device(running)
        _wait_until_loaded(running, entry_id)
        _reject_import_errors()
        battery = _battery_state(running)
        # The mock is started with --no-simulate, so its state of charge stays
        # at MOCK_SOC. Home Assistant reaches the mock; the host running this
        # script often cannot, so the sensor is the comparison.
        if abs(battery - MOCK_SOC) > 0.5:
            raise CheckError(f"battery sensor {battery} does not match the mock's {MOCK_SOC}%")
        _log(f"Marstek loaded on Home Assistant {version}; battery {battery:g}% matches the mock")
    finally:
        shutil.rmtree(config, ignore_errors=True)


def main() -> None:
    """Run the check and always remove the containers it started."""
    cleanup()
    try:
        run_check()
    except CheckError as err:
        _dump_logs()
        print(err, file=sys.stderr)
        raise SystemExit(1) from err
    except Exception:
        _dump_logs()
        raise
    finally:
        cleanup()


if __name__ == "__main__":
    main()
