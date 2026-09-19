#!/usr/bin/env bash
# Per-boot Cloud Agent start: Docker daemon + Home Assistant / mock-device stack.
# Official refs:
#   https://cursor.com/docs/cloud-agent/setup (start Docker with the start command)
#   https://www.home-assistant.io/installation/linux#install-home-assistant-container
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-}"
if [[ -f /workspace/.devcontainer/docker-compose.yml ]]; then
  REPO_ROOT=/workspace
elif [[ -z "${REPO_ROOT}" ]]; then
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
COMPOSE_FILE="${REPO_ROOT}/.devcontainer/docker-compose.yml"
HA_URL="${HA_URL:-http://127.0.0.1:8123/api/onboarding}"
DOCKERD_LOG="${DOCKERD_LOG:-/tmp/dockerd.log}"

log() {
  printf '%s\n' "$*"
}

docker_ready() {
  sudo docker info >/dev/null 2>&1
}

ensure_dockerd() {
  if docker_ready; then
    return 0
  fi

  if command -v service >/dev/null 2>&1; then
    sudo service docker start >/dev/null 2>&1 || true
    sleep 1
    if docker_ready; then
      return 0
    fi
  fi

  sudo mkdir -p /var/run /var/log
  # Nested Cloud Agent VMs often have no systemd (PID 1 is tini).
  sudo dockerd >>"${DOCKERD_LOG}" 2>&1 &
  local i
  for i in $(seq 1 60); do
    if docker_ready; then
      return 0
    fi
    sleep 1
  done
  log "dockerd failed to start; last log lines:"
  sudo tail -n 80 "${DOCKERD_LOG}" || true
  return 1
}

fix_forward_policy() {
  # Cloud VMs using iptables-legacy may drop Docker inter-container traffic.
  if command -v iptables-legacy >/dev/null 2>&1; then
    sudo iptables-legacy -P FORWARD ACCEPT || true
    sudo iptables-legacy -C FORWARD -i br-+ -j ACCEPT 2>/dev/null || sudo iptables-legacy -I FORWARD -i br-+ -j ACCEPT || true
    sudo iptables-legacy -C FORWARD -o br-+ -j ACCEPT 2>/dev/null || sudo iptables-legacy -I FORWARD -o br-+ -j ACCEPT || true
  fi
  if command -v iptables >/dev/null 2>&1; then
    sudo iptables -P FORWARD ACCEPT || true
  fi
}

wait_for_ha() {
  local i
  for i in $(seq 1 90); do
    if curl -fsS --max-time 2 "${HA_URL}" >/dev/null 2>&1; then
      log "Home Assistant is reachable at ${HA_URL}"
      return 0
    fi
    sleep 2
  done
  log "Home Assistant did not become ready at ${HA_URL}"
  sudo docker ps -a || true
  sudo docker logs --tail 80 marstek-ha-dev || true
  return 1
}

ensure_dockerd
fix_forward_policy

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  log "Missing ${COMPOSE_FILE}; Docker daemon is up, compose stack skipped."
  exit 0
fi

sudo docker compose -f "${COMPOSE_FILE}" up -d --build
wait_for_ha
sudo docker compose -f "${COMPOSE_FILE}" ps
