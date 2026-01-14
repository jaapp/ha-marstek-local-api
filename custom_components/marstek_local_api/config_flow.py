"""Config flow for Marstek Local API integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import dhcp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import MarstekAPIError, MarstekUDPClient
from .const import CONF_PORT, DATA_COORDINATOR, DEFAULT_PORT, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any], use_ephemeral_port: bool = False) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    use_ephemeral_port: Deprecated parameter, kept for compatibility
    """
    # Always bind to device port (reuse_port allows multiple instances)
    target_port = data.get(CONF_PORT, DEFAULT_PORT)
    api = MarstekUDPClient(hass, data.get(CONF_HOST), target_port, remote_port=target_port)

    try:
        await api.connect()

        # Try to get device info
        device_info = await api.get_device_info()

        if not device_info:
            raise CannotConnect("Failed to get device information")

        # Return info that you want to store in the config entry.
        return {
            "title": f"{device_info.get('device', 'Marstek Device')} ({device_info.get('ble_mac', device_info.get('wifi_mac', 'Unknown'))})",
            "device": device_info.get("device"),
            "firmware": device_info.get("ver"),
            "wifi_mac": device_info.get("wifi_mac"),
            "ble_mac": device_info.get("ble_mac"),
        }

    except MarstekAPIError as err:
        _LOGGER.error("Error connecting to Marstek device: %s", err)
        raise CannotConnect from err
    finally:
        await api.disconnect()

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Marstek Local API."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_devices: list[dict] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        # Start discovery
        return await self.async_step_discovery()

    async def async_step_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle discovery of devices."""
        errors = {}

        if user_input is None:
            # Perform discovery
            # Temporarily disconnect existing integration clients to avoid port conflicts
            paused_clients = []
            for entry in self._async_current_entries():
                if DOMAIN in self.hass.data and entry.entry_id in self.hass.data[DOMAIN]:
                    coordinator = self.hass.data[DOMAIN][entry.entry_id].get(DATA_COORDINATOR)
                    if coordinator:
                        # Handle both single-device and multi-device coordinators
                        if hasattr(coordinator, 'device_coordinators'):
                            # Multi-device coordinator
                            _LOGGER.debug("Pausing multi-device coordinator %s during discovery", entry.title)
                            for device_coordinator in coordinator.device_coordinators.values():
                                if device_coordinator.api:
                                    await device_coordinator.api.disconnect()
                                    paused_clients.append(device_coordinator.api)
                        elif hasattr(coordinator, 'api') and coordinator.api:
                            # Single-device coordinator
                            _LOGGER.debug("Pausing API client for %s during discovery", entry.title)
                            await coordinator.api.disconnect()
                            paused_clients.append(coordinator.api)

            # Wait a bit for disconnections to complete and sockets to close
            import asyncio
            await asyncio.sleep(1)

            # Bind to same port as device (required by Marstek protocol)
            api = MarstekUDPClient(self.hass, port=DEFAULT_PORT, remote_port=DEFAULT_PORT)
            try:
                await api.connect()
                self._discovered_devices = await api.discover_devices()
                await api.disconnect()

                _LOGGER.info("Discovered %d device(s): %s", len(self._discovered_devices), self._discovered_devices)
            except Exception as err:
                _LOGGER.error("Discovery failed: %s", err, exc_info=True)
                try:
                    await api.disconnect()
                except Exception:
                    pass  # Ignore disconnect errors
                return await self.async_step_manual()
            finally:
                # Wait a bit before resuming to ensure discovery socket is fully closed
                await asyncio.sleep(1)

                # Resume paused clients
                for client in paused_clients:
                    try:
                        _LOGGER.debug("Resuming paused API client for host %s", client.host)
                        await client.connect()
                    except Exception as err:
                        _LOGGER.warning("Failed to resume client for host %s: %s", client.host, err)

            if not self._discovered_devices:
                # No devices found, offer manual entry
                return await self.async_step_manual()

            # Build list of discovered devices
            devices_list = {}

            # Add "All devices" option if multiple devices found
            if len(self._discovered_devices) > 1:
                devices_list["__all__"] = f"All devices ({len(self._discovered_devices)} batteries)"

            for device in self._discovered_devices:
                mac = device["mac"]
                # Show all devices, the abort happens when user selects one already configured
                devices_list[mac] = f"{device['name']} ({device['ip']})"
                _LOGGER.debug("Adding device to list: %s (%s) MAC: %s", device['name'], device['ip'], mac)

            _LOGGER.info("Built device list with %d device(s)", len(devices_list))

            # Add manual entry option
            devices_list["manual"] = "Manual IP entry"

            return self.async_show_form(
                step_id="discovery",
                data_schema=vol.Schema(
                    {
                        vol.Required("device"): vol.In(devices_list),
                    }
                ),
                errors=errors,
            )

        # User selected a device
        selected = user_input["device"]

        if selected == "manual":
            return await self.async_step_manual()

        # Check if user selected "All devices"
        if selected == "__all__":
            # Create multi-device entry using combined BLE MACs for uniqueness
            all_ble_macs = sorted(
                {
                    d["ble_mac"]
                    for d in self._discovered_devices
                    if d.get("ble_mac")
                }
            )
            unique_id = "_".join(all_ble_macs)

            if not unique_id:
                _LOGGER.debug("No BLE MACs found during multi-device selection; skipping duplicate guard")
            else:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"Marstek System ({len(self._discovered_devices)} batteries)",
                data={
                    "devices": [
                        {
                            CONF_HOST: d["ip"],
                            CONF_PORT: DEFAULT_PORT,
                            "wifi_mac": d.get("wifi_mac"),
                            "ble_mac": d.get("ble_mac"),
                            "device": d["name"],
                            "firmware": d["firmware"],
                        }
                        for d in self._discovered_devices
                    ],
                },
            )

        # Find selected device (single device mode)
        device = next(
            (d for d in self._discovered_devices if d["mac"] == selected), None
        )

        if not device:
            errors["base"] = "device_not_found"
            return self.async_show_form(step_id="discovery", errors=errors)

        # Check if already configured
        unique_id = device.get("ble_mac")
        if not unique_id:
            _LOGGER.debug("Device %s missing BLE MAC; continuing without duplicate guard", device.get("ip"))
        else:
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

        # Create entry for single device
        return self.async_create_entry(
            title=f"{device['name']} ({device['ip']})",
            data={
                CONF_HOST: device["ip"],
                CONF_PORT: DEFAULT_PORT,
                "wifi_mac": device.get("wifi_mac"),
                "ble_mac": device.get("ble_mac"),
                "device": device["name"],
                "firmware": device["firmware"],
            },
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual IP entry."""
        errors = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)

                # Check if already configured
                unique_id = info.get("ble_mac")
                if not unique_id:
                    _LOGGER.debug("Manual setup for host %s missing BLE MAC; skipping duplicate guard", user_input.get(CONF_HOST))
                else:
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=info["title"],
                    data={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: user_input[CONF_PORT],
                        "wifi_mac": info["wifi_mac"],
                        "ble_mac": info["ble_mac"],
                        "device": info["device"],
                        "firmware": info["firmware"],
                    },
                )

            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="manual",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_dhcp(
        self, discovery_info: dhcp.DhcpServiceInfo
    ) -> FlowResult:
        """Handle DHCP discovery."""
        # Extract info from DHCP discovery
        host = discovery_info.ip
        mac = discovery_info.macaddress

        # Validate the device using ephemeral port to avoid conflicts
        try:
            info = await validate_input(
                self.hass,
                {CONF_HOST: host, CONF_PORT: DEFAULT_PORT},
                use_ephemeral_port=True
            )

            # Check if already configured
            unique_id = info.get("ble_mac")
            if not unique_id:
                _LOGGER.debug("DHCP discovery for host %s missing BLE MAC; skipping duplicate guard", host)
            else:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})

            # Store discovery info for confirmation
            self.context["title_placeholders"] = {"name": info["title"]}
            self.context["device_info"] = {
                CONF_HOST: host,
                CONF_PORT: DEFAULT_PORT,
                "wifi_mac": info["wifi_mac"],
                "ble_mac": info["ble_mac"],
                "device": info["device"],
                "firmware": info["firmware"],
            }

            return await self.async_step_discovery_confirm()

        except CannotConnect:
            return self.async_abort(reason="cannot_connect")
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception during DHCP discovery")
            return self.async_abort(reason="unknown")

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm discovery."""
        if user_input is not None:
            device_info = self.context["device_info"]
            return self.async_create_entry(
                title=self.context["title_placeholders"]["name"],
                data=device_info,
            )

        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders=self.context.get("title_placeholders"),
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlow()


class OptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Marstek Local API."""

    def __init__(self) -> None:
        """Initialise the options flow."""
        self._discovered_devices: list[dict[str, Any]] = []

    @property
    def _devices(self) -> list[dict[str, Any]]:
        """Return the current list of configured devices."""
        return list(self.config_entry.data.get("devices", []))

    @_devices.setter
    def _devices(self, updated_devices: list[dict[str, Any]]) -> None:
        """Set the current list of configured devices."""
        new_data = {**self.config_entry.data, "devices": updated_devices}
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data=new_data,
        )


    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Entry-point for options flow; present available actions."""
        actions: dict[str, str] = {
            "scan_interval": "Adjust update interval",
        }

        if self._devices:
            actions.update(
                {
                    "rename_device": "Rename a device",
                    "remove_device": "Remove a device",
                    "add_device": "Add a device",
                }
            )

        if user_input is not None:
            action = user_input["action"]
            if action == "scan_interval":
                return await self.async_step_scan_interval()
            if action == "rename_device":
                return await self.async_step_rename_device()
            if action == "remove_device":
                return await self.async_step_remove_device()
            if action == "add_device":
                return await self.async_step_add_device()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("action"): vol.In(actions),
                }
            ),
        )

    async def async_step_scan_interval(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Adjust polling interval."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="scan_interval",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "scan_interval",
                        default=self.config_entry.options.get(
                            "scan_interval", DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=15, max=900)),
                }
            ),
        )

    async def async_step_rename_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Rename an existing device in a multi-device configuration."""
        if not self._devices:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}

        device_options = {
            idx: f"{device.get('device', f'Device {idx + 1}')} ({device.get('host')}:{device.get('port')})"
            for idx, device in enumerate(self._devices)
        }

        if user_input is not None:
            device_index = user_input["device"]
            new_name: str = user_input["name"].strip()

            if not new_name:
                errors["name"] = "invalid_name"
            elif device_index >= len(self._devices):
                errors["base"] = "device_not_found"
            else:
                current_device = self._devices[device_index]
                if current_device.get("device") == new_name:
                    return self.async_create_entry(title="", data={})

                updated_devices = list(self._devices)
                updated_device = dict(updated_devices[device_index])
                updated_device["device"] = new_name
                updated_devices[device_index] = updated_device
                self._devices = updated_devices
                return self.async_create_entry(title="", data={})

        default_index = 0
        default_name = (
            self._devices[default_index].get("device", f"Device {default_index + 1}")
            if self._devices
            else ""
        )

        return self.async_show_form(
            step_id="rename_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device", default=default_index): vol.In(device_options),
                    vol.Required("name", default=default_name): cv.string,
                }
            ),
            errors=errors,
        )

    async def async_step_remove_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Remove a device from a multi-device configuration."""
        if not self._devices:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}

        if len(self._devices) <= 1:
            errors["base"] = "cannot_remove_last_device"

        device_options = {
            idx: f"{device.get('device', f'Device {idx + 1}')} ({device.get('host')}:{device.get('port')})"
            for idx, device in enumerate(self._devices)
        }

        if user_input is not None:
            if errors:
                return self.async_show_form(
                    step_id="remove_device",
                    data_schema=vol.Schema(
                        {
                            vol.Required("device"): vol.In(device_options),
                        }
                    ),
                    errors=errors,
                )

            device_index = user_input["device"]
            if device_index >= len(self._devices):
                errors["base"] = "device_not_found"
            else:
                updated_devices = [
                    device
                    for idx, device in enumerate(self._devices)
                    if idx != device_index
                ]
                if not updated_devices:
                    errors["base"] = "cannot_remove_last_device"
                else:
                    self._devices = updated_devices
                    return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="remove_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): vol.In(device_options),
                }
            ),
            errors=errors,
        )

    async def async_step_add_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Discover and add a new device to the configuration."""
        if not self._devices:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}

        if user_input is None:
            await self._async_discover_devices()

        existing_macs = {
            device.get("ble_mac") or device.get("wifi_mac")
            for device in self._devices
            if device.get("ble_mac") or device.get("wifi_mac")
        }
        discovered_options: dict[str, str] = {}

        for device in self._discovered_devices:
            mac = device.get("ble_mac") or device.get("wifi_mac") or device.get("mac")
            if mac and mac in existing_macs:
                continue
            discovered_options[device["mac"]] = f"{device['name']} ({device['ip']})"

        discovered_options["manual"] = "Manual IP entry"

        if user_input is not None:
            selection = user_input["device"]

            if selection == "manual":
                return await self.async_step_add_device_manual()

            device = next(
                (item for item in self._discovered_devices if item["mac"] == selection),
                None,
            )
            if not device:
                errors["base"] = "device_not_found"
            else:
                if (
                    device.get("ble_mac") in existing_macs
                    or device.get("wifi_mac") in existing_macs
                ):
                    errors["base"] = "device_already_configured"
                else:
                    updated_devices = list(self._devices)
                    updated_devices.append(
                        {
                            CONF_HOST: device["ip"],
                            CONF_PORT: DEFAULT_PORT,
                            "wifi_mac": device.get("wifi_mac"),
                            "ble_mac": device.get("ble_mac"),
                            "device": device["name"],
                            "firmware": device["firmware"],
                        }
                    )
                    self._devices = updated_devices
                    return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="add_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): vol.In(discovered_options),
                }
            ),
            errors=errors,
        )

    async def async_step_add_device_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a device via manual IP entry."""
        if not self._devices:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(
                    self.hass,
                    user_input,
                )

                mac = info.get("ble_mac") or info.get("wifi_mac")
                if any(
                    mac
                    and mac
                    == (device.get("ble_mac") or device.get("wifi_mac"))
                    for device in self._devices
                ):
                    errors["base"] = "device_already_configured"
                else:
                    updated_devices = list(self._devices)
                    updated_devices.append(
                        {
                            CONF_HOST: user_input[CONF_HOST],
                            CONF_PORT: user_input.get(CONF_PORT, DEFAULT_PORT),
                            "wifi_mac": info.get("wifi_mac"),
                            "ble_mac": info.get("ble_mac"),
                            "device": info.get("device"),
                            "firmware": info.get("firmware"),
                        }
                    )
                    self._devices = updated_devices
                    return self.async_create_entry(title="", data={})

            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during manual device addition")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="add_device_manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): cv.string,
                    vol.Optional(
                        CONF_PORT, default=DEFAULT_PORT
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                }
            ),
            errors=errors,
        )

    async def _async_discover_devices(self) -> None:
        """Discover devices using the same strategy as the config flow."""
        paused_clients: list[MarstekUDPClient] = []
        self._discovered_devices = []

        if DOMAIN in self.hass.data:
            for _entry_id, entry_data in self.hass.data[DOMAIN].items():
                coordinator = entry_data.get(DATA_COORDINATOR)
                if not coordinator:
                    continue

                if hasattr(coordinator, "device_coordinators"):
                    for device_coordinator in coordinator.device_coordinators.values():
                        if device_coordinator.api:
                            await device_coordinator.api.disconnect()
                            paused_clients.append(device_coordinator.api)
                elif hasattr(coordinator, "api") and coordinator.api:
                    await coordinator.api.disconnect()
                    paused_clients.append(coordinator.api)

        await asyncio.sleep(1)

        api = MarstekUDPClient(self.hass, port=DEFAULT_PORT, remote_port=DEFAULT_PORT)
        try:
            await api.connect()
            self._discovered_devices = await api.discover_devices()
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Discovery failed during options flow: %s", err, exc_info=True)
        finally:
            try:
                await api.disconnect()
            except Exception:  # pylint: disable=broad-except
                pass

        await asyncio.sleep(1)

        for client in paused_clients:
            try:
                await client.connect()
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.warning("Failed to resume client during options flow: %s", err)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
