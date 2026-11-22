from unittest.mock import MagicMock

import pytest
from flask import Flask

from blueprints.settings import settings_bp
from utils import i18n
from utils.power_manager import PowerManager


class FakeDeviceConfig:
    def __init__(self, config=None):
        self.config = dict(config or {})

    def get_config(self, key=None, default={}):
        if key is not None:
            return self.config.get(key, default)
        return self.config

    def update_config(self, config):
        self.config.update(config)

    def write_config(self):
        pass


@pytest.fixture
def setup(tmp_path):
    device_config = FakeDeviceConfig({"plugin_cycle_interval_seconds": 3600, "language": "en"})
    power_manager = PowerManager(device_config, dev_mode=True, state_file=str(tmp_path / "state.json"))
    refresh_task = MagicMock()

    app = Flask(__name__)
    app.config.update(DEVICE_CONFIG=device_config, POWER_MANAGER=power_manager, REFRESH_TASK=refresh_task)
    i18n.init_app(app, device_config)
    app.register_blueprint(settings_bp)
    return app.test_client(), power_manager, refresh_task


SETTINGS_FORM = {
    "unit": "hour",
    "interval": "1",
    "timezoneName": "UTC",
    "timeFormat": "24h",
    "ps_awake_duration": "3",
    "ps_awake_unit": "minute",
    "ps_sleep_duration": "1",
    "ps_sleep_unit": "hour",
    "ps_turn_off_wifi": "on",
    "ps_turn_off_bluetooth": "on",
    "ps_turn_off_hdmi": "on",
    "ps_turn_off_led": "on",
}


# --- I8: /power_save/toggle ---

@pytest.mark.parametrize("value, expected", [
    (True, True), (False, False), ("true", True), ("false", False), ("0", False), (1, True),
])
def test_toggle_parses_active(setup, value, expected):
    client, power_manager, _ = setup
    power_manager.is_active = not expected

    res = client.post("/power_save/toggle", json={"active": value})

    assert res.status_code == 200
    assert power_manager.is_active is expected
    assert res.get_json()["active"] is expected
    assert res.get_json()["message"] == ("Power save mode activated." if expected else "Power save mode deactivated.")


def test_toggle_rejects_invalid_active(setup):
    client, power_manager, _ = setup

    res = client.post("/power_save/toggle", json={"active": "maybe"})

    assert res.status_code == 400
    assert power_manager.is_active is False


def test_toggle_without_value_flips_state(setup):
    client, power_manager, _ = setup

    client.post("/power_save/toggle", json={})
    assert power_manager.is_active is True
    client.post("/power_save/toggle", json={})
    assert power_manager.is_active is False


# --- I7: /power_save/keep_awake ---

@pytest.mark.parametrize("seconds, expected", [(-100, 60), (5, 60), (600, 600), (10**9, 3600)])
def test_keep_awake_clamps_seconds(setup, seconds, expected, monkeypatch):
    client, power_manager, _ = setup
    requested = []
    monkeypatch.setattr(power_manager, "request_keep_awake", requested.append)

    res = client.post("/power_save/keep_awake", json={"seconds": seconds})

    assert res.status_code == 200
    assert requested == [expected]


def test_keep_awake_rejects_non_numeric(setup):
    client, _, _ = setup

    res = client.post("/power_save/keep_awake", json={"seconds": "abc"})

    assert res.status_code == 400


# --- I9 / I10: /save_settings ---

def test_save_settings_unchanged_does_not_signal_refresh_task(setup):
    client, _, refresh_task = setup
    client.post("/save_settings", data=SETTINGS_FORM)
    refresh_task.signal_config_change.reset_mock()

    res = client.post("/save_settings", data=SETTINGS_FORM)

    assert res.status_code == 200
    refresh_task.signal_config_change.assert_not_called()


def test_save_settings_signals_on_schedule_change(setup):
    client, _, refresh_task = setup
    client.post("/save_settings", data=SETTINGS_FORM)
    refresh_task.signal_config_change.reset_mock()

    client.post("/save_settings", data={**SETTINGS_FORM, "interval": "2"})
    assert refresh_task.signal_config_change.call_count == 1

    client.post("/save_settings", data={**SETTINGS_FORM, "interval": "2", "ps_sleep_duration": "2"})
    assert refresh_task.signal_config_change.call_count == 2


def test_save_settings_does_not_change_power_save_activation(setup):
    client, power_manager, _ = setup

    client.post("/save_settings", data={**SETTINGS_FORM, "ps_active_now": "on"})

    assert power_manager.is_active is False
