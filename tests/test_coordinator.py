"""Tests for the Venus A data derivation and staleness logic in the coordinator."""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.marstek_local_api.compatibility import CompatibilityMatrix
from custom_components.marstek_local_api.coordinator import (
    MarstekDataUpdateCoordinator,
    _scale_pv_string_power,
)

VENUS_A_DEVICE = {
    "device": "Venus A",
    "ver": 148,
    "ble_mac": "2cf8ec203003",
    "wifi_mac": "dc396f386c87",
    "ip": "192.168.178.104",
}

# Venus A firmware omits bat_power and hardcodes pv_power/total_pv_energy to 0
def venus_a_es_status(ongrid=-812):
    return {
        "id": 0,
        "bat_soc": 63,
        "bat_cap": 2080,
        "pv_power": 0,
        "ongrid_power": ongrid,
        "offgrid_power": 0,
        "total_pv_energy": 0,
        "total_grid_output_energy": 15054,
        "total_grid_input_energy": 21229,
        "total_load_energy": 0,
    }


def venus_a_pv_status():
    # Raw per-string data as returned by fw 148 (power units differ per string!)
    return {
        "id": 0,
        "pv1_power": 3415, "pv1_voltage": 41, "pv1_current": 8, "pv1_state": 1,
        "pv2_power": 131, "pv2_voltage": 42, "pv2_current": 3, "pv2_state": 1,
        "pv3_power": 0, "pv3_voltage": 1, "pv3_current": 0, "pv3_state": 0,
        "pv4_power": 0, "pv4_voltage": 0, "pv4_current": 0, "pv4_state": 0,
    }


def venus_a_battery_status():
    return {
        "id": 0,
        "soc": 63,
        "charg_flag": True,
        "dischrg_flag": True,
        "bat_temp": 34.0,
        "bat_capacity": 1285.0,
        "rated_capacity": 2080.0,
    }


def make_api(model="Venus A", firmware=148, es=None, pv_side_effect=None):
    api = MagicMock()
    api.get_device_info = AsyncMock(
        return_value=dict(VENUS_A_DEVICE, device=model, ver=firmware)
    )
    api.get_es_status = AsyncMock(side_effect=lambda **kw: dict(es or venus_a_es_status()))
    api.get_battery_status = AsyncMock(side_effect=lambda **kw: dict(venus_a_battery_status()))
    api.get_pv_status = AsyncMock(
        side_effect=pv_side_effect or (lambda **kw: dict(venus_a_pv_status()))
    )
    api.get_em_status = AsyncMock(return_value=None)
    api.get_es_mode = AsyncMock(return_value=None)
    api.get_wifi_status = AsyncMock(return_value=None)
    api.get_ble_status = AsyncMock(return_value=None)
    return api


def make_coordinator(hass, api, model="Venus A", firmware=148):
    return MarstekDataUpdateCoordinator(
        hass,
        api=api,
        device_name="Test Battery",
        firmware_version=firmware,
        device_model=model,
        scan_interval=60,
    )


@pytest.fixture(autouse=True)
def fast_sleep():
    """Skip the inter-command delays in the polling loop."""
    with patch(
        "custom_components.marstek_local_api.coordinator.asyncio.sleep",
        new=AsyncMock(),
    ):
        yield


async def test_venus_a_derives_pv_and_battery_power(hass):
    """pv_power = scaled string sum; bat_power = pv - ongrid - offgrid."""
    api = make_api()
    coordinator = make_coordinator(hass, api)

    data = await coordinator._async_update_data()

    # Per-string unit disambiguated against V*I at receipt:
    # pv1 raw 3415 @ 41V*8A=328 -> deci-W; pv2 raw 131 @ 42V*3A=126 -> plain W
    assert data["pv"]["pv1_power"] == 341.5
    assert data["pv"]["pv2_power"] == 131.0
    # es.pv_power replaced by the string sum
    assert data["es"]["pv_power"] == pytest.approx(472.5)
    # Charging positive: importing 812 W + 472.5 W solar
    assert data["es"]["bat_power"] == pytest.approx(472.5 + 812)


