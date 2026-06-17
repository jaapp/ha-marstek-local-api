#!/usr/bin/env python3
"""Standalone test tool for Marstek Local API integration.

This utility reuses the integration's real modules to exercise discovery,
diagnostics, and control flows (manual schedules, passive mode, operating
mode changes) without needing a Home Assistant instance.

Usage examples:
  python3 test_tool.py discover
  python3 test_tool.py discover --ip 192.168.7.101
  python3 test_tool.py set-test-schedules --ip 192.168.7.101
  python3 test_tool.py clear-schedules
  python3 test_tool.py set-passive --power -2000 --duration 3600
  python3 test_tool.py set-mode auto --ip 192.168.7.101
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable


def load_module_from_file(module_name: str, file_path: Path):
    """Load a Python module directly from a file path."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Get paths to integration modules
integration_path = Path(__file__).parent.parent / "custom_components" / "marstek_local_api"
if not integration_path.exists():
    integration_path = Path("/config/custom_components/marstek_local_api")

if not integration_path.exists():
    print("ERROR: Cannot find integration at:")
    print(f"  - {Path(__file__).parent.parent / 'custom_components' / 'marstek_local_api'}")
    print("  - /config/custom_components/marstek_local_api")
    sys.exit(1)

# Create a fake package structure to allow relative imports
package_name = "custom_components.marstek_local_api"

custom_components_pkg = sys.modules.get("custom_components")
if custom_components_pkg is None:
    custom_components_pkg = type(sys)("custom_components")
    custom_components_pkg.__path__ = [str(integration_path.parent)]
    sys.modules["custom_components"] = custom_components_pkg

marstek_pkg = sys.modules.get(package_name)
if marstek_pkg is None:
    marstek_pkg = type(sys)(package_name)
    marstek_pkg.__path__ = [str(integration_path)]
    sys.modules[package_name] = marstek_pkg


# Mock Home Assistant modules that are imported by the integration
class MockHomeAssistant:  # pragma: no cover - simple mock
    """Mock HomeAssistant class."""
    pass


class MockDataUpdateCoordinator:  # pragma: no cover - simple mock
    """Mock DataUpdateCoordinator class."""

    def __init__(self, hass, logger, name, update_interval):
        pass


class MockUpdateFailed(Exception):
    """Mock UpdateFailed exception."""


class MockSensorDeviceClass:  # pragma: no cover - simple mock
    """Mock SensorDeviceClass values to satisfy imports."""

    BATTERY = "battery"
    TEMPERATURE = "temperature"
    ENERGY_STORAGE = "energy_storage"
    POWER = "power"
    ENERGY = "energy"
    SIGNAL_STRENGTH = "signal_strength"
    DURATION = "duration"
    VOLTAGE = "voltage"
    CURRENT = "current"


class MockSensorEntity:
    """Mock SensorEntity class."""
    pass


@dataclass
class MockSensorEntityDescription:
    """Mock SensorEntityDescription class."""

    key: str
    name: str | None = None
    native_unit_of_measurement: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    value_fn: Callable[[dict], Any] | None = None
    available_fn: Callable[[dict], bool] | None = None


class MockSensorStateClass:
    """Mock SensorStateClass."""

    MEASUREMENT = "measurement"
    TOTAL_INCREASING = "total_increasing"


class MockConfigEntry:
    """Mock ConfigEntry class."""
    pass


class MockDeviceInfo:
    """Mock DeviceInfo class."""
    pass


class MockCoordinatorEntity:
    """Mock CoordinatorEntity class."""
    pass


class MockAddEntitiesCallback:
    """Mock AddEntitiesCallback class."""
    pass


# Register mock modules
homeassistant_core = type(sys)("homeassistant.core")
homeassistant_core.HomeAssistant = MockHomeAssistant

homeassistant_helpers_update_coordinator = type(sys)("homeassistant.helpers.update_coordinator")
homeassistant_helpers_update_coordinator.DataUpdateCoordinator = MockDataUpdateCoordinator
homeassistant_helpers_update_coordinator.UpdateFailed = MockUpdateFailed
homeassistant_helpers_update_coordinator.CoordinatorEntity = MockCoordinatorEntity

