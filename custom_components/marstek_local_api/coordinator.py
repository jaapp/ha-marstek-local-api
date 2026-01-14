"""Data update coordinator for Marstek Local API."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import random
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MarstekAPIError, MarstekUDPClient
from .compatibility import CompatibilityMatrix
from .const import (
    COMMAND_MAX_ATTEMPTS,
    COMMAND_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_MODEL_VENUS_D,
    UPDATE_INTERVAL_FAST,
    UPDATE_INTERVAL_MEDIUM,
    UPDATE_INTERVAL_SLOW,
    METHOD_BATTERY_STATUS,
    METHOD_ES_STATUS,
)

_LOGGER = logging.getLogger(__name__)


class MarstekMultiDeviceCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from multiple Marstek devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        devices: list[dict[str, Any]],
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the multi-device coordinator."""
        self.devices = devices
        self.device_coordinators: dict[str, MarstekDataUpdateCoordinator] = {}
        self.update_count = 1
        self._config_entry = config_entry

        super().__init__(
            hass,
            _LOGGER,
            name="Marstek System",
            update_interval=timedelta(seconds=scan_interval),
        )
        # Allow enough time for a full round of command retries during each refresh.
        self._timeout = COMMAND_TIMEOUT * COMMAND_MAX_ATTEMPTS + 5

    async def async_setup(self) -> None:
        """Set up individual device coordinators."""
        for device_data in self.devices:
            mac = device_data.get("ble_mac") or device_data.get("wifi_mac")

            # Create API client for this device
            api = MarstekUDPClient(
                self.hass,
                host=device_data["host"],
                port=device_data["port"],
                remote_port=device_data["port"],
            )

            try:
                await api.connect()
            except Exception as err:
                _LOGGER.error("Failed to connect to device %s: %s", mac, err)
                continue

            # Create coordinator for this device
            coordinator = MarstekDataUpdateCoordinator(
                self.hass,
                api,
                device_name=device_data.get("device", "Marstek Device"),
                firmware_version=device_data.get("firmware", 0),
                device_model=device_data.get("device", ""),
                scan_interval=self.update_interval.total_seconds(),
                config_entry=self._config_entry,
                device_mac=mac,
            )

            self.device_coordinators[mac] = coordinator

    def get_device_macs(self) -> list[str]:
        """Get list of device MACs."""
        return list(self.device_coordinators.keys())

    def get_device_data(self, mac: str) -> dict[str, Any]:
        """Get data for a specific device."""
        if mac in self.device_coordinators:
            return self.device_coordinators[mac].data or {}
        return {}

    def _calculate_aggregates(self) -> dict[str, Any]:
        """Calculate aggregate values across all devices."""
        aggregates = {}

        # Collect data from all devices
        all_device_data = []
        for mac, coordinator in self.device_coordinators.items():
            if coordinator.data:
                all_device_data.append(coordinator.data)

        if not all_device_data:
            return aggregates

        # Power aggregates
        total_power = sum(
            d.get("es", {}).get("bat_power", 0) or 0
            for d in all_device_data
        )
        aggregates["total_battery_power"] = total_power
        aggregates["total_power_in"] = sum(
            max(0, d.get("es", {}).get("bat_power", 0) or 0)
            for d in all_device_data
        )
        aggregates["total_power_out"] = sum(
            max(0, -(d.get("es", {}).get("bat_power", 0) or 0))
            for d in all_device_data
        )

        # Capacity aggregates
        aggregates["total_rated_capacity"] = sum(
            d.get("battery", {}).get("rated_capacity", 0) or 0
            for d in all_device_data
        )
        aggregates["total_remaining_capacity"] = sum(
            d.get("battery", {}).get("bat_capacity", 0) or 0
            for d in all_device_data
        )

        # Calculate weighted average SOC
        total_capacity = aggregates["total_rated_capacity"]
        if total_capacity > 0:
            weighted_soc = sum(
                (d.get("battery", {}).get("soc", 0) or 0) *
                (d.get("battery", {}).get("rated_capacity", 0) or 0)
                for d in all_device_data
            )
            aggregates["average_soc"] = weighted_soc / total_capacity
        else:
            aggregates["average_soc"] = None

        # Available capacity
        if total_capacity > 0 and aggregates["average_soc"] is not None:
            aggregates["total_available_capacity"] = (
                (100 - aggregates["average_soc"]) * total_capacity / 100
            )
        else:
            aggregates["total_available_capacity"] = None

        # Combined state
        power_values = [
            d.get("es", {}).get("bat_power", 0) or 0
            for d in all_device_data
        ]
        charging_flags = [p > 0 for p in power_values]
        discharging_flags = [p < 0 for p in power_values]
        idle_flags = [p == 0 for p in power_values]

        has_charging = any(charging_flags)
        has_discharging = any(discharging_flags)

        if has_charging and not has_discharging and all(charging_flags):
            aggregates["combined_state"] = "charging"
        elif has_discharging and not has_charging and all(discharging_flags):
            aggregates["combined_state"] = "discharging"
        elif all(idle_flags):
            aggregates["combined_state"] = "idle"
        elif has_charging and has_discharging:
            aggregates["combined_state"] = "conflicting"
        elif has_charging:
            aggregates["combined_state"] = "partly_charging"
        elif has_discharging:
            aggregates["combined_state"] = "partly_discharging"
        else:
            aggregates["combined_state"] = "idle"

        # Energy aggregates
        aggregates["total_pv_energy"] = sum(
            d.get("es", {}).get("total_pv_energy", 0) or 0
            for d in all_device_data
        )
        aggregates["total_grid_import"] = sum(
            d.get("es", {}).get("total_grid_input_energy", 0) or 0
            for d in all_device_data
        )
        aggregates["total_grid_export"] = sum(
            d.get("es", {}).get("total_grid_output_energy", 0) or 0
            for d in all_device_data
        )
        aggregates["total_load_energy"] = sum(
            d.get("es", {}).get("total_load_energy", 0) or 0
            for d in all_device_data
        )
        aggregates["total_solar_power"] = sum(
            d.get("es", {}).get("pv_power", 0) or 0
            for d in all_device_data
        )
        aggregates["total_grid_power"] = sum(
            d.get("es", {}).get("ongrid_power", 0) or 0
            for d in all_device_data
        )
        aggregates["total_offgrid_power"] = sum(
            d.get("es", {}).get("offgrid_power", 0) or 0
            for d in all_device_data
        )

        return aggregates

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data from all devices."""
        # Update all device coordinators in parallel
        # We call _async_update_data() and manually set the data attribute
        # since we're managing the coordinators directly
        async def update_device(mac: str, coordinator: MarstekDataUpdateCoordinator):
            try:
                await asyncio.sleep(coordinator.poll_jitter)
                data = await coordinator._async_update_data()
                coordinator.data = data  # Manually set data since we're calling _async_update_data directly
                return data
            except Exception as err:
                _LOGGER.error("Error updating device %s: %s", mac, err)
                return coordinator.data  # Return old data on error

        update_tasks = [
            update_device(mac, coordinator)
            for mac, coordinator in self.device_coordinators.items()
        ]

        await asyncio.gather(*update_tasks, return_exceptions=True)

        # Build combined data structure
        data = {
            "devices": {
                mac: coordinator.data
                for mac, coordinator in self.device_coordinators.items()
            },
            "aggregates": self._calculate_aggregates(),
        }

        return data


class MarstekDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Marstek data from the device."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: MarstekUDPClient,
        device_name: str,
        firmware_version: int,
        device_model: str,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        config_entry: ConfigEntry | None = None,
        device_mac: str | None = None,
    ) -> None:
        """Initialize the coordinator."""
        self.api = api
        self.firmware_version = firmware_version
        self.device_model = device_model
        self.update_count = 1  # Start at 1 to skip slow updates on first refresh
        self.last_message_timestamp: float | None = None
        jitter_cap = min(2.5, max(0.5, scan_interval * 0.1))
        self.poll_jitter = random.uniform(0.2, jitter_cap)
        self._last_update_start: float | None = None
        self._config_entry = config_entry
        self._device_mac = device_mac  # Used for multi-device mode to identify which device to update

        # Staleness tracking - track last successful update per category
        self.category_last_updated: dict[str, float] = {}
        self.STALENESS_THRESHOLD = 3  # missed updates before invalidation
        self.STALENESS_FACTOR = {
            "battery": UPDATE_INTERVAL_FAST,
            "es": UPDATE_INTERVAL_FAST,
            "em": UPDATE_INTERVAL_MEDIUM,
            "pv": UPDATE_INTERVAL_MEDIUM,
            "mode": UPDATE_INTERVAL_MEDIUM,
            "ble": UPDATE_INTERVAL_SLOW,
            "wifi": UPDATE_INTERVAL_SLOW,
        }
        self.STATIC_CATEGORIES = {"device", "wifi", "ble", "_diagnostic", "aggregates"}

        # Initialize compatibility matrix for version-specific scaling
        self.compatibility = CompatibilityMatrix(
            device_model=device_model,
            firmware_version=firmware_version,
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"Marstek {device_name}",
            update_interval=timedelta(seconds=scan_interval),
        )
        # Default coordinator timeout (10s) is too short for staged polling + retries.
        self._timeout = COMMAND_TIMEOUT * COMMAND_MAX_ATTEMPTS + 5

    def _update_device_version(self, device_info: dict) -> None:
        """Update device firmware/hardware version and reinitialize compatibility matrix if changed.

        Args:
            device_info: Device info dictionary containing 'ver' (firmware) and 'device' (model)
        """
        new_firmware = device_info.get("ver")
        new_model = device_info.get("device")

        firmware_changed = new_firmware is not None and new_firmware != self.firmware_version
        model_changed = new_model is not None and new_model != self.device_model

        if firmware_changed or model_changed:
            if firmware_changed:
                _LOGGER.info(
                    "Firmware version changed from %d to %d, reinitializing compatibility matrix",
                    self.firmware_version,
                    new_firmware,
                )
                self.firmware_version = new_firmware

            if model_changed:
                _LOGGER.info(
                    "Device model changed from %s to %s, reinitializing compatibility matrix",
                    self.device_model,
                    new_model,
                )
                self.device_model = new_model

            # Reinitialize compatibility matrix with new version(s)
            self.compatibility = CompatibilityMatrix(
                device_model=self.device_model,
                firmware_version=self.firmware_version,
            )

            # Update config entry data so DeviceInfo shows current firmware/model
            if self._config_entry:
                new_data = dict(self._config_entry.data)

                # Handle both single-device and multi-device config entries
                if "devices" in new_data and self._device_mac:
                    # Multi-device mode: update the specific device in the devices list
                    devices = list(new_data["devices"])
                    for i, device in enumerate(devices):
                        device_mac = device.get("ble_mac") or device.get("wifi_mac")
                        if device_mac == self._device_mac:
                            device_copy = dict(device)
                            if firmware_changed:
                                device_copy["firmware"] = new_firmware
                            if model_changed:
                                device_copy["device"] = new_model
                            devices[i] = device_copy
                            break
                    new_data["devices"] = devices
                else:
                    # Single-device mode: update top-level fields
                    if firmware_changed:
                        new_data["firmware"] = new_firmware
                    if model_changed:
                        new_data["device"] = new_model

                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data=new_data
                )
                _LOGGER.info(
                    "Updated config entry with new firmware=%s, model=%s (device_mac=%s)",
                    new_firmware if firmware_changed else "unchanged",
                    new_model if model_changed else "unchanged",
                    self._device_mac or "single-device",
                )

    def _get_seconds_since_last_message(self) -> int | None:
        """Get seconds since last successful message (Design Doc §556-576)."""
        if self.last_message_timestamp is None:
            return None
        return int(time.time() - self.last_message_timestamp)

    def is_category_fresh(self, category: str) -> bool:
        """Check if category data is fresh enough to display.

        Args:
            category: The data category to check (battery, es, em, pv, mode, etc.)

        Returns:
            True if data is fresh, False if stale or never received
        """
        # Static categories never go stale
        if category in self.STATIC_CATEGORIES:
            return True

        # Check if we ever received data for this category
        if category not in self.category_last_updated:
            return False

        # Calculate time since last update
        last_update = self.category_last_updated[category]
        elapsed = time.time() - last_update

        category_stale_factor = self.STALENESS_FACTOR.get(category, 1)

        # Calculate max age (update interval * threshold)
        max_age = self.update_interval.total_seconds() * self.STALENESS_THRESHOLD * category_stale_factor

        return elapsed < max_age

    def _build_command_diagnostics(self, prefix: str, stats: dict[str, Any] | None) -> dict[str, Any]:
        """Transform command stats into diagnostic fields."""
        if not stats:
            return {}

        total_attempts = stats.get("total_attempts", 0)
        total_success = stats.get("total_success", 0)
        total_timeouts = stats.get("total_timeouts", 0)
        success_rate = (
            (total_success / total_attempts) * 100 if total_attempts else None
        )

        last_success = stats.get("last_success")
        diag: dict[str, Any] = {
            f"{prefix}_last_latency": stats.get("last_latency"),
            f"{prefix}_last_attempt": stats.get("last_attempt"),
            f"{prefix}_last_success": None if last_success is None else int(bool(last_success)),
            f"{prefix}_timeout_total": total_timeouts,
            f"{prefix}_success_total": total_success,
            f"{prefix}_request_total": total_attempts,
            f"{prefix}_success_rate": success_rate,
            f"{prefix}_last_error": stats.get("last_error"),
        }
        return diag

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API with tiered polling strategy."""
        try:
            update_started = time.time()
            actual_interval = None
            if self._last_update_start is not None:
                actual_interval = update_started - self._last_update_start
            self._last_update_start = update_started

            # Check if this is truly the first update (never been run before)
            is_first_update = self.data is None
            _LOGGER.debug("Update starting - is_first_update=%s, self.data=%s", is_first_update, "None" if self.data is None else f"dict with {len(self.data)} keys")

            # Start with previous data to preserve values on partial failures
            data = dict(self.data) if self.data else {}
            had_success = False

            def _command_kwargs() -> dict[str, Any]:
                """Use shorter timeouts/attempts during the very first refresh."""
                if is_first_update and not had_success:
                    return {"timeout": min(5, COMMAND_TIMEOUT), "max_attempts": 1}
                return {}

            def _command_delay() -> float:
                """Back off a little between calls; go faster while probing initial contact."""
                return 0.2 if is_first_update and not had_success else 1.0

            if is_first_update:
                _LOGGER.debug("First update - fetching device info")
                try:
                    await asyncio.sleep(_command_delay())  # Delay before first API call
                    device_info = await self.api.get_device_info(**_command_kwargs())
                    if device_info:
                        data["device"] = device_info
                        self.category_last_updated["device"] = time.time()
                        self._update_device_version(device_info)
                        had_success = True
                except Exception as err:
                    _LOGGER.warning("Failed to get device info on first update: %s", err)

            # High priority - every update (~60s)
            # ES.GetStatus and Bat.GetStatus for real-time power/energy data
            try:
                await asyncio.sleep(_command_delay())  # Delay between API calls
                es_status = await self.api.get_es_status(**_command_kwargs())
            except Exception as err:
                _LOGGER.debug("Failed to get ES status: %s", err)
                es_status = None

            if es_status:
                # Scale firmware-dependent values
                if "bat_power" in es_status:
                    es_status["bat_power"] = self.compatibility.scale_value(
                        es_status["bat_power"], "bat_power"
                    )
                if "total_grid_input_energy" in es_status:
                    es_status["total_grid_input_energy"] = self.compatibility.scale_value(
                        es_status["total_grid_input_energy"], "total_grid_input_energy"
                    )
                if "total_grid_output_energy" in es_status:
                    es_status["total_grid_output_energy"] = self.compatibility.scale_value(
                        es_status["total_grid_output_energy"], "total_grid_output_energy"
                    )
                if "total_load_energy" in es_status:
                    es_status["total_load_energy"] = self.compatibility.scale_value(
                        es_status["total_load_energy"], "total_load_energy"
                    )

                data["es"] = es_status
                self.category_last_updated["es"] = time.time()
                had_success = True

            try:
                await asyncio.sleep(_command_delay())  # Delay between API calls
                battery_status = await self.api.get_battery_status(**_command_kwargs())
            except Exception as err:
                _LOGGER.debug("Failed to get battery status: %s", err)
                battery_status = None

            if battery_status:
                # Scale firmware/hardware-dependent values
                if "bat_temp" in battery_status:
                    battery_status["bat_temp"] = self.compatibility.scale_value(
                        battery_status["bat_temp"], "bat_temp"
                    )
                if "bat_capacity" in battery_status:
                    battery_status["bat_capacity"] = self.compatibility.scale_value(
                        battery_status["bat_capacity"], "bat_capacity"
                    )
                if "bat_voltage" in battery_status:
                    battery_status["bat_voltage"] = self.compatibility.scale_value(
                        battery_status["bat_voltage"], "bat_voltage"
                    )
                if "bat_current" in battery_status:
                    battery_status["bat_current"] = self.compatibility.scale_value(
                        battery_status["bat_current"], "bat_current"
                    )
                data["battery"] = battery_status
                self.category_last_updated["battery"] = time.time()
                had_success = True

            # Medium priority - every 5th update (~300s)
            # EM, PV, Mode - slower-changing data
            run_medium = self.update_count == 1 or self.update_count % UPDATE_INTERVAL_MEDIUM == 0
            if is_first_update and not had_success:
                run_medium = False
            if run_medium:
                try:
                    await asyncio.sleep(_command_delay())  # Delay between API calls
                    em_status = await self.api.get_em_status(**_command_kwargs())
                    if em_status:
                        data["em"] = em_status
                        self.category_last_updated["em"] = time.time()
                        had_success = True
                except Exception as err:
                    _LOGGER.debug("Failed to get EM status: %s", err)

                # Only query PV for Venus D
                if self.device_model == DEVICE_MODEL_VENUS_D:
                    try:
                        await asyncio.sleep(_command_delay())  # Delay between API calls
                        pv_status = await self.api.get_pv_status(**_command_kwargs())
                        if pv_status:
                            data["pv"] = pv_status
                            self.category_last_updated["pv"] = time.time()
                            had_success = True
                    except Exception as err:
                        _LOGGER.debug("Failed to get PV status: %s", err)

                try:
                    await asyncio.sleep(_command_delay())  # Delay between API calls
                    mode_status = await self.api.get_es_mode(**_command_kwargs())
                    if mode_status:
                        data["mode"] = mode_status
                        self.category_last_updated["mode"] = time.time()
                        had_success = True
                except Exception as err:
                    _LOGGER.debug("Failed to get mode status: %s", err)

            # Low priority - every 10th update (~600s)
            # Device, WiFi, BLE - static/diagnostic data
            run_slow = self.update_count % UPDATE_INTERVAL_SLOW == 0
            if is_first_update and not had_success:
                run_slow = False
            if run_slow:
                try:
                    await asyncio.sleep(_command_delay())  # Delay between API calls
                    device_info = await self.api.get_device_info(**_command_kwargs())
                    if device_info:
                        data["device"] = device_info
                        self.category_last_updated["device"] = time.time()
                        self._update_device_version(device_info)
                        had_success = True
                except Exception as err:
                    _LOGGER.debug("Failed to get device info: %s", err)

                try:
                    await asyncio.sleep(_command_delay())  # Delay between API calls
                    wifi_status = await self.api.get_wifi_status(**_command_kwargs())
                    if wifi_status:
                        data["wifi"] = wifi_status
                        self.category_last_updated["wifi"] = time.time()
                        had_success = True
                except Exception as err:
                    _LOGGER.debug("Failed to get wifi status: %s", err)

                try:
                    await asyncio.sleep(_command_delay())  # Delay between API calls
                    ble_status = await self.api.get_ble_status(**_command_kwargs())
                    if ble_status:
                        data["ble"] = ble_status
                        self.category_last_updated["ble"] = time.time()
                        had_success = True
                except Exception as err:
                    _LOGGER.debug("Failed to get BLE status: %s", err)

            # Increment update counter
            self.update_count += 1

            # On first update, we need at least some data to proceed
            # But we're lenient - even just device info is enough
            if is_first_update and not had_success:
                _LOGGER.warning(
                    "First update did not receive any data from device; continuing setup and retrying in background"
                )

            # If we got any new data, update the last message timestamp
            # (We compare with the preserved old data to see if anything changed)
            if had_success:
                self.last_message_timestamp = time.time()
                _LOGGER.debug("Updated data - at least one API call succeeded (keys: %s)", list(data.keys()))
            else:
                _LOGGER.debug(
                    "No fresh data this update - all API calls may have timed out, keeping previous values (keys: %s)",
                    list(data.keys()),
                )

            # Add diagnostic data (will be recalculated on sensor access)
            es_stats = self.api.get_command_stats(METHOD_ES_STATUS)
            bat_stats = self.api.get_command_stats(METHOD_BATTERY_STATUS)
            target_interval = (
                self.update_interval.total_seconds() if self.update_interval else None
            )

            diagnostic_data = {
                "last_message_seconds": self._get_seconds_since_last_message(),
                "target_interval": target_interval,
                "actual_interval": actual_interval,
            }
            diagnostic_data.update(self._build_command_diagnostics("es", es_stats))
            diagnostic_data.update(self._build_command_diagnostics("bat", bat_stats))

            data["_diagnostic"] = diagnostic_data

            return data

        except MarstekAPIError as err:
            # Only fail if this is the first update (no existing data to preserve)
            if is_first_update:
                raise UpdateFailed(f"Error communicating with API: {err}") from err
            # Otherwise log and return preserved data
            _LOGGER.warning("API error during update, keeping old values: %s", err)
            return self.data if self.data else {}
        except Exception as err:
            # Only fail if this is the first update (no existing data to preserve)
            if is_first_update:
                raise UpdateFailed(f"Unexpected error: {err}") from err
            # Otherwise log and return preserved data
            _LOGGER.error("Unexpected error during update, keeping old values: %s", err, exc_info=True)
            return self.data if self.data else {}
