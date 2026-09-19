#!/usr/bin/env bash
# Idempotent Cloud Agent install: Python 3.14.2+ (HA 2026.9 / pytest harness)
# and Docker Engine with fuse-overlayfs + iptables-legacy for nested Docker.
# Official refs:
#   https://docs.docker.com/engine/install/ubuntu/
#   https://cursor.com/docs/cloud-agent/setup (Running Docker)
#   https://github.com/home-assistant/core/blob/2026.9.2/pyproject.toml
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
APT_GET=(sudo apt-get -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)
REPO_ROOT="${REPO_ROOT:-}"
if [[ -f /workspace/requirements_test.txt ]]; then
  REPO_ROOT=/workspace
elif [[ -z "${REPO_ROOT}" ]]; then
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
VENV_DIR="${HOME}/.venvs/ha-marstek"
HA_IMAGE="ghcr.io/home-assistant/home-assistant:2026.9.3"

log() {
  printf '%s\n' "$*"
}

ensure_apt_packages() {
  "${APT_GET[@]}" update -y
  "${APT_GET[@]}" install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    software-properties-common \
    build-essential \
    pkg-config \
    libffi-dev \
    libssl-dev \
    libjpeg-dev \
    zlib1g-dev \
    fuse-overlayfs \
    iptables \
    uidmap \
    dbus
}

ensure_python314() {
  if ! command -v python3.14 >/dev/null 2>&1; then
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    "${APT_GET[@]}" update -y
  fi
  "${APT_GET[@]}" install -y --no-install-recommends \
    python3.14 \
    python3.14-venv \
    python3.14-dev
}

ensure_python_version() {
  python3.14 - <<'PY'
import sys

if sys.version_info < (3, 14, 2):
    raise SystemExit(
        f"Python {sys.version.split()[0]} is older than 3.14.2 required by "
        "Home Assistant Core 2026.9 / pytest-homeassistant-custom-component"
    )
print(f"Using Python {sys.version.split()[0]}")
PY
}

ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    "${APT_GET[@]}" remove -y docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc || true
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    "${APT_GET[@]}" update -y
    "${APT_GET[@]}" install -y \
      docker-ce \
      docker-ce-cli \
      containerd.io \
      docker-buildx-plugin \
      docker-compose-plugin
  fi

  sudo mkdir -p /etc/docker
  sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "storage-driver": "fuse-overlayfs",
  "iptables": true,
  "live-restore": false
}
EOF

  if command -v update-alternatives >/dev/null 2>&1; then
    sudo update-alternatives --set iptables /usr/sbin/iptables-legacy || true
    sudo update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy || true
  fi

  sudo groupadd -f docker
  sudo usermod -aG docker "$(id -un)" || true
}

link_venv_tools() {
  mkdir -p "${HOME}/.local/bin"
  sudo mkdir -p /usr/local/bin /etc/profile.d
  # A symlink of venv/bin/python into /usr/local/bin loses the venv prefix
  # (pyvenv.cfg is resolved from the executable's directory). Use a wrapper.
  # Remove any existing symlink first so tee does not follow it and overwrite
  # /usr/bin/python3.14.
  sudo rm -f /usr/local/bin/python3 /usr/local/bin/python
  sudo tee /usr/local/bin/python3 >/dev/null <<EOF
#!/bin/sh
exec "${VENV_DIR}/bin/python" "\$@"
EOF
  sudo chmod +x /usr/local/bin/python3
  sudo ln -sfn /usr/local/bin/python3 /usr/local/bin/python
  sudo ln -sfn /usr/bin/python3.14 /usr/local/bin/python3.14
  local tool
  for tool in pip pip3 pytest ruff mypy coverage; do
    if [[ -x "${VENV_DIR}/bin/${tool}" ]]; then
      sudo ln -sfn "${VENV_DIR}/bin/${tool}" "/usr/local/bin/${tool}"
    fi
  done
  sudo tee /etc/profile.d/ha-marstek-venv.sh >/dev/null <<EOF
export PATH="${VENV_DIR}/bin:\$PATH"
EOF
}

install_python_deps() {
  mkdir -p "$(dirname "${VENV_DIR}")"
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    python3.14 -m venv "${VENV_DIR}"
  fi
  "${VENV_DIR}/bin/python" -m pip install --upgrade pip
  "${VENV_DIR}/bin/python" -m pip install -r "${REPO_ROOT}/requirements_test.txt"
  link_venv_tools
}

install_node_deps() {
  if [[ -f "${REPO_ROOT}/package-lock.json" ]]; then
    (cd "${REPO_ROOT}" && npm ci)
  else
    (cd "${REPO_ROOT}" && npm install)
  fi
}

start_dockerd_for_cache() {
  if docker info >/dev/null 2>&1 || sudo docker info >/dev/null 2>&1; then
    return 0
  fi
  sudo mkdir -p /tmp
  sudo dockerd >/tmp/dockerd.log 2>&1 &
  local i
  for i in $(seq 1 40); do
    if sudo docker info >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  log "dockerd did not become ready; skipping image cache"
  return 1
}

cache_docker_images() {
  if ! start_dockerd_for_cache; then
    return 0
  fi
  sudo docker pull "${HA_IMAGE}" || true
  if [[ -f "${REPO_ROOT}/.devcontainer/docker-compose.yml" ]]; then
    sudo docker compose -f "${REPO_ROOT}/.devcontainer/docker-compose.yml" build || true
  fi
}

sync_helper_scripts() {
  if [[ -f /workspace/.cursor/install.sh && -f /workspace/.cursor/start.sh ]]; then
    sudo cp /workspace/.cursor/install.sh /usr/local/bin/marstek-cloud-install.sh
    sudo cp /workspace/.cursor/start.sh /usr/local/bin/marstek-cloud-start.sh
    sudo chmod +x /usr/local/bin/marstek-cloud-install.sh /usr/local/bin/marstek-cloud-start.sh
  fi
}

ensure_apt_packages
ensure_python314
ensure_python_version
ensure_docker
install_python_deps
install_node_deps
cache_docker_images
sync_helper_scripts

log "Cloud Agent install complete."
log "python3 -> $(command -v python3) ($(python3 --version 2>/dev/null || true))"
log "docker -> $(command -v docker || true)"