homeassistant_components_sensor = type(sys)("homeassistant.components.sensor")
homeassistant_components_sensor.SensorDeviceClass = MockSensorDeviceClass
homeassistant_components_sensor.SensorEntity = MockSensorEntity
homeassistant_components_sensor.SensorEntityDescription = MockSensorEntityDescription
homeassistant_components_sensor.SensorStateClass = MockSensorStateClass

homeassistant_config_entries = type(sys)("homeassistant.config_entries")
homeassistant_config_entries.ConfigEntry = MockConfigEntry

homeassistant_const = type(sys)("homeassistant.const")
homeassistant_const.PERCENTAGE = "%"
homeassistant_const.UnitOfElectricCurrent = type("UnitOfElectricCurrent", (), {"AMPERE": "A"})()
homeassistant_const.UnitOfElectricPotential = type("UnitOfElectricPotential", (), {"VOLT": "V"})()
homeassistant_const.UnitOfEnergy = type("UnitOfEnergy", (), {"WATT_HOUR": "Wh", "KILO_WATT_HOUR": "kWh"})()
homeassistant_const.UnitOfPower = type("UnitOfPower", (), {"WATT": "W"})()
homeassistant_const.UnitOfTemperature = type("UnitOfTemperature", (), {"CELSIUS": "°C"})()
homeassistant_const.UnitOfTime = type("UnitOfTime", (), {"SECONDS": "s"})()

homeassistant_helpers_entity = type(sys)("homeassistant.helpers.entity")
homeassistant_helpers_entity.DeviceInfo = MockDeviceInfo

homeassistant_helpers_entity_platform = type(sys)("homeassistant.helpers.entity_platform")
homeassistant_helpers_entity_platform.AddEntitiesCallback = MockAddEntitiesCallback

sys.modules["homeassistant"] = type(sys)("homeassistant")
sys.modules["homeassistant.core"] = homeassistant_core
sys.modules["homeassistant.helpers"] = type(sys)("homeassistant.helpers")
sys.modules["homeassistant.helpers.update_coordinator"] = homeassistant_helpers_update_coordinator
sys.modules["homeassistant.components"] = type(sys)("homeassistant.components")
sys.modules["homeassistant.components.sensor"] = homeassistant_components_sensor
sys.modules["homeassistant.config_entries"] = homeassistant_config_entries
sys.modules["homeassistant.const"] = homeassistant_const
sys.modules["homeassistant.helpers.entity"] = homeassistant_helpers_entity
sys.modules["homeassistant.helpers.entity_platform"] = homeassistant_helpers_entity_platform

# Load integration modules in dependency order
const = load_module_from_file(f"{package_name}.const", integration_path / "const.py")
api_module = load_module_from_file(f"{package_name}.api", integration_path / "api.py")
coordinator_module = load_module_from_file(f"{package_name}.coordinator", integration_path / "coordinator.py")
sensor_module = load_module_from_file(f"{package_name}.sensor", integration_path / "sensor.py")

# Extract integration components we need
MarstekUDPClient = api_module.MarstekUDPClient
DEFAULT_PORT = const.DEFAULT_PORT
DEVICE_MODEL_VENUS_D = const.DEVICE_MODEL_VENUS_D
SENSOR_TYPES = sensor_module.SENSOR_TYPES
MODE_AUTO = const.MODE_AUTO
MODE_AI = const.MODE_AI
MODE_MANUAL = const.MODE_MANUAL
MODE_PASSIVE = const.MODE_PASSIVE
WEEKDAY_MAP = const.WEEKDAY_MAP
MAX_SCHEDULE_SLOTS = const.MAX_SCHEDULE_SLOTS

class MockHass:
    """Mock Home Assistant object for testing."""

    def __init__(self):
        self.data = {}