async def test_venus_a_pv_fallback_on_dropped_poll(hass):
    """A dropped PV.GetStatus reuses the last scaled strings."""
    calls = {"n": 0}

    def flaky_pv(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return dict(venus_a_pv_status())
        raise TimeoutError("device dropped the request")

    api = make_api(pv_side_effect=flaky_pv)
    coordinator = make_coordinator(hass, api)

    first = await coordinator._async_update_data()
    coordinator.data = first

    second = await coordinator._async_update_data()

    # Strings kept from the first poll, not re-scaled a second time
    assert second["pv"]["pv1_power"] == 341.5
    assert second["pv"]["pv2_power"] == 131.0
    assert second["es"]["pv_power"] == pytest.approx(472.5)
    assert second["es"]["bat_power"] == pytest.approx(472.5 + 812)


async def test_venus_a_ac_only_when_no_pv_data(hass):
    """Without any PV data the derivation falls back to the AC-side flows."""
    api = make_api(pv_side_effect=lambda **kw: (_ for _ in ()).throw(TimeoutError()))
    coordinator = make_coordinator(hass, api)

    data = await coordinator._async_update_data()

    assert "pv" not in data
    # es.pv_power stays at the firmware value (0)
    assert data["es"]["pv_power"] == 0
    assert data["es"]["bat_power"] == pytest.approx(812)


async def test_venus_e_path_unchanged(hass):
    """Venus E: no PV polling on the fast tier, no bat_power synthesis."""
    es = {
        "id": 0,
        "bat_power": 500,
        "pv_power": 0,
        "ongrid_power": -500,
        "offgrid_power": 0,
        "total_grid_input_energy": 100,
        "total_grid_output_energy": 100,
        "total_load_energy": 100,
    }
    api = make_api(model="VenusE", firmware=154, es=es)
    coordinator = make_coordinator(hass, api, model="VenusE", firmware=154)

    data = await coordinator._async_update_data()

    api.get_pv_status.assert_not_awaited()
    # fw 154: bat_power raw W, energies in centi-Wh (x100)
    assert data["es"]["bat_power"] == 500.0
    assert data["es"]["total_grid_input_energy"] == 10000.0
    assert data["es"]["pv_power"] == 0


async def test_staleness_uses_polling_tier(hass):
    """em/mode stay fresh across their 300s polling gap; es does not."""
    api = make_api()
    coordinator = make_coordinator(hass, api)

    now = time.time()
    coordinator.category_last_updated = {
        "es": now - 250,
        "em": now - 250,
        "mode": now - 250,
        "pv": now - 250,
    }

    # base interval 60s, threshold 3: fast tier limit 180s, medium 900s
    assert not coordinator.is_category_fresh("es")
    assert coordinator.is_category_fresh("em")
    assert coordinator.is_category_fresh("mode")
    # Venus A polls pv on the fast tier -> stale at 250s
    assert not coordinator.is_category_fresh("pv")


async def test_staleness_pv_medium_tier_on_venus_d(hass):
    api = make_api(model="VenusD", firmware=154)
    coordinator = make_coordinator(hass, api, model="VenusD", firmware=154)

    coordinator.category_last_updated = {"pv": time.time() - 250}
    assert coordinator.is_category_fresh("pv")


class TestPvStringUnitDisambiguation:
    """Venus A fw 148 mixes deci-W and plain W between strings (live-observed)."""

    compat = CompatibilityMatrix("Venus A", 148)

    def test_deci_watt_string(self):
        # live sample: raw 541 @ 47V*1A -> 54.1 W
        pv = {"pv1_power": 541, "pv1_voltage": 47, "pv1_current": 1}
        assert _scale_pv_string_power(self.compat, pv, 1) == 54.1

    def test_plain_watt_string(self):
        # live sample: raw 323 @ 40V*7A=280 -> 323 W (app shows 324)
        pv = {"pv2_power": 323, "pv2_voltage": 40, "pv2_current": 7}
        assert _scale_pv_string_power(self.compat, pv, 2) == 323.0

    def test_low_reference_falls_back_to_matrix(self):
        # V*I too small to discriminate -> matrix scaling (deci-W)
        pv = {"pv1_power": 40, "pv1_voltage": 45, "pv1_current": 0}
        assert _scale_pv_string_power(self.compat, pv, 1) == 4.0

    def test_missing_voltage_falls_back_to_matrix(self):
        pv = {"pv1_power": 541}
        assert _scale_pv_string_power(self.compat, pv, 1) == 54.1

    def test_missing_power_returns_none(self):
        assert _scale_pv_string_power(self.compat, {}, 1) is None


class TestCircuitBreaker:
    """Reduced polling pressure while the device answers nothing."""

    @staticmethod
    def dead_api():
        api = MagicMock()
        for name in (
            "get_device_info", "get_es_status", "get_battery_status",
            "get_pv_status", "get_em_status", "get_es_mode",
            "get_wifi_status", "get_ble_status",
        ):
            setattr(api, name, AsyncMock(side_effect=TimeoutError("dead")))
        return api

    async def run_cycles(self, coordinator, n):
        for _ in range(n):
            coordinator.data = await coordinator._async_update_data()

    async def test_reduces_to_single_probe_when_unresponsive(self, hass):
        api = self.dead_api()
        coordinator = make_coordinator(hass, api)

        # first update + threshold full cycles
        await self.run_cycles(coordinator, 4)
        assert coordinator._consecutive_failed_cycles >= 3

        es_before = api.get_es_status.await_count
        bat_before = api.get_battery_status.await_count
        await self.run_cycles(coordinator, 2)
        # wedged cycles: exactly one es probe each, nothing else
        assert api.get_es_status.await_count == es_before + 2
        assert api.get_battery_status.await_count == bat_before
        api.get_es_status.assert_awaited_with(timeout=5, max_attempts=1)

    async def test_recovers_to_full_polling(self, hass):
        api = self.dead_api()
        coordinator = make_coordinator(hass, api)
        await self.run_cycles(coordinator, 4)
        assert coordinator._consecutive_failed_cycles >= 3

        # device comes back
        api.get_es_status = AsyncMock(side_effect=lambda **kw: dict(venus_a_es_status()))
        api.get_battery_status = AsyncMock(side_effect=lambda **kw: dict(venus_a_battery_status()))
        api.get_pv_status = AsyncMock(side_effect=lambda **kw: dict(venus_a_pv_status()))

        coordinator.data = await coordinator._async_update_data()

        assert coordinator._consecutive_failed_cycles == 0
        # full cycle ran after the successful probe
        assert api.get_battery_status.await_count == 1
        assert coordinator.data["es"]["bat_power"] is not None
