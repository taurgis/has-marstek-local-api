"""Sensor statistics helpers for Marstek integration."""

from __future__ import annotations

from typing import Any

from ..coordinator import MarstekDataUpdateCoordinator


def command_success_rate(coordinator: MarstekDataUpdateCoordinator, method: str) -> float | None:
    """Return success rate for a command as a percentage."""
    stats = coordinator.udp_client.get_command_stats_for_ip(coordinator.device_ip)
    if not isinstance(stats, dict):
        return None
    bucket = stats.get(method)
    if not isinstance(bucket, dict):
        return None
    attempts = bucket.get("total_attempts")
    success = bucket.get("total_success")
    if not isinstance(attempts, (int, float)) or not isinstance(success, (int, float)):
        return None
    if attempts <= 0:
        return None
    return (success / attempts) * 100.0


def command_stats_attributes(
    coordinator: MarstekDataUpdateCoordinator, method: str
) -> dict[str, Any] | None:
    """Return attributes for a command stats bucket."""
    stats = coordinator.udp_client.get_command_stats_for_ip(coordinator.device_ip)
    if not isinstance(stats, dict):
        return None
    bucket = stats.get(method)
    if not isinstance(bucket, dict):
        return None
    attributes = {
        "total_attempts": bucket.get("total_attempts"),
        "total_success": bucket.get("total_success"),
        "total_timeouts": bucket.get("total_timeouts"),
        "total_failures": bucket.get("total_failures"),
        "last_success": bucket.get("last_success"),
        "last_timeout": bucket.get("last_timeout"),
        "last_error": bucket.get("last_error"),
        "last_latency": bucket.get("last_latency"),
        "last_updated": bucket.get("last_updated"),
    }
    return {key: value for key, value in attributes.items() if value is not None}


def overall_command_success_rate(
    coordinator: MarstekDataUpdateCoordinator,
) -> float | None:
    """Return success rate across all command buckets."""
    stats = coordinator.udp_client.get_command_stats_for_ip(coordinator.device_ip)
    if not isinstance(stats, dict):
        return None
    attempts_total = 0.0
    success_total = 0.0
    for bucket in stats.values():
        if not isinstance(bucket, dict):
            continue
        attempts = bucket.get("total_attempts")
        success = bucket.get("total_success")
        if not isinstance(attempts, (int, float)) or not isinstance(success, (int, float)):
            continue
        attempts_total += float(attempts)
        success_total += float(success)
    if attempts_total <= 0:
        return None
    return (success_total / attempts_total) * 100.0


def overall_command_stats_attributes(
    coordinator: MarstekDataUpdateCoordinator,
) -> dict[str, Any] | None:
    """Return aggregated command stats attributes."""
    stats = coordinator.udp_client.get_command_stats_for_ip(coordinator.device_ip)
    if not isinstance(stats, dict):
        return None
    attempts_total = 0
    success_total = 0
    timeout_total = 0
    failure_total = 0
    has_data = False
    for bucket in stats.values():
        if not isinstance(bucket, dict):
            continue
        attempts = bucket.get("total_attempts")
        success = bucket.get("total_success")
        if not isinstance(attempts, (int, float)):
            continue
        if not isinstance(success, (int, float)):
            continue
        timeouts = bucket.get("total_timeouts", 0)
        failures = bucket.get("total_failures", 0)
        timeout_value = timeouts if isinstance(timeouts, (int, float)) else 0
        failure_value = failures if isinstance(failures, (int, float)) else 0
        attempts_total += int(attempts)
        success_total += int(success)
        timeout_total += int(timeout_value)
        failure_total += int(failure_value)
        has_data = True
    if not has_data:
        return None
    attributes = {
        "total_attempts": attempts_total,
        "total_success": success_total,
        "total_timeouts": timeout_total,
        "total_failures": failure_total,
    }
    return {key: value for key, value in attributes.items() if value is not None}