def format_value(value: Any, unit: str = "") -> str:
    """Format value with unit for display."""
    if value is None:
        return "N/A"
    if isinstance(value, (int, float)):
        return f"{value}{unit}"
    return str(value)


def _days_to_week_set(days: list[str]) -> int:
    """Convert list of day names to week_set bitmap."""
    return sum(WEEKDAY_MAP[day] for day in days)


async def _select_target_device(api: MarstekUDPClient, target_ip: str | None, action: str):
    """Resolve the device to operate on, returning device metadata."""
    if target_ip:
        print(f"{action}: Using specified device {target_ip}")
        api.host = target_ip
        return {"name": target_ip, "ip": target_ip}

    print("Discovering devices...")
    devices = await api.discover_devices(timeout=9)
    if not devices:
        print("❌ No devices found!")
        return None

    device = devices[0]
    api.host = device["ip"]
    print(f"Using first discovered device: {device['name']} ({device['ip']})")
    return device


async def _with_api_client(target_ip: str | None, action: str, coro):
    """Helper to connect, select device, run coroutine, and disconnect."""
    hass = MockHass()
    api = MarstekUDPClient(hass, port=DEFAULT_PORT)

    try:
        await api.connect()
        device = await _select_target_device(api, target_ip, action)
        if not device:
            return
        await coro(api, device)
    except PermissionError as err:
        print(f"❌ Unable to open UDP socket on port {DEFAULT_PORT}: {err}")
    except Exception as err:  # pragma: no cover - diagnostic output
        print(f"❌ Error: {err}")
        import traceback
        traceback.print_exc()
    finally:
        await api.disconnect()


async def run_set_test_schedules(target_ip: str | None) -> None:
    """Configure two sample manual-mode schedules."""

    async def _apply(api: MarstekUDPClient, device: dict[str, Any]) -> None:
        print("=" * 80)
        print("Marstek Local API - Apply Test Schedules")
        print("=" * 80)
        print(f"Target device: {device['name']} ({device['ip']})")
        print()

        schedules = [
            {
                "time_num": 0,
                "start_time": "08:00",
                "end_time": "16:00",
                "week_set": _days_to_week_set(["mon", "tue", "wed", "thu", "fri"]),
                "power": -2000,  # Negative = charge
                "enable": 1,
            },
            {
                "time_num": 1,
                "start_time": "18:00",
                "end_time": "22:00",
                "week_set": _days_to_week_set(["mon", "tue", "wed", "thu", "fri"]),
                "power": 800,  # Positive = discharge
                "enable": 1,
            },
        ]

        print("Applying schedules:")
        print("  Slot 0: 08:00-16:00 Mon-Fri, charge limit 2000W")
        print("  Slot 1: 18:00-22:00 Mon-Fri, discharge limit 800W")
        print()

        failed_slots: list[int] = []

        for schedule in schedules:
            config = {"mode": MODE_MANUAL, "manual_cfg": schedule}
            try:
                success = await api.set_es_mode(config)
                if success:
                    print(f"  ✅ Schedule slot {schedule['time_num']} applied")
                else:
                    print(f"  ❌ Device rejected slot {schedule['time_num']}")
                    failed_slots.append(schedule["time_num"])
            except Exception as err:
                print(f"  ❌ Error applying slot {schedule['time_num']}: {err}")
                failed_slots.append(schedule["time_num"])

            await asyncio.sleep(0.5)

        if failed_slots:
            print()
            print(f"❌ Failed to set slots: {failed_slots}")
        else:
            print()
            print("✅ Successfully set all test schedules!")

        print()
        print("=" * 80)

    await _with_api_client(target_ip, "Set test schedules", _apply)


