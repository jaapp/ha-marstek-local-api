"""Tests for config-flow abort paths (already-configured devices)."""
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResultType

try:
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
except ImportError:  # pragma: no cover - older cores
    from homeassistant.components.dhcp import DhcpServiceInfo

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek_local_api.const import CONF_PORT, DOMAIN

DEVICE_INFO = {
    "title": "Venus A",
    "device": "Venus A",
    "firmware": 148,
    "ble_mac": "2cf8ec203003",
    "wifi_mac": "dc396f386c87",
}


def existing_entry(host="192.168.178.50"):
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_INFO["ble_mac"],
        data={
            CONF_HOST: host,
            CONF_PORT: 30000,
            "ble_mac": DEVICE_INFO["ble_mac"],
            "wifi_mac": DEVICE_INFO["wifi_mac"],
            "device": DEVICE_INFO["device"],
            "firmware": DEVICE_INFO["firmware"],
        },
    )


def patch_validate_input():
    return patch(
        "custom_components.marstek_local_api.config_flow.validate_input",
        new=AsyncMock(return_value=dict(DEVICE_INFO)),
    )


def dhcp_renewal(ip="192.168.178.104"):
    return DhcpServiceInfo(ip=ip, hostname="venus", macaddress="2cf8ec205731")


async def test_dhcp_rediscovery_aborts_already_configured(hass):
    """A DHCP lease renewal of a configured device aborts cleanly, updating the host.

    Regression: AbortFlow raised by _abort_if_unique_id_configured() was caught
    by the broad exception handler and logged as an ERROR on every renewal.
    """
    entry = existing_entry(host="192.168.178.50")
    entry.add_to_hass(hass)

    with patch_validate_input():
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_DHCP},
            data=dhcp_renewal(ip="192.168.178.104"),
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.178.104"


async def test_dhcp_discovery_new_device_asks_confirmation(hass):
    with patch_validate_input():
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_DHCP},
            data=dhcp_renewal(),
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"


async def test_manual_setup_aborts_already_configured(hass):
    """Manually re-adding a configured device aborts instead of showing 'unknown'."""
    entry = existing_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "manual"}
    )
    assert result["type"] == FlowResultType.FORM

    with patch_validate_input():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.178.50", CONF_PORT: 30000}
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
