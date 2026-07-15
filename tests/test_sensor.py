"""Tests for per-model sensor gating and the per-string PV descriptions."""
from types import SimpleNamespace

import pytest

from custom_components.marstek_local_api.compatibility import CompatibilityMatrix
from custom_components.marstek_local_api.sensor import (
    EnergyIntegrator,
    INTEGRATED_ENERGY_SENSOR_TYPES,
    PV_STRING_SENSOR_TYPES,
    SENSOR_TYPES,
    VENUS_A_UNSUPPORTED_KEYS,
    VENUS_A_ZERO_COUNTER_KEYS,
    _standard_sensor_types,
)


def coordinator_stub(model, firmware):
    return SimpleNamespace(compatibility=CompatibilityMatrix(model, firmware))


def test_venus_a_skips_fields_the_firmware_never_sends():
    keys = {d.key for d in _standard_sensor_types(coordinator_stub("Venus A", 148))}
    assert keys & VENUS_A_UNSUPPORTED_KEYS == set()
    # Everything else is still there
    assert "battery_power" in keys
    assert "battery_soc" in keys
    assert "total_grid_import" in keys


def test_venus_a_zero_counters_disabled_by_default():
    descriptions = {
        d.key: d for d in _standard_sensor_types(coordinator_stub("Venus A", 148))
    }
    for key in VENUS_A_ZERO_COUNTER_KEYS:
        assert descriptions[key].entity_registry_enabled_default is False
    # Working counters stay enabled
    assert descriptions["total_grid_import"].entity_registry_enabled_default is True


def test_other_models_get_unmodified_sensor_types():
    for model, firmware in (("VenusE", 154), ("VenusE 3.0", 139), ("VenusD", 154), ("VenusC", 154)):
        assert _standard_sensor_types(coordinator_stub(model, firmware)) is SENSOR_TYPES


def test_pv_string_descriptions_cover_four_strings_without_current():
    keys = {d.key for d in PV_STRING_SENSOR_TYPES}
    expected = set()
    for n in range(1, 5):
        expected.add(f"pv{n}_power")
        expected.add(f"pv{n}_voltage")
    assert keys == expected


def test_pv_string_value_fns_bind_their_own_key():
    """Guard against the classic late-binding closure bug in the comprehension."""
    data = {"pv": {f"pv{n}_power": float(n) for n in range(1, 5)}}
    data["pv"].update({f"pv{n}_voltage": 10.0 * n for n in range(1, 5)})

    for description in PV_STRING_SENSOR_TYPES:
        assert description.value_fn(data) == data["pv"][description.key]


def test_pv_string_value_fn_handles_missing_data():
    assert PV_STRING_SENSOR_TYPES[0].value_fn({}) is None


class TestEnergyIntegrator:
    """Left-Riemann accumulation with gap and None handling."""

    def test_accumulates_left_riemann(self):
        integ = EnergyIntegrator(max_gap_seconds=180)
        integ.add_sample(100.0, 1000.0)
        # 100 W held for 60 s = 1.667 Wh, regardless of the new sample's value
        assert integ.add_sample(500.0, 1060.0) == pytest.approx(100 * 60 / 3600)
        # 500 W held for another 60 s
        assert integ.add_sample(0.0, 1120.0) == pytest.approx((100 + 500) * 60 / 3600)

    def test_gap_longer_than_max_is_skipped(self):
        integ = EnergyIntegrator(max_gap_seconds=180)
        integ.add_sample(100.0, 1000.0)
        # 10-minute gap (restart / device offline): no accumulation
        assert integ.add_sample(100.0, 1600.0) == 0.0
        # but integration resumes afterwards
        assert integ.add_sample(100.0, 1660.0) == pytest.approx(100 * 60 / 3600)

    def test_none_power_pauses_accumulation(self):
        integ = EnergyIntegrator(max_gap_seconds=180)
        integ.add_sample(None, 1000.0)
        assert integ.add_sample(100.0, 1060.0) == 0.0
        assert integ.add_sample(None, 1120.0) == pytest.approx(100 * 60 / 3600)
        # stale sample contributed nothing further
        assert integ.add_sample(100.0, 1180.0) == pytest.approx(100 * 60 / 3600)

    def test_non_positive_elapsed_ignored(self):
        integ = EnergyIntegrator(max_gap_seconds=180)
        integ.add_sample(100.0, 1000.0)
        assert integ.add_sample(100.0, 1000.0) == 0.0
        assert integ.add_sample(100.0, 990.0) == 0.0


def test_integrated_energy_descriptions_split_power_by_sign():
    descriptions = {d.key: d for d in INTEGRATED_ENERGY_SENSOR_TYPES}
    assert set(descriptions) == {
        "pv_energy_integrated",
        "battery_energy_in_integrated",
        "battery_energy_out_integrated",
    }
    charging = {"es": {"bat_power": 500, "pv_power": 120}}
    discharging = {"es": {"bat_power": -300, "pv_power": 0}}
    assert descriptions["battery_energy_in_integrated"].value_fn(charging) == 500
    assert descriptions["battery_energy_in_integrated"].value_fn(discharging) == 0
    assert descriptions["battery_energy_out_integrated"].value_fn(charging) == 0
    assert descriptions["battery_energy_out_integrated"].value_fn(discharging) == 300
    assert descriptions["pv_energy_integrated"].value_fn(charging) == 120