async def run_clear_schedules(target_ip: str | None) -> None:
    """Disable all manual-mode schedules."""

    async def _clear(api: MarstekUDPClient, device: dict[str, Any]) -> None:
        print("=" * 80)
        print("Marstek Local API - Clear All Manual Schedules")
        print("=" * 80)
        print(f"Target device: {device['name']} ({device['ip']})")
        print()

        failed_slots: list[int] = []

        for slot in range(MAX_SCHEDULE_SLOTS):
            config = {
                "mode": MODE_MANUAL,
                "manual_cfg": {
                    "time_num": slot,
                    "start_time": "00:00",
                    "end_time": "00:00",
                    "week_set": 0,
                    "power": 0,
                    "enable": 0,
                },
            }

            try:
                success = await api.set_es_mode(config)
                if success:
                    print(f"  ✅ Cleared slot {slot}")
                else:
                    print(f"  ❌ Device rejected clearing slot {slot}")
                    failed_slots.append(slot)
            except Exception as err:
                print(f"  ❌ Error clearing slot {slot}: {err}")
                failed_slots.append(slot)

            await asyncio.sleep(0.3)

        if failed_slots:
            print()
            print(f"❌ Failed to clear slots: {failed_slots}")
        else:
            print()
            print("✅ Successfully cleared all schedules!")

        print()
        print("=" * 80)

    await _with_api_client(target_ip, "Clear schedules", _clear)


async def run_set_passive_mode(target_ip: str | None, power: int, duration: int) -> None:
    """Set passive mode with the requested power and duration."""

    async def _apply(api: MarstekUDPClient, device: dict[str, Any]) -> None:
        print("=" * 80)
        print("Marstek Local API - Set Passive Mode")
        print("=" * 80)
        print(f"Target device: {device['name']} ({device['ip']})")
        print(f"Requested power:    {power} W")
        print(f"Requested duration: {duration} s")
        print()

        config = {
            "mode": MODE_PASSIVE,
            "passive_cfg": {"power": power, "cd_time": duration},
        }

        try:
            success = await api.set_es_mode(config)
            if success:
                print("✅ Passive mode command accepted by device")
            else:
                print("❌ Device rejected passive mode command")
        except Exception as err:
            print(f"❌ Error setting passive mode: {err}")

        print()
        print("=" * 80)

    await _with_api_client(target_ip, "Set passive mode", _apply)


MODE_CONFIG_MAP = {
    "auto": {"mode": MODE_AUTO, "auto_cfg": {"enable": 1}},
    "ai": {"mode": MODE_AI, "ai_cfg": {"enable": 1}},
    "manual": {
        "mode": MODE_MANUAL,
        "manual_cfg": {
            "time_num": 9,
            "start_time": "00:00",
            "end_time": "00:00",
            "week_set": 0,
            "power": 0,
            "enable": 0,
        },
    },
}


async def run_set_operating_mode(target_ip: str | None, mode_key: str) -> None:
    """Switch the operating mode."""

    mode_label = mode_key.capitalize()
    config_template = MODE_CONFIG_MAP[mode_key]

    async def _apply(api: MarstekUDPClient, device: dict[str, Any]) -> None:
        print("=" * 80)
        print("Marstek Local API - Set Operating Mode")
        print("=" * 80)
        print(f"Target device: {device['name']} ({device['ip']})")
        print(f"Requested mode:  {mode_label}")
        print()

        try:
            success = await api.set_es_mode(json.loads(json.dumps(config_template)))
            if success:
                print(f"✅ Operating mode switched to {mode_label}")
            else:
                print(f"❌ Device rejected operating mode change to {mode_label}")
        except Exception as err:
            print(f"❌ Error setting operating mode: {err}")

        print()
        print("=" * 80)

    await _with_api_client(target_ip, f"Set mode {mode_label}", _apply)


