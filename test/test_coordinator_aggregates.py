"""Tests for multi-device aggregate energy counters."""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
import sys
from types import SimpleNamespace


test_tool_path = Path(__file__).with_name("test_tool.py")
test_tool_spec = importlib.util.spec_from_file_location(
    "marstek_test_tool",
    test_tool_path,
)
assert test_tool_spec is not None
assert test_tool_spec.loader is not None
test_tool_module = importlib.util.module_from_spec(test_tool_spec)
sys.modules[test_tool_spec.name] = test_tool_module
test_tool_spec.loader.exec_module(test_tool_module)

coordinator_module = test_tool_module.coordinator_module
MarstekMultiDeviceCoordinator = coordinator_module.MarstekMultiDeviceCoordinator


def _coordinator(devices: dict[str, dict]) -> MarstekMultiDeviceCoordinator:
    """Create a coordinator with device data without Home Assistant setup."""
    coordinator = object.__new__(MarstekMultiDeviceCoordinator)
    coordinator.device_coordinators = {
        mac: SimpleNamespace(data=data) for mac, data in devices.items()
    }
    coordinator._unavailable_energy_counters = {}
    return coordinator


def _device(
    grid_input: int | float | None = 0,
    grid_output: int | float | None = 0,
) -> dict:
    """Build minimal device data for aggregate calculations."""
    es = {
        "total_grid_input_energy": grid_input,
        "total_grid_output_energy": grid_output,
    }
    return {"es": es}


def test_grid_energy_counters_sum_valid_values() -> None:
    """Aggregate valid lifetime counters from all devices."""
    coordinator = _coordinator(
        {
            "AA:AA": _device(100, 10),
            "BB:BB": _device(50, 5),
        }
    )

    aggregates = coordinator._calculate_aggregates()

    assert aggregates["total_grid_import"] == 150
    assert aggregates["total_grid_export"] == 15


def test_grid_energy_counter_returns_none_when_counter_missing(
    caplog,
) -> None:
    """Return unavailable rather than treating a missing counter as zero."""
    unavailable_device = _device()
    del unavailable_device["es"]["total_grid_input_energy"]
    coordinator = _coordinator(
        {
            "AA:AA": _device(100),
            "BB:BB": unavailable_device,
        }
    )

    with caplog.at_level(logging.WARNING, logger=coordinator_module.__name__):
        aggregates = coordinator._calculate_aggregates()

    assert aggregates["total_grid_import"] is None
    assert "total_grid_import" in caplog.text
    assert "BB:BB" in caplog.text
    assert "returning None instead of treating missing lifetime energy as zero" in caplog.text


def test_grid_energy_counter_includes_zero() -> None:
    """Include zero as a valid lifetime counter value."""
    coordinator = _coordinator(
        {
            "AA:AA": _device(100),
            "BB:BB": _device(0),
        }
    )

    assert coordinator._calculate_aggregates()["total_grid_import"] == 100


def test_grid_energy_export_returns_none_when_counter_missing() -> None:
    """Return unavailable for a missing grid export lifetime counter."""
    coordinator = _coordinator(
        {
            "AA:AA": _device(100, 10),
            "BB:BB": _device(50, None),
        }
    )

    assert coordinator._calculate_aggregates()["total_grid_export"] is None


def test_grid_energy_counter_logs_missing_macs_once_and_recovers(
    caplog,
) -> None:
    """Log aggregate availability transitions without repeated warnings."""
    devices = {
        "AA:AA": _device(100),
        "BB:BB": _device(None),
        "CC:CC": _device(None),
    }
    coordinator = _coordinator(devices)

    with caplog.at_level(logging.INFO, logger=coordinator_module.__name__):
        assert coordinator._calculate_aggregates()["total_grid_import"] is None
        assert coordinator._calculate_aggregates()["total_grid_import"] is None

        devices["BB:BB"]["es"]["total_grid_input_energy"] = 50
        devices["CC:CC"]["es"]["total_grid_input_energy"] = 25
        assert coordinator._calculate_aggregates()["total_grid_import"] == 175

    messages = [
        record.getMessage()
        for record in caplog.records
        if "total_grid_import" in record.getMessage()
    ]
    warnings = [message for message in messages if "unavailable" in message]
    recoveries = [message for message in messages if "available again" in message]

    assert len(warnings) == 1
    assert "BB:BB" in warnings[0]
    assert "CC:CC" in warnings[0]
    assert len(recoveries) == 1
    assert "BB:BB" in recoveries[0]
    assert "CC:CC" in recoveries[0]
