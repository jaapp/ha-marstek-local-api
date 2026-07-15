"""Diagnostics support for Marstek Local API."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import MarstekDataUpdateCoordinator, MarstekMultiDeviceCoordinator

TO_REDACT = ["wifi_name", "ssid"]


def _command_compatibility_summary(command_stats: dict[str, Any]) -> dict[str, Any]:
    """Generate compatibility summary from command statistics."""
    supported = []
    unsupported = []
    unknown = []

    for method, stats in command_stats.items():
        support_status = stats.get("supported")
        if support_status is True:
            supported.append(method)
        elif support_status is False:
            unsupported.append(method)
        else:
            unknown.append(method)

    return {
        "supported_commands": supported,
        "unsupported_commands": unsupported,
        "unknown_commands": unknown,
        "support_ratio": f"{len(supported)}/{len(command_stats)}",
    }


def _command_stats_snapshot(coordinator: MarstekDataUpdateCoordinator) -> dict[str, Any]:
    """Get all command statistics for compatibility tracking."""
    return coordinator.api.get_all_command_stats()


def _coordinator_snapshot(coordinator: MarstekDataUpdateCoordinator) -> dict[str, Any]:
    diagnostic_payload = coordinator.data.get("_diagnostic") if coordinator.data else None
    update_interval = coordinator.update_interval.total_seconds() if coordinator.update_interval else None

    # Get device identification from coordinator data
    device_info = coordinator.data.get("device", {}) if coordinator.data else {}

    # Get command stats
    command_stats = _command_stats_snapshot(coordinator)
    compatibility_summary = _command_compatibility_summary(command_stats)

    snapshot = {
        # Device identification
        "device_model": device_info.get("device") or coordinator.device_model,
        "firmware_version": device_info.get("ver") or coordinator.firmware_version,
        "ble_mac": device_info.get("ble_mac"),
        "wifi_mac": device_info.get("wifi_mac"),
        "wifi_name": device_info.get("wifi_name"),
        "device_ip": device_info.get("ip"),

        # Coordinator info
        "device_name": coordinator.name,
        "update_interval": update_interval,
        "update_count": coordinator.update_count,
        "last_update_started": coordinator._last_update_start,  # pylint: disable=protected-access

        # Current sensor data
        "sensor_data": coordinator.data,

        # Diagnostic payload
        "diagnostic_payload": diagnostic_payload,

        # Command compatibility matrix
        "command_compatibility": command_stats,
        "compatibility_summary": compatibility_summary,

        # Selected scaling profile (hardware/firmware detection)
        "compatibility": coordinator.compatibility.get_info(),
    }

    return async_redact_data(snapshot, TO_REDACT)


def _multi_diagnostics(coordinator: MarstekMultiDeviceCoordinator) -> dict[str, Any]:
    devices: dict[str, Any] = {}
    for mac, device_coordinator in coordinator.device_coordinators.items():
        devices[mac] = _coordinator_snapshot(device_coordinator)

    aggregates = coordinator.data.get("aggregates") if coordinator.data else None

    return {
        "requested_interval": coordinator.update_interval.total_seconds() if coordinator.update_interval else None,
        "diagnostic_payload": coordinator.data.get("_diagnostic") if coordinator.data else None,
        "devices": devices,
        "aggregates": aggregates,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        return {"error": "integration_not_initialized"}

    coordinator = data.get(DATA_COORDINATOR)

    if isinstance(coordinator, MarstekMultiDeviceCoordinator):
        return {
            "entry": {
                "title": entry.title,
                "device_count": len(coordinator.device_coordinators),
            },
            "multi": _multi_diagnostics(coordinator),
        }

    if isinstance(coordinator, MarstekDataUpdateCoordinator):
        return {
            "entry": {
                "title": entry.title,
                "device": entry.data.get("device"),
                "ble_mac": entry.data.get("ble_mac"),
            },
            "device": _coordinator_snapshot(coordinator),
        }

    return {"error": "unknown_coordinator"}
