"""Coordinator marks entities unavailable once the battery has been unreachable
for longer than STALE_AFTER, and keeps the last values for shorter outages."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sonnenbatterie import coordinator as coord_mod
from custom_components.sonnenbatterie.coordinator import SonnenbatterieCoordinator

DATA = {
    "battery_system": {"battery_system": {"system": {"storage_capacity_per_module": 2500}}, "modules": 4},
    "status": {"BatteryCharging": False, "BatteryDischarging": False, "RSOC": 80},
}


def make_client():
    c = MagicMock()
    for name in ("login", "logout", "get_battery", "get_inverter", "get_powermeter",
                 "get_systemdata", "get_api_configuration", "get_commissioning_settings"):
        setattr(c, name, AsyncMock(return_value={}))
    c.get_status = AsyncMock(return_value=DATA["status"])
    c.get_batterysystem = AsyncMock(return_value=DATA["battery_system"])
    c.sb2 = MagicMock()
    for name in ("get_status", "get_configurations", "get_latest_data"):
        setattr(c.sb2, name, AsyncMock(return_value={}))
    return c


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def setup(hass):
    entry = MockConfigEntry(domain="sonnenbatterie", data={
        "username": "User", "password": "x", "ip_address": "192.0.2.1"})
    entry.add_to_hass(hass)
    client = make_client()
    clock = Clock()
    with patch.object(coord_mod, "AsyncSonnenBatterie", return_value=client), \
         patch.object(coord_mod, "time", clock):
        coordinator = SonnenbatterieCoordinator(hass, entry, "175206")
        yield coordinator, client, clock


async def test_short_outage_keeps_values(setup):
    coordinator, client, clock = setup
    await coordinator.async_refresh()
    assert coordinator.last_update_success

    client.get_battery.side_effect = TimeoutError("socket read")
    for _ in range(4):              # 0 s, 60 s, 120 s, 180 s of failures
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        clock.now += 60
    assert coordinator.latestData["status"]["RSOC"] == 80


async def test_long_outage_marks_unavailable_then_recovers(setup):
    coordinator, client, clock = setup
    await coordinator.async_refresh()

    client.get_battery.side_effect = TimeoutError("socket read")
    await coordinator.async_refresh()   # first failure starts the clock
    clock.now += 181
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert "181 seconds" in str(coordinator.last_exception)

    client.get_battery.side_effect = None
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator._last_error is None

    # A new outage gets a fresh grace period.
    client.get_battery.side_effect = TimeoutError("socket read")
    clock.now += 500
    await coordinator.async_refresh()
    assert coordinator.last_update_success


async def test_no_data_yet_fails_immediately(setup):
    coordinator, client, clock = setup
    client.get_battery.side_effect = TimeoutError("socket read")
    await coordinator.async_refresh()
    assert not coordinator.last_update_success


async def test_login_failure_is_reported(setup):
    coordinator, client, clock = setup
    client.login.side_effect = OSError("host unreachable")
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
