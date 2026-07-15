"""Tests for the version/hardware compatibility matrix."""
import pytest

from custom_components.marstek_local_api.compatibility import (
    HW_VERSION_2,
    HW_VERSION_3,
    HW_VERSION_VENUS_A,
    CompatibilityMatrix,
    get_base_model,
    parse_hardware_version,
)


@pytest.mark.parametrize(
    ("device_model", "expected"),
    [
        ("VenusE", HW_VERSION_2),
        ("VenusC", HW_VERSION_2),
        ("VenusD", HW_VERSION_2),
        ("VenusE 3.0", HW_VERSION_3),
        ("Venus A", HW_VERSION_VENUS_A),
        ("VenusA", HW_VERSION_VENUS_A),
        ("venus a", HW_VERSION_VENUS_A),
        ("", HW_VERSION_2),
    ],
)
def test_parse_hardware_version(device_model, expected):
    """Model strings map to the right hardware profile."""
    assert parse_hardware_version(device_model) == expected


def test_get_base_model_strips_version_suffix():
    assert get_base_model("VenusE 3.0") == "VenusE"
    assert get_base_model("VenusE") == "VenusE"
    assert get_base_model("") == ""


class TestVenusAScaling:
    """Venus A (fw 148) reports plain units; nothing should be rescaled."""

    matrix = CompatibilityMatrix("Venus A", 148)

    def test_hardware_detection(self):
        assert self.matrix.hardware_version == HW_VERSION_VENUS_A

    @pytest.mark.parametrize(
        ("field", "raw", "expected"),
        [
            ("bat_temp", 34.0, 34.0),
            ("bat_capacity", 1285.0, 1285.0),
            ("bat_power", 812, 812.0),
            ("total_grid_input_energy", 21229, 21229.0),
            ("total_grid_output_energy", 15054, 15054.0),
            ("total_load_energy", 0, 0.0),
        ],
    )
    def test_plain_units(self, field, raw, expected):
        assert self.matrix.scale_value(raw, field) == expected

    def test_pv_string_power_is_deci_watts(self):
        # Observed live: raw 3415 at 41 V / ~8.3 A -> 341.5 W
        assert self.matrix.scale_value(3415, "pv_string_power") == 341.5

    def test_unknown_field_passthrough(self):
        assert self.matrix.scale_value(123, "ongrid_power") == 123

    def test_none_passthrough(self):
        assert self.matrix.scale_value(None, "bat_temp") is None


class TestVenus2Scaling:
    """HW 2.0 profiles must be unaffected by the Venus A additions."""

    def test_old_firmware_energy_is_deca_wh(self):
        matrix = CompatibilityMatrix("VenusE", 153)
        assert matrix.scale_value(100, "total_grid_input_energy") == 1000.0

    def test_new_firmware_energy_is_centi_wh(self):
        matrix = CompatibilityMatrix("VenusE", 154)
        assert matrix.scale_value(100, "total_grid_input_energy") == 10000.0

    def test_old_firmware_bat_power_is_deca_w(self):
        matrix = CompatibilityMatrix("VenusE", 100)
        assert matrix.scale_value(100, "bat_power") == 10.0

    def test_new_firmware_bat_temp_times_ten(self):
        # (HW2, 154) divisor is 0.1, i.e. raw x 10 (upstream behavior, PR #32)
        matrix = CompatibilityMatrix("VenusE", 154)
        assert matrix.scale_value(3.45, "bat_temp") == pytest.approx(34.5)

    def test_pv_string_power_not_scaled_for_hw2(self):
        # No HW 2.0 entry exists for pv_string_power -> raw passthrough
        matrix = CompatibilityMatrix("VenusD", 154)
        assert matrix.scale_value(341, "pv_string_power") == 341


class TestVenus3Scaling:
    def test_temp_deca_c_from_fw139(self):
        matrix = CompatibilityMatrix("VenusE 3.0", 139)
        assert matrix.scale_value(340, "bat_temp") == 34.0

    def test_capacity_deci_wh_from_fw139(self):
        matrix = CompatibilityMatrix("VenusE 3.0", 139)
        assert matrix.scale_value(100, "bat_capacity") == 1000.0

    def test_energy_plain_wh(self):
        matrix = CompatibilityMatrix("VenusE 3.0", 139)
        assert matrix.scale_value(21229, "total_grid_input_energy") == 21229.0