async def discover_and_test(target_ip: str | None) -> None:
    """Discover devices and exercise API methods."""
    hass = MockHass()
    api = MarstekUDPClient(hass, port=DEFAULT_PORT)

    try:
        await api.connect()

        devices = None
        if target_ip:
            api.host = target_ip
            devices = [{"name": target_ip, "ip": target_ip, "mac": None, "firmware": "Unknown"}]
        else:
            print("=" * 80)
            print("Marstek Local API Integration - Standalone Test")
            print("=" * 80)
            print()
            print("Step 1: Discovering devices on network...")
            print(f"Broadcasting on port {DEFAULT_PORT}...")
            print()
            devices = await api.discover_devices(timeout=9)

        if not devices:
            print("❌ No devices found!")
            print()
            print("Troubleshooting:")
            print("  1. Ensure Marstek device is powered on")
            print("  2. Check Local API is enabled in Marstek app")
            print("  3. Verify device and computer are on same network")
            print("  4. Check firewall allows UDP port 30000")
            return

        if not target_ip:
            print(f"✅ Found {len(devices)} device(s):")
            print()
            for i, device in enumerate(devices, 1):
                print(f"Device {i}:")
                print(f"  Model:       {device['name']}")
                print(f"  IP Address:  {device['ip']}")
                print(f"  MAC:         {device['mac']}")
                print(f"  Firmware:    v{device['firmware']}")
                print()

        for device_idx, device in enumerate(devices, 1):
            print("=" * 80)
            print(f"Testing Device {device_idx}: {device['name']} ({device['ip']})")
            print("=" * 80)
            print()

            api.host = device["ip"]
            firmware = device.get("firmware", "Unknown")
            is_venus_d = device.get("name") == DEVICE_MODEL_VENUS_D

            await asyncio.sleep(1.0)
            print("📋 Device Information")
            print("-" * 80)
            device_info = await api.get_device_info()
            if device_info:
                print(f"  Device Model:      {device_info.get('device', 'N/A')}")
                print(f"  Firmware Version:  {device_info.get('ver', 'N/A')}")
                print(f"  BLE MAC:           {device_info.get('ble_mac', 'N/A')}")
                print(f"  WiFi MAC:          {device_info.get('wifi_mac', 'N/A')}")
                print(f"  WiFi Name:         {device_info.get('wifi_name', 'N/A')}")
                print(f"  IP Address:        {device_info.get('ip', 'N/A')}")
            else:
                print("  ⚠️  Failed to get device info")
            print()

            await asyncio.sleep(1.0)
            print("📶 WiFi Status")
            print("-" * 80)
            wifi_status = await api.get_wifi_status()
            if wifi_status:
                print(f"  SSID:              {wifi_status.get('ssid', 'N/A')}")
                print(f"  Signal Strength:   {format_value(wifi_status.get('rssi'), ' dBm')}")
                print(f"  IP Address:        {wifi_status.get('sta_ip', 'N/A')}")
                print(f"  Gateway:           {wifi_status.get('sta_gate', 'N/A')}")
                print(f"  Subnet Mask:       {wifi_status.get('sta_mask', 'N/A')}")
                print(f"  DNS Server:        {wifi_status.get('sta_dns', 'N/A')}")
            else:
                print("  ⚠️  Failed to get WiFi status")
            print()

            await asyncio.sleep(1.0)
            print("🔵 Bluetooth Status")
            print("-" * 80)
            ble_status = await api.get_ble_status()
            if ble_status:
                print(f"  State:             {ble_status.get('state', 'N/A')}")
                print(f"  MAC Address:       {ble_status.get('ble_mac', 'N/A')}")
            else:
                print("  ⚠️  Failed to get Bluetooth status")
            print()

            coordinator = coordinator_module.MarstekDataUpdateCoordinator(
                hass=hass,
                api=api,
                device_name=device["name"],
                firmware_version=firmware,
                device_model=device["name"],
                scan_interval=15,
            )

            await asyncio.sleep(1.0)
            print("🔋 Battery Status")
            print("-" * 80)
            battery_status = await api.get_battery_status()
            if battery_status:
                bat_temp = coordinator.compatibility.scale_value(battery_status.get("bat_temp"), "bat_temp")
                bat_capacity = coordinator.compatibility.scale_value(battery_status.get("bat_capacity"), "bat_capacity")

                soc = battery_status.get("soc")
                rated_capacity = battery_status.get("rated_capacity")

                print(f"  State of Charge:        {format_value(soc, '%')}")
                print(f"  Temperature:            {format_value(bat_temp, '°C')}")
                print(f"  Remaining Capacity:     {format_value(bat_capacity, ' Wh')}")
                print(f"  Rated Capacity:         {format_value(rated_capacity, ' Wh')}")
                print(f"  Charging Enabled:       {battery_status.get('charg_flag', False)}")
                print(f"  Discharging Enabled:    {battery_status.get('dischrg_flag', False)}")
            else:
                print("  ⚠️  Failed to get battery status")
            print()

            await asyncio.sleep(1.0)
            print("⚡ Energy System Status")
            print("-" * 80)
            es_status = await api.get_es_status()
            if es_status:
                bat_power = coordinator.compatibility.scale_value(es_status.get("bat_power"), "bat_power")
                total_grid_input = coordinator.compatibility.scale_value(
                    es_status.get("total_grid_input_energy"), "total_grid_input_energy"
                )
                total_grid_output = coordinator.compatibility.scale_value(
                    es_status.get("total_grid_output_energy"), "total_grid_output_energy"
                )
                total_load = coordinator.compatibility.scale_value(es_status.get("total_load_energy"), "total_load_energy")

                data = {
                    "es": {
                        **es_status,
                        "bat_power": bat_power,
                        "total_grid_input_energy": total_grid_input,
                        "total_grid_output_energy": total_grid_output,
                        "total_load_energy": total_load,
                    }
                }
                if battery_status:
                    data["battery"] = battery_status

                sensor_map = {desc.key: desc for desc in SENSOR_TYPES}
                bat_power_in = sensor_map["battery_power_in"].value_fn(data)
                bat_power_out = sensor_map["battery_power_out"].value_fn(data)
                bat_state = sensor_map["battery_state"].value_fn(data)
                available_capacity = (
                    sensor_map["battery_available_capacity"].value_fn(data) if battery_status else None
                )

                print(f"  Battery SOC:            {format_value(es_status.get('bat_soc'), '%')}")
                print(f"  Battery Capacity:       {format_value(es_status.get('bat_cap'), ' Wh')}")
                print(f"  Battery Power:          {format_value(bat_power, ' W')}")
                print(f"  Battery State:          {bat_state}")
                print(f"  Battery Power In:       {format_value(bat_power_in, ' W')}")
                print(f"  Battery Power Out:      {format_value(bat_power_out, ' W')}")
                print(f"  Available Capacity:     {format_value(available_capacity, ' Wh')}")
                print(f"  Grid Power:             {format_value(es_status.get('ongrid_power'), ' W')}")
                print(f"  Off-Grid Power:         {format_value(es_status.get('offgrid_power'), ' W')}")
                print(f"  Solar Power:            {format_value(es_status.get('pv_power'), ' W')}")
                print(f"  Total Solar Energy:     {format_value(es_status.get('total_pv_energy'), ' Wh')}")
                print(f"  Total Grid Import:      {format_value(total_grid_input, ' Wh')}")
                print(f"  Total Grid Export:      {format_value(total_grid_output, ' Wh')}")
                print(f"  Total Load Energy:      {format_value(total_load, ' Wh')}")
            else:
                print("  ⚠️  Failed to get energy system status")
            print()

            await asyncio.sleep(1.0)
            print("⚙️  Operating Mode")
            print("-" * 80)
            mode_status = await api.get_es_mode()
            if mode_status:
                print(f"  Current Mode:           {mode_status.get('mode', 'N/A')}")
                print(f"  Grid Power:             {format_value(mode_status.get('ongrid_power'), ' W')}")
                print(f"  Off-Grid Power:         {format_value(mode_status.get('offgrid_power'), ' W')}")
                print(f"  Battery SOC:            {format_value(mode_status.get('bat_soc'), '%')}")
            else:
                print("  ⚠️  Failed to get operating mode")
            print()

            await asyncio.sleep(1.0)
            print("📊 Energy Meter (CT) Status")
            print("-" * 80)
            em_status = await api.get_em_status()
            if em_status:
                ct_state = em_status.get("ct_state")
                ct_connected = ct_state == 1
                print(f"  CT Connected:           {ct_connected}")
                if ct_connected:
                    print(f"  Phase A Power:          {format_value(em_status.get('a_power'), ' W')}")
                    print(f"  Phase B Power:          {format_value(em_status.get('b_power'), ' W')}")
                    print(f"  Phase C Power:          {format_value(em_status.get('c_power'), ' W')}")
                    print(f"  Total Power:            {format_value(em_status.get('total_power'), ' W')}")
                    print(f"  Input Energy:           {format_value(em_status.get('input_energy'), ' dWh')}")
                    print(f"  Output Energy:          {format_value(em_status.get('output_energy'), ' dWh')}")
                else:
                    print("  (No CT connected)")
            else:
                print("  ⚠️  Failed to get energy meter status")
            print()

            if is_venus_d:
                await asyncio.sleep(1.0)
                print("☀️  Solar PV Status (Venus D)")
                print("-" * 80)
                pv_status = await api.get_pv_status()
                if pv_status:
                    print(f"  PV Power:               {format_value(pv_status.get('pv_power'), ' W')}")
                    print(f"  PV Voltage:             {format_value(pv_status.get('pv_voltage'), ' V')}")
                    print(f"  PV Current:             {format_value(pv_status.get('pv_current'), ' A')}")
                else:
                    print("  ⚠️  Failed to get PV status")
                print()

        print("=" * 80)
        print("Test Complete!")
        print("=" * 80)
    finally:
        await api.disconnect()


def build_parser() -> argparse.ArgumentParser:
    """Construct argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        description="Control and diagnostics tool for Marstek Local API devices",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--ip",
        help="Target device IP address. When omitted, the first discovered device is used.",
    )
    subparsers = parser.add_subparsers(dest="command")
    parser.set_defaults(command="discover")

    discover_parser = subparsers.add_parser("discover", help="Discover devices and print diagnostics")
    discover_parser.set_defaults(command="discover")

    set_sched_parser = subparsers.add_parser(
        "set-test-schedules",
        help="Apply two sample manual schedules (charge/discharge windows)",
    )
    set_sched_parser.set_defaults(command="set_test_schedules")

    clear_sched_parser = subparsers.add_parser(
        "clear-schedules",
        help="Disable all manual schedule slots on the target device",
    )
    clear_sched_parser.set_defaults(command="clear_schedules")

    passive_parser = subparsers.add_parser(
        "set-passive",
        help="Switch to passive mode with the given power and duration",
    )
    passive_parser.add_argument("--power", type=int, default=-2000, help="Passive mode power in watts")
    passive_parser.add_argument(
        "--duration",
        type=int,
        default=3600,
        help="Passive mode duration in seconds (1-86400)",
    )
    passive_parser.set_defaults(command="set_passive")

    mode_parser = subparsers.add_parser(
        "set-mode",
        help="Change the operating mode to Auto/AI/Manual",
    )
    mode_parser.add_argument("mode", choices=sorted(MODE_CONFIG_MAP.keys()), help="Target operating mode")
    mode_parser.set_defaults(command="set_mode")

    return parser


def main() -> None:
    """Parse CLI arguments and execute the requested command."""
    parser = build_parser()
    args = parser.parse_args()
    command = args.command or "discover"
    target_ip = getattr(args, "ip", None)

    try:
        if command == "set_test_schedules":
            asyncio.run(run_set_test_schedules(target_ip))
        elif command == "clear_schedules":
            asyncio.run(run_clear_schedules(target_ip))
        elif command == "set_passive":
            asyncio.run(run_set_passive_mode(target_ip, args.power, args.duration))
        elif command == "set_mode":
            asyncio.run(run_set_operating_mode(target_ip, args.mode))
        else:  # "discover"
            asyncio.run(discover_and_test(target_ip))
    except KeyboardInterrupt:
        print("\n\nOperation interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
